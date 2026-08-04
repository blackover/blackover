"""Chase-view 3D renderer.

A small software pipeline: world NED -> camera space -> near-plane clip ->
perspective projection -> screen clip -> painter's algorithm. There is no
depth buffer, so polygons are sorted back to front, which is adequate for a
heightfield and a handful of convex bodies and costs nothing to explain.

The camera keeps world up as its up vector rather than rolling with the
aircraft. That is what makes a chase view readable: the horizon stays level
and the aircraft visibly banks against it, instead of the aircraft staying
level and the whole world rotating around it.

Everything expensive is done for the whole frame at once. NumPy on
four-element arrays is almost entirely call overhead, so a per-polygon
transform-project-cull chain over a thousand cells costs more than the
rasterisation it feeds.

The renderer is strictly a viewer. It reads the state and feeds nothing back.
"""

from __future__ import annotations

import math

import numpy as np
import pygame

from .sky import Sky, lerp

NEAR_PLANE = 0.35

# Terrain detail: a cascade of square rings, each with cells RING_FACTOR times
# larger than the ring inside it and hollowed out where that finer ring already
# covers. Three levels reach 64 times the near ring's radius for three times
# its cost, which two levels cannot do -- either the near cells grow until the
# heightfield is undersampled and the ground reads as folded paper, or the far
# edge closes in until the horizon is a visible wall of quads.
#
# Cell count is the single biggest lever on frame time, so these are sized
# against a 60 Hz budget and the overlap between rings is paid for twice.
RING_CELLS = 13
RING_FACTOR = 4
RING_LEVELS = 3

# Near-cell size, before the altitude-dependent snapping below.
MIN_CELL = 120.0  # m
MAX_CELL = 1600.0  # m


def _snap(size: float, maximum: float = MAX_CELL) -> float:
    """Round a cell size to one, two or five times a power of ten.

    Snapping at all is what stops the grid shimmering as the aircraft climbs:
    a cell size that varies continuously slides the whole heightfield lattice
    under the view every frame.

    The exponent has to be a *floor*, not a round. Rounding it sends anything
    above 3.16 x 10^k up to the next decade, and ``size / magnitude`` then
    rounds to zero -- which pinned the near cell to its own floor for every
    altitude between about 900 m and 7 km, and cost the outer ring three
    quarters of its reach at exactly the altitudes where the horizon is
    furthest away.
    """
    size = max(MIN_CELL, min(maximum, size))
    magnitude = 10.0 ** math.floor(math.log10(size))
    for multiple in (1.0, 2.0, 5.0, 10.0):
        candidate = multiple * magnitude
        if size <= candidate * 1.5:
            return max(MIN_CELL, min(maximum, candidate))
    return max(MIN_CELL, min(maximum, 10.0 * magnitude))


_lerp = lerp  # re-exported for callers that imported it from here


class Camera:
    """Chase camera: behind and above the aircraft, world-up, looking ahead."""

    def __init__(self, fov_degrees: float = 60.0) -> None:
        self.fov = math.radians(fov_degrees)
        self.distance = 42.0
        self.height = 9.0
        self.position = np.zeros(3)
        self.basis = np.eye(3)  # rows: right, up, forward -- all in NED
        self._smoothed_heading: float | None = None
        self._smoothed_pitch = 0.0

    def follow(self, state, dt: float) -> None:
        """Place the camera behind the aircraft's heading, smoothed.

        The camera lags the aircraft's heading through a first-order filter.
        Without it, a rapid roll snaps the whole world sideways and the view
        becomes unreadable exactly when the most is happening.
        """
        derived = state.derived
        heading = derived.yaw
        if self._smoothed_heading is None:
            self._smoothed_heading = heading
            self._smoothed_pitch = derived.pitch
        else:
            delta = math.atan2(
                math.sin(heading - self._smoothed_heading),
                math.cos(heading - self._smoothed_heading),
            )
            blend = 1.0 - math.exp(-dt / 0.45)
            self._smoothed_heading += delta * blend
            self._smoothed_pitch += (derived.pitch - self._smoothed_pitch) * (
                1.0 - math.exp(-dt / 0.70)
            )

        # Follow only part of the aircraft's pitch. Following all of it keeps
        # the aircraft centred but hides the very thing a chase view exists to
        # show: a 40 degree climb then looks identical to level flight.
        pitch = max(-0.55, min(0.55, self._smoothed_pitch * 0.45))

        back = np.array(
            [
                -math.cos(self._smoothed_heading) * math.cos(pitch),
                -math.sin(self._smoothed_heading) * math.cos(pitch),
                math.sin(pitch),
            ]
        )
        self.position = (
            state.position + back * self.distance + np.array([0.0, 0.0, -self.height])
        )
        self._look_at(state.position + np.array([0.0, 0.0, -0.05 * self.distance]))

    def _look_at(self, target: np.ndarray) -> None:
        forward = target - self.position
        norm = float(np.linalg.norm(forward))
        forward = np.array([1.0, 0.0, 0.0]) if norm < 1.0e-6 else forward / norm

        world_up = np.array([0.0, 0.0, -1.0])
        right = np.cross(forward, world_up)
        norm = float(np.linalg.norm(right))
        right = np.array([0.0, 1.0, 0.0]) if norm < 1.0e-6 else right / norm
        up = np.cross(right, forward)

        self.basis = np.array([right, up, forward])

    def to_camera(self, points_ned: np.ndarray) -> np.ndarray:
        """Transform an (n, 3) array of NED points into camera space."""
        return (points_ned - self.position) @ self.basis.T


class Renderer:
    """Draws the world and the aircraft into a pygame surface."""

    def __init__(self, surface: pygame.Surface, sky: Sky) -> None:
        self.surface = surface
        self.sky = sky
        self.camera = Camera()
        self.viewport = surface.get_rect()
        self.show_shadow = True
        self._mesh_cache: dict[int, tuple] = {}
        # Terrain vertex grids, one per LOD ring, keyed by cell size.
        self._ring_cache: dict[tuple, tuple] = {}
        self.resize(surface)

    def resize(self, surface: pygame.Surface) -> None:
        self.surface = surface
        self.resize_view(surface.get_rect())

    def resize_view(self, rect: pygame.Rect) -> None:
        """Aim the projection at a sub-rectangle of the window.

        The 3D view occupies only the area above the instrument panel, so the
        principal point has to be the centre of *that* rectangle. Projecting
        about the window centre instead puts the horizon behind the panel.
        """
        if rect.width <= 0 or rect.height <= 0:
            return
        self.viewport = pygame.Rect(rect)
        self.width, self.height = rect.width, rect.height
        self.centre = (rect.centerx, rect.centery)
        self.focal = (rect.width * 0.5) / math.tan(self.camera.fov * 0.5)

    # -- projection --------------------------------------------------------

    def project(self, camera_points: np.ndarray) -> np.ndarray:
        """Perspective projection of camera-space points to screen pixels."""
        z = np.maximum(camera_points[:, 2], 1.0e-6)
        sx = self.centre[0] + self.focal * camera_points[:, 0] / z
        sy = self.centre[1] - self.focal * camera_points[:, 1] / z
        return np.stack([sx, sy], axis=1)

    @staticmethod
    def clip_near(points: np.ndarray) -> np.ndarray | None:
        """Sutherland-Hodgman clip of a camera-space polygon at the near plane.

        Without this a polygon straddling the camera plane projects with one
        vertex behind the eye, which flings that vertex to the far side of the
        screen and paints a triangle across the whole view.
        """
        inside = points[:, 2] >= NEAR_PLANE
        if inside.all():
            return points
        if not inside.any():
            return None

        out: list[np.ndarray] = []
        count = len(points)
        for i in range(count):
            current, nxt = points[i], points[(i + 1) % count]
            current_in, next_in = inside[i], inside[(i + 1) % count]
            if current_in:
                out.append(current)
            if current_in != next_in:
                span = nxt[2] - current[2]
                if abs(span) > 1.0e-12:
                    t = (NEAR_PLANE - current[2]) / span
                    out.append(current + t * (nxt - current))
        return np.array(out) if len(out) >= 3 else None

    @staticmethod
    def clip_to_rect(points, left, top, right, bottom):
        """Sutherland-Hodgman clip of a screen polygon to the viewport.

        A performance measure, not a correctness one -- pygame already clips
        what it draws, but it clips while *rasterising*, so a ground quad
        320 km across still costs a scan conversion over its whole bounding
        box. Bounding it first turns the terrain from three frames a second
        into sixty.
        """
        def clip(pts, inside, intersect):
            if not pts:
                return pts
            out = []
            count = len(pts)
            for i in range(count):
                current, nxt = pts[i], pts[(i + 1) % count]
                current_in, next_in = inside(current), inside(nxt)
                if current_in:
                    out.append(current)
                if current_in != next_in:
                    out.append(intersect(current, nxt))
            return out

        def lerp_x(a, b, x):
            t = (x - a[0]) / (b[0] - a[0])
            return (x, a[1] + t * (b[1] - a[1]))

        def lerp_y(a, b, y):
            t = (y - a[1]) / (b[1] - a[1])
            return (a[0] + t * (b[0] - a[0]), y)

        points = clip(points, lambda p: p[0] >= left, lambda a, b: lerp_x(a, b, left))
        points = clip(points, lambda p: p[0] <= right, lambda a, b: lerp_x(a, b, right))
        points = clip(points, lambda p: p[1] >= top, lambda a, b: lerp_y(a, b, top))
        points = clip(points, lambda p: p[1] <= bottom, lambda a, b: lerp_y(a, b, bottom))
        return points

    def _fill(self, camera_points: np.ndarray, colour) -> None:
        clipped = self.clip_near(camera_points)
        if clipped is None:
            return
        screen = self.project(clipped)

        viewport = self.viewport
        left, top = float(viewport.left), float(viewport.top)
        right, bottom = float(viewport.right), float(viewport.bottom)

        if (
            screen[:, 0].max() < left
            or screen[:, 0].min() > right
            or screen[:, 1].max() < top
            or screen[:, 1].min() > bottom
        ):
            return

        points = [(float(p[0]), float(p[1])) for p in screen]
        if (
            screen[:, 0].min() < left
            or screen[:, 0].max() > right
            or screen[:, 1].min() < top
            or screen[:, 1].max() > bottom
        ):
            points = self.clip_to_rect(points, left, top, right, bottom)
        if len(points) < 3:
            return
        try:
            pygame.draw.polygon(self.surface, colour, points)
        except (ValueError, TypeError):
            pass

    # -- sky ---------------------------------------------------------------

    def horizon_y(self) -> float:
        """Screen row the true horizon projects to.

        All horizontal directions project to one line, because the camera
        keeps world up as its up vector. Taking the camera's own forward
        direction flattened into the horizontal plane gives a point on it.
        """
        forward = self.camera.basis[2]
        flat = np.array([forward[0], forward[1], 0.0])
        norm = float(np.linalg.norm(flat))
        if norm < 1.0e-9:
            # Straight up or straight down: the horizon is off the screen
            # entirely, above it when looking down and below it when looking
            # up. z is down-positive, so its sign says which. Ten viewport
            # heights away rather than a nominal infinity, because the caller
            # turns this into a pygame Rect and pygame rectangles are 32-bit.
            return self.centre[1] - 10.0 * self.height * (
                1.0 if forward[2] > 0.0 else -1.0
            )
        flat /= norm
        along = float(forward @ flat)  # the horizontal component's length
        return self.centre[1] - self.focal * float(self.camera.basis[1] @ flat) / along

    def draw_sky(self) -> None:
        gradient = self.sky.gradient(self.width, self.height)
        viewport = self.viewport

        # The strip carries the horizon colour at its middle row; slide it so
        # that row sits on the projected horizon. Looking far enough up or down
        # slides it clear of the viewport, and the gap it leaves is whichever
        # end of the gradient ran out.
        top = int(round(self.horizon_y())) - self.height
        bottom = top + gradient.get_height()

        previous = self.surface.get_clip()
        self.surface.set_clip(viewport)
        if top > viewport.top:
            self.surface.fill(
                self.sky.zenith,
                pygame.Rect(viewport.left, viewport.top, self.width, top - viewport.top),
            )
        if bottom < viewport.bottom:
            self.surface.fill(
                self.sky.horizon,
                pygame.Rect(viewport.left, bottom, self.width, viewport.bottom - bottom),
            )
        self.surface.blit(gradient, (viewport.left, top))
        self._draw_sun()
        self.surface.set_clip(previous)

    def _draw_sun(self) -> None:
        """Blit the sun where it projects, if it is in front of the camera."""
        if self.sky.daylight <= 0.02:
            return
        direction = self.sky.to_sun_ned
        camera_direction = self.camera.basis @ direction
        if camera_direction[2] <= 0.05:
            return

        sprite = self.sky.sun_sprite()
        x = self.centre[0] + self.focal * camera_direction[0] / camera_direction[2]
        y = self.centre[1] - self.focal * camera_direction[1] / camera_direction[2]
        rect = sprite.get_rect(center=(int(x), int(y)))
        if rect.colliderect(self.viewport):
            self.surface.blit(sprite, rect, special_flags=pygame.BLEND_RGBA_ADD)

    def draw_clouds(self, state) -> None:
        """Billboarded puffs at the cloud base, back to front."""
        clouds = self.sky.clouds()
        if len(clouds) == 0:
            return

        camera = self.camera
        # Tile the field so it repeats around the aircraft instead of running
        # out at the edges of one patch.
        extent = 120000.0
        offset = np.round(state.position[:2] / extent) * extent
        positions = clouds[:, :3].copy()
        positions[:, 0] += offset[0]
        positions[:, 1] += offset[1]

        cameraspace = camera.to_camera(positions)
        depth = cameraspace[:, 2]
        visible = (depth > 200.0) & (depth < self.sky.visibility * 3.0)
        if not visible.any():
            return

        indices = np.flatnonzero(visible)
        order = indices[np.argsort(-depth[indices])]

        sprites = self.sky.cloud_sprites()
        focal = self.focal
        cx, cy = self.centre
        viewport = self.viewport
        surface = self.surface

        for i in order:
            z = depth[i]
            screen_size = clouds[i, 3] * focal / z
            if screen_size < 22.0 or screen_size > 6000.0:
                continue
            x = cx + focal * cameraspace[i, 0] / z
            y = cy - focal * cameraspace[i, 1] / z

            # Nearest pre-scaled sprite, so nothing is scaled per frame.
            level = min(
                len(sprites) - 1,
                max(0, int(round(math.log2(max(screen_size, 8.0) / 16.0)))),
            )
            sprite = sprites[level]
            rect = sprite.get_rect(center=(int(x), int(y)))
            if not rect.colliderect(viewport):
                continue

            fade = math.exp(-z / (self.sky.visibility * 2.4))
            sprite.set_alpha(int(255 * max(0.28, fade)))
            surface.blit(sprite, rect)

    # -- terrain -----------------------------------------------------------

    def _terrain_colours(
        self,
        heights: np.ndarray,
        normals_z: np.ndarray,
        lit: np.ndarray,
        cell_n: np.ndarray,
        cell_e: np.ndarray,
    ):
        """Colour the ground by elevation, light it by slope, texture it by cell."""
        sky = self.sky
        low = np.array(sky.ground_low, dtype=float)
        mid = np.array(sky.ground_mid, dtype=float)
        high = np.array(sky.ground_high, dtype=float)
        peak = np.array(sky.ground_peak, dtype=float)

        # Elevation bands, blended so there are no hard contour lines.
        h = heights
        t1 = np.clip(h / 260.0, 0.0, 1.0)[:, None]
        t2 = np.clip((h - 260.0) / 420.0, 0.0, 1.0)[:, None]
        t3 = np.clip((h - 900.0) / 500.0, 0.0, 1.0)[:, None]
        colour = low + (mid - low) * t1
        colour = colour + (high - colour) * t2
        colour = colour + (peak - colour) * t3

        # Steep ground shows rock whatever its height.
        steep = np.clip((0.86 - np.abs(normals_z)) / 0.30, 0.0, 1.0)[:, None]
        colour = colour + (high - colour) * steep * 0.7

        # Per-cell tint from the cell's own integer coordinates. Without it,
        # gentle ground renders as one smooth sheet with no sense of motion
        # over it -- the relief gives the horizon a shape, but the field
        # underneath still needs a texture to move past.
        key = (cell_n.astype(np.int64) * 73856093) ^ (cell_e.astype(np.int64) * 19349663)
        tint = (((key >> 8) & 0xFF).astype(float) / 255.0 - 0.5)
        colour = colour * (1.0 + 0.17 * tint)[:, None]

        level = np.clip(sky.ambient + (1.0 - sky.ambient) * np.maximum(lit, 0.0), 0.0, 1.3)
        return np.clip(colour * level[:, None], 0, 255)

    def _draw_ring(self, state, terrain, cell: float, half: int, hole: float) -> None:
        """One square ring of terrain cells, back to front.

        ``hole`` is the half-width in *metres* of the region a finer ring
        already covers, measured from the aircraft. It has to be metres rather
        than a cell count: each ring snaps its own grid to its own cell size,
        so two rings are never aligned with each other, and a hollow counted in
        the coarse ring's cells lands up to one coarse cell off the fine ring's
        actual footprint. That leaves a band of bare ground showing between
        them, straight across the middle distance.
        """
        camera = self.camera
        origin_n = math.floor(state.position[0] / cell)
        origin_e = math.floor(state.position[1] / cell)

        span = 2 * half + 2
        # The grid is snapped to the cell size, so it only changes when the
        # aircraft crosses a cell boundary -- every seven seconds or so for the
        # outer ring. Evaluating the heightfield every frame regardless was the
        # single largest term in the terrain profile, and all but a fraction of
        # a percent of it was recomputing the same numbers.
        key = (cell, half)
        cached = self._ring_cache.get(key)
        if cached is not None and cached[0] == (origin_n, origin_e):
            grid = cached[1]
        else:
            axis_n = (np.arange(span) + origin_n - half) * cell
            axis_e = (np.arange(span) + origin_e - half) * cell
            mesh_n, mesh_e = np.meshgrid(axis_n, axis_e, indexing="ij")
            heights = terrain.heights(mesh_n, mesh_e)
            grid = np.stack(
                [mesh_n.ravel(), mesh_e.ravel(), -heights.ravel()], axis=1
            )
            if len(self._ring_cache) > 3 * RING_LEVELS:
                # Cell size changes with altitude, so a long climb accumulates
                # entries for sizes it will not use again. Bounded rather than
                # evicted individually: there is nothing here worth an LRU.
                self._ring_cache.clear()
            self._ring_cache[key] = ((origin_n, origin_e), grid)

        grid_camera = camera.to_camera(grid)

        depth_all = grid_camera[:, 2]
        safe = np.maximum(depth_all, 1.0e-6)
        screen_x = self.centre[0] + self.focal * grid_camera[:, 0] / safe
        screen_y = self.centre[1] - self.focal * grid_camera[:, 1] / safe

        rows, columns = np.meshgrid(
            np.arange(span - 1), np.arange(span - 1), indexing="ij"
        )
        rows, columns = rows.ravel(), columns.ravel()
        corner = rows * span + columns
        indices = np.stack([corner, corner + span, corner + span + 1, corner + 1], axis=1)

        cell_depth = depth_all[indices]
        mean_depth = cell_depth.mean(axis=1)
        cell_x, cell_y = screen_x[indices], screen_y[indices]
        min_x, max_x = cell_x.min(axis=1), cell_x.max(axis=1)
        min_y, max_y = cell_y.min(axis=1), cell_y.max(axis=1)

        viewport = self.viewport
        keep = (
            (cell_depth >= NEAR_PLANE).all(axis=1)
            & (max_x >= viewport.left)
            & (min_x <= viewport.right)
            & (max_y >= viewport.top)
            & (min_y <= viewport.bottom)
            & ((max_x - min_x > 1.2) | (max_y - min_y > 1.2))
        )
        if hole > 0.0:
            # Hollow out cells that lie wholly inside what a finer ring covers.
            # Testing both edges of each cell, not its centre, is what keeps
            # the two rings overlapping rather than merely abutting.
            low_n = (origin_n - half + rows) * cell - state.position[0]
            low_e = (origin_e - half + columns) * cell - state.position[1]
            reach_n = np.maximum(np.abs(low_n), np.abs(low_n + cell))
            reach_e = np.maximum(np.abs(low_e), np.abs(low_e + cell))
            keep &= ~((reach_n < hole) & (reach_e < hole))

        selected = np.flatnonzero(keep)
        if selected.size == 0:
            return

        # Cell normals from the corner heights, for slope shading.
        picked = indices[selected]
        cell_h = -grid[:, 2][picked]
        dh_n = (cell_h[:, 1] + cell_h[:, 2]) - (cell_h[:, 0] + cell_h[:, 3])
        dh_e = (cell_h[:, 3] + cell_h[:, 2]) - (cell_h[:, 0] + cell_h[:, 1])
        nx = -dh_n / (2.0 * cell)
        ny = -dh_e / (2.0 * cell)
        length = np.sqrt(nx * nx + ny * ny + 1.0)
        # Normal in NED is (nx, ny, -1) normalised: z is up, so down-positive.
        sun = self.sky.sun_ned
        lit = -(nx * sun[0] + ny * sun[1] + (-1.0) * sun[2]) / length

        mean_h = cell_h.mean(axis=1)
        colours = self._terrain_colours(
            mean_h,
            1.0 / length,
            lit,
            origin_n - half + rows[selected],
            origin_e - half + columns[selected],
        )
        colours = self.sky.haze_array(colours, np.maximum(mean_depth[selected], 1.0))
        colours = colours.astype(int)

        order = np.argsort(-mean_depth[selected])
        left, top = float(viewport.left), float(viewport.top)
        right, bottom = float(viewport.right), float(viewport.bottom)
        surface = self.surface

        for k in order:
            index = selected[k]
            points = [
                (float(screen_x[v]), float(screen_y[v])) for v in indices[index]
            ]
            if (
                min_x[index] < left
                or max_x[index] > right
                or min_y[index] < top
                or max_y[index] > bottom
            ):
                points = self.clip_to_rect(points, left, top, right, bottom)
                if len(points) < 3:
                    continue
            rgb = colours[k]
            pygame.draw.polygon(surface, (int(rgb[0]), int(rgb[1]), int(rgb[2])), points)

    def draw_terrain(self, state, terrain, runway) -> None:
        """The ground: a far base plane, two rings of heightfield, the runway."""
        camera = self.camera
        base_height = terrain.field_elevation

        # A base plane past the visible horizon, so there is always ground
        # under the rings rather than sky showing through the gaps.
        extent = 220000.0
        base = np.array(
            [
                [camera.position[0] - extent, camera.position[1] - extent, -base_height],
                [camera.position[0] + extent, camera.position[1] - extent, -base_height],
                [camera.position[0] + extent, camera.position[1] + extent, -base_height],
                [camera.position[0] - extent, camera.position[1] + extent, -base_height],
            ]
        )
        # Hazed as if it were at its own far edge, so where it shows past the
        # outermost ring it is already exactly the horizon colour. Hazing it
        # for a nearer distance leaves a bright band between the last cells and
        # the sky, which is the one place the eye is guaranteed to be looking.
        self._fill(
            camera.to_camera(base), self.sky.haze(self.sky.ground_low, extent * 0.5)
        )

        # Near-cell size follows altitude: fixed cells vanish into a uniform
        # smear from high up and crawl past far too slowly down low. Snapped to
        # a round number so the grid does not shimmer as the aircraft climbs.
        # Capped by visibility as well as by altitude: ground more than twice
        # the visibility away is already fully hazed to the horizon colour, so
        # cells drawn out there cost a frame and paint nothing the base plane
        # was not painting already.
        altitude = max(5.0, state.altitude - base_height)
        reach = max(30_000.0, 2.0 * self.sky.visibility)
        largest = min(MAX_CELL, reach / (RING_CELLS * RING_FACTOR ** (RING_LEVELS - 1)))
        cell = _snap(altitude * 0.14, maximum=largest)

        # Far to near, so each ring paints over the coarser one behind it. A
        # ring's grid is snapped to its own cell size, so it is guaranteed to
        # cover the aircraft out to half its cells in every direction; that
        # distance, less one of the coarser ring's own cells so the two
        # overlap, is the hole the coarser ring leaves for it.
        for level in reversed(range(RING_LEVELS)):
            size = cell * RING_FACTOR**level
            covered = RING_CELLS * cell * RING_FACTOR ** (level - 1)
            self._draw_ring(
                state,
                terrain,
                size,
                RING_CELLS,
                hole=0.0 if level == 0 else covered - size,
            )

        if runway is not None:
            runway.draw(self)

    # -- aircraft ----------------------------------------------------------

    def _mesh_buffer(self, facets):
        """Flatten a facet list into one vertex array, cached between frames.

        The mesh is constant in body axes, so the concatenation is done once
        and only the transform is repeated.
        """
        # Keyed by identity, and there is more than one mesh in play: the
        # aircraft and its shadow silhouette alternate every frame, and a
        # single-slot cache would rebuild both of them twice a frame.
        key = id(facets)
        cached = self._mesh_cache.get(key)
        if cached is not None and cached[0] is facets:
            return cached[1]
        points = np.concatenate([f.points for f in facets], axis=0)
        bounds = np.cumsum([0] + [len(f.points) for f in facets])
        # Colours and the highlight mask are constant too, and pulling them out
        # of a hundred and twenty-six dataclasses every frame is not free.
        colours = np.array([f.colour for f in facets], dtype=float)
        highlight = np.array([bool(f.highlight) for f in facets])
        self._mesh_cache[key] = (facets, (points, bounds, colours, highlight))
        return self._mesh_cache[key][1]

    def _shadow_layer(self) -> pygame.Surface:
        """A reusable translucent layer. Allocating one per frame is not free."""
        layer = getattr(self, "_shadow_surface", None)
        if layer is None or layer.get_size() != self.viewport.size:
            layer = pygame.Surface(self.viewport.size, pygame.SRCALPHA)
            self._shadow_surface = layer
        else:
            layer.fill((0, 0, 0, 0))
        return layer

    def draw_shadow(self, state, facets, dcm_body_to_ned, terrain) -> None:
        """The aircraft's shadow, cast onto the ground along the sun.

        Only drawn near the ground, which is the only place it reads as depth
        rather than as a smudge. It is the single strongest cue for how high
        the aircraft actually is during a landing.
        """
        if not self.show_shadow or self.sky.daylight < 0.15:
            return

        ground = terrain.height_at(float(state.position[0]), float(state.position[1]))
        agl = state.altitude - ground
        if not 0.0 < agl < 500.0:
            return

        sun = self.sky.sun_ned  # points from the sun toward the scene
        if sun[2] <= 0.15:  # sun on or below the horizon: no usable shadow
            return

        points, bounds = self._mesh_buffer(facets)[:2]
        world = state.position + points @ dcm_body_to_ned.T

        # Slide each vertex along the sun direction until it reaches the plane.
        travel = (-ground - world[:, 2]) / sun[2]
        shadow = world + travel[:, None] * sun
        shadow[:, 2] = -ground - 0.35  # a little proud, to avoid z-fighting

        cameraspace = self.camera.to_camera(shadow)
        fade = 1.0 - agl / 500.0
        alpha = int(120 * fade * self.sky.daylight)
        if alpha < 8:
            return

        layer = self._shadow_layer()
        left, top = 0.0, 0.0
        right, bottom = float(self.viewport.width), float(self.viewport.height)
        origin_x, origin_y = self.viewport.left, self.viewport.top

        # Project every vertex at once, then walk the facets. The shadow has
        # the same vertex count as the aircraft, so a per-facet projection
        # doubles the most expensive pass in the frame.
        in_front = cameraspace[:, 2] >= NEAR_PLANE
        screen_all = self.project(cameraspace)
        colour = (12, 16, 20, alpha)

        for index in range(len(bounds) - 1):
            start, end = bounds[index], bounds[index + 1]
            if not in_front[start:end].all():
                continue  # straddling facets are not worth clipping for a shadow
            screen = screen_all[start:end]
            pts = [
                (float(p[0]) - origin_x, float(p[1]) - origin_y) for p in screen
            ]
            pts = self.clip_to_rect(pts, left, top, right, bottom)
            if len(pts) < 3:
                continue
            try:
                pygame.draw.polygon(layer, colour, pts)
            except (ValueError, TypeError):
                pass

        self.surface.blit(layer, self.viewport.topleft)

    def draw_aircraft(self, state, facets, dcm_body_to_ned: np.ndarray) -> None:
        """Draw the aircraft mesh, lit and depth-sorted.

        Every stage that can be is done for all facets at once. Written the
        obvious way -- a loop with a normal, a dot product and a projection per
        facet -- a hundred and twenty-six facets cost 8 ms a frame, almost all
        of it numpy dispatch on three-element vectors rather than arithmetic.
        Batched, the same picture costs about a millisecond.
        """
        if not facets:
            return

        camera = self.camera
        points, bounds, base, highlight = self._mesh_buffer(facets)
        starts, ends = bounds[:-1], bounds[1:]

        world_all = state.position + points @ dcm_body_to_ned.T
        camera_all = (world_all - camera.position) @ camera.basis.T

        z = camera_all[:, 2]
        # reduceat walks the ragged facet boundaries in one pass, which is what
        # replaces roughly fifteen hundred single-element reductions a frame.
        depth = np.add.reduceat(z, starts) / (ends - starts)
        visible = np.maximum.reduceat(z, starts) >= NEAR_PLANE
        straddles = np.minimum.reduceat(z, starts) < NEAR_PLANE

        # Facet normals from the first three vertices of each facet.
        anchor = world_all[starts]
        edge1 = world_all[starts + 1] - anchor
        edge2 = world_all[starts + 2] - anchor
        normals = np.cross(edge1, edge2)
        lengths = np.linalg.norm(normals, axis=1)
        degenerate = lengths < 1.0e-9
        normals /= np.where(degenerate, 1.0, lengths)[:, None]

        # Face each normal toward the camera so both sides of a thin surface
        # such as a fin are lit rather than one of them going black.
        view = camera.position - anchor
        normals = np.where(
            ((normals * view).sum(axis=1) < 0.0)[:, None], -normals, normals
        )

        sun = self.sky.sun_ned
        ambient = self.sky.ambient
        lit = -(normals @ sun)
        level = np.minimum(1.25, ambient + (1.0 - ambient) * np.maximum(0.0, lit))

        # A narrow specular lobe about the half vector. Flat Lambert on flat
        # facets makes an aircraft look like paper; one highlight term is what
        # makes it look like a surface.
        view_length = np.linalg.norm(view, axis=1)
        half = view / np.maximum(view_length, 1.0e-6)[:, None] - sun
        half_length = np.maximum(np.linalg.norm(half, axis=1), 1.0e-6)
        cosine = np.maximum(0.0, (normals * half).sum(axis=1) / half_length)
        level += 0.55 * cosine**24 * self.sky.daylight * (lit > 0.0)

        shaded = np.minimum(255.0, base * level[:, None])
        shaded = self.sky.haze_array(shaded, np.maximum(depth, 1.0))
        shaded = np.where(highlight[:, None], base, shaded).astype(int)

        # Project once for the whole mesh; only facets crossing the near plane
        # need the per-polygon clip, and on a solid body there are few of them.
        screen_all = self.project(camera_all)
        sx, sy = screen_all[:, 0], screen_all[:, 1]
        viewport = self.viewport
        left, top = float(viewport.left), float(viewport.top)
        right, bottom = float(viewport.right), float(viewport.bottom)
        xmin = np.minimum.reduceat(sx, starts)
        xmax = np.maximum.reduceat(sx, starts)
        ymin = np.minimum.reduceat(sy, starts)
        ymax = np.maximum.reduceat(sy, starts)
        offscreen = (xmax < left) | (xmin > right) | (ymax < top) | (ymin > bottom)
        clipped = (xmin < left) | (xmax > right) | (ymin < top) | (ymax > bottom)

        drawable = visible & ~degenerate & ~(offscreen & ~straddles)
        order = np.argsort(-depth)
        surface = self.surface

        for index in order[drawable[order]]:
            start, end = starts[index], ends[index]
            colour = tuple(shaded[index])
            if straddles[index]:
                self._fill(camera_all[start:end], colour)
                continue
            pts = [(float(x), float(y)) for x, y in screen_all[start:end]]
            if clipped[index]:
                pts = self.clip_to_rect(pts, left, top, right, bottom)
                if len(pts) < 3:
                    continue
            try:
                pygame.draw.polygon(surface, colour, pts)
            except (ValueError, TypeError):
                pass


class Runway:
    """A paved strip on the terrain, with the markings a pilot flies to.

    The geometry never moves, so it is built once in world coordinates and
    only transformed per frame. Drawing a hundred and thirty markings as a
    hundred and thirty separate projections costs as much as the aircraft.
    """

    def __init__(
        self,
        *,
        length: float = 3200.0,
        width: float = 45.0,
        heading: float = 0.0,
        elevation: float = 0.0,
        threshold_north: float = 0.0,
        threshold_east: float = 0.0,
    ) -> None:
        self.length = length
        self.width = width
        self.heading = heading
        self.elevation = elevation
        self.threshold = np.array([threshold_north, threshold_east])
        self._build()

    # -- geometry ----------------------------------------------------------

    def _plane(self, along: float, across: float, lift: float = 0.0) -> tuple:
        c, s = math.cos(self.heading), math.sin(self.heading)
        return (
            self.threshold[0] + along * c - across * s,
            self.threshold[1] + along * s + across * c,
            -(self.elevation + lift),
        )

    def _quad(self, a0, c0, a1, c1, lift: float):
        return [
            self._plane(a0, c0, lift),
            self._plane(a1, c0, lift),
            self._plane(a1, c1, lift),
            self._plane(a0, c1, lift),
        ]

    def _build(self) -> None:
        half = self.width * 0.5
        quads: list[list[tuple]] = []
        colours: list[tuple[int, int, int]] = []

        def add(quad, colour):
            quads.append(quad)
            colours.append(colour)

        # Shoulder, then pavement. The lighter shoulder is what makes the strip
        # read as a made surface rather than a rectangle painted on grass.
        add(self._quad(-160.0, -half - 22.0, self.length + 160.0, half + 22.0, 0.0),
            (78, 76, 70))
        add(self._quad(-120.0, -half, self.length + 120.0, half, 0.02), (58, 58, 64))

        marking = (228, 228, 232)

        # Threshold piano keys.
        stripe = self.width / 12.0
        for i in range(6):
            offset = (i - 3) * (stripe * 1.9) + stripe * 0.5
            add(self._quad(8.0, offset, 38.0, offset + stripe, 0.05), marking)

        # Touchdown zone bars and the aiming point.
        for distance, count in ((150.0, 3), (300.0, 2), (450.0, 1)):
            for side in (-1.0, 1.0):
                for bar in range(count):
                    inner = side * (6.0 + bar * 4.0)
                    add(
                        self._quad(
                            distance, inner, distance + 22.0, inner + side * 2.6, 0.05
                        ),
                        marking,
                    )
        for side in (-1.0, 1.0):
            add(self._quad(300.0, side * 11.0, 345.0, side * 17.0, 0.05), marking)

        # Centreline.
        distance = 90.0
        while distance < self.length - 40.0:
            add(self._quad(distance, -0.9, distance + 30.0, 0.9, 0.05), marking)
            distance += 60.0

        self._static_count = len(quads)

        # Edge lights are coloured at draw time, because how bright they are
        # depends on how little daylight is left.
        self._light_start = len(quads)
        distance = 0.0
        while distance < self.length:
            for side in (-1.0, 1.0):
                add(
                    self._quad(
                        distance, side * (half + 3.0), distance + 6.0,
                        side * (half + 6.0), 0.4,
                    ),
                    marking,
                )
            distance += 120.0

        self._quads = np.array(quads, dtype=float)  # (M, 4, 3)
        self._colours = np.array(colours, dtype=float)  # (M, 3)
        self._flat = self._quads.reshape(-1, 3)

    @property
    def start(self) -> np.ndarray:
        return np.array(self._plane(0.0, 0.0))

    # -- drawing -----------------------------------------------------------

    def draw(self, renderer: "Renderer") -> None:
        camera = renderer.camera
        sky = renderer.sky

        cameraspace = camera.to_camera(self._flat)
        depth = cameraspace[:, 2].reshape(-1, 4)
        mean_depth = depth.mean(axis=1)

        # Everything behind the eye, or so far away it is a pixel, is dropped
        # before any of it is projected.
        candidate = (mean_depth > NEAR_PLANE) & (mean_depth < 45000.0)
        if not candidate.any():
            return

        screen = renderer.project(cameraspace).reshape(-1, 4, 2)
        min_xy = screen.min(axis=1)
        max_xy = screen.max(axis=1)

        viewport = renderer.viewport
        left, top = float(viewport.left), float(viewport.top)
        right, bottom = float(viewport.right), float(viewport.bottom)

        fully_front = (depth >= NEAR_PLANE).all(axis=1)
        on_screen = (
            (max_xy[:, 0] >= left)
            & (min_xy[:, 0] <= right)
            & (max_xy[:, 1] >= top)
            & (min_xy[:, 1] <= bottom)
        )
        big_enough = (max_xy[:, 0] - min_xy[:, 0] > 0.8) | (
            max_xy[:, 1] - min_xy[:, 1] > 0.8
        )

        colours = self._colours.copy()
        if len(colours) > self._light_start:
            glow = 1.0 - sky.daylight
            colours[self._light_start:] = np.array(
                lerp((196, 198, 206), (255, 226, 150), glow), dtype=float
            )
        hazed = sky.haze_array(colours, np.maximum(mean_depth, 1.0)).astype(int)

        selected = np.flatnonzero(candidate & on_screen & big_enough)
        order = selected[np.argsort(-mean_depth[selected])]
        surface = renderer.surface

        for index in order:
            if not fully_front[index]:
                # Rare: only the pavement, and only when the camera is on it.
                renderer._fill(cameraspace[index * 4:index * 4 + 4], tuple(hazed[index]))
                continue
            points = [(float(p[0]), float(p[1])) for p in screen[index]]
            if (
                min_xy[index, 0] < left
                or max_xy[index, 0] > right
                or min_xy[index, 1] < top
                or max_xy[index, 1] > bottom
            ):
                points = renderer.clip_to_rect(points, left, top, right, bottom)
                if len(points) < 3:
                    continue
            rgb = hazed[index]
            pygame.draw.polygon(surface, (int(rgb[0]), int(rgb[1]), int(rgb[2])), points)

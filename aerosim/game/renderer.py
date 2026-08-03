"""Chase-view 3D renderer.

A small software pipeline: world NED -> camera space -> near-plane clip ->
perspective projection -> painter's algorithm. There is no depth buffer, so
polygons are sorted back to front, which is adequate for a handful of convex
bodies against a ground plane and costs nothing to explain.

The camera keeps world up as its up vector rather than rolling with the
aircraft. That is what makes a chase view readable: the horizon stays level
and the aircraft visibly banks against it, instead of the aircraft staying
level and the whole world rotating around it.

The renderer is strictly a viewer. It reads the state and feeds nothing back.
"""

from __future__ import annotations

import math

import numpy as np
import pygame

NEAR_PLANE = 0.35


def _lerp(a, b, t: float):
    t = max(0.0, min(1.0, t))
    return tuple(int(round(x + (y - x) * t)) for x, y in zip(a, b))


class Sky:
    """Sky, sun and haze colours as a function of time of day."""

    def __init__(self, time_of_day: float, visibility: float) -> None:
        self.time_of_day = time_of_day
        self.visibility = max(1000.0, visibility)

        # Sun elevation: a crude but continuous day cycle. Noon overhead,
        # sunrise and sunset near 06:00 and 18:00.
        self.sun_elevation = math.radians(
            72.0 * math.sin(math.pi * (time_of_day - 6.0) / 12.0)
        )
        azimuth = math.radians(90.0 + 15.0 * (time_of_day - 12.0))

        # Sun direction in NED, pointing FROM the sun toward the scene.
        self.sun_ned = np.array(
            [
                -math.cos(self.sun_elevation) * math.cos(azimuth),
                -math.cos(self.sun_elevation) * math.sin(azimuth),
                math.sin(self.sun_elevation),
            ]
        )

        daylight = max(0.0, min(1.0, (math.degrees(self.sun_elevation) + 6.0) / 18.0))
        dusk = max(0.0, 1.0 - abs(math.degrees(self.sun_elevation)) / 12.0) * (
            1.0 if abs(time_of_day - 12.0) > 3.0 else 0.0
        )

        night_top, night_bottom = (8, 11, 26), (26, 32, 54)
        day_top, day_bottom = (58, 118, 196), (156, 196, 236)
        dusk_bottom = (232, 146, 92)

        self.zenith = _lerp(night_top, day_top, daylight)
        horizon = _lerp(night_bottom, day_bottom, daylight)
        self.horizon = _lerp(horizon, dusk_bottom, dusk * 0.75)

        ground_night, ground_day = (18, 22, 20), (72, 96, 58)
        self.ground = _lerp(ground_night, ground_day, daylight)
        self.ambient = 0.34 + 0.26 * daylight
        self.daylight = daylight

    def haze(self, colour, distance: float):
        """Blend a colour toward the horizon with distance."""
        t = 1.0 - math.exp(-distance / self.visibility)
        return _lerp(colour, self.horizon, t * 0.92)

    def shade(self, colour, normal_ned: np.ndarray):
        """Lambert shading against the sun, floored at the ambient level."""
        lit = float(-(normal_ned @ self.sun_ned))
        level = self.ambient + (1.0 - self.ambient) * max(0.0, lit)
        level = max(0.0, min(1.25, level))
        return tuple(int(max(0, min(255, round(c * level)))) for c in colour)


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
            # Wrap-safe angular smoothing.
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
        # show: a 40 degree climb then looks identical to level flight, because
        # the camera has climbed with it. Following none of it loses the
        # aircraft off the top of the frame in any real climb.
        pitch = max(-0.55, min(0.55, self._smoothed_pitch * 0.45))
        distance = self.distance
        height = self.height

        back = np.array(
            [
                -math.cos(self._smoothed_heading) * math.cos(pitch),
                -math.sin(self._smoothed_heading) * math.cos(pitch),
                math.sin(pitch),
            ]
        )
        self.position = state.position + back * distance + np.array([0.0, 0.0, -height])

        target = state.position + np.array([0.0, 0.0, -0.05 * self.distance])
        self._look_at(target)

    def _look_at(self, target: np.ndarray) -> None:
        forward = target - self.position
        norm = float(np.linalg.norm(forward))
        if norm < 1.0e-6:
            forward = np.array([1.0, 0.0, 0.0])
        else:
            forward = forward / norm

        world_up = np.array([0.0, 0.0, -1.0])
        right = np.cross(forward, world_up)
        norm = float(np.linalg.norm(right))
        if norm < 1.0e-6:
            right = np.array([0.0, 1.0, 0.0])
        else:
            right = right / norm
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
        self._sky_cache: pygame.Surface | None = None
        self.viewport = surface.get_rect()
        self.resize(surface)

    def resize(self, surface: pygame.Surface) -> None:
        self.surface = surface
        self.resize_view(surface.get_rect())

    def resize_view(self, rect: pygame.Rect) -> None:
        """Aim the projection at a sub-rectangle of the window.

        The 3D view occupies only the area above the instrument panel, so the
        principal point has to be the centre of *that* rectangle. Projecting
        about the window centre instead puts the horizon behind the panel and
        quietly changes the field of view with every resize.
        """
        if rect.width <= 0 or rect.height <= 0:
            return
        changed = (rect.width, rect.height) != (
            self.viewport.width,
            self.viewport.height,
        )
        self.viewport = pygame.Rect(rect)
        self.width, self.height = rect.width, rect.height
        self.centre = (rect.centerx, rect.centery)
        self.focal = (rect.width * 0.5) / math.tan(self.camera.fov * 0.5)
        if changed:
            self._sky_cache = None

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
    def clip_to_rect(
        points: list[tuple[float, float]],
        left: float,
        top: float,
        right: float,
        bottom: float,
    ) -> list[tuple[float, float]]:
        """Sutherland-Hodgman clip of a screen polygon to the viewport.

        This is a performance measure, not a correctness one -- pygame already
        clips what it draws. But it clips while *rasterising*, so a ground quad
        320 km across, whose projected corners land millions of pixels off
        screen, still costs a scan conversion over that entire bounding box.
        Bounding the polygon to the viewport first turns the terrain from three
        frames a second into sixty.
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

        # Cheap rejection first: a polygon entirely off one edge cannot
        # contribute, and rejecting it costs four comparisons.
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

    def draw_sky(self) -> None:
        if self._sky_cache is None:
            # Built one pixel wide and scaled up: a per-pixel gradient over the
            # whole viewport every frame costs far more than the blit.
            cache = pygame.Surface((1, self.height))
            for y in range(self.height):
                t = y / max(1, self.height - 1)
                cache.set_at((0, y), _lerp(self.sky.zenith, self.sky.horizon, t))
            self._sky_cache = pygame.transform.scale(cache, (self.width, self.height))
        self.surface.blit(self._sky_cache, self.viewport.topleft)

    # -- terrain -----------------------------------------------------------

    def draw_terrain(self, state, field_elevation: float, runway) -> None:
        """Ground plane, a scrolling patchwork for motion cues, and the runway."""
        camera = self.camera
        ground_z = -field_elevation  # NED down-positive: the plane is at z = -elev

        # The base plane out to well past the visible horizon, so there is
        # always ground under the patchwork rather than sky showing through.
        extent = 160000.0
        base = np.array(
            [
                [camera.position[0] - extent, camera.position[1] - extent, ground_z],
                [camera.position[0] + extent, camera.position[1] - extent, ground_z],
                [camera.position[0] + extent, camera.position[1] + extent, ground_z],
                [camera.position[0] - extent, camera.position[1] + extent, ground_z],
            ]
        )
        self._fill(camera.to_camera(base), self.sky.haze(self.sky.ground, 40000.0))

        # Patchwork cells sized to the altitude: fixed-size cells vanish into a
        # uniform smear from high up and crawl past far too slowly down low.
        altitude = max(5.0, state.altitude - field_elevation)
        raw_cell = max(220.0, min(4200.0, altitude * 0.55))
        cell = float(10 ** round(math.log10(raw_cell)) * round(raw_cell / 10 ** round(math.log10(raw_cell))))
        cell = max(220.0, cell)

        half_cells = 11
        origin_x = math.floor(state.position[0] / cell)
        origin_y = math.floor(state.position[1] / cell)

        # Everything below is done for the WHOLE grid at once. NumPy on
        # four-element arrays is almost entirely call overhead, and a per-cell
        # transform-project-cull chain over several hundred cells costs more
        # than the rasterisation it feeds. One pass over the grid, then a plain
        # Python loop over only the cells that survive.
        span = 2 * half_cells + 2
        axis_x = (np.arange(span) + origin_x - half_cells) * cell
        axis_y = (np.arange(span) + origin_y - half_cells) * cell
        mesh_x, mesh_y = np.meshgrid(axis_x, axis_y, indexing="ij")
        grid = np.stack(
            [mesh_x.ravel(), mesh_y.ravel(), np.full(mesh_x.size, ground_z)], axis=1
        )
        grid_camera = camera.to_camera(grid)

        depth_all = grid_camera[:, 2]
        safe_depth = np.maximum(depth_all, 1.0e-6)
        screen_x = self.centre[0] + self.focal * grid_camera[:, 0] / safe_depth
        screen_y = self.centre[1] - self.focal * grid_camera[:, 1] / safe_depth

        # Corner indices of every cell, in winding order.
        rows, columns = np.meshgrid(
            np.arange(span - 1), np.arange(span - 1), indexing="ij"
        )
        rows, columns = rows.ravel(), columns.ravel()
        corner = rows * span + columns
        indices = np.stack([corner, corner + span, corner + span + 1, corner + 1], axis=1)

        cell_depth = depth_all[indices]
        in_front = cell_depth >= NEAR_PLANE
        mean_depth = cell_depth.mean(axis=1)

        cell_x = screen_x[indices]
        cell_y = screen_y[indices]
        min_x, max_x = cell_x.min(axis=1), cell_x.max(axis=1)
        min_y, max_y = cell_y.min(axis=1), cell_y.max(axis=1)

        viewport = self.viewport
        keep = (
            in_front.all(axis=1)  # straddling cells are left to the base plane
            & (mean_depth < 55000.0)
            & (max_x >= viewport.left)
            & (min_x <= viewport.right)
            & (max_y >= viewport.top)
            & (min_y <= viewport.bottom)
            # Sub-pixel cells near the horizon cost a fill and change nothing.
            & ((max_x - min_x > 1.5) | (max_y - min_y > 1.5))
        )

        selected = np.flatnonzero(keep)
        if selected.size == 0:
            if runway is not None:
                runway.draw(self)
            return

        # Deterministic per-cell tint from its integer world coordinates, so the
        # pattern is stable in the world and scrolls with the aircraft rather
        # than shimmering underneath it.
        world_i = (origin_x - half_cells + rows[selected]).astype(np.int64)
        world_j = (origin_y - half_cells + columns[selected]).astype(np.int64)
        shade = (((world_i * 73856093) ^ (world_j * 19349663)) >> 8 & 0xFF) / 255.0

        ground = np.array(self.sky.ground, dtype=float)
        tint = np.stack(
            [
                ground[0] * (0.80 + 0.34 * shade),
                ground[1] * (0.84 + 0.30 * shade),
                ground[2] * (0.78 + 0.36 * shade),
            ],
            axis=1,
        )
        horizon = np.array(self.sky.horizon, dtype=float)
        blend = (1.0 - np.exp(-mean_depth[selected] / self.sky.visibility)) * 0.92
        colours = np.clip(tint + (horizon - tint) * blend[:, None], 0, 255).astype(int)

        order = selected[np.argsort(-mean_depth[selected])]
        colour_by_cell = {int(c): colours[k] for k, c in enumerate(selected)}

        left, top = float(viewport.left), float(viewport.top)
        right, bottom = float(viewport.right), float(viewport.bottom)
        surface = self.surface

        for index in order:
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
            rgb = colour_by_cell[int(index)]
            pygame.draw.polygon(surface, (int(rgb[0]), int(rgb[1]), int(rgb[2])), points)

        if runway is not None:
            runway.draw(self)

    # -- aircraft ----------------------------------------------------------

    def _mesh_buffer(self, facets):
        """Flatten a facet list into one vertex array, cached between frames.

        The mesh is constant in body axes, so the concatenation is done once
        and only the transform is repeated. Transforming facet by facet means a
        matrix multiply per facet, and with a hundred-odd facets the NumPy call
        overhead alone costs more than every polygon fill in the frame.
        """
        cached = getattr(self, "_mesh_cache", None)
        if cached is not None and cached[0] is facets:
            return cached[1]

        points = np.concatenate([f.points for f in facets], axis=0)
        bounds = np.cumsum([0] + [len(f.points) for f in facets])
        buffer = (points, bounds)
        self._mesh_cache = (facets, buffer)
        return buffer

    def draw_aircraft(self, state, facets, dcm_body_to_ned: np.ndarray) -> None:
        """Draw the aircraft mesh, lit and depth-sorted."""
        if not facets:
            return

        camera = self.camera
        points, bounds = self._mesh_buffer(facets)

        # Body -> NED -> camera, for every vertex in the aircraft at once.
        world_all = state.position + points @ dcm_body_to_ned.T
        camera_all = (world_all - camera.position) @ camera.basis.T

        eye = camera.position
        sun = self.sky.sun_ned
        ambient = self.sky.ambient
        drawable: list[tuple[float, np.ndarray, tuple]] = []

        for index, facet in enumerate(facets):
            start, end = bounds[index], bounds[index + 1]
            cameraspace = camera_all[start:end]
            if cameraspace[:, 2].max() < NEAR_PLANE:
                continue

            depth = float(cameraspace[:, 2].mean())

            if facet.highlight:
                colour = facet.colour
            else:
                world = world_all[start:end]
                a, b, c = world[0], world[1], world[2]
                # Cross product written out: np.cross on three-element vectors
                # spends nearly all its time in axis bookkeeping.
                e1x, e1y, e1z = b[0] - a[0], b[1] - a[1], b[2] - a[2]
                e2x, e2y, e2z = c[0] - a[0], c[1] - a[1], c[2] - a[2]
                nx = e1y * e2z - e1z * e2y
                ny = e1z * e2x - e1x * e2z
                nz = e1x * e2y - e1y * e2x
                norm = math.sqrt(nx * nx + ny * ny + nz * nz)
                if norm < 1.0e-9:
                    continue
                nx, ny, nz = nx / norm, ny / norm, nz / norm

                # Face the normal toward the camera so both sides of a thin
                # surface such as a fin are lit rather than one going black.
                if (
                    nx * (eye[0] - a[0])
                    + ny * (eye[1] - a[1])
                    + nz * (eye[2] - a[2])
                ) < 0.0:
                    nx, ny, nz = -nx, -ny, -nz

                lit = -(nx * sun[0] + ny * sun[1] + nz * sun[2])
                level = min(1.25, ambient + (1.0 - ambient) * max(0.0, lit))
                base = facet.colour
                colour = self.sky.haze(
                    (
                        min(255, int(base[0] * level)),
                        min(255, int(base[1] * level)),
                        min(255, int(base[2] * level)),
                    ),
                    max(depth, 1.0),
                )

            drawable.append((depth, cameraspace, colour))

        for _, cameraspace, colour in sorted(drawable, key=lambda item: -item[0]):
            self._fill(cameraspace, colour)


class Runway:
    """A single paved strip on the terrain plane, with centreline markings."""

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

    def _plane(self, along: float, across: float) -> np.ndarray:
        c, s = math.cos(self.heading), math.sin(self.heading)
        return np.array(
            [
                self.threshold[0] + along * c - across * s,
                self.threshold[1] + along * s + across * c,
                -self.elevation,
            ]
        )

    @property
    def start(self) -> np.ndarray:
        return self._plane(0.0, 0.0)

    def draw(self, renderer: "Renderer") -> None:
        camera = renderer.camera
        half = self.width * 0.5

        surface = np.array(
            [
                self._plane(-120.0, -half),
                self._plane(self.length + 120.0, -half),
                self._plane(self.length + 120.0, half),
                self._plane(-120.0, half),
            ]
        )
        depth = float(camera.to_camera(surface)[:, 2].mean())
        if depth < -5000.0:
            return
        renderer._fill(
            camera.to_camera(surface), renderer.sky.haze((58, 58, 64), max(abs(depth), 1.0))
        )

        # Threshold bar and centreline dashes, drawn slightly proud of the
        # surface so they do not z-fight with it under the painter's algorithm.
        marking = renderer.sky.haze((222, 222, 226), max(abs(depth), 1.0))
        bar = np.array(
            [
                self._plane(6.0, -half + 3.0),
                self._plane(30.0, -half + 3.0),
                self._plane(30.0, half - 3.0),
                self._plane(6.0, half - 3.0),
            ]
        )
        bar[:, 2] -= 0.05
        renderer._fill(camera.to_camera(bar), marking)

        step = 120.0
        distance = 90.0
        while distance < self.length - 40.0:
            dash = np.array(
                [
                    self._plane(distance, -1.2),
                    self._plane(distance + 55.0, -1.2),
                    self._plane(distance + 55.0, 1.2),
                    self._plane(distance, 1.2),
                ]
            )
            dash[:, 2] -= 0.05
            dash_depth = float(camera.to_camera(dash)[:, 2].mean())
            if dash_depth > NEAR_PLANE:
                renderer._fill(
                    camera.to_camera(dash), renderer.sky.haze((236, 236, 240), dash_depth)
                )
            distance += step

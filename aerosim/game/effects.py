"""Visual effects driven by the flight state.

Everything here reads the simulation and writes nothing back, exactly like the
renderer. An effect that could change the aircraft's path would be a second,
undocumented force model, and the whole point of the split is that there is
only one.

The effects are gated on the physical conditions that produce them rather than
on a switch, so they carry information: the fighter's plume tells you the
afterburner is lit, a contrail tells you the air outside is cold enough for
one, and vortices off the wingtips tell you the wing is working hard. They are
qualitative -- a plume length is not a nozzle calculation and a contrail is not
a microphysics model -- and ``docs/known_limitations.md`` says so.
"""

from __future__ import annotations

import math
from collections import deque

import numpy as np
import pygame

from ..core.frames import dcm_body_to_ned
from .mesh import exhaust_ports

# Contrails form when the ambient air is cold enough that the engine's water
# vapour saturates on mixing. The Appleman criterion depends on pressure and
# humidity; this uses a fixed temperature threshold with a soft edge, which is
# the right shape and the wrong precision.
CONTRAIL_TEMPERATURE = 233.15  # K, about -40 C
CONTRAIL_FADE = 8.0  # K of transition either side

# Trail sampling. Points are emitted per metre travelled rather than per
# second, so a trail is the same length in the air whatever the speed -- which
# is what a trail actually is.
TRAIL_SPACING = 55.0  # m between emitted points
TRAIL_POINTS = 90  # about 5 km of trail


def _soft_step(t: float) -> float:
    """Clamp to [0, 1] with a smooth shoulder, so effects fade in."""
    t = max(0.0, min(1.0, t))
    return t * t * (3.0 - 2.0 * t)


class Trail:
    """A ribbon of world-space points streaming behind one emitter."""

    __slots__ = ("points", "strengths", "_last")

    def __init__(self) -> None:
        self.points: deque[np.ndarray] = deque(maxlen=TRAIL_POINTS)
        self.strengths: deque[float] = deque(maxlen=TRAIL_POINTS)
        self._last: np.ndarray | None = None

    def emit(self, position: np.ndarray, strength: float) -> None:
        """Add a point if the emitter has moved far enough since the last one."""
        position = np.asarray(position, dtype=float)
        if strength <= 0.02:
            # Terminate the ribbon rather than joining across the gap: a trail
            # that stops and restarts must not be drawn as one line through
            # the part of the sky where nothing was produced.
            if self.points and self.strengths[-1] > 0.0:
                self.points.append(position.copy())
                self.strengths.append(0.0)
                self._last = position.copy()
            return
        if (
            self._last is not None
            and float(np.linalg.norm(position - self._last)) < TRAIL_SPACING
        ):
            return
        self._last = position.copy()
        self.points.append(self._last)
        self.strengths.append(float(strength))

    def clear(self) -> None:
        self.points.clear()
        self.strengths.clear()
        self._last = None

    def array(self) -> tuple[np.ndarray, np.ndarray]:
        if not self.points:
            return np.zeros((0, 3)), np.zeros(0)
        return np.array(self.points), np.array(self.strengths)


class Effects:
    """Exhaust plumes, contrails and vortices for one aircraft."""

    def __init__(self, model) -> None:
        self.model = model

        span = model.get("geometry", "wing_span")
        length = model.get("geometry", "fuselage_length", span)
        self.half_span = 0.5 * span

        # Nozzle positions and sizes come from the module that drew the mesh,
        # so the flame comes out of the hole that is actually in the picture.
        ports = exhaust_ports(model)
        if not ports:
            ports = [(np.array([-0.35 * length, 0.0, 0.0]), max(0.35, 0.04 * length))]
        self.nozzles = [position for position, _ in ports]
        self.nozzle_radii = [radius for _, radius in ports]

        self.wingtips = [
            np.array([-0.25, self.half_span * 0.97, 0.0]),
            np.array([-0.25, -self.half_span * 0.97, 0.0]),
        ]

        self.contrails = [Trail() for _ in self.nozzles]
        self.vortices = [Trail() for _ in self.wingtips]
        self._previous: np.ndarray | None = None

    # -- update ------------------------------------------------------------

    def update(self, state, sim) -> None:
        """Sample the trails. Called once per rendered frame, not per step.

        Sampling per frame rather than per physics step is deliberate: a trail
        is a picture of where the aircraft has been, and 55 m of travel is many
        steps at any speed this thing flies.
        """
        derived = state.derived
        dcm = dcm_body_to_ned(state.quaternion)

        # A discontinuity in position means the aircraft did not fly from the
        # last sample to this one -- a restart, or a replay seek. The trail is
        # a record of a path, so there is no trail across a jump. The threshold
        # is far above what any frame of real flight can cover: the fighter at
        # Mach 2 moves 20 m in a thirtieth of a second.
        jumped = (
            self._previous is not None
            and float(np.linalg.norm(state.position - self._previous)) > 800.0
        )
        if jumped:
            self.reset()
        self._previous = np.array(state.position, dtype=float)

        # Contrails: cold air, engines producing, and airborne.
        cold = _soft_step(
            (CONTRAIL_TEMPERATURE + CONTRAIL_FADE - derived.temperature)
            / (2.0 * CONTRAIL_FADE)
        )
        # Zipped rather than indexed: a package may declare fewer positions
        # than engines, in which case the propulsion model synthesises the
        # missing ones and there is no nacelle drawn for them either.
        engines = sim.fdm.propulsion.engines
        for nozzle, trail, engine in zip(self.nozzles, self.contrails, engines):
            power = 0.0
            if not engine.failed and engine.running and derived.altitude_agl > 30.0:
                power = _soft_step((engine.n1 - 0.45) / 0.35)
            trail.emit(state.position + dcm @ nozzle, cold * power)

        # Wingtip vortices: visible when the wing is loaded hard in moist air,
        # which is why they show in a break turn and not in the cruise.
        humid = _soft_step(0.85 - derived.altitude / 6000.0)
        loaded = _soft_step((abs(derived.load_factor) - 2.6) / 2.2)
        strength = humid * loaded
        for tip, trail in zip(self.wingtips, self.vortices):
            trail.emit(state.position + dcm @ tip, strength)

    def reset(self) -> None:
        for trail in (*self.contrails, *self.vortices):
            trail.clear()
        self._previous = None

    # -- drawing -----------------------------------------------------------

    @staticmethod
    def _alpha_layer(renderer, name: str = "effects") -> pygame.Surface:
        return renderer.alpha_layer(name)

    def draw_trails(self, renderer) -> None:
        """Contrails and vortices, as ribbons that thicken and fade with age."""
        ribbons = [(trail, (236, 240, 248), 4.5, 34.0) for trail in self.contrails]
        ribbons += [(trail, (226, 232, 245), 1.6, 9.0) for trail in self.vortices]
        if not any(len(trail.points) >= 2 for trail, *_ in ribbons):
            return

        layer = self._alpha_layer(renderer)
        origin_x, origin_y = renderer.viewport.left, renderer.viewport.top
        drew = False

        for trail, colour, width_new, width_old in ribbons:
            points, strengths = trail.array()
            count = len(points)
            if count < 2:
                continue

            cameraspace = renderer.camera.to_camera(points)
            depth = cameraspace[:, 2]
            screen = renderer.project(cameraspace)
            in_front = depth >= 1.0

            # Age runs 0 at the newest point to 1 at the oldest. A contrail
            # spreads as it ages while it fades, and both together are what
            # make it read as something left behind rather than a drawn line.
            age = 1.0 - np.arange(count) / (count - 1)
            widths = width_new + (width_old - width_new) * age
            alphas = strengths * (1.0 - age**1.7) * 210.0
            # Fixed width in metres, so it shrinks with distance like the rest
            # of the scene.
            pixels = np.clip(
                widths * renderer.focal / np.maximum(depth, 1.0), 1.0, 90.0
            )

            for i in range(count - 1):
                if not (in_front[i] and in_front[i + 1]):
                    continue
                alpha = int(min(alphas[i], alphas[i + 1]))
                if alpha < 6:
                    continue
                pygame.draw.line(
                    layer,
                    (*colour, alpha),
                    (screen[i, 0] - origin_x, screen[i, 1] - origin_y),
                    (screen[i + 1, 0] - origin_x, screen[i + 1, 1] - origin_y),
                    max(1, int(round(0.5 * (pixels[i] + pixels[i + 1])))),
                )
                drew = True

        if drew:
            renderer.surface.blit(layer, renderer.viewport.topleft)

    # Nested plume shells: a blue-violet shroud outside an orange body outside
    # a white-hot core, each shorter than the one around it. Ordered outside
    # in so the core lands on top.
    #
    # Sized against the nozzle, not against what looks dramatic. A chase view
    # sits directly behind the aircraft and therefore looks straight down the
    # exhaust, so a plume drawn even slightly too wide stops being a flame
    # behind the aeroplane and becomes a disc in front of it.
    SHELLS = (
        (0.86, 0.55, (86, 120, 255), 0.22, True),
        (0.62, 0.80, (255, 148, 56), 0.42, False),
        (0.34, 1.00, (255, 236, 198), 0.72, False),
    )

    def draw_exhaust(self, renderer, state, sim) -> None:
        """The plume behind each nozzle, additive so it glows.

        Length and brightness come from the engine's own spool state and the
        afterburner flag, so the plume is a readout: nothing at idle, a short
        shimmer at military power, a long flame in reheat.
        """
        dcm = dcm_body_to_ned(state.quaternion)
        layer: pygame.Surface | None = None

        engines = sim.fdm.propulsion.engines
        for index, (nozzle, engine) in enumerate(zip(self.nozzles, engines)):
            if engine.failed or not engine.running:
                continue

            reheat = 1.0 if engine.afterburner else 0.0
            power = _soft_step((engine.n1 - 0.55) / 0.40)
            intensity = max(power * 0.35, reheat * power)
            if intensity < 0.04:
                continue

            # Flicker from the step index rather than a wall clock, so a
            # replay of a run produces the same picture the run did.
            step = sim.clock.step_index
            flicker = (
                1.0
                + 0.09 * math.sin(step * 0.41 + index * 2.1)
                + 0.05 * math.sin(step * 1.07 + index)
            )
            radius = self.nozzle_radii[index] * (0.80 + 0.30 * reheat)
            length = radius * (1.6 + 4.4 * reheat) * intensity * flicker

            for scale_r, scale_l, colour, opacity, reheat_only in self.SHELLS:
                if reheat_only and reheat < 0.5:
                    continue
                if layer is None:
                    layer = self._alpha_layer(renderer, "exhaust")
                self._draw_cone(
                    layer,
                    renderer,
                    state,
                    dcm,
                    nozzle,
                    radius * scale_r,
                    length * scale_l,
                    colour,
                    intensity * opacity,
                )

        if layer is not None:
            renderer.surface.blit(
                layer, renderer.viewport.topleft, special_flags=pygame.BLEND_RGBA_ADD
            )

    @staticmethod
    def _draw_cone(layer, renderer, state, dcm, nozzle, radius, length, colour, opacity):
        """One tapered plume shell, as a fan of triangles."""
        if length <= 0.05 or opacity <= 0.02:
            return

        sides = 8
        angles = np.linspace(0.0, 2.0 * math.pi, sides, endpoint=False)
        ring = np.stack(
            [
                np.full(sides, nozzle[0]),
                nozzle[1] + radius * np.cos(angles),
                nozzle[2] + radius * np.sin(angles),
            ],
            axis=1,
        )
        tip = np.array([nozzle[0] - length, nozzle[1], nozzle[2]])

        world = state.position + np.vstack([ring, tip]) @ dcm.T
        cameraspace = renderer.camera.to_camera(world)
        if cameraspace[:, 2].min() < 0.5:
            return  # partly behind the eye; not worth clipping a plume

        screen = renderer.project(cameraspace)
        origin_x, origin_y = renderer.viewport.left, renderer.viewport.top
        apex = (screen[-1, 0] - origin_x, screen[-1, 1] - origin_y)
        alpha = int(min(255, 235 * opacity))

        for i in range(sides):
            j = (i + 1) % sides
            pygame.draw.polygon(
                layer,
                (*colour, alpha),
                [
                    (screen[i, 0] - origin_x, screen[i, 1] - origin_y),
                    (screen[j, 0] - origin_x, screen[j, 1] - origin_y),
                    apex,
                ],
            )


class PrecipitationField:
    """Rain or snow, as screen-space particles moving with the aircraft.

    Screen space rather than world space on purpose. A world-space particle
    field dense enough to look like rain at the windscreen needs millions of
    drops out to the visibility, and all but a handful of them project to less
    than a pixel. What a pilot sees is the near field, and the near field is
    a band a few tens of metres across that travels with the aeroplane.

    The motion is derived from the step index, not a wall clock, so a replay
    of a run produces the same picture the run did.
    """

    __slots__ = ("_seeded", "_size", "_field", "_seed")

    COUNT = 520

    def __init__(self, seed: int = 1) -> None:
        self._seed = seed
        self._size: tuple[int, int] = (0, 0)
        self._field: np.ndarray | None = None
        self._seeded = False

    def _particles(self, size: tuple[int, int]) -> np.ndarray:
        """Positions and depths, laid out once per viewport size."""
        if self._field is not None and self._size == size:
            return self._field
        rng = np.random.default_rng(self._seed + 4111)
        width, height = size
        self._field = np.stack(
            [
                rng.uniform(-0.2 * width, 1.2 * width, self.COUNT),
                rng.uniform(-0.2 * height, 1.2 * height, self.COUNT),
                # Depth, 0 near to 1 far: near drops are longer and faster,
                # which is the only cue that gives the field any depth at all.
                rng.uniform(0.0, 1.0, self.COUNT),
            ],
            axis=1,
        )
        self._size = size
        return self._field

    def draw(self, renderer, state, weather, sim) -> None:
        spec = weather.precipitation
        if spec.intensity <= 0.0:
            return

        viewport = renderer.viewport
        field = self._particles(viewport.size)
        width, height = viewport.size

        # Frozen precipitation falls slowly and tumbles; liquid falls fast and
        # is streaked backwards by the aircraft's own speed.
        speed = state.derived.vtas
        if spec.frozen:
            fall = 0.9 * height
            slant = -0.35 * min(1.0, speed / 90.0) * height
            length, colour, alpha = 3.0, (238, 242, 250), 190
        else:
            fall = 3.2 * height
            slant = -1.9 * min(1.0, speed / 90.0) * height
            length, colour, alpha = 26.0, (198, 212, 232), 150

        time = sim.clock.time
        shown = int(self.COUNT * spec.intensity)
        if shown <= 0:
            return

        near = 0.35 + 0.65 * (1.0 - field[:shown, 2])
        x = field[:shown, 0] + slant * near * time
        y = field[:shown, 1] + fall * near * time
        # Wrap through a band larger than the viewport, so nothing pops in at
        # an edge the eye is looking at.
        x = np.mod(x + 0.2 * width, 1.4 * width) - 0.2 * width
        y = np.mod(y + 0.2 * height, 1.4 * height) - 0.2 * height

        layer = renderer.alpha_layer("precipitation")
        left, top = viewport.left, viewport.top
        dx = slant / max(abs(fall), 1.0) * length
        for i in range(shown):
            sx, sy, scale = float(x[i]), float(y[i]), float(near[i])
            if spec.frozen:
                pygame.draw.circle(
                    layer, (*colour, alpha), (int(sx), int(sy)), max(1, int(2.0 * scale))
                )
            else:
                pygame.draw.line(
                    layer,
                    (*colour, alpha),
                    (sx, sy),
                    (sx + dx * scale, sy + length * scale),
                    1 if scale < 0.7 else 2,
                )
        renderer.surface.blit(layer, (left, top))

"""Pre-flight setup: every condition is chosen before the simulation starts.

Nothing here talks to the flight model. The screen produces a ``SimConditions``
and the simulation is built from it once, which is what makes a flight
repeatable -- the same settings and the same seed fly the same flight.

The briefing panel on the right recomputes live from the actual aircraft data
package, so the stall speed shown next to a 90 % fuel load is the stall speed
that load really produces, not a placard figure.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable

import pygame

from ..core.model_package import PackageError, load_aircraft
from ..core.units import (
    G0,
    deg,
    ft,
    kt,
    to_deg,
    to_ft,
    to_kt,
)
from ..env.wind import TURBULENCE_PRESETS
from .config import (
    DATA_ROOT,
    FAILURE_LABELS,
    START_MODE_LABELS,
    FailureMode,
    SimConditions,
    StartMode,
)
from .instruments import (
    AMBER,
    CYAN,
    DIM,
    GREEN,
    PANEL_EDGE,
    RED,
    WHITE,
    Fonts,
    draw_text,
)

BACKGROUND = (13, 15, 20)
CARD = (20, 24, 31)
HEADER = (150, 200, 240)
SELECT = (36, 62, 96)


@dataclass
class Setting:
    """One adjustable row on the setup screen."""

    label: str
    show: Callable[[SimConditions], str]
    adjust: Callable[[SimConditions, int, bool], None]
    enabled: Callable[[SimConditions], bool] = lambda c: True
    note: str = ""


def _cycle(values: list, current, direction: int):
    index = values.index(current) if current in values else 0
    return values[(index + direction) % len(values)]


def _clamp(value, low, high):
    return max(low, min(high, value))


def build_settings() -> list[tuple[str, list[Setting]]]:
    """The full pre-flight configuration, grouped into sections."""
    aircraft_ids = ["aeroliner_200", "aerofalcon_x"]
    aircraft_names = {
        "aeroliner_200": "AeroLiner-200   (passenger)",
        "aerofalcon_x": "AeroFalcon-X    (war)",
    }
    turbulence_levels = list(TURBULENCE_PRESETS)
    failures = [
        FailureMode.NONE,
        FailureMode.ENGINE_OUT,
        FailureMode.HYDRAULIC,
        FailureMode.ELEVATOR_JAM,
        FailureMode.FUEL_LEAK,
    ]
    start_modes = [StartMode.RUNWAY, StartMode.AIRBORNE, StartMode.APPROACH]

    def airborne(c: SimConditions) -> bool:
        return c.start_mode != StartMode.RUNWAY

    return [
        (
            "AIRCRAFT",
            [
                Setting(
                    "Type",
                    lambda c: aircraft_names[c.aircraft],
                    lambda c, d, f: setattr(c, "aircraft", _cycle(aircraft_ids, c.aircraft, d)),
                    note="war plane or passenger jet",
                ),
            ],
        ),
        (
            "INITIAL CONDITION",
            [
                Setting(
                    "Start",
                    lambda c: START_MODE_LABELS[c.start_mode],
                    lambda c, d, f: setattr(c, "start_mode", _cycle(start_modes, c.start_mode, d)),
                ),
                Setting(
                    "Altitude",
                    lambda c: f"{to_ft(c.altitude):,.0f} ft"
                    if c.start_mode == StartMode.AIRBORNE
                    else "1,900 ft on the glidepath",
                    lambda c, d, f: setattr(
                        c, "altitude", _clamp(c.altitude + d * ft(5000 if f else 500), 0.0, ft(50000))
                    ),
                    enabled=lambda c: c.start_mode == StartMode.AIRBORNE,
                ),
                Setting(
                    "Airspeed",
                    lambda c: f"{to_kt(c.airspeed):.0f} kt CAS"
                    if c.start_mode == StartMode.AIRBORNE
                    else "Vref, computed for the landing configuration",
                    lambda c, d, f: setattr(
                        c, "airspeed", _clamp(c.airspeed + d * kt(25 if f else 5), kt(80), kt(600))
                    ),
                    enabled=lambda c: c.start_mode == StartMode.AIRBORNE,
                    note="approach and runway starts compute their own speed",
                ),
                Setting(
                    "Heading",
                    lambda c: f"{to_deg(c.heading) % 360:03.0f} deg",
                    lambda c, d, f: setattr(
                        c, "heading", (c.heading + d * deg(45 if f else 5)) % (2 * math.pi)
                    ),
                ),
                Setting(
                    "Field elevation",
                    lambda c: f"{to_ft(c.field_elevation):,.0f} ft",
                    lambda c, d, f: setattr(
                        c,
                        "field_elevation",
                        _clamp(c.field_elevation + d * ft(500 if f else 100), 0.0, ft(9000)),
                    ),
                ),
            ],
        ),
        (
            "LOADING",
            [
                Setting(
                    "Fuel",
                    lambda c: f"{c.fuel_fraction * 100:.0f} %",
                    lambda c, d, f: setattr(
                        c, "fuel_fraction", _clamp(c.fuel_fraction + d * (0.25 if f else 0.05), 0.02, 1.0)
                    ),
                ),
                Setting(
                    "Payload",
                    lambda c: f"{c.payload_fraction * 100:.0f} %"
                    + (" of stores" if c.is_military else " of cabin"),
                    lambda c, d, f: setattr(
                        c,
                        "payload_fraction",
                        _clamp(c.payload_fraction + d * (0.25 if f else 0.05), 0.0, 1.0),
                    ),
                ),
            ],
        ),
        (
            "WEATHER",
            [
                Setting(
                    "Wind speed",
                    lambda c: f"{to_kt(c.wind_speed):.0f} kt",
                    lambda c, d, f: setattr(
                        c, "wind_speed", _clamp(c.wind_speed + d * kt(10 if f else 2), 0.0, kt(120))
                    ),
                ),
                Setting(
                    "Wind from",
                    lambda c: f"{to_deg(c.wind_direction) % 360:03.0f} deg",
                    lambda c, d, f: setattr(
                        c, "wind_direction", (c.wind_direction + d * deg(45 if f else 10)) % (2 * math.pi)
                    ),
                    enabled=lambda c: c.wind_speed > 0.4,
                ),
                Setting(
                    "Wind shear",
                    lambda c: "veers and strengthens with height" if c.wind_shear else "uniform with height",
                    lambda c, d, f: setattr(c, "wind_shear", not c.wind_shear),
                    enabled=lambda c: c.wind_speed > 0.4,
                ),
                Setting(
                    "Turbulence",
                    lambda c: f"{c.turbulence}  ({TURBULENCE_PRESETS[c.turbulence]:.1f} m/s rms)",
                    lambda c, d, f: setattr(c, "turbulence", _cycle(turbulence_levels, c.turbulence, d)),
                ),
                Setting(
                    "ISA offset",
                    lambda c: f"{c.temperature_offset:+.0f} C",
                    lambda c, d, f: setattr(
                        c, "temperature_offset", _clamp(c.temperature_offset + d * (5 if f else 1), -40.0, 40.0)
                    ),
                ),
                Setting(
                    "QNH",
                    lambda c: f"{c.qnh / 100.0:.0f} hPa",
                    lambda c, d, f: setattr(c, "qnh", _clamp(c.qnh + d * (1000 if f else 100), 95000.0, 105000.0)),
                ),
                Setting(
                    "Time of day",
                    lambda c: f"{int(c.time_of_day):02d}:{int((c.time_of_day % 1) * 60):02d}",
                    lambda c, d, f: setattr(c, "time_of_day", (c.time_of_day + d * (3.0 if f else 0.5)) % 24.0),
                ),
                Setting(
                    "Visibility",
                    lambda c: f"{c.visibility / 1000.0:.0f} km",
                    lambda c, d, f: setattr(
                        c, "visibility", _clamp(c.visibility + d * (10000 if f else 2000), 2000.0, 80000.0)
                    ),
                ),
            ],
        ),
        (
            "FAILURES",
            [
                Setting(
                    "Failure",
                    lambda c: FAILURE_LABELS[c.failure],
                    lambda c, d, f: setattr(c, "failure", _cycle(failures, c.failure, d)),
                ),
                Setting(
                    "Trigger at",
                    lambda c: f"T + {c.failure_time:.0f} s",
                    lambda c, d, f: setattr(
                        c, "failure_time", _clamp(c.failure_time + d * (60 if f else 10), 0.0, 3600.0)
                    ),
                    enabled=lambda c: c.failure != FailureMode.NONE,
                ),
            ],
        ),
        (
            "SIMULATION",
            [
                Setting(
                    "Flight assist",
                    lambda c: "on  (damped rate commands)" if c.flight_assist else "off (direct surface)",
                    lambda c, d, f: setattr(c, "flight_assist", not c.flight_assist),
                ),
                Setting(
                    "Step size",
                    lambda c: f"{c.dt * 1000:.0f} ms  ({1 / c.dt:.0f} Hz)"
                    + ("  above validated" if c.dt > 0.0101 else ""),
                    lambda c, d, f: setattr(c, "dt", _cycle([0.005, 0.01, 0.02], c.dt, d)),
                ),
                Setting(
                    "Seed",
                    lambda c: str(c.seed),
                    lambda c, d, f: setattr(c, "seed", max(1, c.seed + d * (100 if f else 1))),
                    note="same seed, same gusts",
                ),
            ],
        ),
    ]


class SetupScreen:
    """The pre-flight screen. Returns a SimConditions, or None if cancelled."""

    def __init__(self, surface: pygame.Surface, conditions: SimConditions | None = None) -> None:
        self.surface = surface
        self.conditions = conditions or SimConditions()
        self.sections = build_settings()
        self.rows = [s for _, group in self.sections for s in group]
        self.index = 0
        self.fonts = Fonts(1.0)
        self._model_cache: dict[str, object] = {}
        self._error: str | None = None

    # ---------------------------------------------------------------------

    def model(self):
        """Load and cache the selected aircraft package."""
        name = self.conditions.aircraft
        if name not in self._model_cache:
            try:
                self._model_cache[name] = load_aircraft(DATA_ROOT / name)
                self._error = None
            except PackageError as exc:
                self._model_cache[name] = None
                self._error = str(exc)
        return self._model_cache[name]

    def _enabled_rows(self) -> list[int]:
        return [i for i, row in enumerate(self.rows) if row.enabled(self.conditions)]

    def _move(self, direction: int) -> None:
        enabled = self._enabled_rows()
        if not enabled:
            return
        if self.index in enabled:
            position = enabled.index(self.index)
            self.index = enabled[(position + direction) % len(enabled)]
        else:
            self.index = enabled[0]

    # ---------------------------------------------------------------------

    def run(self, clock: pygame.time.Clock) -> SimConditions | None:
        """Event loop. Returns the chosen conditions, or None to quit."""
        while True:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    return None
                if event.type == pygame.VIDEORESIZE:
                    self.surface = pygame.display.set_mode(
                        (event.w, event.h), pygame.RESIZABLE
                    )
                if event.type == pygame.KEYDOWN:
                    fast = bool(event.mod & pygame.KMOD_SHIFT)
                    if event.key in (pygame.K_ESCAPE, pygame.K_q):
                        return None
                    if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_SPACE):
                        if self.model() is not None:
                            self.conditions.validate(self.model())
                            return self.conditions
                    elif event.key in (pygame.K_UP, pygame.K_w):
                        self._move(-1)
                    elif event.key in (pygame.K_DOWN, pygame.K_s):
                        self._move(1)
                    elif event.key in (pygame.K_LEFT, pygame.K_a):
                        self.rows[self.index].adjust(self.conditions, -1, fast)
                    elif event.key in (pygame.K_RIGHT, pygame.K_d):
                        self.rows[self.index].adjust(self.conditions, 1, fast)
                    elif event.key == pygame.K_r:
                        self.conditions = SimConditions()

                if event.type == pygame.MOUSEBUTTONDOWN and event.button in (1, 3):
                    self._handle_click(event.pos, -1 if event.button == 3 else 1)

            self.draw()
            pygame.display.flip()
            clock.tick(60)

    def _handle_click(self, position, direction: int) -> None:
        for i, rect in getattr(self, "_row_rects", []):
            if rect.collidepoint(position):
                self.index = i
                if self.rows[i].enabled(self.conditions):
                    # Clicking the right half increases, the left half decreases.
                    step = 1 if position[0] > rect.centerx else -1
                    self.rows[i].adjust(self.conditions, step * direction, False)
                return

    # ---------------------------------------------------------------------

    def draw(self) -> None:
        surface = self.surface
        surface.fill(BACKGROUND)
        width, height = surface.get_size()

        draw_text(surface, self.fonts.huge, "AeroSim Lab", (34, 22), WHITE)
        draw_text(
            surface,
            self.fonts.small,
            "flight simulator  --  set every condition before departure",
            (36, 66),
            DIM,
        )

        column_width = int(width * 0.56)
        self._draw_settings(pygame.Rect(24, 96, column_width - 34, height - 150))
        self._draw_briefing(
            pygame.Rect(column_width, 96, width - column_width - 24, height - 150)
        )

        keys = (
            "arrows / WASD move and change   |   SHIFT+arrow coarse   |   "
            "ENTER start flight   |   R reset   |   ESC quit"
        )
        draw_text(surface, self.fonts.small, keys, (34, height - 36), DIM)

    def _draw_settings(self, rect: pygame.Rect) -> None:
        surface = self.surface
        self._row_rects: list[tuple[int, pygame.Rect]] = []

        y = rect.y
        row_index = 0
        line_height = 25

        for title, group in self.sections:
            draw_text(surface, self.fonts.small, title, (rect.x + 4, y), HEADER)
            pygame.draw.line(
                surface, PANEL_EDGE, (rect.x + 4, y + 19), (rect.right - 8, y + 19), 1
            )
            y += 26

            for setting in group:
                enabled = setting.enabled(self.conditions)
                row_rect = pygame.Rect(rect.x, y - 3, rect.width, line_height)
                self._row_rects.append((row_index, row_rect))

                if row_index == self.index:
                    pygame.draw.rect(surface, SELECT, row_rect, border_radius=4)

                label_colour = WHITE if enabled else (70, 76, 88)
                value_colour = (
                    CYAN if enabled else (66, 72, 84)
                )
                if row_index == self.index and enabled:
                    value_colour = (255, 226, 140)

                draw_text(surface, self.fonts.small, setting.label, (rect.x + 12, y), label_colour)
                draw_text(
                    surface,
                    self.fonts.small,
                    self.rows[row_index].show(self.conditions),
                    (rect.x + 190, y),
                    value_colour,
                )
                if setting.note and row_index == self.index:
                    draw_text(
                        surface,
                        self.fonts.tiny,
                        setting.note,
                        (rect.right - 10, y + 3),
                        DIM,
                        "topright",
                    )

                y += line_height
                row_index += 1
            y += 8

    def _draw_briefing(self, rect: pygame.Rect) -> None:
        surface = self.surface
        pygame.draw.rect(surface, CARD, rect, border_radius=8)
        pygame.draw.rect(surface, PANEL_EDGE, rect, 1, border_radius=8)

        model = self.model()
        x = rect.x + 20
        y = rect.y + 18

        if model is None:
            draw_text(surface, self.fonts.medium, "Aircraft package failed to load", (x, y), RED)
            for line in (self._error or "").splitlines()[:14]:
                y += 20
                draw_text(surface, self.fonts.tiny, line[:90], (x, y), AMBER)
            return

        conditions = self.conditions

        draw_text(surface, self.fonts.large, model.display_name, (x, y), WHITE)
        y += 34
        draw_text(
            surface,
            self.fonts.small,
            f"{model.raw('manifest', 'role', model.category)}",
            (x, y),
            CYAN if model.category == "passenger" else AMBER,
        )
        y += 22
        draw_text(
            surface,
            self.fonts.tiny,
            f"package v{model.version}   sha256 {model.checksum[:16]}",
            (x, y),
            DIM,
        )
        y += 24

        # Wrapped description straight out of the manifest.
        description = " ".join(model.description.split())
        y = self._wrap(description, x, y, rect.width - 40, DIM, self.fonts.tiny, limit=4)
        y += 10

        # -- computed performance for the chosen loading --------------------
        from ..fdm.aero import AeroModel
        from ..fdm.mass import MassModel
        from ..fdm.propulsion import PropulsionModel
        from ..env.atmosphere import Atmosphere

        aero = AeroModel(model)
        mass_model = MassModel(model)
        propulsion = PropulsionModel(model)
        atmosphere = Atmosphere(conditions.temperature_offset, conditions.qnh)

        mass_model.set_fuel(conditions.fuel_fraction * mass_model.fuel_capacity)
        mass_model.set_payload(conditions.payload_fraction * mass_model.max_payload)
        properties = mass_model.compute()

        surface_air = atmosphere.sample(conditions.field_elevation)
        vs_clean = aero.stall_speed(properties.mass, surface_air.density)
        vs_landing = aero.stall_speed(properties.mass, surface_air.density, flap=1.0)
        static_thrust = propulsion.steady_thrust(1.0, surface_air.density_ratio, 0.0, True)
        mtom = model.get("mass_properties", "max_takeoff_mass")

        draw_text(surface, self.fonts.small, "COMPUTED FOR THIS LOADING", (x, y), HEADER)
        y += 22

        overweight = properties.mass > mtom
        rows = [
            ("Take-off mass", f"{properties.mass / 1000.0:,.1f} t  of {mtom / 1000.0:,.1f} t max", RED if overweight else WHITE),
            ("Fuel / payload", f"{properties.fuel_mass:,.0f} kg  /  {properties.payload_mass:,.0f} kg", DIM),
            ("Centre of gravity", f"{properties.cg[0]:+.2f} m"
             + ("" if mass_model.cg_within_limits(properties.cg[0]) else "   OUT OF LIMITS"),
             WHITE if mass_model.cg_within_limits(properties.cg[0]) else RED),
            ("Stall speed clean", f"{to_kt(vs_clean):.0f} kt", WHITE),
            ("Stall speed landing", f"{to_kt(vs_landing):.0f} kt", WHITE),
            ("Rotate / approach", f"{to_kt(vs_clean * 1.15):.0f} kt  /  {to_kt(vs_landing * 1.3):.0f} kt", GREEN),
            ("Thrust / weight", f"{static_thrust / (properties.mass * G0):.3f}"
             + ("  with augmentation" if propulsion.has_afterburner else ""), WHITE),
            ("CL max / peak L/D", f"{aero.cl_max:.2f}  /  {self._peak_ld(aero):.1f}", DIM),
            ("Static margin", f"{abs(aero.cm_alpha) / aero.cl_alpha * 100:.1f} % MAC", DIM),
            ("Aspect ratio", f"{aero.aspect_ratio:.2f}", DIM),
        ]
        for label, value, colour in rows:
            draw_text(surface, self.fonts.tiny, label, (x, y), DIM)
            draw_text(surface, self.fonts.small, value, (x + 168, y - 2), colour)
            y += 21

        y += 8

        # -- warnings -------------------------------------------------------
        warnings = conditions.validate(model)
        if overweight:
            warnings = warnings + [
                f"take-off mass {properties.mass / 1000.0:.1f} t exceeds the "
                f"{mtom / 1000.0:.1f} t maximum"
            ]

        draw_text(
            surface,
            self.fonts.small,
            "PRE-FLIGHT CHECKS" if warnings else "PRE-FLIGHT CHECKS   all clear",
            (x, y),
            AMBER if warnings else GREEN,
        )
        y += 22
        if not warnings:
            draw_text(
                surface,
                self.fonts.tiny,
                "Configuration is inside the aircraft's declared envelope.",
                (x, y),
                DIM,
            )
        for warning in warnings[:6]:
            y = self._wrap("- " + warning, x, y, rect.width - 40, AMBER, self.fonts.tiny, limit=2)
            y += 2

        if warnings:
            y += 6
            draw_text(
                surface,
                self.fonts.tiny,
                "These are warnings, not blocks -- the flight will still start.",
                (x, y),
                DIM,
            )

    def _peak_ld(self, aero) -> float:
        best = 0.0
        for degrees in range(-2, 200):
            alpha = math.radians(degrees * 0.1)
            cl = aero.cl_table.lookup(alpha)
            cd = aero.cd0 + cl * cl / (math.pi * aero.aspect_ratio * aero.oswald)
            if cd > 1e-9:
                best = max(best, cl / cd)
        return best

    def _wrap(self, text, x, y, width, colour, font, limit: int = 3) -> int:
        words = text.split()
        line = ""
        drawn = 0
        for word in words:
            candidate = f"{line} {word}".strip()
            if font.size(candidate)[0] > width and line:
                draw_text(self.surface, font, line, (x, y), colour)
                y += font.get_height() + 1
                drawn += 1
                line = word
                if drawn >= limit:
                    return y
            else:
                line = candidate
        if line and drawn < limit:
            draw_text(self.surface, font, line, (x, y), colour)
            y += font.get_height() + 1
        return y

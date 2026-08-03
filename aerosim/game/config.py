"""Everything the pilot chooses before the flight starts.

The setup screen writes one of these; the simulation reads it once and never
consults it again. Keeping the whole pre-flight configuration in a single
plain object is what makes a flight reproducible: the same ``SimConditions``
and the same seed produce the same flight, gust for gust.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ..core.units import ft, kt, to_deg, to_ft, to_kt

DATA_ROOT = Path(__file__).resolve().parent.parent / "data" / "aircraft"


class StartMode:
    RUNWAY = "runway"
    AIRBORNE = "airborne"
    APPROACH = "approach"


START_MODE_LABELS = {
    StartMode.RUNWAY: "Runway (line up for take-off)",
    StartMode.AIRBORNE: "Airborne (trimmed and level)",
    StartMode.APPROACH: "Approach (6 nm final, 3 deg)",
}


class FailureMode:
    NONE = "none"
    ENGINE_OUT = "engine_out"
    HYDRAULIC = "hydraulic"
    ELEVATOR_JAM = "elevator_jam"
    FUEL_LEAK = "fuel_leak"


FAILURE_LABELS = {
    FailureMode.NONE: "None",
    FailureMode.ENGINE_OUT: "Engine failure",
    FailureMode.HYDRAULIC: "Hydraulic pressure loss",
    FailureMode.ELEVATOR_JAM: "Elevator jam",
    FailureMode.FUEL_LEAK: "Fuel leak",
}


@dataclass
class SimConditions:
    """The complete pre-flight configuration.

    Stored in SI throughout, exactly like the rest of the simulator. The setup
    screen converts to feet and knots for display and straight back again; no
    aviation unit survives past the widget that drew it.
    """

    # -- aircraft ---------------------------------------------------------
    aircraft: str = "aeroliner_200"

    # -- initial condition ------------------------------------------------
    start_mode: str = StartMode.AIRBORNE
    altitude: float = ft(10000.0)  # m, for airborne and approach starts
    airspeed: float = kt(280.0)  # m/s CAS
    heading: float = 0.0  # rad true

    # -- loading ----------------------------------------------------------
    fuel_fraction: float = 0.60  # of total tank capacity
    payload_fraction: float = 0.55  # of maximum payload / stores

    # -- weather ----------------------------------------------------------
    wind_speed: float = 0.0  # m/s at the surface
    wind_direction: float = 0.0  # rad, meteorological (from)
    wind_shear: bool = False  # veer and strengthen with height
    turbulence: str = "none"
    temperature_offset: float = 0.0  # K from ISA
    qnh: float = 101325.0  # Pa
    visibility: float = 30000.0  # m, affects the haze only
    time_of_day: float = 12.0  # hours, drives the lighting

    # -- terrain ----------------------------------------------------------
    field_elevation: float = 0.0  # m

    # -- failures ---------------------------------------------------------
    failure: str = FailureMode.NONE
    failure_time: float = 60.0  # s after start

    # -- determinism ------------------------------------------------------
    seed: int = 1
    dt: float = 0.01

    # -- assists ----------------------------------------------------------
    flight_assist: bool = True  # damps rate commands, does not fly for you

    warnings: list[str] = field(default_factory=list)

    # ---------------------------------------------------------------------

    @property
    def package_path(self) -> Path:
        return DATA_ROOT / self.aircraft

    @property
    def is_military(self) -> bool:
        return self.aircraft == "aerofalcon_x"

    def describe(self) -> list[tuple[str, str]]:
        """Human-readable summary, for the briefing panel."""
        wind = (
            "calm"
            if self.wind_speed < 0.5
            else f"{to_deg(self.wind_direction):03.0f} deg / {to_kt(self.wind_speed):.0f} kt"
            + (" sheared" if self.wind_shear else "")
        )
        return [
            ("Start", START_MODE_LABELS[self.start_mode]),
            ("Altitude", f"{to_ft(self.altitude):,.0f} ft"),
            ("Airspeed", f"{to_kt(self.airspeed):.0f} kt CAS"),
            ("Heading", f"{to_deg(self.heading):03.0f} deg"),
            ("Fuel", f"{self.fuel_fraction * 100:.0f} %"),
            ("Payload", f"{self.payload_fraction * 100:.0f} %"),
            ("Wind", wind),
            ("Turbulence", self.turbulence),
            ("ISA offset", f"{self.temperature_offset:+.0f} C"),
            ("Failure", FAILURE_LABELS[self.failure]),
            ("Seed", str(self.seed)),
        ]

    def validate(self, model=None) -> list[str]:
        """Check the configuration against the aircraft, returning warnings.

        Warnings, not errors: a configuration outside the validated envelope is
        allowed -- the simulator will fly it and the envelope monitor will say
        so. Refusing to start would hide the very behaviour someone setting up
        an unusual condition is probably trying to see.
        """
        problems: list[str] = []

        if not self.package_path.is_dir():
            problems.append(f"aircraft package {self.aircraft} not found")
            return problems

        if self.dt <= 0.0:
            problems.append("step size must be positive")
        elif self.dt > 0.0101:
            problems.append(
                f"step size {self.dt:.3f} s is above the validated 0.01 s; "
                "integration accuracy is not characterised here"
            )

        if model is not None:
            vmo = model.get("limitations", "vmo")
            ceiling = model.get("limitations", "max_operating_altitude", 15000.0)

            if self.start_mode != StartMode.RUNWAY:
                if self.airspeed > vmo:
                    problems.append(
                        f"start speed {to_kt(self.airspeed):.0f} kt is above VMO "
                        f"{to_kt(vmo):.0f} kt"
                    )
                stall = model.get("limitations", "stall_speed_clean")
                if self.airspeed < stall * 1.05:
                    problems.append(
                        f"start speed {to_kt(self.airspeed):.0f} kt is at or below "
                        f"the clean stall speed {to_kt(stall):.0f} kt"
                    )
            if self.altitude > ceiling:
                problems.append(
                    f"start altitude {to_ft(self.altitude):,.0f} ft is above the "
                    f"{to_ft(ceiling):,.0f} ft ceiling"
                )

            max_crosswind = model.get("limitations", "max_crosswind", kt(35.0))
            if self.start_mode == StartMode.RUNWAY and self.wind_speed > 0.5:
                crosswind = abs(
                    self.wind_speed * math.sin(self.wind_direction - self.heading)
                )
                if crosswind > max_crosswind:
                    problems.append(
                        f"crosswind component {to_kt(crosswind):.0f} kt exceeds the "
                        f"{to_kt(max_crosswind):.0f} kt demonstrated limit"
                    )

        self.warnings = problems
        return problems

    def to_dict(self) -> dict:
        data = asdict(self)
        data.pop("warnings", None)
        return data

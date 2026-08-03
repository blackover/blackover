"""Control surface actuators: rate limits, position limits and failure modes.

A pilot commands a position; the surface gets there at a finite rate through a
finite travel. Modelling that matters because rate limiting is what turns a
rapid stick input into a pilot-induced oscillation, and because reduced
hydraulic pressure shows up as a reduced actuator rate rather than as a
mysteriously less responsive aeroplane.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum


class ActuatorFailure(Enum):
    NONE = "none"
    JAMMED = "jammed"  # frozen at its current position
    RUNAWAY = "runaway"  # drives to a hardover and stays there
    REDUCED_RATE = "reduced_rate"  # degraded hydraulic supply
    FLOATING = "floating"  # no authority, trails to neutral


@dataclass
class Actuator:
    """One control axis, normalised to [-1, 1] of available travel."""

    name: str
    max_rate: float = 4.0  # units of normalised travel per second
    lower_limit: float = -1.0
    upper_limit: float = 1.0
    time_constant: float = 0.05  # first-order lag behind the rate limiter
    position: float = 0.0
    commanded: float = 0.0
    failure: ActuatorFailure = ActuatorFailure.NONE
    runaway_target: float = 1.0
    rate_scale: float = 1.0  # 1.0 = full hydraulic pressure

    def reset(self) -> None:
        self.position = 0.0
        self.commanded = 0.0
        self.failure = ActuatorFailure.NONE
        self.rate_scale = 1.0

    @property
    def rate_limited(self) -> bool:
        """True when the surface cannot keep up with the command."""
        return abs(self.commanded - self.position) > self.max_rate * self.rate_scale * 0.02

    def update(self, dt: float, command: float) -> float:
        """Drive the surface one step toward ``command``; return its position."""
        self.commanded = max(self.lower_limit, min(self.upper_limit, command))

        if self.failure is ActuatorFailure.JAMMED:
            return self.position

        if self.failure is ActuatorFailure.RUNAWAY:
            target = self.runaway_target
            rate = self.max_rate * self.rate_scale
        elif self.failure is ActuatorFailure.FLOATING:
            target = 0.0
            rate = self.max_rate * 0.3
        else:
            target = self.commanded
            rate = self.max_rate * self.rate_scale
            if self.failure is ActuatorFailure.REDUCED_RATE:
                rate *= 0.35

        # Rate limit first, then the first-order lag. In that order, because a
        # lag applied before the limiter would let a fast command sneak through
        # as a fast surface.
        error = target - self.position
        max_step = rate * dt
        step = max(-max_step, min(max_step, error))

        if abs(error) <= max_step and self.time_constant > 0.0:
            alpha = 1.0 - math.exp(-dt / self.time_constant)
            step = error * alpha

        self.position += step
        self.position = max(self.lower_limit, min(self.upper_limit, self.position))
        return self.position


class ControlSurfaces:
    """The set of actuators for one aircraft, built from its data package."""

    def __init__(self, model) -> None:
        def rate(axis: str, default: float) -> float:
            """Surface rate in normalised travel per second.

            The data package quotes a physical rate in deg/s and a physical
            travel in deg; the ratio is what the normalised actuator needs.
            """
            travel = model.get("flight_controls", f"{axis}.max_deflection", math.radians(25.0))
            rate_value = model.get("flight_controls", f"{axis}.max_rate", math.radians(60.0))
            return rate_value / travel if travel > 1.0e-6 else default

        self.elevator = Actuator(
            "elevator",
            max_rate=rate("elevator", 3.0),
            time_constant=model.get("flight_controls", "elevator.time_constant", 0.06),
        )
        self.aileron = Actuator(
            "aileron",
            max_rate=rate("aileron", 4.0),
            time_constant=model.get("flight_controls", "aileron.time_constant", 0.05),
        )
        self.rudder = Actuator(
            "rudder",
            max_rate=rate("rudder", 2.5),
            time_constant=model.get("flight_controls", "rudder.time_constant", 0.07),
        )

        # Flaps and gear are slow, one-way-at-a-time devices rather than
        # continuously commanded surfaces.
        self.flap = Actuator(
            "flap",
            max_rate=1.0 / model.get("flight_controls", "flaps.transit_time", 12.0),
            lower_limit=0.0,
            time_constant=0.0,
        )
        self.speedbrake = Actuator(
            "speedbrake",
            max_rate=1.0 / model.get("flight_controls", "speedbrake.transit_time", 2.0),
            lower_limit=0.0,
            time_constant=0.0,
        )

        self.gear_transit_time = model.get("landing_gear", "transit_time", 10.0)
        self.gear_position = 1.0  # 1.0 down and locked, 0.0 up and locked
        self.gear_commanded_down = True

        self.max_elevator_deflection = model.get(
            "flight_controls", "elevator.max_deflection", math.radians(25.0)
        )
        self.max_aileron_deflection = model.get(
            "flight_controls", "aileron.max_deflection", math.radians(20.0)
        )
        self.max_rudder_deflection = model.get(
            "flight_controls", "rudder.max_deflection", math.radians(25.0)
        )
        self.max_flap_deflection = model.get(
            "flight_controls", "flaps.max_deflection", math.radians(40.0)
        )

    @property
    def actuators(self) -> list[Actuator]:
        return [self.elevator, self.aileron, self.rudder, self.flap, self.speedbrake]

    def set_hydraulic_pressure(self, fraction: float) -> None:
        """Scale every actuator's rate with available hydraulic pressure.

        No special-case logic connects a hydraulic fault to aircraft response:
        pressure sets rate, rate sets how fast a surface moves, and the
        handling change falls out of the dynamics.
        """
        scale = max(0.05, min(1.0, fraction))
        for actuator in self.actuators:
            actuator.rate_scale = scale

    def update(
        self,
        dt: float,
        *,
        elevator: float,
        aileron: float,
        rudder: float,
        flap: float,
        speedbrake: float,
        gear_down: bool,
    ) -> None:
        self.elevator.update(dt, elevator)
        self.aileron.update(dt, aileron)
        self.rudder.update(dt, rudder)
        self.flap.update(dt, flap)
        self.speedbrake.update(dt, speedbrake)

        self.gear_commanded_down = gear_down
        target = 1.0 if gear_down else 0.0
        rate = dt / max(self.gear_transit_time, 1.0e-3)
        if self.gear_position < target:
            self.gear_position = min(target, self.gear_position + rate)
        elif self.gear_position > target:
            self.gear_position = max(target, self.gear_position - rate)

    @property
    def gear_in_transit(self) -> bool:
        return 0.001 < self.gear_position < 0.999

    @property
    def gear_effective_down(self) -> bool:
        """Gear counts as down for ground reaction only when locked down."""
        return self.gear_position > 0.999

    def reset(self) -> None:
        for actuator in self.actuators:
            actuator.reset()
        self.gear_position = 1.0
        self.gear_commanded_down = True

"""Turbofan model: thrust, spool dynamics and fuel flow.

Thrust is an algebraic map of N1, density ratio and Mach number, with
first-order spool dynamics. This is *not* a thermodynamic cycle deck. There is
no compressor stall, no surge, no thermal transient, no bleed-air effect and no
difference between accelerating and decelerating fuel schedules. EGT is an
indicative linear function of N1 and must not be read as a temperature
prediction.

Acceptable for thrust response timing, engine-out handling, fuel bookkeeping
and autothrottle behaviour. Not acceptable for engine performance analysis.

The afterburner term fitted to the military variant is a generic thrust and
fuel-flow multiplier. It represents no specific technology.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from ..core.frames import cross3
from .tables import Table1D

IDLE_N1 = 0.20


@dataclass
class EngineState:
    """Per-engine running state."""

    n1: float = IDLE_N1  # spool speed, fraction of maximum
    thrust: float = 0.0  # N
    fuel_flow: float = 0.0  # kg/s
    egt: float = 300.0  # K, indicative only
    running: bool = True
    failed: bool = False
    afterburner: bool = False


@dataclass
class PropulsionOutput:
    force_body: np.ndarray = field(default_factory=lambda: np.zeros(3))
    moment_body: np.ndarray = field(default_factory=lambda: np.zeros(3))
    total_thrust: float = 0.0
    total_fuel_flow: float = 0.0


class PropulsionModel:
    """A set of identical engines mounted at fixed body stations."""

    def __init__(self, model) -> None:
        self.engine_count = int(model.get("propulsion", "engine_count"))
        self.max_thrust = model.get("propulsion", "max_thrust_per_engine")
        self.spool_up_time = model.get("propulsion", "spool_up_time", 4.0)
        self.spool_down_time = model.get("propulsion", "spool_down_time", 3.0)
        self.sfc = model.get("propulsion", "specific_fuel_consumption", 1.7e-5)
        self.idle_thrust_fraction = model.get("propulsion", "idle_thrust_fraction", 0.05)
        self.thrust_angle = model.get("propulsion", "thrust_angle", 0.0)

        # Afterburner: absent on the airliner, present on the fighter.
        self.has_afterburner = bool(model.raw("propulsion", "afterburner.available", False))
        self.ab_thrust_factor = model.get("propulsion", "afterburner.thrust_factor", 1.0)
        self.ab_fuel_factor = model.get("propulsion", "afterburner.fuel_factor", 1.0)
        self.ab_spool_time = model.get("propulsion", "afterburner.spool_time", 1.2)

        # Engine positions in body axes, for the yawing moment on engine-out.
        positions = model.raw("propulsion", "positions", [])
        self.positions: list[np.ndarray] = []
        for i in range(self.engine_count):
            if i < len(positions):
                entry = positions[i]
                from ..core.units import to_si

                self.positions.append(
                    np.array(
                        [
                            to_si(entry.get("x", 0.0)),
                            to_si(entry.get("y", 0.0)),
                            to_si(entry.get("z", 0.0)),
                        ]
                    )
                )
            else:
                # Fall back to a symmetric pair either side of the centreline.
                offset = 0.0 if self.engine_count == 1 else (5.0 if i % 2 else -5.0)
                self.positions.append(np.array([0.0, offset, 0.5]))

        # Thrust lapse with Mach number: ram recovery first, then loss.
        mach_spec = model.raw("propulsion", "mach_thrust_table")
        if isinstance(mach_spec, dict):
            self.mach_lapse = Table1D.from_spec(mach_spec, "mach_thrust")
        elif self.has_afterburner:
            # A military engine gains thrust with ram pressure well past M 1.
            self.mach_lapse = Table1D(
                [0.0, 0.4, 0.8, 1.2, 1.6, 2.0],
                [1.00, 1.02, 1.12, 1.30, 1.45, 1.40],
                policy="clamp",
                name="mach_thrust",
            )
        else:
            self.mach_lapse = Table1D(
                [0.0, 0.2, 0.4, 0.6, 0.8, 0.9],
                [1.00, 0.90, 0.83, 0.79, 0.78, 0.79],
                policy="clamp",
                name="mach_thrust",
            )

        self.density_exponent = model.get("propulsion", "density_exponent", 0.85)
        self.engines = [EngineState() for _ in range(self.engine_count)]

    # ---------------------------------------------------------------------

    def reset(self, n1: float = IDLE_N1) -> None:
        for engine in self.engines:
            engine.n1 = n1
            engine.thrust = 0.0
            engine.fuel_flow = 0.0
            engine.egt = 300.0
            engine.running = True
            engine.failed = False
            engine.afterburner = False

    def fail_engine(self, index: int) -> None:
        if 0 <= index < len(self.engines):
            self.engines[index].failed = True
            self.engines[index].running = False

    @property
    def any_failed(self) -> bool:
        return any(engine.failed for engine in self.engines)

    def _thrust_for(self, n1: float, density_ratio: float, mach: float, ab: bool) -> float:
        """Static thrust map, lapsed for altitude and Mach."""
        # Below idle the engine produces almost nothing; above it, thrust rises
        # roughly with the square of the spool fraction above idle.
        if n1 <= IDLE_N1:
            fraction = self.idle_thrust_fraction * (n1 / IDLE_N1)
        else:
            span = (n1 - IDLE_N1) / (1.0 - IDLE_N1)
            fraction = self.idle_thrust_fraction + (
                1.0 - self.idle_thrust_fraction
            ) * span ** 1.35

        thrust = self.max_thrust * fraction
        thrust *= max(density_ratio, 0.0) ** self.density_exponent
        thrust *= self.mach_lapse.lookup(abs(mach))

        if ab and self.has_afterburner:
            thrust *= self.ab_thrust_factor

        return thrust

    def steady_output(
        self, throttle: float, density_ratio: float, mach: float, afterburner: bool = False
    ) -> PropulsionOutput:
        """Force and moment once the spools have settled, without stepping them.

        Used by the trim solver, which needs the equilibrium condition for a
        throttle position rather than whatever the spools happen to be doing.

        This deliberately mirrors ``update`` -- same thrust map, same engine
        positions, same moment arms -- because a trim solver that assembles
        forces its own way finds the equilibrium of a model that does not
        exist. The symptom is not an error message: it is a trimmed aircraft
        that starts every flight with a small, persistent oscillation nobody
        can account for.
        """
        throttle = min(1.0, max(0.0, throttle))
        n1 = IDLE_N1 + (1.0 - IDLE_N1) * throttle
        want_ab = afterburner and self.has_afterburner and throttle > 0.85

        force = np.zeros(3)
        moment = np.zeros(3)
        total_thrust = 0.0
        total_flow = 0.0

        for engine, position in zip(self.engines, self.positions):
            if engine.failed:
                continue
            thrust = self._thrust_for(n1, density_ratio, mach, want_ab)
            thrust_vector = np.array(
                [
                    thrust * math.cos(self.thrust_angle),
                    0.0,
                    -thrust * math.sin(self.thrust_angle),
                ]
            )
            force += thrust_vector
            moment += cross3(position, thrust_vector)
            total_thrust += thrust
            total_flow += self.sfc * thrust * (self.ab_fuel_factor if want_ab else 1.0)

        return PropulsionOutput(
            force_body=force,
            moment_body=moment,
            total_thrust=total_thrust,
            total_fuel_flow=total_flow,
        )

    def steady_thrust(
        self, throttle: float, density_ratio: float, mach: float, afterburner: bool = False
    ) -> float:
        """Total steady thrust magnitude, for performance calculations."""
        return self.steady_output(throttle, density_ratio, mach, afterburner).total_thrust

    def update(
        self,
        dt: float,
        *,
        throttle: float,
        afterburner: bool,
        density_ratio: float,
        mach: float,
        fuel_available: bool = True,
    ) -> PropulsionOutput:
        """Advance the spools one step and return the resulting force."""
        throttle = min(1.0, max(0.0, throttle))
        target_n1 = IDLE_N1 + (1.0 - IDLE_N1) * throttle

        force = np.zeros(3)
        moment = np.zeros(3)
        total_thrust = 0.0
        total_flow = 0.0

        for engine, position in zip(self.engines, self.positions):
            if engine.failed or not fuel_available:
                engine.running = False
                target = 0.0
                tau = self.spool_down_time
            else:
                engine.running = True
                target = target_n1
                tau = self.spool_up_time if target > engine.n1 else self.spool_down_time

            # First-order spool lag, exact for a step input over dt.
            alpha = 1.0 - math.exp(-dt / max(tau, 1.0e-3))
            engine.n1 += (target - engine.n1) * alpha
            engine.n1 = max(0.0, min(1.05, engine.n1))

            want_ab = (
                afterburner
                and self.has_afterburner
                and engine.running
                and throttle > 0.85
            )
            engine.afterburner = want_ab

            if engine.failed:
                # A failed engine windmills down but produces no net thrust and
                # burns no fuel. Letting the decaying spool feed the thrust map
                # leaves it pushing for the rest of the flight -- only a newton
                # or so, which is exactly why it would never be noticed.
                engine.thrust = 0.0
                engine.fuel_flow = 0.0
            else:
                engine.thrust = self._thrust_for(engine.n1, density_ratio, mach, want_ab)

                # Fuel flow proportional to thrust, penalised in afterburner.
                engine.fuel_flow = self.sfc * engine.thrust
                if want_ab:
                    engine.fuel_flow *= self.ab_fuel_factor

            # Indicative EGT only. Not a temperature prediction.
            engine.egt = 300.0 + 620.0 * engine.n1 + (180.0 if want_ab else 0.0)

            thrust_vector = np.array(
                [
                    engine.thrust * math.cos(self.thrust_angle),
                    0.0,
                    -engine.thrust * math.sin(self.thrust_angle),
                ]
            )
            force += thrust_vector
            moment += cross3(position, thrust_vector)

            total_thrust += engine.thrust
            total_flow += engine.fuel_flow

        return PropulsionOutput(
            force_body=force,
            moment_body=moment,
            total_thrust=total_thrust,
            total_fuel_flow=total_flow,
        )

    @property
    def mean_n1(self) -> float:
        if not self.engines:
            return 0.0
        return sum(engine.n1 for engine in self.engines) / len(self.engines)

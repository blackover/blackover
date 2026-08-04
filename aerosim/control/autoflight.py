"""Autoflight: the aircraft flies itself.

A phase state machine sitting on top of the autopilot rather than beside it.
Every phase works by moving the *targets* of the same cascade the pilot gets
with keys 1/2/3 -- altitude, heading, vertical speed, airspeed -- so there is
one set of control laws in this simulator, tuned and tested once.

    TAKEOFF -> CLIMB -> CRUISE -> TO_FAF -> APPROACH -> FLARE -> ROLLOUT -> DONE

The alternative, handing each phase its own controller, is what produced a
7 m/s touchdown during development: a separate flare law meant a control
handover at the exact moment the aircraft could least afford one.

Guidance geometry
-----------------
The runway is a threshold point, a heading and an elevation. Everything else
is derived: along-track distance, cross-track error, and the height the
glidepath wants at this range. Nothing here knows about the renderer.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum

from ..core.units import clamp, ft, kt, to_ft, to_kt, wrap_pi
from .autopilot import LateralMode, ThrustMode, VerticalMode


class Phase(Enum):
    OFF = "OFF"
    TAKEOFF = "T/O"
    CLIMB = "CLB"
    CRUISE = "CRZ"
    TO_FAF = "NAV"
    APPROACH = "APP"
    FLARE = "FLARE"
    ROLLOUT = "ROLL"
    GO_AROUND = "G/A"
    DONE = "DONE"


@dataclass
class Runway:
    """Guidance geometry only -- no geometry the renderer needs."""

    north: float = 0.0
    east: float = 0.0
    heading: float = 0.0  # rad true
    elevation: float = 0.0  # m
    length: float = 3200.0
    glidepath: float = math.radians(3.0)

    def track(self, position) -> tuple[float, float]:
        """Along-track and cross-track position relative to the threshold.

        ``along`` is negative before the threshold and positive past it.
        ``cross`` is positive when the aircraft is right of the centreline.
        """
        dn = float(position[0]) - self.north
        de = float(position[1]) - self.east
        c, s = math.cos(self.heading), math.sin(self.heading)
        return dn * c + de * s, -dn * s + de * c

    def glidepath_altitude(self, along: float) -> float:
        """Height the glidepath wants at this along-track distance."""
        distance = max(0.0, -along)
        return self.elevation + distance * math.tan(self.glidepath)


@dataclass
class AutolandConfig:
    """Per-aircraft autoland numbers, read from the data package."""

    vref_factor: float = 1.3
    flare_height: float = 40.0
    flare_exponent: float = 1.3
    flare_base_rate: float = 0.35  # m/s still descending at touchdown
    flare_entry_rate: float = 3.2  # m/s bled off through the flare
    retard_height: float = 6.0
    # Take-off. Rotation is flown to an ATTITUDE, not by ramping the stick for
    # a fixed time: an open-loop ramp has no idea what attitude it reached, and
    # over-rotates a light aircraft straight into the stall warning.
    rotation_pitch: float = math.radians(12.0)
    climb_out_height: float = 120.0  # m AGL at which the autopilot takes over
    gear_up_height: float = 15.0  # m AGL
    gear_range: float = 9000.0  # m before threshold
    flap_half_range: float = 14000.0
    flap_full_range: float = 8000.0
    faf_range: float = 13000.0
    # Capture is judged as an ANGLE off the centreline, the way a localizer
    # is. An absolute distance accepts a capture two kilometres wide at long
    # range, and the correction that follows arrives as a low-altitude bank.
    capture_angle: float = math.radians(2.5)
    # Below this height the approach must be lined up or it goes around.
    minimums_height: float = 90.0
    minimums_cross: float = 40.0

    @classmethod
    def from_model(cls, model) -> "AutolandConfig":
        if not model.has("autopilot"):
            return cls()

        def get(key: str, default: float) -> float:
            return model.get("autopilot", f"autoland.{key}", default)

        base = cls()
        return cls(
            vref_factor=get("vref_factor", base.vref_factor),
            flare_height=get("flare_height", base.flare_height),
            flare_exponent=get("flare_exponent", base.flare_exponent),
            flare_base_rate=get("flare_base_rate", base.flare_base_rate),
            flare_entry_rate=get("flare_entry_rate", base.flare_entry_rate),
            retard_height=get("retard_height", base.retard_height),
            rotation_pitch=get("rotation_pitch", base.rotation_pitch),
            climb_out_height=get("climb_out_height", base.climb_out_height),
            gear_up_height=get("gear_up_height", base.gear_up_height),
            gear_range=get("gear_range", base.gear_range),
            flap_half_range=get("flap_half_range", base.flap_half_range),
            flap_full_range=get("flap_full_range", base.flap_full_range),
            faf_range=get("faf_range", base.faf_range),
            capture_angle=get("capture_angle", base.capture_angle),
            minimums_height=get("minimums_height", base.minimums_height),
            minimums_cross=get("minimums_cross", base.minimums_cross),
        )


class AutoFlight:
    """Flies the whole thing, or just the landing."""

    def __init__(self, model, runway: Runway) -> None:
        self.model = model
        self.runway = runway
        self.config = AutolandConfig.from_model(model)

        self.phase = Phase.OFF
        self.message = ""
        self.cruise_altitude = ft(10000.0)
        self.cruise_speed = kt(280.0)
        self.landing_armed = False
        self.touchdown_rate = 0.0

        self._vref = kt(140.0)
        self._rotate_speed = kt(150.0)
        self._climb_speed = kt(180.0)
        self._events: list[str] = []

        self.max_bank_approach = model.get(
            "autopilot", "autoland.max_bank", math.radians(20.0)
        ) if model.has("autopilot") else math.radians(20.0)

    # ---------------------------------------------------------------------

    @property
    def engaged(self) -> bool:
        return self.phase not in (Phase.OFF, Phase.DONE)

    def annunciation(self) -> str:
        if self.phase is Phase.OFF:
            return ""
        suffix = " LAND" if self.landing_armed and self.phase in (
            Phase.CLIMB, Phase.CRUISE
        ) else ""
        return f"AUTO {self.phase.value}{suffix}"

    def drain_events(self) -> list[str]:
        events, self._events = self._events, []
        return events

    def _announce(self, message: str) -> None:
        self._events.append(message)

    def _enter(self, phase: Phase, message: str = "") -> None:
        if phase is self.phase:
            return
        self.phase = phase
        if message:
            self._announce(message)

    # -- engagement --------------------------------------------------------

    def engage(self, sim) -> None:
        """Take over from wherever the aircraft currently is."""
        self._refresh_speeds(sim)
        if sim.fdm.state.on_ground:
            self.cruise_altitude = max(
                self.cruise_altitude, self.runway.elevation + ft(5000.0)
            )
            self._enter(Phase.TAKEOFF, "AUTO: take-off, full thrust")
        else:
            self.cruise_altitude = sim.fdm.state.derived.altitude
            self.cruise_speed = sim.fdm.state.derived.vcas
            self._enter(Phase.CRUISE, "AUTO: holding present altitude and heading")
            self._engage_cruise(sim)

    def arm_landing(self, sim) -> None:
        """Fly to the runway and land, from wherever the aircraft is."""
        self._refresh_speeds(sim)
        self.landing_armed = True
        if sim.fdm.state.on_ground:
            self._announce("AUTOLAND: on the ground, nothing to land")
            self.landing_armed = False
            return
        if self.phase in (Phase.OFF, Phase.CRUISE, Phase.CLIMB):
            self._enter(Phase.TO_FAF, "AUTOLAND armed: routing to final approach")
        self._engage_cruise(sim)

    def disengage(self, sim=None) -> None:
        if sim is not None and self.engaged:
            sim.handover_trim()
            sim.autopilot.disengage()
        self.phase = Phase.OFF
        self.landing_armed = False

    # -- helpers -----------------------------------------------------------

    def _refresh_speeds(self, sim) -> None:
        aero = sim.fdm.aero
        mass = sim.fdm.mass_properties.mass
        density = sim.fdm.atmosphere.sample(self.runway.elevation).density
        self._vref = self.config.vref_factor * aero.stall_speed(mass, density, flap=1.0)
        self._rotate_speed = 1.16 * aero.stall_speed(mass, density, flap=0.5)
        self._climb_speed = max(self._vref * 1.25, 1.4 * aero.stall_speed(mass, density))

    def _engage_cruise(self, sim) -> None:
        sim.autopilot.hold_altitude(self.cruise_altitude)
        sim.autopilot.hold_heading(sim.fdm.state.derived.yaw)
        sim.autopilot.hold_speed(self.cruise_speed)

    @staticmethod
    def _hands_off(sim) -> None:
        """The autopilot only holds an axis while the stick is centred."""
        sim.pilot.pitch = 0.0
        sim.pilot.roll = 0.0
        sim.pilot.yaw = 0.0

    def _steer_to_heading(self, sim, heading: float) -> None:
        if sim.autopilot.lateral is not LateralMode.HEADING:
            sim.autopilot.hold_heading(heading)
        else:
            sim.autopilot.targets.heading = wrap_pi(heading)

    # -- the step ----------------------------------------------------------

    def update(self, sim, dt: float) -> None:
        """Drive one step of whichever phase is active."""
        if not self.engaged:
            return

        # The pilot always wins. Take-off and rollout are excluded because the
        # autoflight is itself commanding the stick in those phases, and would
        # otherwise disconnect on its own input.
        if self.phase not in (Phase.TAKEOFF, Phase.ROLLOUT):
            if max(abs(sim.pilot.pitch), abs(sim.pilot.roll)) > 0.15:
                self._announce("AUTO: disconnected by pilot input")
                self.disengage(sim)
                return

        derived = sim.fdm.state.derived
        along, cross = self.runway.track(sim.fdm.state.position)

        handler = {
            Phase.TAKEOFF: self._takeoff,
            Phase.CLIMB: self._climb,
            Phase.CRUISE: self._cruise,
            Phase.TO_FAF: self._to_faf,
            Phase.APPROACH: self._approach,
            Phase.FLARE: self._flare,
            Phase.ROLLOUT: self._rollout,
            Phase.GO_AROUND: self._go_around,
        }[self.phase]
        handler(sim, dt, derived, along, cross)

    # -- phases ------------------------------------------------------------

    def _takeoff(self, sim, dt, derived, along, cross) -> None:
        sim.throttle = 1.0
        sim.pilot.brake = 0.0
        config = self.config
        agl = derived.altitude_agl - sim.fdm.ground_clearance()

        # Rotation and initial climb are hand-flown to an attitude: the
        # autopilot has no ground mode, and its pitch loop has no authority to
        # lift a nosewheel off. Proportional on attitude error with rate
        # damping, never pushing -- the same structure as everywhere else.
        def hold_attitude(target: float, floor: float = 0.0) -> None:
            error = target - derived.pitch
            sim.pilot.pitch = clamp(1.8 * error - 0.8 * derived.q, floor, 0.7)

        if sim.fdm.state.on_ground:
            # Keep the centreline with the nosewheel, not with the ailerons.
            sim.pilot.yaw = clamp(-0.02 * cross - 1.2 * derived.r, -0.6, 0.6)
            sim.pilot.roll = 0.0
            if derived.vcas < self._rotate_speed:
                sim.pilot.pitch = 0.0
            else:
                hold_attitude(config.rotation_pitch)
            return

        if agl > config.gear_up_height and sim.pilot.gear_down:
            sim.pilot.gear_down = False
            self._announce("AUTO: positive rate, gear up")

        if agl < config.climb_out_height:
            # Wings level on the initial climb, still hand-flown. Airborne the
            # law is allowed to push a little, so an overshoot past the target
            # attitude is checked rather than left to the airframe's own
            # damping -- which on a light aircraft at full thrust means
            # arriving at the autopilot handover ten degrees nose-high.
            hold_attitude(config.rotation_pitch, floor=-0.30)
            sim.pilot.roll = clamp(-1.6 * derived.roll - 0.4 * derived.p, -0.4, 0.4)
            sim.pilot.yaw = clamp(-2.0 * derived.beta, -0.4, 0.4)
            return

        self._hands_off(sim)
        sim.autopilot.hold_speed(self._climb_speed)
        sim.autopilot.hold_altitude(self.cruise_altitude)
        self._steer_to_heading(sim, self.runway.heading)
        self._enter(Phase.CLIMB, f"AUTO: climbing to {to_ft(self.cruise_altitude):,.0f} ft")

    def _climb(self, sim, dt, derived, along, cross) -> None:
        self._hands_off(sim)
        if derived.altitude_agl > ft(800.0) and sim.pilot.flap > 0.0:
            sim.pilot.flap = 0.0
            self._announce("AUTO: flaps up")

        sim.autopilot.targets.altitude = self.cruise_altitude
        sim.autopilot.targets.airspeed = self._climb_speed

        if abs(derived.altitude - self.cruise_altitude) < ft(250.0):
            sim.autopilot.targets.airspeed = self.cruise_speed
            if self.landing_armed:
                self._enter(Phase.TO_FAF, "AUTO: routing to final approach")
            else:
                self._enter(Phase.CRUISE, "AUTO: level at cruise")

    def _cruise(self, sim, dt, derived, along, cross) -> None:
        self._hands_off(sim)
        sim.autopilot.targets.altitude = self.cruise_altitude
        sim.autopilot.targets.airspeed = self.cruise_speed
        if self.landing_armed:
            self._enter(Phase.TO_FAF, "AUTO: routing to final approach")

    def _to_faf(self, sim, dt, derived, along, cross) -> None:
        """Fly to the final approach fix and line up on the extended centreline."""
        self._hands_off(sim)
        config = self.config

        # The fix sits on the centreline. When the aircraft is a long way off
        # to one side, aim further out so there is room to converge before the
        # threshold: arriving abeam the fix still pointing across it leaves
        # only a hard turn onto final, and a hard turn onto final finishes at
        # low altitude with a wing down.
        faf_along = -(config.faf_range + clamp(abs(cross) * 3.0, 0.0, 20000.0))
        faf_north = self.runway.north + faf_along * math.cos(self.runway.heading)
        faf_east = self.runway.east + faf_along * math.sin(self.runway.heading)

        dn = faf_north - float(sim.fdm.state.position[0])
        de = faf_east - float(sim.fdm.state.position[1])
        bearing = math.atan2(de, dn)

        faf_altitude = self.runway.glidepath_altitude(faf_along)
        sim.autopilot.targets.altitude = faf_altitude
        sim.autopilot.targets.airspeed = max(self._vref * 1.3, self.cruise_speed * 0.75)
        self._steer_to_heading(sim, bearing)

        aligned = abs(wrap_pi(derived.yaw - self.runway.heading)) < math.radians(25.0)
        inbound = along < -config.faf_range * 0.45
        angular = abs(math.atan2(cross, max(-along, 500.0)))
        established = angular < config.capture_angle and aligned and inbound

        if established:
            sim.autopilot.targets.bank_limit = self.max_bank_approach
            self._enter(
                Phase.APPROACH,
                f"AUTOLAND: established, Vref {to_kt(self._vref):.0f} kt",
            )

    def _approach(self, sim, dt, derived, along, cross) -> None:
        """Track the localizer and the glidepath down to the flare."""
        self._hands_off(sim)
        config = self.config
        agl = derived.altitude_agl
        distance = max(0.0, -along)

        if along > 300.0:
            self._enter(Phase.GO_AROUND, "AUTOLAND: past the threshold, going around")
            return

        # Not lined up at minimums: go around rather than bank it onto the
        # runway from the side. This is the check that turns a bad approach
        # into another circuit instead of a wing strike.
        if agl < config.minimums_height and abs(cross) > config.minimums_cross:
            self._enter(
                Phase.GO_AROUND,
                f"AUTOLAND: not established at minimums, {abs(cross):.0f} m off "
                "the centreline -- going around",
            )
            return

        # Lateral: pursuit guidance onto the centreline, with the bank limit
        # closing down near the ground so a late correction cannot put a
        # wingtip into it.
        lookahead = clamp(distance * 0.45, 400.0, 2500.0)
        intercept = clamp(
            math.atan2(-cross, lookahead), -math.radians(30.0), math.radians(30.0)
        )
        self._steer_to_heading(sim, self.runway.heading + intercept)
        sim.autopilot.targets.bank_limit = min(
            self.max_bank_approach,
            math.radians(4.0 + 21.0 * clamp(agl / 200.0, 0.0, 1.0)),
        )

        # Configuration schedule.
        if distance < config.flap_half_range and sim.pilot.flap < 0.5:
            sim.pilot.flap = 0.5
            self._announce("AUTOLAND: flaps half")
        if distance < config.gear_range and not sim.pilot.gear_down:
            sim.pilot.gear_down = True
            self._announce("AUTOLAND: gear down")
        if distance < config.flap_full_range and sim.pilot.flap < 1.0:
            sim.pilot.flap = 1.0
            self._announce("AUTOLAND: flaps full")

        # Vertical: fly the glidepath's own descent rate, corrected toward the
        # path. A pure altitude hold would chase the path with a lag that grows
        # as the range closes.
        target_altitude = self.runway.glidepath_altitude(along)
        path_error = target_altitude - derived.altitude
        nominal = -derived.vtas * math.sin(self.runway.glidepath)
        sim.autopilot.targets.vertical_speed = clamp(
            nominal + 0.25 * path_error, -12.0, 4.0
        )
        if sim.autopilot.vertical is not VerticalMode.VERTICAL_SPEED:
            sim.autopilot.hold_vertical_speed(sim.autopilot.targets.vertical_speed)

        sim.autopilot.targets.airspeed = self._vref
        if sim.autopilot.thrust is not ThrustMode.SPEED:
            sim.autopilot.hold_speed(self._vref)

        if agl <= config.flare_height:
            self._enter(Phase.FLARE, "AUTOLAND: flare")

    def _flare(self, sim, dt, derived, along, cross) -> None:
        self._hands_off(sim)
        config = self.config
        agl = max(0.0, derived.altitude_agl)

        if sim.fdm.state.on_ground:
            self.touchdown_rate = sim.fdm.diagnostics.touchdown_rate
            self._enter(
                Phase.ROLLOUT,
                f"AUTOLAND: touchdown {self.touchdown_rate:.1f} m/s "
                f"({self.touchdown_rate * 196.85:.0f} fpm)",
            )
            return

        # Bleed the descent along a power curve so the pull stays inside the
        # flaps-extended load limit. Arresting it in a short flare also works,
        # and costs 2.9 g on a 2.0 g placard.
        fraction = (agl / config.flare_height) ** config.flare_exponent
        sim.autopilot.targets.vertical_speed = -(
            config.flare_base_rate + config.flare_entry_rate * fraction
        )
        # Hold the centreline, gently -- there is no room left for a big bank.
        self._steer_to_heading(
            sim,
            self.runway.heading + clamp(
                math.atan2(-cross, 400.0), -math.radians(8.0), math.radians(8.0)
            ),
        )

        if agl < config.retard_height:
            if sim.autopilot.thrust is ThrustMode.SPEED:
                sim.autopilot.thrust = ThrustMode.OFF
                self._announce("AUTOLAND: retard")
            sim.throttle = max(0.0, sim.throttle - 0.5 * dt)

    def _rollout(self, sim, dt, derived, along, cross) -> None:
        if sim.autopilot.engaged:
            sim.handover_trim()
            sim.autopilot.disengage()

        sim.throttle = 0.0
        sim.pilot.brake = 1.0
        sim.pilot.speedbrake = 1.0
        sim.pilot.roll = 0.0
        # Lower the nose, then hold the centreline on the nosewheel.
        sim.pilot.pitch = clamp(sim.pilot.pitch - 0.7 * dt, 0.0, 1.0)
        sim.pilot.yaw = clamp(-0.02 * cross - 1.2 * derived.r, -0.6, 0.6)

        if derived.ground_speed < kt(10.0):
            sim.pilot.brake = 1.0
            sim.pilot.yaw = 0.0
            self.landing_armed = False
            self._enter(Phase.DONE, "AUTOLAND: stopped on the runway")

    def _go_around(self, sim, dt, derived, along, cross) -> None:
        self._hands_off(sim)
        sim.pilot.gear_down = False
        sim.pilot.flap = 0.5
        sim.autopilot.targets.airspeed = self._climb_speed
        sim.autopilot.targets.altitude = self.runway.elevation + ft(3000.0)
        if sim.autopilot.vertical is not VerticalMode.ALTITUDE:
            sim.autopilot.hold_altitude(sim.autopilot.targets.altitude)
        self._steer_to_heading(sim, self.runway.heading)

        if derived.altitude_agl > ft(2500.0):
            self._enter(Phase.TO_FAF, "AUTO: re-routing to final approach")

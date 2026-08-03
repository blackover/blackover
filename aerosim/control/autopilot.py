"""Autopilot: mode state machine and cascaded control laws.

Each axis is a cascade of simple loops rather than one clever one, because a
cascade can be tuned, tested and explained a loop at a time:

    altitude error -> vertical speed target -> pitch target -> elevator
    heading error  -> bank target           -> roll rate    -> aileron
    speed error    -> throttle

Every inner target is limited to something the aircraft can actually do, so a
2000 ft altitude error asks for a 15 degree pitch rather than a 90 degree one.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum

from ..core.units import clamp, wrap_pi


class LateralMode(Enum):
    OFF = "OFF"
    ROLL_HOLD = "ROLL"
    HEADING = "HDG"


class VerticalMode(Enum):
    OFF = "OFF"
    PITCH_HOLD = "PTCH"
    ALTITUDE = "ALT"
    VERTICAL_SPEED = "V/S"


class ThrustMode(Enum):
    OFF = "OFF"
    SPEED = "SPD"


@dataclass
class PID:
    """A PID with an integrator that stops winding when the output saturates.

    Anti-windup by conditional integration: if the output is already on a limit
    and the error would push it further into that limit, the integrator is not
    advanced. Without it, a long climb at a pitch limit leaves an integrator
    charged with several seconds of error that has to unwind before the
    aircraft will respond at all.
    """

    kp: float = 1.0
    ki: float = 0.0
    kd: float = 0.0
    output_min: float = -1.0
    output_max: float = 1.0
    integral: float = 0.0
    previous_error: float = 0.0
    initialised: bool = False

    def reset(self) -> None:
        self.integral = 0.0
        self.previous_error = 0.0
        self.initialised = False

    def update(self, error: float, dt: float, derivative: float | None = None) -> float:
        if not self.initialised:
            self.previous_error = error
            self.initialised = True

        if derivative is None:
            derivative = (error - self.previous_error) / dt if dt > 0.0 else 0.0
        self.previous_error = error

        unclamped = self.kp * error + self.ki * self.integral + self.kd * derivative
        output = clamp(unclamped, self.output_min, self.output_max)

        saturated_high = unclamped > self.output_max and error > 0.0
        saturated_low = unclamped < self.output_min and error < 0.0
        if not (saturated_high or saturated_low):
            self.integral += error * dt

        return output


@dataclass
class AutopilotTargets:
    altitude: float = 0.0  # m
    heading: float = 0.0  # rad
    airspeed: float = 0.0  # m/s CAS
    vertical_speed: float = 0.0  # m/s
    bank_limit: float = math.radians(25.0)


@dataclass
class AutopilotOutput:
    elevator: float = 0.0
    aileron: float = 0.0
    rudder: float = 0.0
    throttle: float = 0.0
    engaged: bool = False
    annunciation: list[str] = field(default_factory=list)


class Autopilot:
    """Three-axis autopilot plus autothrottle."""

    def __init__(self, model) -> None:
        gains = model.raw("autopilot", "gains", {}) if model.has("autopilot") else {}

        def gain(name: str, default: float) -> float:
            value = gains.get(name, default)
            return float(value)

        self.max_bank = model.get("autopilot", "max_bank", math.radians(25.0)) if model.has(
            "autopilot"
        ) else math.radians(25.0)
        self.max_pitch = model.get("autopilot", "max_pitch", math.radians(15.0)) if model.has(
            "autopilot"
        ) else math.radians(15.0)
        self.max_vertical_speed = (
            model.get("autopilot", "max_vertical_speed", 12.7)
            if model.has("autopilot")
            else 12.7
        )

        # altitude (m) -> vertical speed (m/s)
        self.altitude_loop = PID(
            kp=gain("altitude_kp", 0.05),
            ki=gain("altitude_ki", 0.0),
            output_min=-self.max_vertical_speed,
            output_max=self.max_vertical_speed,
        )
        # Vertical speed error -> a *correction* to the feed-forward pitch, not
        # the whole of it. The attitude that flies a given vertical speed is
        # gamma + alpha, and gamma is asin(vs/V) -- a known quantity, not
        # something a proportional gain should be asked to discover. Driving
        # pitch from the error alone needs a gain of roughly 1/V, and a gain
        # tuned at one airspeed saturates at another: the loop then commands
        # maximum pitch, the aircraft stalls, and the autopilot holds it there
        # all the way down.
        self.vs_loop = PID(
            kp=gain("vs_kp", 0.004),
            ki=gain("vs_ki", 0.002),
            output_min=-math.radians(6.0),
            output_max=math.radians(6.0),
        )
        # pitch (rad) -> elevator
        self.pitch_loop = PID(
            kp=gain("pitch_kp", 3.2),
            ki=gain("pitch_ki", 0.8),
            kd=gain("pitch_kd", 0.6),
            output_min=-1.0,
            output_max=1.0,
        )
        # heading (rad) -> bank (rad)
        self.heading_loop = PID(
            kp=gain("heading_kp", 1.4),
            output_min=-self.max_bank,
            output_max=self.max_bank,
        )
        # bank (rad) -> aileron
        self.roll_loop = PID(
            kp=gain("roll_kp", 2.2),
            ki=gain("roll_ki", 0.15),
            kd=gain("roll_kd", 0.35),
            output_min=-1.0,
            output_max=1.0,
        )
        # speed (m/s) -> throttle
        self.speed_loop = PID(
            kp=gain("speed_kp", 0.055),
            ki=gain("speed_ki", 0.012),
            output_min=0.0,
            output_max=1.0,
        )
        # sideslip -> rudder, the yaw damper
        self.yaw_damper_gain = gain("yaw_damper", 2.5)

        self.lateral = LateralMode.OFF
        self.vertical = VerticalMode.OFF
        self.thrust = ThrustMode.OFF
        self.targets = AutopilotTargets()

        # Slowly filtered angle of attack, used by the vertical feed-forward.
        # See the comment where it is applied for why it must not be the
        # instantaneous value.
        self._alpha_reference: float | None = None
        self.alpha_filter_tau = 8.0

        # Rate limit on the commanded attitude, not just on its magnitude.
        self._pitch_command: float | None = None
        self.pitch_command_rate = model.get(
            "autopilot", "pitch_command_rate", math.radians(4.0)
        ) if model.has("autopilot") else math.radians(4.0)

    # ---------------------------------------------------------------------

    @property
    def engaged(self) -> bool:
        return (
            self.lateral is not LateralMode.OFF
            or self.vertical is not VerticalMode.OFF
            or self.thrust is not ThrustMode.OFF
        )

    def disengage(self) -> None:
        self.lateral = LateralMode.OFF
        self.vertical = VerticalMode.OFF
        self.thrust = ThrustMode.OFF
        self.reset_loops()

    def reset_loops(self) -> None:
        for loop in (
            self.altitude_loop,
            self.vs_loop,
            self.pitch_loop,
            self.heading_loop,
            self.roll_loop,
            self.speed_loop,
        ):
            loop.reset()
        self._alpha_reference = None
        self._pitch_command = None

    def annunciation(self) -> str:
        if not self.engaged:
            return "AP OFF"
        parts = []
        if self.lateral is not LateralMode.OFF:
            parts.append(self.lateral.value)
        if self.vertical is not VerticalMode.OFF:
            parts.append(self.vertical.value)
        if self.thrust is not ThrustMode.OFF:
            parts.append(self.thrust.value)
        return " ".join(parts)

    # ---------------------------------------------------------------------

    def update(self, dt: float, derived, current_throttle: float) -> AutopilotOutput:
        """Run the engaged loops and return the commands they produce."""
        output = AutopilotOutput(engaged=self.engaged, throttle=current_throttle)
        if not self.engaged or dt <= 0.0:
            return output

        # -- lateral -------------------------------------------------------
        bank_target = 0.0
        if self.lateral is LateralMode.HEADING:
            heading_error = wrap_pi(self.targets.heading - derived.yaw)
            bank_target = self.heading_loop.update(heading_error, dt)
            bank_target = clamp(
                bank_target, -self.targets.bank_limit, self.targets.bank_limit
            )
        elif self.lateral is LateralMode.ROLL_HOLD:
            bank_target = 0.0

        if self.lateral is not LateralMode.OFF:
            roll_error = bank_target - derived.roll
            # Feed the measured roll rate in as the derivative term instead of
            # differencing the error: differentiating a noisy error signal
            # amplifies exactly the frequencies the loop should be damping.
            # The target moves slowly, so d(error)/dt is -p.
            output.aileron = self.roll_loop.update(roll_error, dt, derivative=-derived.p)
            output.aileron = clamp(output.aileron, -1.0, 1.0)

        # -- vertical ------------------------------------------------------
        if self.vertical is not VerticalMode.OFF:
            if self.vertical is VerticalMode.ALTITUDE:
                altitude_error = self.targets.altitude - derived.altitude
                vs_target = self.altitude_loop.update(altitude_error, dt)
            elif self.vertical is VerticalMode.VERTICAL_SPEED:
                vs_target = clamp(
                    self.targets.vertical_speed,
                    -self.max_vertical_speed,
                    self.max_vertical_speed,
                )
            else:
                vs_target = None

            if vs_target is None:
                pitch_target = derived.pitch
            else:
                # Feed-forward: the attitude that actually flies this vertical
                # speed at this airspeed. The PID only trims the residual.
                #
                # The alpha term is FILTERED, not instantaneous. Since
                # alpha = pitch - gamma, feeding the measured alpha straight
                # back in makes the command chase its own output: pitch up
                # raises alpha, which raises the pitch command, which raises
                # alpha. The loop has no restoring action and ratchets the
                # aircraft up to its pitch limit while barely climbing.
                # Filtering at several seconds leaves the fast dynamics seeing
                # a constant while the slow trim still comes out right.
                if self._alpha_reference is None:
                    self._alpha_reference = derived.alpha
                else:
                    self._alpha_reference += (derived.alpha - self._alpha_reference) * (
                        1.0 - math.exp(-dt / self.alpha_filter_tau)
                    )

                speed = max(derived.vtas, 30.0)
                gamma_target = math.asin(clamp(vs_target / speed, -0.5, 0.5))
                pitch_target = gamma_target + self._alpha_reference

                vs_error = vs_target - derived.vertical_speed
                pitch_target += self.vs_loop.update(vs_error, dt)

                # In a bank, more pitch is needed for the same vertical path.
                bank = abs(derived.roll)
                if bank < math.radians(60.0):
                    pitch_target += (1.0 / max(math.cos(bank), 0.5) - 1.0) * math.radians(
                        3.0
                    )

            pitch_target = clamp(pitch_target, -self.max_pitch, self.max_pitch)

            # Rate-limit the commanded attitude. Engaging a mode with a large
            # altitude error otherwise steps this target, which saturates the
            # inner pitch loop; on a high-authority airframe a saturated pitch
            # loop is a bunt, not a capture, and what follows is a tumble
            # rather than a level-off.
            if self._pitch_command is None:
                self._pitch_command = derived.pitch
            step = self.pitch_command_rate * dt
            self._pitch_command += clamp(
                pitch_target - self._pitch_command, -step, step
            )
            pitch_target = self._pitch_command

            pitch_error = pitch_target - derived.pitch
            # Measured pitch rate as the derivative term, same reasoning as
            # the roll loop.
            output.elevator = -self.pitch_loop.update(
                pitch_error, dt, derivative=-derived.q
            )
            output.elevator = clamp(output.elevator, -1.0, 1.0)

        # -- autothrottle --------------------------------------------------
        if self.thrust is ThrustMode.SPEED:
            speed_error = self.targets.airspeed - derived.vcas
            output.throttle = self.speed_loop.update(speed_error, dt)

        # -- yaw damper ----------------------------------------------------
        # Always active when anything is engaged: it is what stops a swept-wing
        # aircraft wallowing in Dutch roll while the autopilot flies it.
        output.rudder = clamp(-self.yaw_damper_gain * derived.beta, -0.5, 0.5)

        output.engaged = True
        return output

    # -- mode selection ----------------------------------------------------

    def hold_altitude(self, altitude: float) -> None:
        self.targets.altitude = altitude
        self.vertical = VerticalMode.ALTITUDE
        self.altitude_loop.reset()
        self.vs_loop.reset()
        self.pitch_loop.reset()

    def hold_heading(self, heading: float) -> None:
        self.targets.heading = wrap_pi(heading)
        self.lateral = LateralMode.HEADING
        self.heading_loop.reset()
        self.roll_loop.reset()

    def hold_speed(self, vcas: float) -> None:
        self.targets.airspeed = vcas
        self.thrust = ThrustMode.SPEED
        self.speed_loop.reset()

    def hold_vertical_speed(self, vs: float) -> None:
        self.targets.vertical_speed = vs
        self.vertical = VerticalMode.VERTICAL_SPEED
        self.vs_loop.reset()
        self.pitch_loop.reset()

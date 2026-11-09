"""Stability augmentation: the yaw damper.

A swept-wing jet transport has a badly damped dutch roll and there is no
sensible amount of fin that fixes it. The AeroLiner-200's is a worked example:
its fin was undersized at a volume coefficient of 0.0765, and correcting that
to 0.089 -- inside the band a narrow-body is usually drawn with -- moved the
damping from 0.05 to 0.065 against a Level 1 target of 0.08. Getting the rest
from the airframe would mean a fin far outside the band, carried as drag and
weight for the whole life of the aeroplane, to fix an oscillation a small box
can damp electrically. That is the trade every swept-wing jet since the 707
has made, and the box is the yaw damper.

What makes it a damper rather than a rudder autopilot is the **washout**. A
plain yaw-rate feedback opposes *all* yaw rate, including the rate the pilot
asked for in a turn -- it fights the turn, holds the aircraft out of balance,
and the crew flies against it. A washout filter is a high-pass: it passes the
oscillation and blocks the steady rate, so the damper is silent in a turn and
present for the dutch roll.

The authority is deliberately small -- a few degrees of rudder. A damper with
full rudder authority is a system whose failure case is a hardover, and the
cure for that is to make the hardover survivable rather than to detect it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class YawDamperState:
    """What the damper is doing, for the panel and the telemetry."""

    engaged: bool = False
    #: Rudder command it is contributing, in normalised deflection.
    command: float = 0.0
    #: The steady yaw rate the washout has learned, rad/s.
    washed_out: float = 0.0
    #: True when the command is against its authority limit.
    saturated: bool = False


class YawDamper:
    """Washed-out yaw-rate feedback to the rudder.

    The washout is a first-order high pass with time constant ``washout``:

        y_dot = (r - y) / washout        y tracks the steady rate
        transient = r - y                 what is left is the oscillation
        rudder = gain * transient         clamped to the authority limit

    At the dutch-roll frequency the washout is essentially transparent. For
    this aircraft the mode's period is about 5.5 s, so omega is 1.14 rad/s,
    and with washout = 4 s the high pass passes |j*omega*tau / (1 + j*omega*tau)|
    = 0.98 of the signal. At the rate of a steady turn -- a few degrees per
    second held for tens of seconds -- it passes almost none.
    """

    def __init__(self, model) -> None:
        self.available = bool(
            model.raw("flight_controls", "yaw_damper.available", False)
        )
        self.gain = model.get("flight_controls", "yaw_damper.gain", 0.0)
        self.washout = max(
            0.1, model.get("flight_controls", "yaw_damper.washout", 4.0)
        )
        self.authority = model.get("flight_controls", "yaw_damper.authority", 0.20)

        self.engaged = self.available
        self.failed = False
        self._washed_out = 0.0
        self._command = 0.0
        self._saturated = False

    # ---------------------------------------------------------------------

    @property
    def active(self) -> bool:
        return self.available and self.engaged and not self.failed

    def reset(self) -> None:
        self._washed_out = 0.0
        self._command = 0.0
        self._saturated = False
        self.engaged = self.available
        self.failed = False

    def update(self, dt: float, yaw_rate: float) -> float:
        """Advance the washout and return the rudder command.

        Sign: a positive yaw rate is nose right, and the damper answers it
        with the rudder that opposes it. Getting a damper's sign backwards
        does not produce a weak damper, it produces positive feedback.
        """
        if not self.active:
            # Bleed the washout state back to zero rather than freezing it,
            # so re-engaging mid-turn does not kick the rudder with a stale
            # estimate of a yaw rate that has since changed.
            self._washed_out *= math.exp(-dt / self.washout)
            self._command = 0.0
            self._saturated = False
            return 0.0

        # Exact discretisation of the first-order lag: stable at any step.
        blend = math.exp(-dt / self.washout)
        self._washed_out = blend * self._washed_out + (1.0 - blend) * yaw_rate

        transient = yaw_rate - self._washed_out
        raw = self.gain * transient
        self._command = max(-self.authority, min(self.authority, raw))
        self._saturated = abs(raw) > self.authority + 1.0e-9
        return self._command

    def state(self) -> YawDamperState:
        return YawDamperState(
            engaged=self.active,
            command=self._command,
            washed_out=self._washed_out,
            saturated=self._saturated,
        )

    # -- for the analysis tools -------------------------------------------

    def equivalent_gain(self, frequency: float) -> float:
        """The gain the damper presents at one frequency, rad of rudder per rad/s.

        The linearised mode analysis needs a single number rather than a
        filter. The washout's magnitude response is |j w tau| / |1 + j w tau|,
        so at the dutch-roll frequency this is within a couple of per cent of
        the full gain, and near zero at the rate of a steady turn.
        """
        if not self.active:
            return 0.0
        wt = frequency * self.washout
        return self.gain * wt / math.sqrt(1.0 + wt * wt)

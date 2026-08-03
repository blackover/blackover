"""Deterministic fixed-step simulation clock.

Simulation time is ``step_index * dt``, never an accumulated sum. Accumulating
0.01 s a hundred and twenty thousand times lands on 1200.0000000000073 s; the
multiplication lands on 1200.0. That difference is the whole reason two runs
of the same scenario can be compared at all.
"""

from __future__ import annotations

from dataclasses import dataclass, field


VALIDATED_DT = 0.01
DT_WARNING_THRESHOLD = 0.02


@dataclass
class Clock:
    """Fixed-step clock driving the simulation kernel."""

    dt: float = VALIDATED_DT
    step_index: int = 0
    warnings: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.dt <= 0.0:
            raise ValueError(f"dt must be positive, got {self.dt}")
        if self.dt > DT_WARNING_THRESHOLD:
            self.warnings.append(
                f"dt = {self.dt:.4f} s exceeds the validated step of "
                f"{VALIDATED_DT} s; integration accuracy is not characterised "
                f"above {DT_WARNING_THRESHOLD} s"
            )

    @property
    def time(self) -> float:
        """Current simulation time in seconds."""
        return self.step_index * self.dt

    @property
    def rate_hz(self) -> float:
        return 1.0 / self.dt

    def advance(self) -> float:
        """Advance one step and return the new simulation time."""
        self.step_index += 1
        return self.time

    def reset(self) -> None:
        self.step_index = 0

    def steps_for(self, duration: float) -> int:
        """Number of whole steps spanning ``duration`` seconds."""
        if duration < 0.0:
            raise ValueError("duration must not be negative")
        return int(round(duration / self.dt))


class FrameAccumulator:
    """Decouples a variable render rate from the fixed physics rate.

    A game renders whenever the machine can; the flight model must step at
    exactly ``dt`` or it is not the same flight model. This takes real elapsed
    wall time and hands back how many fixed steps are owed, with a ceiling so
    that a long stall (a window drag, a breakpoint) does not produce a
    thousand-step catch-up burst that flies the aircraft into the ground.
    """

    def __init__(self, dt: float, max_steps_per_frame: int = 8) -> None:
        self.dt = dt
        self.max_steps_per_frame = max_steps_per_frame
        self._pending = 0.0
        self.dropped_steps = 0

    def add(self, wall_dt: float) -> int:
        """Feed real elapsed seconds, return how many fixed steps to run."""
        self._pending += max(0.0, wall_dt)
        steps = int(self._pending / self.dt)
        if steps > self.max_steps_per_frame:
            self.dropped_steps += steps - self.max_steps_per_frame
            steps = self.max_steps_per_frame
            self._pending = 0.0
        else:
            self._pending -= steps * self.dt
        return steps

    @property
    def alpha(self) -> float:
        """Fraction of a step remaining, for render-side interpolation."""
        return self._pending / self.dt

    def reset(self) -> None:
        self._pending = 0.0

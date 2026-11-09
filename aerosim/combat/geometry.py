"""The geometry two aircraft are in, in the terms air combat is taught in.

Basic fighter manoeuvres are not reasoned about in positions and velocities.
They are reasoned about in five or six derived quantities, and every decision
a fighter pilot makes in a turning fight is a function of them:

**Range** -- how far apart. Sets which manoeuvres are even available: you
cannot overshoot something two miles away.

**Closure** -- range rate, negative when closing. The number that decides
whether an attack ends in a gun solution or in flying out in front of the
other aircraft.

**Angle off tail (AOT)** -- the angle between the defender's tail and the
line to the attacker. Zero is directly behind. This is *the* measure of who
is winning: an attacker's whole job is to drive it to zero and keep it there.

**Aspect angle** -- the same idea from the other side: the angle between the
defender's tail and the line of sight, measured at the defender. Low aspect
means you are looking at a tailpipe.

**Antenna train angle (ATA)** -- how far off your own nose the other aircraft
is. Zero means you are pointing at it. The difference between ATA and AOT is
what separates the three pursuit curves.

**Specific energy** -- altitude plus kinetic energy expressed as a height,
``h + V^2/2g``. Energy is the currency of a dogfight: you spend it to turn
and you climb to store it, and the pilot with more of it chooses when to
fight. Comparing two aircraft's specific energy says who can afford what.

Everything here is a pure function of two states. Nothing in this module
moves an aircraft.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from ..core.units import G0


@dataclass(frozen=True)
class Geometry:
    """The relative picture between two aircraft, from one side's point of view.

    "Own" is the aircraft this geometry is computed *for*; "target" is the
    other one. Every angle is in radians and every distance in metres,
    because this is the simulator's interior and the interior is SI.
    """

    #: Distance between the two, metres.
    range: float
    #: Rate of change of range, m/s. Negative is closing.
    closure: float
    #: Angle between own nose and the line of sight to the target, rad.
    #: Zero means pointing straight at it.
    antenna_train: float
    #: Angle between the target's tail and the line of sight, rad. Zero means
    #: directly behind the target.
    angle_off_tail: float
    #: Angle at the target between its own nose and the line of sight back
    #: to you, rad. ZERO means it is looking straight at you, and 180 deg
    #: means you are looking at its tailpipe. It is the supplement of
    #: ``angle_off_tail``, which is measured from the same point but off the
    #: target's tail instead of its nose.
    aspect: float
    #: Rate at which the line of sight is rotating in space, rad/s. A high
    #: line-of-sight rate at short range is what an overshoot looks like
    #: before it happens.
    line_of_sight_rate: float
    #: Own specific energy, metres. Altitude plus V^2/2g.
    own_energy: float
    #: The target's specific energy, metres.
    target_energy: float
    #: Own altitude minus the target's, metres. Positive is above.
    height_advantage: float
    #: Unit vector from own aircraft to the target, NED.
    line_of_sight: np.ndarray

    # -- derived judgements ------------------------------------------------

    @property
    def energy_advantage(self) -> float:
        """Own specific energy minus the target's, metres.

        Positive means you can afford a manoeuvre the other aircraft cannot.
        """
        return self.own_energy - self.target_energy

    @property
    def closing(self) -> bool:
        return self.closure < 0.0

    @property
    def offensive(self) -> bool:
        """Behind the target and pointing at it, at a range where that matters.

        The three conditions together, because any one of them alone is a
        different situation: pointing at something from in front of it is a
        head-on pass, and sitting behind something at ten miles is not an
        attack.
        """
        return (
            self.angle_off_tail < math.radians(60.0)
            and self.antenna_train < math.radians(45.0)
            and self.range < 4000.0
        )

    @property
    def defensive(self) -> bool:
        """The other aircraft is behind you and pointing at you.

        "Pointing at you" is a SMALL aspect angle, because aspect is measured
        off the target's nose: zero means its nose is on you. Reading it the
        other way round -- testing for a large aspect, as though it were the
        angle off its tail -- produces a condition that cannot be satisfied
        by anything, and an adversary that never notices it is being
        attacked and so never breaks.
        """
        return (
            self.aspect < math.radians(60.0)
            and abs(self.antenna_train) > math.radians(90.0)
            and self.range < 4000.0
        )

    def describe(self) -> str:
        return (
            f"range {self.range:5.0f} m  closure {self.closure:+6.0f} m/s  "
            f"AOT {math.degrees(self.angle_off_tail):5.1f} deg  "
            f"ATA {math.degrees(self.antenna_train):5.1f} deg  "
            f"dE {self.energy_advantage:+6.0f} m"
        )


def _nose(state) -> np.ndarray:
    """Unit vector along the body x axis, in NED."""
    from ..core.frames import dcm_body_to_ned

    return dcm_body_to_ned(state.quaternion) @ np.array([1.0, 0.0, 0.0])


def specific_energy(state) -> float:
    """Altitude plus kinetic energy as a height, metres.

    The quantity a fighter pilot calls "energy": it is conserved in a
    zoom-and-dive, spent by turning, and bought back with thrust. Expressed
    as a height so it can be compared directly with the other aircraft's and
    with the ground.
    """
    speed = float(np.linalg.norm(state.velocity_ned))
    return state.altitude + speed * speed / (2.0 * G0)


def observe(own, target, previous: Geometry | None = None, dt: float = 0.0) -> Geometry:
    """Compute the BFM geometry between two states.

    ``previous`` and ``dt`` are only needed for the line-of-sight rate, which
    is a derivative and therefore needs two samples. Without them it is
    computed from the velocities directly, which is exact rather than
    differenced and is what this does by default.
    """
    separation = target.position - own.position
    distance = float(np.linalg.norm(separation))
    if distance < 1.0e-6:
        line_of_sight = np.array([1.0, 0.0, 0.0])
        distance = 1.0e-6
    else:
        line_of_sight = separation / distance

    relative_velocity = target.velocity_ned - own.velocity_ned
    # Range rate is the component of relative velocity along the line of
    # sight; what is left over is what rotates the line of sight.
    closure = float(relative_velocity @ line_of_sight)
    perpendicular = relative_velocity - closure * line_of_sight
    los_rate = float(np.linalg.norm(perpendicular)) / distance

    own_nose = _nose(own)
    target_nose = _nose(target)

    # Antenna train angle: how far the target is off my nose.
    antenna_train = _angle_between(own_nose, line_of_sight)
    # Aspect: at the target, between its nose and the line back to me.
    aspect = _angle_between(target_nose, -line_of_sight)
    # Angle off tail is measured from the target's tail, so it is the
    # supplement of the aspect angle: 0 deg AOT is dead astern.
    angle_off_tail = math.pi - aspect

    return Geometry(
        range=distance,
        closure=closure,
        antenna_train=antenna_train,
        angle_off_tail=angle_off_tail,
        aspect=aspect,
        line_of_sight_rate=los_rate,
        own_energy=specific_energy(own),
        target_energy=specific_energy(target),
        height_advantage=own.altitude - target.altitude,
        line_of_sight=line_of_sight,
    )


def _angle_between(a: np.ndarray, b: np.ndarray) -> float:
    """Unsigned angle between two vectors, 0 to pi, numerically safe."""
    cosine = float(a @ b) / max(
        1.0e-12, float(np.linalg.norm(a)) * float(np.linalg.norm(b))
    )
    return math.acos(max(-1.0, min(1.0, cosine)))


# --------------------------------------------------------------------------
# Turn performance: what the aircraft can do about the geometry
# --------------------------------------------------------------------------


def corner_speed(aero, mass: float, density: float, load_limit: float) -> float:
    """The speed where the aircraft turns best, in TAS.

    The slowest speed at which the structural load limit can be pulled. Below
    it the wing runs out of lift before the airframe runs out of strength, so
    the turn is lift-limited and slower; above it the turn radius grows with
    speed. Corner is where the two curves cross, and a fighter pilot spends a
    dogfight trying to live there.
    """
    denominator = 0.5 * density * aero.wing_area * aero.cl_max
    if denominator <= 0.0 or load_limit <= 0.0:
        return 0.0
    return math.sqrt(load_limit * mass * G0 / denominator)


def instantaneous_turn(speed: float, load_factor: float) -> tuple[float, float]:
    """Turn rate (rad/s) and radius (m) for a level turn at a load factor."""
    if speed <= 1.0 or load_factor <= 1.0:
        return 0.0, float("inf")
    lateral = G0 * math.sqrt(load_factor * load_factor - 1.0)
    return lateral / speed, speed * speed / lateral


def available_load_factor(aero, mass: float, density: float, speed: float,
                          load_limit: float) -> float:
    """The most g this aircraft can pull right now: lift or structure, whichever bites.

    Below corner speed it is the wing; above it, the placard. Which one it is
    decides whether the answer to being out-turned is to slow down or speed
    up, and getting that backwards is how an energy fight is lost.
    """
    qbar = 0.5 * density * speed * speed
    lift_limited = qbar * aero.wing_area * aero.cl_max / max(mass * G0, 1.0)
    return max(1.0, min(load_limit, lift_limited))

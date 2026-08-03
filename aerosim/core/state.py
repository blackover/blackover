"""Aircraft state vector and the derived quantities computed from it.

The integrator owns thirteen continuous states and nothing else:

    0-2   position NED         (m)      north, east, down
    3-5   velocity body        (m/s)    u, v, w
    6-9   attitude quaternion  (-)      w, x, y, z  (NED -> body)
    10-12 angular rate body    (rad/s)  p, q, r

Everything else -- airspeed, Mach, altitude, load factor, flight path angle --
is *derived*. Derived values are recomputed from the state whenever it
changes, never stored and updated independently, because two copies of the
same number are two numbers that will eventually disagree.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from . import frames

N_STATES = 13

IX_POS = slice(0, 3)
IX_VEL = slice(3, 6)
IX_QUAT = slice(6, 10)
IX_RATE = slice(10, 13)


@dataclass
class Derived:
    """Quantities computed from the state vector and the local air mass.

    Populated once per step after integration. Before the first step these
    hold their initialised values rather than stale nonsense -- the reference
    implementation published zeros here on step 0, which showed up as an
    airspeed needle that flicked to zero on the first frame of every flight.
    """

    vtas: float = 0.0  # true airspeed, m/s
    vcas: float = 0.0  # calibrated airspeed, m/s
    veas: float = 0.0  # equivalent airspeed, m/s
    mach: float = 0.0
    alpha: float = 0.0  # angle of attack, rad
    beta: float = 0.0  # sideslip, rad
    qbar: float = 0.0  # dynamic pressure, Pa
    altitude: float = 0.0  # height above the reference plane, m
    altitude_agl: float = 0.0  # height above terrain, m
    roll: float = 0.0
    pitch: float = 0.0
    yaw: float = 0.0
    p: float = 0.0  # body roll rate, rad/s
    q: float = 0.0  # body pitch rate, rad/s
    r: float = 0.0  # body yaw rate, rad/s
    gamma: float = 0.0  # inertial flight path angle, rad
    track: float = 0.0  # ground track, rad
    ground_speed: float = 0.0  # m/s
    vertical_speed: float = 0.0  # m/s, positive up
    load_factor: float = 1.0  # g, along body -z
    accel_body: np.ndarray = field(default_factory=lambda: np.zeros(3))
    density: float = 1.225
    temperature: float = 288.15
    pressure: float = 101325.0
    sound_speed: float = 340.294


@dataclass
class State:
    """The 13-element continuous state, plus its derived view."""

    x: np.ndarray = field(default_factory=lambda: np.zeros(N_STATES))
    derived: Derived = field(default_factory=Derived)
    on_ground: bool = False

    def __post_init__(self) -> None:
        if self.x.shape != (N_STATES,):
            raise ValueError(f"state vector must be {N_STATES} elements")
        if not np.any(self.x[IX_QUAT]):
            self.x[IX_QUAT] = frames.quat_identity()

    # -- views onto the vector -------------------------------------------

    @property
    def position(self) -> np.ndarray:
        return self.x[IX_POS]

    @property
    def velocity_body(self) -> np.ndarray:
        return self.x[IX_VEL]

    @property
    def quaternion(self) -> np.ndarray:
        return self.x[IX_QUAT]

    @property
    def rates(self) -> np.ndarray:
        return self.x[IX_RATE]

    @property
    def altitude(self) -> float:
        """Height above the reference plane. Position is *down*-positive."""
        return -float(self.x[2])

    @property
    def velocity_ned(self) -> np.ndarray:
        return frames.dcm_body_to_ned(self.quaternion) @ self.velocity_body

    def euler(self) -> tuple[float, float, float]:
        return frames.euler_from_quat(self.quaternion)

    def copy(self) -> "State":
        clone = State(x=self.x.copy())
        clone.on_ground = self.on_ground
        # Derived is a snapshot, so a shallow field copy is what we want.
        for name, value in vars(self.derived).items():
            setattr(
                clone.derived,
                name,
                value.copy() if isinstance(value, np.ndarray) else value,
            )
        return clone

    # -- construction ------------------------------------------------------

    @classmethod
    def from_conditions(
        cls,
        *,
        altitude: float = 0.0,
        vtas: float = 0.0,
        alpha: float = 0.0,
        beta: float = 0.0,
        roll: float = 0.0,
        pitch: float = 0.0,
        heading: float = 0.0,
        north: float = 0.0,
        east: float = 0.0,
    ) -> "State":
        """Build a state from the conditions a pilot would actually name.

        ``pitch`` is the body attitude. If you want a given flight path angle
        instead, set ``pitch = gamma + alpha``.
        """
        state = cls()
        state.x[0] = north
        state.x[1] = east
        state.x[2] = -altitude

        ca, sa = math.cos(alpha), math.sin(alpha)
        cb, sb = math.cos(beta), math.sin(beta)
        state.x[3] = vtas * ca * cb
        state.x[4] = vtas * sb
        state.x[5] = vtas * sa * cb

        state.x[IX_QUAT] = frames.quat_from_euler(roll, pitch, heading)
        state.x[IX_RATE] = 0.0
        return state

    # -- housekeeping ------------------------------------------------------

    def normalise(self) -> None:
        """Renormalise the attitude quaternion in place."""
        self.x[IX_QUAT] = frames.quat_normalise(self.x[IX_QUAT])

    def is_finite(self) -> bool:
        """False as soon as the integrator has produced a NaN or infinity.

        Checked every step. A diverged state that keeps being integrated turns
        into a screen full of NaN two hundred steps after the actual fault,
        by which point the cause is gone.
        """
        return bool(np.all(np.isfinite(self.x)))

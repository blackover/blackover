"""Six-degree-of-freedom equations of motion and the RK4 integrator.

Fixed-step RK4 over the thirteen continuous states. Control surface positions,
thrust and mass properties are held constant across the four RK4 stages
(zero-order hold), because those subsystems contain rate limiters and discrete
logic that are not differentiable and must not be re-evaluated inside a stage.

That makes this a co-simulation split: continuous states integrate at RK4
accuracy while discrete states update once per kernel step and are therefore
first-order accurate. At 100 Hz the resulting error is small relative to the
modelling uncertainty in the aerodynamic data, but it is a real limitation.
"""

from __future__ import annotations

import numpy as np

from ..core import frames
from ..core.state import IX_POS, IX_QUAT, IX_RATE, IX_VEL, N_STATES
from ..core.units import G0


def state_derivative(
    x: np.ndarray,
    force_body: np.ndarray,
    moment_body: np.ndarray,
    mass: float,
    inertia: np.ndarray,
    inertia_inverse: np.ndarray,
) -> np.ndarray:
    """Time derivative of the 13-element state.

    ``force_body`` and ``moment_body`` are the *total* external force and
    moment about the centre of gravity, gravity included.
    """
    dx = np.zeros(N_STATES)

    velocity_body = x[IX_VEL]
    quaternion = x[IX_QUAT]
    omega = x[IX_RATE]

    # Position: rotate body velocity into NED.
    dx[IX_POS] = frames.dcm_body_to_ned(quaternion) @ velocity_body

    # Translation in a rotating frame: the cross term is what makes a
    # coordinated turn work rather than a straight line with a rolled attitude.
    dx[IX_VEL] = force_body / mass - np.cross(omega, velocity_body)

    # Attitude.
    dx[IX_QUAT] = frames.quat_derivative(quaternion, omega)

    # Rotation: Euler's equation with the full gyroscopic coupling term.
    dx[IX_RATE] = inertia_inverse @ (moment_body - np.cross(omega, inertia @ omega))

    return dx


def gravity_body(quaternion: np.ndarray, mass: float) -> np.ndarray:
    """Weight vector resolved into body axes."""
    return frames.dcm_ned_to_body(quaternion) @ np.array([0.0, 0.0, mass * G0])


def rk4_step(x: np.ndarray, dt: float, derivative) -> np.ndarray:
    """One classical fourth-order Runge-Kutta step.

    ``derivative(x)`` must return the state derivative for a given state. The
    forces are frozen for the whole step by the caller; only the state varies
    between stages.
    """
    k1 = derivative(x)
    k2 = derivative(x + 0.5 * dt * k1)
    k3 = derivative(x + 0.5 * dt * k2)
    k4 = derivative(x + dt * k3)
    return x + (dt / 6.0) * (k1 + 2.0 * k2 + 2.0 * k3 + k4)


def euler_step(x: np.ndarray, dt: float, derivative) -> np.ndarray:
    """Forward Euler, provided only for step-size comparison in tests."""
    return x + dt * derivative(x)


def load_factor(force_body_no_gravity: np.ndarray, mass: float) -> float:
    """Normal load factor in g, measured the way an accelerometer would.

    An accelerometer measures specific force, so it reads the aerodynamic and
    propulsive load only -- gravity must be excluded, not added.
    """
    if mass <= 0.0:
        return 1.0
    return float(-force_body_no_gravity[2]) / (mass * G0)

"""Reference frames, attitude representation and aerodynamic angles.

Frame conventions
-----------------
NED   north-east-down, origin at the scenario reference point, flat earth.
Body  x forward through the nose, y out the right wing, z down.

Attitude is carried as a unit quaternion ``q = [w, x, y, z]`` rotating a
vector from NED into body axes. Euler angles are derived from it for display
and never integrated directly -- that is what avoids gimbal lock when the
fighter goes over the top.
"""

from __future__ import annotations

import math

import numpy as np

from .units import wrap_pi


# --------------------------------------------------------------------------
# Quaternion primitives
# --------------------------------------------------------------------------


def quat_identity() -> np.ndarray:
    return np.array([1.0, 0.0, 0.0, 0.0])


def quat_normalise(q: np.ndarray) -> np.ndarray:
    """Return ``q`` scaled to unit norm.

    Renormalising every step is what stops RK4 drift from slowly turning the
    quaternion into a scaling as well as a rotation.
    """
    n = math.sqrt(float(q[0] * q[0] + q[1] * q[1] + q[2] * q[2] + q[3] * q[3]))
    if n < 1.0e-12:
        return quat_identity()
    return q / n


def quat_multiply(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Hamilton product ``a * b``."""
    aw, ax, ay, az = a
    bw, bx, by, bz = b
    return np.array(
        [
            aw * bw - ax * bx - ay * by - az * bz,
            aw * bx + ax * bw + ay * bz - az * by,
            aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw,
        ]
    )


def quat_conjugate(q: np.ndarray) -> np.ndarray:
    return np.array([q[0], -q[1], -q[2], -q[3]])


def quat_from_euler(roll: float, pitch: float, yaw: float) -> np.ndarray:
    """Build a quaternion from a 3-2-1 (yaw, pitch, roll) Euler sequence."""
    cr, sr = math.cos(roll * 0.5), math.sin(roll * 0.5)
    cp, sp = math.cos(pitch * 0.5), math.sin(pitch * 0.5)
    cy, sy = math.cos(yaw * 0.5), math.sin(yaw * 0.5)
    return np.array(
        [
            cr * cp * cy + sr * sp * sy,
            sr * cp * cy - cr * sp * sy,
            cr * sp * cy + sr * cp * sy,
            cr * cp * sy - sr * sp * cy,
        ]
    )


def euler_from_quat(q: np.ndarray) -> tuple[float, float, float]:
    """Recover (roll, pitch, yaw) in radians from a unit quaternion."""
    w, x, y, z = quat_normalise(q)

    # roll (x-axis rotation)
    sinr_cosp = 2.0 * (w * x + y * z)
    cosr_cosp = 1.0 - 2.0 * (x * x + y * y)
    roll = math.atan2(sinr_cosp, cosr_cosp)

    # pitch (y-axis rotation), clamped because asin outside [-1, 1] is a
    # numerical artefact at exactly 90 deg nose-up, not a real attitude
    sinp = 2.0 * (w * y - z * x)
    sinp = max(-1.0, min(1.0, sinp))
    pitch = math.asin(sinp)

    # yaw (z-axis rotation)
    siny_cosp = 2.0 * (w * z + x * y)
    cosy_cosp = 1.0 - 2.0 * (y * y + z * z)
    yaw = math.atan2(siny_cosp, cosy_cosp)

    return roll, pitch, yaw


def dcm_ned_to_body(q: np.ndarray) -> np.ndarray:
    """Direction cosine matrix taking a NED vector into body axes."""
    w, x, y, z = quat_normalise(q)
    xx, yy, zz = x * x, y * y, z * z
    wx, wy, wz = w * x, w * y, w * z
    xy, xz, yz = x * y, x * z, y * z
    return np.array(
        [
            [1.0 - 2.0 * (yy + zz), 2.0 * (xy + wz), 2.0 * (xz - wy)],
            [2.0 * (xy - wz), 1.0 - 2.0 * (xx + zz), 2.0 * (yz + wx)],
            [2.0 * (xz + wy), 2.0 * (yz - wx), 1.0 - 2.0 * (xx + yy)],
        ]
    )


def dcm_body_to_ned(q: np.ndarray) -> np.ndarray:
    """Direction cosine matrix taking a body vector into NED axes."""
    return dcm_ned_to_body(q).T


def quat_derivative(q: np.ndarray, omega_body: np.ndarray) -> np.ndarray:
    """Time derivative of the attitude quaternion given body rates.

    ``omega_body`` is (p, q, r) in rad/s about the body x, y, z axes.
    """
    p, qq, r = omega_body
    omega_quat = np.array([0.0, p, qq, r])
    return 0.5 * quat_multiply(q, omega_quat)


# --------------------------------------------------------------------------
# Aerodynamic angles
# --------------------------------------------------------------------------


def wind_angles(u: float, v: float, w: float) -> tuple[float, float, float]:
    """Return (true airspeed, angle of attack, sideslip) from body velocity.

    ``u``, ``v``, ``w`` are the body-axis components of the velocity of the
    aircraft *relative to the air mass*, not relative to the ground.
    """
    vtas = math.sqrt(u * u + v * v + w * w)

    # Below about a walking pace the angles are meaningless -- the aircraft is
    # parked and dividing by airspeed produces noise, not information.
    if vtas < 0.5:
        return vtas, 0.0, 0.0

    alpha = math.atan2(w, u)
    beta = math.asin(max(-1.0, min(1.0, v / vtas)))
    return vtas, alpha, beta


def body_from_wind(alpha: float, beta: float) -> np.ndarray:
    """Matrix rotating a wind-axis vector into body axes.

    Wind axes: x along the relative wind, z in the plane of symmetry pointing
    down. Lift acts along -z_wind, drag along -x_wind.
    """
    ca, sa = math.cos(alpha), math.sin(alpha)
    cb, sb = math.cos(beta), math.sin(beta)
    return np.array(
        [
            [ca * cb, -ca * sb, -sa],
            [sb, cb, 0.0],
            [sa * cb, -sa * sb, ca],
        ]
    )


def flight_path_angle(velocity_ned: np.ndarray) -> float:
    """Inertial flight path angle, positive climbing."""
    horizontal = math.hypot(float(velocity_ned[0]), float(velocity_ned[1]))
    if horizontal < 1.0e-6 and abs(velocity_ned[2]) < 1.0e-6:
        return 0.0
    return math.atan2(-float(velocity_ned[2]), horizontal)


def track_angle(velocity_ned: np.ndarray) -> float:
    """Ground track, radians from north, wrapped to (-pi, pi]."""
    return wrap_pi(math.atan2(float(velocity_ned[1]), float(velocity_ned[0])))

"""Natural modes of motion, by numerical linearisation about trim.

The five classical modes -- short period, phugoid, dutch roll, roll subsidence
and spiral -- are what a design *feels* like. Every other figure of merit says
what an aircraft can do; these say whether a pilot will enjoy doing it, and
they are the numbers a handling-qualities specification is actually written
in.

They are obtained here by finite-differencing the real force model rather than
by assembling a stability matrix from the derivatives directly. That costs a
little accuracy and buys the guarantee that what is analysed is what is flown:
if the flap increments, the ground effect and the Prandtl-Glauert factor are in
the force build-up, they are in the eigenvalues too.

Longitudinal and lateral motion decouple for a symmetric aircraft in
wings-level trim, so the two blocks are extracted separately. Taking
eigenvalues of the whole system instead leaves the short period and the dutch
roll in the same frequency band with nothing to tell them apart.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from ..core import frames
from ..core.state import IX_QUAT, IX_RATE, IX_VEL, N_STATES
from ..fdm.fdm import Controls
from ..fdm.rigid_body import state_derivative

# Reduced state, in the order used below:
#   0 u   body forward velocity      m/s
#   1 v   body lateral velocity      m/s
#   2 w   body normal velocity       m/s
#   3 p   roll rate                  rad/s
#   4 q   pitch rate                 rad/s
#   5 r   yaw rate                   rad/s
#   6 phi roll attitude              rad
#   7 theta pitch attitude           rad
#   8 h   altitude                   m
LONGITUDINAL = (0, 2, 4, 7)  # u, w, q, theta
LATERAL = (1, 3, 5, 6)  # v, p, r, phi

# Perturbation sizes, one per state, chosen small enough to be linear and
# large enough to stay well clear of the force model's own rounding.
_STEP = np.array([0.05, 0.05, 0.05, 1e-3, 1e-3, 1e-3, 1e-3, 1e-3, 1.0])


@dataclass
class Mode:
    """One natural mode: how fast it oscillates and how quickly it decays."""

    name: str
    eigenvalue: complex
    #: Undamped natural frequency, rad/s. Zero for a non-oscillatory mode.
    frequency: float = 0.0
    #: Damping ratio. For a real root this is +1 (convergent) or -1 (divergent).
    damping: float = 0.0
    #: Time constant of a real root, seconds. NaN for an oscillatory mode.
    time_constant: float = float("nan")

    @property
    def oscillatory(self) -> bool:
        return abs(self.eigenvalue.imag) > 1.0e-6

    @property
    def stable(self) -> bool:
        return self.eigenvalue.real < 0.0

    @property
    def period(self) -> float:
        """Seconds per cycle, or infinity for a non-oscillatory mode."""
        if not self.oscillatory:
            return float("inf")
        return 2.0 * math.pi / abs(self.eigenvalue.imag)

    @property
    def half_life(self) -> float:
        """Seconds to halve -- or, if divergent, to double."""
        if abs(self.eigenvalue.real) < 1.0e-9:
            return float("inf")
        return math.log(2.0) / abs(self.eigenvalue.real)

    def describe(self) -> str:
        if self.oscillatory:
            verb = "halves" if self.stable else "DOUBLES"
            return (
                f"period {self.period:6.1f} s, damping {self.damping:+.3f}, "
                f"{verb} in {self.half_life:5.1f} s"
            )
        verb = "halves" if self.stable else "DOUBLES"
        return f"time constant {abs(self.time_constant):6.2f} s, {verb} in {self.half_life:5.1f} s"


def _reduced_to_full(z: np.ndarray, yaw: float) -> np.ndarray:
    """Build the 13-element state the force model wants from the reduced one."""
    x = np.zeros(N_STATES)
    x[2] = -z[8]  # NED down
    x[IX_VEL] = z[0:3]
    x[IX_QUAT] = frames.quat_from_euler(z[6], z[7], yaw)
    x[IX_RATE] = z[3:6]
    return x


def _full_to_reduced_rate(z: np.ndarray, dx: np.ndarray) -> np.ndarray:
    """Map the 13-element derivative back onto the reduced state's rates.

    The attitude rates come from the Euler kinematic relations rather than
    from the quaternion derivative, because the reduced state carries angles.
    """
    p, q, r = float(z[3]), float(z[4]), float(z[5])
    phi, theta = float(z[6]), float(z[7])
    tan_theta = math.tan(theta)

    dz = np.zeros(9)
    dz[0:3] = dx[IX_VEL]
    dz[3:6] = dx[IX_RATE]
    dz[6] = p + (q * math.sin(phi) + r * math.cos(phi)) * tan_theta
    dz[7] = q * math.cos(phi) - r * math.sin(phi)
    dz[8] = -float(dx[2])
    return dz


def linearise(fdm, controls: Controls | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Numerical Jacobian about the current state.

    Returns ``(A, z0)``: the 9x9 system matrix of the reduced state and the
    reduced state it was taken at. The aircraft is assumed to be at or near
    trim -- the caller's job, not this function's.
    """
    if controls is None:
        controls = fdm.controls

    state = fdm.state
    roll, pitch, yaw = frames.euler_from_quat(state.quaternion)
    z0 = np.array(
        [
            state.x[IX_VEL][0],
            state.x[IX_VEL][1],
            state.x[IX_VEL][2],
            state.x[IX_RATE][0],
            state.x[IX_RATE][1],
            state.x[IX_RATE][2],
            roll,
            pitch,
            state.altitude,
        ]
    )

    mass_properties = fdm.mass_properties
    cg = mass_properties.cg

    def rates(z: np.ndarray) -> np.ndarray:
        # The atmosphere is resampled per perturbation so the altitude column
        # of the Jacobian carries the density gradient, which is what makes
        # the phugoid's altitude coupling appear at all.
        air = fdm.atmosphere.sample(float(z[8]))
        x = _reduced_to_full(z, yaw)
        force, moment, _ = fdm._forces(x, controls, air, mass_properties, cg)
        dx = state_derivative(
            x,
            force,
            moment,
            mass_properties.mass,
            mass_properties.inertia,
            mass_properties.inertia_inverse,
        )
        return _full_to_reduced_rate(z, dx)

    matrix = np.zeros((9, 9))
    for column in range(9):
        step = _STEP[column]
        forward, backward = z0.copy(), z0.copy()
        forward[column] += step
        backward[column] -= step
        # Central difference: the one-sided version carries a first-order
        # truncation error that shows up as spurious damping on the phugoid,
        # which is the lightest-damped mode and therefore the one it ruins.
        matrix[:, column] = (rates(forward) - rates(backward)) / (2.0 * step)

    return matrix, z0


def _classify(eigenvalue: complex, name: str) -> Mode:
    real, imag = float(eigenvalue.real), float(eigenvalue.imag)
    if abs(imag) > 1.0e-6:
        frequency = math.hypot(real, imag)
        damping = -real / frequency if frequency > 0.0 else 0.0
        return Mode(name, eigenvalue, frequency, damping)
    time_constant = float("inf") if abs(real) < 1e-12 else 1.0 / real
    return Mode(name, eigenvalue, 0.0, 1.0 if real < 0.0 else -1.0, time_constant)


def natural_modes(fdm, controls: Controls | None = None) -> dict[str, Mode]:
    """The five classical modes, named.

    Named by their place in the ordered eigenvalues of each block rather than
    by a frequency band: the short period is *by definition* the faster
    longitudinal pair and the phugoid the slower one, and a band test
    mislabels both on any aircraft whose numbers are unusual -- which is
    exactly the aircraft a designer wants the report for.
    """
    matrix, _ = linearise(fdm, controls)
    modes: dict[str, Mode] = {}

    longitudinal = np.linalg.eigvals(matrix[np.ix_(LONGITUDINAL, LONGITUDINAL)])
    pairs = _complex_pairs(longitudinal)
    if len(pairs) >= 2:
        pairs.sort(key=lambda value: abs(value.imag), reverse=True)
        modes["short_period"] = _classify(pairs[0], "short period")
        modes["phugoid"] = _classify(pairs[1], "phugoid")
    elif pairs:
        modes["short_period"] = _classify(pairs[0], "short period")

    lateral = np.linalg.eigvals(matrix[np.ix_(LATERAL, LATERAL)])
    pairs = _complex_pairs(lateral)
    if pairs:
        modes["dutch_roll"] = _classify(pairs[0], "dutch roll")

    reals = sorted(
        (value for value in lateral if abs(value.imag) <= 1.0e-9),
        key=lambda value: value.real,
    )
    if reals:
        modes["roll"] = _classify(reals[0], "roll subsidence")
    if len(reals) > 1:
        modes["spiral"] = _classify(reals[-1], "spiral")

    return modes


def _complex_pairs(eigenvalues) -> list[complex]:
    """One representative -- the positive-imaginary half -- of each pair."""
    return [complex(value) for value in eigenvalues if value.imag > 1.0e-9]

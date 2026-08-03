"""Steady-flight trim solver.

Finds the angle of attack, elevator deflection and throttle that hold a given
airspeed, altitude and flight path angle in equilibrium. Starting an airborne
scenario from an untrimmed state is what produces the slow altitude wander
people mistake for a bug in the integrator: a jet transport's phugoid is very
lightly damped by nature, roughly zeta ~ 1/(sqrt(2) * L/D), so an untrimmed
release oscillates for minutes.

The solver is a damped Newton iteration on the real force model rather than on
a linearised copy of it, so it cannot drift out of agreement with the physics
it is supposed to trim.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from ..core import frames
from ..core.state import State
from ..core.units import G0


@dataclass
class TrimResult:
    alpha: float = 0.0
    elevator: float = 0.0
    throttle: float = 0.0
    pitch: float = 0.0
    converged: bool = False
    residual: float = 0.0
    iterations: int = 0

    def summary(self) -> str:
        status = "converged" if self.converged else "DID NOT CONVERGE"
        return (
            f"trim {status}: alpha={math.degrees(self.alpha):.2f} deg, "
            f"elevator={self.elevator:+.3f}, throttle={self.throttle:.3f}, "
            f"pitch={math.degrees(self.pitch):.2f} deg, "
            f"residual={self.residual:.3e} after {self.iterations} iterations"
        )


def _residuals(
    fdm,
    unknowns: np.ndarray,
    vtas: float,
    altitude: float,
    gamma: float,
    flap: float,
    gear_down: bool,
    afterburner: bool,
) -> np.ndarray:
    """Force and moment imbalance for a candidate trim state.

    Returns (Fx, Fz, My) in body axes, all of which must be zero in steady
    flight. Normalised by weight and by weight-times-chord so the three
    residuals are comparable in magnitude and the Jacobian is well conditioned.
    """
    alpha, elevator, throttle = unknowns
    pitch = alpha + gamma

    air = fdm.atmosphere.sample(altitude)
    mass_properties = fdm.mass_model.compute()
    mass = mass_properties.mass
    cg = mass_properties.cg

    aero = fdm.aero.compute(
        vtas=vtas,
        alpha=alpha,
        beta=0.0,
        rates=np.zeros(3),
        density=air.density,
        mach=vtas / air.sound_speed,
        elevator=elevator,
        aileron=0.0,
        rudder=0.0,
        flap=flap,
        gear_down=gear_down,
        height_agl=1.0e6,
    )

    # Engine force AND moment from the propulsion model itself, so the moment
    # arm of an underwing pylon is in the equilibrium the solver finds rather
    # than only in the model the solver hands its answer to.
    propulsion = fdm.propulsion.steady_output(
        throttle, air.density_ratio, vtas / air.sound_speed, afterburner
    )

    quaternion = frames.quat_from_euler(0.0, pitch, 0.0)
    weight = frames.dcm_ned_to_body(quaternion) @ np.array([0.0, 0.0, mass * G0])

    force = aero.force_body + propulsion.force_body + weight

    aero_ref = np.array([fdm.aero.aero_ref_x, 0.0, 0.0])
    moment = aero.moment_body + np.cross(aero_ref - cg, aero.force_body)
    moment = moment + propulsion.moment_body + np.cross(-cg, propulsion.force_body)

    scale = mass * G0
    return np.array(
        [
            force[0] / scale,
            force[2] / scale,
            moment[1] / (scale * fdm.aero.mean_chord),
        ]
    )


def trim_level_flight(
    fdm,
    *,
    altitude: float,
    vtas: float,
    gamma: float = 0.0,
    flap: float = 0.0,
    gear_down: bool = False,
    afterburner: bool = False,
    max_iterations: int = 60,
    tolerance: float = 1.0e-8,
) -> TrimResult:
    """Solve for equilibrium at a given speed, altitude and climb angle."""
    air = fdm.atmosphere.sample(altitude)
    mass = fdm.mass_model.compute().mass

    # First guess from the lift equation, which is close enough that the
    # Newton iteration converges in a handful of steps.
    qbar = 0.5 * air.density * vtas * vtas
    if qbar > 1.0:
        cl_required = mass * G0 * math.cos(gamma) / (qbar * fdm.aero.wing_area)
    else:
        cl_required = fdm.aero.cl0
    alpha_guess = (cl_required - fdm.aero.cl0 - fdm.aero.flap_cl * flap) / max(
        fdm.aero.cl_alpha, 1.0e-6
    )
    alpha_guess = max(-0.25, min(0.30, alpha_guess))

    elevator_guess = -(fdm.aero.cm0 + fdm.aero.cm_alpha * alpha_guess) / fdm.aero.cm_de
    elevator_guess = max(-0.9, min(0.9, elevator_guess))

    unknowns = np.array([alpha_guess, elevator_guess, 0.5])
    result = TrimResult()

    step = np.array([1.0e-6, 1.0e-6, 1.0e-6])
    residual = _residuals(
        fdm, unknowns, vtas, altitude, gamma, flap, gear_down, afterburner
    )

    for iteration in range(max_iterations):
        error = float(np.linalg.norm(residual))
        result.iterations = iteration
        result.residual = error
        if error < tolerance:
            result.converged = True
            break

        # Numerical Jacobian: three extra force evaluations per iteration,
        # which is cheap next to maintaining an analytic one that has to be
        # rederived every time a derivative is added to the model.
        jacobian = np.zeros((3, 3))
        for j in range(3):
            perturbed = unknowns.copy()
            perturbed[j] += step[j]
            jacobian[:, j] = (
                _residuals(
                    fdm, perturbed, vtas, altitude, gamma, flap, gear_down, afterburner
                )
                - residual
            ) / step[j]

        try:
            delta = np.linalg.solve(jacobian, -residual)
        except np.linalg.LinAlgError:
            break

        # Damped step with a line search: an undamped Newton step can jump the
        # alpha guess straight past the stall, where the Jacobian it was
        # computed from no longer describes the surface it is standing on.
        damping = 1.0
        for _ in range(20):
            candidate = unknowns + damping * delta
            candidate[0] = max(-0.30, min(0.35, candidate[0]))
            candidate[1] = max(-1.0, min(1.0, candidate[1]))
            candidate[2] = max(0.0, min(1.0, candidate[2]))
            trial = _residuals(
                fdm, candidate, vtas, altitude, gamma, flap, gear_down, afterburner
            )
            if float(np.linalg.norm(trial)) < error:
                unknowns = candidate
                residual = trial
                break
            damping *= 0.5
        else:
            break

    result.alpha = float(unknowns[0])
    result.elevator = float(unknowns[1])
    result.throttle = float(unknowns[2])
    result.pitch = result.alpha + gamma
    result.residual = float(np.linalg.norm(residual))
    result.converged = result.residual < 1.0e-5
    return result


def apply_trim(
    fdm,
    trim: TrimResult,
    *,
    vtas: float,
    altitude: float,
    heading: float = 0.0,
    north: float = 0.0,
    east: float = 0.0,
    controls=None,
) -> State:
    """Build the trimmed state, load it into the model and spool the engines.

    The reset and the spool-up are done here, in this order, on purpose. Doing
    them at the call site invites the reverse order, and resetting after
    spooling drops every engine back to idle -- which shows up not as an error
    but as an aircraft that sinks for four seconds at the start of every
    airborne scenario while the spools catch up with a throttle lever that was
    already where it needed to be.
    """
    from .fdm import Controls
    from .propulsion import IDLE_N1

    state = State.from_conditions(
        altitude=altitude,
        vtas=vtas,
        alpha=trim.alpha,
        beta=0.0,
        roll=0.0,
        pitch=trim.pitch,
        heading=heading,
        north=north,
        east=east,
    )

    trimmed_controls = controls or Controls()
    trimmed_controls.elevator = trim.elevator
    trimmed_controls.throttle = trim.throttle

    fdm.reset(state, trimmed_controls)

    for engine in fdm.propulsion.engines:
        engine.n1 = IDLE_N1 + (1.0 - IDLE_N1) * trim.throttle
        engine.running = not engine.failed

    return state

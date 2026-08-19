"""Aerodynamic force and moment build-up from stability derivatives.

Coefficients are assembled linearly in the flight variables, with three
deliberate non-linearities layered on top: a synthesised stall curve, a
Prandtl-Glauert compressibility correction with a drag-rise table, and ground
effect.

Documented limitations that bite here
-------------------------------------
* Lateral-directional derivatives are linear in sideslip. There is no lateral
  stall, no departure, no spin. Beyond roughly 20 degrees of sideslip the model
  produces smooth, plausible, *incorrect* numbers rather than failing visibly,
  so ``out_of_envelope`` is raised instead and the caller posts a caution.
* The only unsteady term is Cm_q. No downwash lag, no dynamic-stall hysteresis.
* Compressibility is Prandtl-Glauert plus a drag rise. There is no Mach tuck,
  no shock-induced separation, no aileron reversal, and the Prandtl-Glauert
  factor is clamped so it cannot go singular. Behaviour above about M 0.85 is
  not representative.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from ..core.frames import body_from_wind
from ..env.weather import ICE_ALPHA_LOSS, ICE_CD0, ICE_CL_LOSS
from .tables import Table1D

MACH_CLAMP = 0.92
BETA_ENVELOPE = math.radians(20.0)


@dataclass
class AeroForces:
    """Aerodynamic force and moment in body axes, plus the coefficients."""

    force_body: np.ndarray
    moment_body: np.ndarray
    cl: float = 0.0
    cd: float = 0.0
    cy: float = 0.0
    cm: float = 0.0
    lift: float = 0.0
    drag: float = 0.0
    stalled: bool = False
    out_of_envelope: bool = False
    envelope_reason: str = ""


def synthesise_lift_curve(
    cl0: float, cl_alpha: float, alpha_stall: float, cl_max_factor: float = 0.98
) -> Table1D:
    """Build a CL(alpha) table from three numbers.

    Linear to the stall angle, a rounded peak, then decay towards a flat-plate
    value. Post-stall lift is representative in shape but not validated in
    magnitude -- supply an explicit ``cl_table`` in the data package if the
    post-stall region matters.

    The curve is continuous by construction and is *not* symmetric about zero
    alpha: mirroring the upright peak onto the inverted side silently deletes
    the camber term and misplaces the zero-lift angle.
    """
    alpha_zero_lift = -cl0 / cl_alpha if abs(cl_alpha) > 1e-9 else 0.0
    cl_peak = cl_max_factor * (cl0 + cl_alpha * alpha_stall)

    # Inverted stall happens nearer zero alpha and peaks lower, because the
    # camber is working against the flow.
    alpha_stall_neg = alpha_zero_lift - 0.85 * (alpha_stall - alpha_zero_lift)
    cl_peak_neg = cl_max_factor * (cl0 + cl_alpha * alpha_stall_neg)

    flat_plate = 1.1  # 2 sin(a) cos(a) peaks near this at 45 deg

    points: list[tuple[float, float]] = []
    for deg_value in range(-180, 181, 1):
        a = math.radians(deg_value)

        if alpha_stall_neg <= a <= alpha_stall:
            cl = cl0 + cl_alpha * a
            # Round the peak so the derivative is continuous into the stall.
            if a > 0.75 * alpha_stall:
                t = (a - 0.75 * alpha_stall) / (0.25 * alpha_stall)
                linear = cl0 + cl_alpha * a
                cl = linear + (cl_peak - linear) * (t * t) * 0.6
            elif a < 0.75 * alpha_stall_neg:
                t = (a - 0.75 * alpha_stall_neg) / (0.25 * alpha_stall_neg)
                linear = cl0 + cl_alpha * a
                cl = linear + (cl_peak_neg - linear) * (t * t) * 0.6
        else:
            # Post-stall: blend from the peak down to a flat-plate behaviour.
            if a > alpha_stall:
                excess = a - alpha_stall
                blend = min(1.0, excess / math.radians(18.0))
                plate = flat_plate * math.sin(2.0 * a)
                cl = cl_peak * (1.0 - blend) + plate * blend
            else:
                excess = alpha_stall_neg - a
                blend = min(1.0, excess / math.radians(18.0))
                plate = flat_plate * math.sin(2.0 * a)
                cl = cl_peak_neg * (1.0 - blend) + plate * blend

        points.append((a, cl))

    return Table1D(
        [p[0] for p in points],
        [p[1] for p in points],
        policy="clamp",
        name="cl_alpha_synthesised",
    )


class AeroModel:
    """Stability-derivative aerodynamic model for one aircraft."""

    def __init__(self, model) -> None:
        self.model = model

        self.wing_area = model.get("geometry", "wing_area")
        self.wing_span = model.get("geometry", "wing_span")
        self.mean_chord = model.get("geometry", "mean_chord")
        self.aspect_ratio = self.wing_span**2 / self.wing_area
        self.aero_ref_x = model.get("geometry", "aero_reference_x", 0.0)

        g = model.get
        self.cl0 = g("aerodynamics", "lift.cl0")
        self.cl_alpha = g("aerodynamics", "lift.cl_alpha")
        self.cl_q = g("aerodynamics", "lift.cl_q", 0.0)
        self.cl_de = g("aerodynamics", "lift.cl_de", 0.0)
        self.alpha_stall = g("aerodynamics", "lift.alpha_stall")

        self.cd0 = g("aerodynamics", "drag.cd0")
        self.oswald = g("aerodynamics", "drag.oswald_efficiency", 0.80)
        self.cd_de = g("aerodynamics", "drag.cd_de", 0.0)
        self.cd_gear = g("aerodynamics", "drag.cd_gear", 0.020)
        self.cd_speedbrake = g("aerodynamics", "drag.cd_speedbrake", 0.045)

        self.cm0 = g("aerodynamics", "pitch.cm0", 0.0)
        self.cm_alpha = g("aerodynamics", "pitch.cm_alpha")
        self.cm_q = g("aerodynamics", "pitch.cm_q")
        self.cm_de = g("aerodynamics", "pitch.cm_de")

        self.cy_beta = g("aerodynamics", "lateral.cy_beta")
        self.cy_dr = g("aerodynamics", "lateral.cy_dr", 0.0)
        self.cl_beta = g("aerodynamics", "lateral.cl_beta")
        self.cl_p = g("aerodynamics", "lateral.cl_p")
        self.cl_r = g("aerodynamics", "lateral.cl_r", 0.0)
        self.cl_da = g("aerodynamics", "lateral.cl_da")
        self.cl_dr = g("aerodynamics", "lateral.cl_dr", 0.0)
        self.cn_beta = g("aerodynamics", "lateral.cn_beta")
        self.cn_p = g("aerodynamics", "lateral.cn_p", 0.0)
        self.cn_r = g("aerodynamics", "lateral.cn_r")
        self.cn_da = g("aerodynamics", "lateral.cn_da", 0.0)
        self.cn_dr = g("aerodynamics", "lateral.cn_dr")

        self.beta_max = g("aerodynamics", "lateral.beta_max", BETA_ENVELOPE)
        self.mach_validated_max = g("aerodynamics", "mach_validated_max", 0.85)

        # Configuration drag added at set-up time -- external stores, a cargo
        # pod, anything bolted on that is not a control surface. It is a
        # configuration, not a control, so it is set once rather than commanded.
        self.extra_cd0 = 0.0

        # Airframe icing, 0 clean to 1 fully contaminated. Owned by the
        # orchestrator, which integrates it against the weather; the aero
        # model only says what a given amount of it costs.
        self.ice = 0.0

        # Flap effects, applied as increments proportional to flap fraction.
        self.flap_cl = g("aerodynamics", "flaps.delta_cl_max", 0.0)
        self.flap_cd = g("aerodynamics", "flaps.delta_cd_max", 0.0)
        self.flap_cm = g("aerodynamics", "flaps.delta_cm_max", 0.0)
        self.flap_alpha_stall = g("aerodynamics", "flaps.delta_alpha_stall_max", 0.0)

        # Lift curve: explicit table if supplied, synthesised otherwise.
        table_spec = model.raw("aerodynamics", "lift.cl_table")
        if isinstance(table_spec, dict):
            self.cl_table = Table1D.from_spec(
                table_spec, "cl_alpha", convert=lambda v: math.radians(float(v))
            )
            self.cl_table_is_synthesised = False
        else:
            self.cl_table = synthesise_lift_curve(
                self.cl0, self.cl_alpha, self.alpha_stall
            )
            self.cl_table_is_synthesised = True

        # Transonic drag rise.
        mach_spec = model.raw("aerodynamics", "drag.mach_drag_table")
        if isinstance(mach_spec, dict):
            self.mach_drag = Table1D.from_spec(mach_spec, "mach_drag")
        else:
            self.mach_drag = Table1D(
                [0.0, 0.60, 0.75, 0.82, 0.86, 0.90, 0.95, 1.05, 1.40, 2.00],
                [0.0, 0.0, 0.0008, 0.0035, 0.0110, 0.0260, 0.0420, 0.0480, 0.0330, 0.0250],
                policy="clamp",
                name="mach_drag",
            )

        self.cl_max = max(self.cl_table.y)

    # -- helpers -----------------------------------------------------------

    def stall_speed(self, mass: float, density: float, flap: float = 0.0, load_factor: float = 1.0) -> float:
        """Stall speed in TAS for a given mass, air density and load factor.

        Ice is included, because the number this returns is what the panel
        draws as the red band and what the autoflight computes Vref from. A
        stall speed that ignores the contamination on the wing is the one
        number in the simulator it would be most dangerous to get wrong.
        """
        ice = max(0.0, min(1.0, self.ice))
        cl_max = (self.cl_max + self.flap_cl * flap) * (1.0 - ICE_CL_LOSS * ice)
        weight = mass * 9.80665 * max(0.1, load_factor)
        denom = 0.5 * density * self.wing_area * cl_max
        return math.sqrt(weight / denom) if denom > 0.0 else 0.0

    def _prandtl_glauert(self, mach: float) -> float:
        """Subsonic compressibility lift correction, clamped short of M 1."""
        m = min(abs(mach), MACH_CLAMP)
        return 1.0 / math.sqrt(1.0 - m * m)

    def _ground_effect(self, height_agl: float) -> tuple[float, float]:
        """Return (lift multiplier, induced-drag multiplier).

        Empirical, a function of height in wingspans only: no dependence on
        aspect ratio, sweep or attitude. It vanishes exactly one span above the
        ground, which is conventional but abrupt compared with reality.
        """
        h = max(height_agl, 0.0) / self.wing_span
        if h >= 1.0:
            return 1.0, 1.0
        phi = (33.0 * h**1.5) / (1.0 + 33.0 * h**1.5)
        return 1.0 + 0.08 * (1.0 - h), phi

    # -- the build-up ------------------------------------------------------

    def compute(
        self,
        *,
        vtas: float,
        alpha: float,
        beta: float,
        rates: np.ndarray,
        density: float,
        mach: float,
        elevator: float,
        aileron: float,
        rudder: float,
        flap: float = 0.0,
        speedbrake: float = 0.0,
        gear_down: bool = False,
        height_agl: float = 1.0e6,
    ) -> AeroForces:
        """Assemble the aerodynamic force and moment in body axes."""
        qbar = 0.5 * density * vtas * vtas

        if vtas < 1.0 or qbar < 1.0e-6:
            return AeroForces(np.zeros(3), np.zeros(3))

        p, q, r = float(rates[0]), float(rates[1]), float(rates[2])

        # Non-dimensional rates. The 2V normalisation is what makes these
        # derivatives comparable across speeds.
        chord_hat = self.mean_chord / (2.0 * vtas)
        span_hat = self.wing_span / (2.0 * vtas)

        pg = self._prandtl_glauert(mach)
        ge_lift, ge_induced = self._ground_effect(height_agl)

        # -- lift ----------------------------------------------------------
        # Ice thickens and roughens the section: it costs maximum lift and it
        # moves the stall to a lower angle. Both matter, and the second is the
        # one that kills, because the stall arrives while the attitude and the
        # speed both still look normal.
        ice = max(0.0, min(1.0, self.ice))
        ice_lift = 1.0 - ICE_CL_LOSS * ice

        alpha_shift = self.flap_alpha_stall * flap
        cl_base = self.cl_table.lookup(alpha - alpha_shift) * ice_lift
        cl = cl_base * pg * ge_lift
        cl += self.flap_cl * flap * ice_lift
        cl += self.cl_q * q * chord_hat
        cl += self.cl_de * elevator

        # Stall detection compares against where the curve actually peaks,
        # not against a hard alpha threshold, so it stays right when a data
        # package supplies its own lift curve.
        alpha_peak = (self.alpha_stall + alpha_shift) * (1.0 - ICE_ALPHA_LOSS * ice)
        stalled = alpha > alpha_peak or alpha < -0.85 * alpha_peak

        # -- drag ----------------------------------------------------------
        cl_induced = cl_base * pg
        cd_induced = (
            ge_induced
            * cl_induced * cl_induced
            / (math.pi * self.aspect_ratio * self.oswald)
        )
        cd = self.cd0 + self.extra_cd0 + ICE_CD0 * ice + cd_induced
        cd += self.mach_drag.lookup(abs(mach))
        cd += self.flap_cd * flap
        cd += self.cd_speedbrake * speedbrake
        cd += abs(self.cd_de * elevator)
        if gear_down:
            cd += self.cd_gear
        # Separated flow past the stall costs a great deal of drag.
        if stalled:
            excess = abs(alpha) - alpha_peak
            cd += 1.2 * max(0.0, math.sin(excess)) ** 2

        # -- side force ----------------------------------------------------
        cy = self.cy_beta * beta + self.cy_dr * rudder

        # -- moments -------------------------------------------------------
        cm = self.cm0 + self.cm_alpha * alpha + self.cm_q * q * chord_hat
        cm += self.cm_de * elevator
        cm += self.flap_cm * flap
        if stalled:
            # Loss of lift behind the CG pitches the nose down at the stall,
            # which is the recovery cue a pilot expects to feel.
            cm -= 0.35 * min(1.0, (abs(alpha) - alpha_peak) / math.radians(10.0))

        cl_roll = (
            self.cl_beta * beta
            + self.cl_p * p * span_hat
            + self.cl_r * r * span_hat
            + self.cl_da * aileron
            + self.cl_dr * rudder
        )
        cn = (
            self.cn_beta * beta
            + self.cn_p * p * span_hat
            + self.cn_r * r * span_hat
            + self.cn_da * aileron
            + self.cn_dr * rudder
        )
        if stalled:
            # Roll damping collapses when the wing is separated.
            cl_roll *= 0.45

        # -- dimensionalise ------------------------------------------------
        lift = qbar * self.wing_area * cl
        drag = qbar * self.wing_area * cd
        side = qbar * self.wing_area * cy

        # Lift acts along -z_wind, drag along -x_wind, side force along +y_wind.
        force_wind = np.array([-drag, side, -lift])
        force_body = body_from_wind(alpha, beta) @ force_wind

        moment_body = np.array(
            [
                qbar * self.wing_area * self.wing_span * cl_roll,
                qbar * self.wing_area * self.mean_chord * cm,
                qbar * self.wing_area * self.wing_span * cn,
            ]
        )

        out_of_envelope = False
        reason = ""
        if abs(beta) > self.beta_max:
            out_of_envelope = True
            reason = f"sideslip {math.degrees(beta):.0f} deg beyond linear range"
        elif alpha > 1.5 * alpha_peak:
            out_of_envelope = True
            reason = f"alpha {math.degrees(alpha):.0f} deg beyond 1.5x stall"
        elif abs(mach) > self.mach_validated_max:
            out_of_envelope = True
            reason = f"M {abs(mach):.2f} beyond validated compressibility range"

        return AeroForces(
            force_body=force_body,
            moment_body=moment_body,
            cl=cl,
            cd=cd,
            cy=cy,
            cm=cm,
            lift=lift,
            drag=drag,
            stalled=stalled,
            out_of_envelope=out_of_envelope,
            envelope_reason=reason,
        )

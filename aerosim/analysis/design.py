"""A design report card for an aircraft data package.

    python -m aerosim --design aeroliner_200

Every figure below is computed from the same models the simulator flies, at a
loading and altitude you can choose, and compared against a target band. The
bands come from published handling-qualities and performance practice, not
from these two aircraft: the point of the tool is to grade a *new* package,
and a target derived from the existing ones would only ever say "you have
built one of these already".

A verdict of ``off`` is not a failure. It means the design is unusual in that
respect and the reason should be deliberate -- a fighter is supposed to fail
the transport's wing-loading band, and it is supposed to fail it in the
direction that makes it a fighter.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from ..core.units import G0, to_ft, to_kt
from ..fdm.aero import AeroModel
from ..fdm.mass import MassModel
from ..fdm.propulsion import PropulsionModel
from ..env.atmosphere import Atmosphere
from .modes import Mode

# Handling-qualities targets. MIL-F-8785C Level 1, Category B (cruise and
# climb) where a category has to be chosen -- the band a pilot would describe
# as pleasant rather than merely survivable.
HANDLING_TARGETS: dict[str, tuple[str, float, float]] = {
    # mode key -> (quantity, low, high)
    "short_period": ("damping", 0.30, 2.00),
    "phugoid": ("damping", 0.04, 1.00),
    "dutch_roll": ("damping", 0.08, 1.00),
}
ROLL_TIME_CONSTANT_MAX = 1.4  # s, Level 1 Category B
BUFFET_MARGIN = 0.85  # fraction of CLmax a sustained turn may use
SPIRAL_DOUBLE_TIME_MIN = 20.0  # s, Level 1


@dataclass
class Figure:
    """One measured quantity with its target band and a verdict."""

    name: str
    value: float
    unit: str
    low: float | None = None
    high: float | None = None
    note: str = ""
    fmt: str = "{:,.2f}"

    @property
    def verdict(self) -> str:
        if self.low is None and self.high is None:
            return "--"
        if self.low is not None and self.value < self.low:
            return "low"
        if self.high is not None and self.value > self.high:
            return "high"
        return "ok"

    def rendered(self) -> str:
        return self.fmt.format(self.value)

    def band(self) -> str:
        if self.low is None and self.high is None:
            return ""
        low = self.fmt.format(self.low) if self.low is not None else ""
        high = self.fmt.format(self.high) if self.high is not None else ""
        if not low:
            return f"< {high}"
        if not high:
            return f"> {low}"
        return f"{low} - {high}"


@dataclass
class Section:
    title: str
    figures: list[Figure] = field(default_factory=list)


@dataclass
class DesignReport:
    aircraft: str
    display_name: str
    category: str
    mass: float
    altitude: float
    sections: list[Section] = field(default_factory=list)
    modes: dict[str, Mode] = field(default_factory=dict)
    remarks: list[str] = field(default_factory=list)

    def figures(self) -> list[Figure]:
        return [figure for section in self.sections for figure in section.figures]

    @property
    def off_target(self) -> list[Figure]:
        return [figure for figure in self.figures() if figure.verdict not in ("ok", "--")]


# --------------------------------------------------------------------------


def _peak_lift_to_drag(aero: AeroModel) -> tuple[float, float]:
    """Best L/D and the angle of attack that produces it."""
    best, best_alpha = 0.0, 0.0
    for degrees in np.arange(-2.0, 18.0, 0.05):
        alpha = math.radians(float(degrees))
        cl = aero.cl_table.lookup(alpha)
        cd = aero.cd0 + cl * cl / (math.pi * aero.aspect_ratio * aero.oswald)
        if cd > 0.0 and cl / cd > best:
            best, best_alpha = cl / cd, alpha
    return best, best_alpha


def _sustained_turn(
    aero: AeroModel, propulsion: PropulsionModel, mass: float, density: float,
    sound_speed: float, altitude: float,
) -> tuple[float, float]:
    """Best sustained turn rate and the load factor that gives it.

    Sustained means thrust equals drag: the aircraft holds the turn instead of
    trading speed for it. The classic single number for a fighter, and the one
    that a wing area and a thrust rating between them decide.
    """
    weight = mass * G0
    best_rate, best_n = 0.0, 1.0

    for speed in np.arange(60.0, 700.0, 5.0):
        qbar = 0.5 * density * speed * speed
        if qbar <= 0.0:
            continue
        mach = speed / sound_speed
        available = propulsion.steady_output(
            throttle=1.0, density_ratio=density / 1.225, mach=mach, afterburner=True
        ).total_thrust
        if available <= 0.0:
            continue

        # Drag at load factor n: parasite plus induced, induced going as n^2.
        k = 1.0 / (math.pi * aero.aspect_ratio * aero.oswald)
        parasite = (aero.cd0 + aero.mach_drag.lookup(mach)) * qbar * aero.wing_area
        if available <= parasite:
            continue
        induced_allowed = available - parasite
        # induced = k * (n W / (qbar S))^2 * qbar * S
        n_squared = induced_allowed * qbar * aero.wing_area / (k * weight * weight)
        if n_squared <= 1.0:
            continue
        n = math.sqrt(n_squared)

        # Lift-limited as well as thrust-limited, and the lift limit is the
        # buffet boundary rather than CLmax: a turn held at maximum lift is
        # one the wing is shaking through, which nobody calls sustained.
        n_lift = BUFFET_MARGIN * aero.cl_max * qbar * aero.wing_area / weight
        n = min(n, n_lift)
        if n <= 1.0:
            continue

        rate = G0 * math.sqrt(n * n - 1.0) / speed  # rad/s
        if rate > best_rate:
            best_rate, best_n = rate, n

    return math.degrees(best_rate), best_n


def _range_estimate(
    aero: AeroModel, propulsion: PropulsionModel, mass_model: MassModel,
    peak_ld: float, sound_speed: float,
) -> float:
    """Breguet range at best L/D, in metres.

    A cruise-only figure: no reserves, no climb, no descent, no diversion.
    Useful for comparing two designs, useless as a flight-planning number,
    and labelled as such in the report.
    """
    sfc = propulsion.sfc
    if sfc <= 0.0 or peak_ld <= 0.0:
        return 0.0
    start = mass_model.empty_mass + mass_model.max_payload + mass_model.fuel_capacity
    end = mass_model.empty_mass + mass_model.max_payload
    if end <= 0.0 or start <= end:
        return 0.0
    # V / (g * sfc) * (L/D) * ln(m0/m1), with sfc in kg/(N s).
    # V / (g * TSFC) * (L/D) * ln(m0/m1), with TSFC in kg/(N s) so the g in
    # the numerator turns the mass ratio's weight into the thrust it needs.
    speed = 0.78 * sound_speed
    return speed / (G0 * sfc) * peak_ld * math.log(start / end)


def design_report(
    model,
    *,
    fuel_fraction: float = 0.6,
    payload_fraction: float = 0.6,
    altitude: float = 6000.0,
    modes: dict[str, Mode] | None = None,
) -> DesignReport:
    """Grade a data package against general design practice."""
    aero = AeroModel(model)
    propulsion = PropulsionModel(model)
    mass_model = MassModel(model)
    mass_model.set_fuel(fuel_fraction * mass_model.fuel_capacity)
    mass_model.set_payload(payload_fraction * mass_model.max_payload)
    mass = mass_model.compute().mass

    atmosphere = Atmosphere()
    sea_level = atmosphere.sample(0.0)
    air = atmosphere.sample(altitude)

    military = model.category == "military"
    report = DesignReport(
        aircraft=model.name,
        display_name=model.display_name,
        category=model.category,
        mass=mass,
        altitude=altitude,
    )

    # -- geometry and loading ---------------------------------------------
    wing_loading = mass * G0 / aero.wing_area
    static_thrust = propulsion.engine_count * propulsion.max_thrust
    if propulsion.has_afterburner:
        static_thrust *= propulsion.ab_thrust_factor
    thrust_to_weight = static_thrust / (mass * G0)

    geometry = Section("Geometry and loading")
    geometry.figures += [
        Figure(
            "Wing loading",
            wing_loading,
            "N/m2",
            *( (2500.0, 5500.0) if military else (3500.0, 7000.0) ),
            fmt="{:,.0f}",
            note="low turns tighter and rides rougher; high cruises better",
        ),
        Figure(
            "Thrust / weight",
            thrust_to_weight,
            "",
            *((0.75, 1.35) if military else (0.25, 0.40)),
            fmt="{:.3f}",
            note="static, sea level" + (", reheat" if propulsion.has_afterburner else ""),
        ),
        Figure(
            "Aspect ratio",
            aero.aspect_ratio,
            "",
            *((2.2, 4.5) if military else (7.0, 11.0)),
            fmt="{:.2f}",
            note="high is efficient and slow to roll; low is the reverse",
        ),
        Figure(
            "Fuel fraction",
            mass_model.fuel_capacity
            / (mass_model.empty_mass + mass_model.max_payload + mass_model.fuel_capacity),
            "",
            0.18,
            0.45,
            fmt="{:.3f}",
            note="of maximum ramp mass",
        ),
        Figure(
            "Empty / max take-off",
            mass_model.empty_mass / mass_model.max_takeoff_mass,
            "",
            0.40,
            0.65,
            fmt="{:.3f}",
            note="structure and systems as a share of the whole aircraft",
        ),
    ]
    report.sections.append(geometry)

    # -- aerodynamic efficiency -------------------------------------------
    peak_ld, ld_alpha = _peak_lift_to_drag(aero)
    aerodynamics = Section("Aerodynamics")
    aerodynamics.figures += [
        Figure(
            "CL max, clean",
            aero.cl_max,
            "",
            1.1,
            1.9,
            fmt="{:.2f}",
            note="with flap: "
            f"{aero.cl_max + aero.flap_cl:.2f}",
        ),
        Figure(
            "Peak L/D",
            peak_ld,
            "",
            *((5.0, 11.0) if military else (14.0, 20.0)),
            fmt="{:.1f}",
            note=f"at alpha {math.degrees(ld_alpha):.1f} deg",
        ),
        Figure(
            "Parasite drag CD0",
            aero.cd0,
            "",
            *((0.016, 0.030) if military else (0.016, 0.026)),
            fmt="{:.4f}",
        ),
        Figure(
            "Oswald efficiency",
            aero.oswald,
            "",
            0.70,
            0.90,
            fmt="{:.2f}",
            note="how close the span loading is to elliptical",
        ),
        Figure(
            "Stall angle",
            math.degrees(aero.alpha_stall),
            "deg",
            *((18.0, 32.0) if military else (12.0, 18.0)),
            fmt="{:.1f}",
        ),
    ]
    report.sections.append(aerodynamics)

    # -- speeds ------------------------------------------------------------
    stall_clean = aero.stall_speed(mass, sea_level.density)
    stall_land = aero.stall_speed(mass, sea_level.density, flap=1.0)
    vmo = model.get("limitations", "vmo")
    speeds = Section("Speeds, sea level, this loading")
    speeds.figures += [
        Figure("Stall, clean", to_kt(stall_clean), "kt", fmt="{:.0f}"),
        Figure("Stall, landing flap", to_kt(stall_land), "kt", fmt="{:.0f}"),
        Figure(
            "Approach (1.3 Vs)",
            to_kt(1.3 * stall_land),
            "kt",
            *((110.0, 160.0) if military else (110.0, 160.0)),
            fmt="{:.0f}",
            note="above about 160 kt the runway has to be long",
        ),
        Figure(
            "VMO / stall margin",
            vmo / stall_clean,
            "",
            2.0,
            5.0,
            fmt="{:.2f}",
            note="the usable speed range, as a ratio",
        ),
    ]
    report.sections.append(speeds)

    # -- performance -------------------------------------------------------
    turn_rate, turn_n = _sustained_turn(
        aero, propulsion, mass, air.density, air.sound_speed, altitude
    )
    available = propulsion.steady_output(
        throttle=1.0, density_ratio=air.density / sea_level.density, mach=0.4,
        afterburner=False,
    ).total_thrust
    drag_at_ld = mass * G0 / peak_ld if peak_ld > 0.0 else float("inf")
    climb_angle = math.degrees(math.asin(min(1.0, max(-1.0, (available - drag_at_ld) / (mass * G0)))))

    performance = Section(f"Performance at {to_ft(altitude):,.0f} ft")
    performance.figures += [
        Figure(
            "Sustained turn rate",
            turn_rate,
            "deg/s",
            *((10.0, 22.0) if military else (2.5, 6.0)),
            fmt="{:.1f}",
            note=f"at {turn_n:.1f} g, full thrust",
        ),
        Figure(
            "Climb angle at best L/D",
            climb_angle,
            "deg",
            *((12.0, 45.0) if military else (4.0, 12.0)),
            fmt="{:.1f}",
            note="dry thrust, this loading",
        ),
        Figure(
            "Cruise range (Breguet)",
            _range_estimate(aero, propulsion, mass_model, peak_ld, air.sound_speed)
            / 1852.0,
            "nm",
            fmt="{:,.0f}",
            note="cruise only: no reserves, climb, descent or diversion",
        ),
    ]
    report.sections.append(performance)

    # -- stability ---------------------------------------------------------
    static_margin = -aero.cm_alpha / aero.cl_alpha if aero.cl_alpha > 0.0 else 0.0
    stability = Section("Static stability")
    stability.figures += [
        Figure(
            "Static margin",
            static_margin * 100.0,
            "% MAC",
            *((-2.0, 12.0) if military else (5.0, 25.0)),
            fmt="{:.1f}",
            note="pitch stiffness; negative needs a stability augmentation system",
        ),
        Figure(
            "Cm alpha",
            aero.cm_alpha,
            "1/rad",
            None,
            -0.05,
            fmt="{:.3f}",
            note="must be negative or the aircraft diverges in pitch",
        ),
        Figure(
            "Cn beta",
            aero.cn_beta,
            "1/rad",
            0.04,
            None,
            fmt="{:.3f}",
            note="weathercock stability",
        ),
        Figure(
            "Cl beta",
            aero.cl_beta,
            "1/rad",
            None,
            -0.02,
            fmt="{:.3f}",
            note="dihedral effect; too much makes the dutch roll unpleasant",
        ),
    ]
    report.sections.append(stability)

    if modes:
        report.modes = modes
        report.sections.append(_handling_section(modes))

    report.remarks = _remarks(report, aero, propulsion, military)
    return report


def _handling_section(modes: dict[str, Mode]) -> Section:
    section = Section("Natural modes (linearised about trim)")
    for key, mode in modes.items():
        target = HANDLING_TARGETS.get(key)
        if target is not None and mode.oscillatory:
            _, low, high = target
            section.figures.append(
                Figure(
                    f"{mode.name} damping",
                    mode.damping,
                    "",
                    low,
                    high,
                    fmt="{:+.3f}",
                    note=f"period {mode.period:.1f} s",
                )
            )
        elif key == "roll":
            section.figures.append(
                Figure(
                    "roll time constant",
                    abs(mode.time_constant),
                    "s",
                    None,
                    ROLL_TIME_CONSTANT_MAX,
                    fmt="{:.2f}",
                    note="how quickly a roll rate settles after the stick moves",
                )
            )
        elif key == "spiral":
            doubling = mode.half_life if not mode.stable else float("inf")
            section.figures.append(
                Figure(
                    "spiral doubling time",
                    min(doubling, 999.0),
                    "s",
                    SPIRAL_DOUBLE_TIME_MIN,
                    None,
                    fmt="{:.0f}",
                    note="convergent" if mode.stable else "divergent, needs attention",
                )
            )
    return section


def _remarks(report: DesignReport, aero, propulsion, military: bool) -> list[str]:
    """Plain-language design advice from what the figures came out at."""
    remarks: list[str] = []
    values = {figure.name: figure for figure in report.figures()}

    def off(name: str) -> str:
        figure = values.get(name)
        return figure.verdict if figure else "--"

    if off("Wing loading") == "high":
        remarks.append(
            "Wing loading is high for the class: the approach speed and the "
            "runway length go up with it, and the turn radius goes up with it "
            "too. Add wing area or take mass out before adding thrust."
        )
    if off("Wing loading") == "low":
        remarks.append(
            "Wing loading is low: it will turn well and land short, and it "
            "will also ride badly in turbulence and cruise inefficiently, "
            "because a big wing is mostly parasite drag at speed."
        )
    if off("Aspect ratio") == "low" and not military:
        remarks.append(
            "Aspect ratio is low for a transport. Induced drag goes as the "
            "inverse of it, so this costs range directly -- the peak L/D "
            "figure above is where the bill arrives."
        )
    if off("Thrust / weight") == "low":
        remarks.append(
            "Thrust-to-weight is low: the take-off roll will be long, the "
            "climb shallow, and an engine failure at rotation marginal. "
            "Check the climb angle figure before adding mass anywhere."
        )
    if off("Static margin") == "low":
        remarks.append(
            "The static margin is small or negative. That buys manoeuvrability "
            "and trim drag, and it costs the aircraft its own pitch stability: "
            "it needs the flight assist on, and a real one would need a full "
            "stability augmentation system."
        )
    if off("Static margin") == "high":
        remarks.append(
            "The static margin is large. The aircraft will be stable and "
            "docile and will need a lot of tailplane to manoeuvre, which is "
            "trim drag carried for the whole flight."
        )
    if off("Approach (1.3 Vs)") == "high":
        remarks.append(
            "The approach speed is high. Either more flap effectiveness "
            "(delta_cl_max) or more wing area; without one of them this "
            "aircraft needs a long, dry runway every time."
        )
    if off("Peak L/D") == "low":
        remarks.append(
            "Peak L/D is low, so range and glide performance both suffer. "
            "The two levers are parasite drag (cd0) and aspect ratio, and on "
            "most designs cd0 is the cheaper one to move."
        )

    short_period = report.modes.get("short_period")
    if short_period is not None and short_period.damping < 0.30:
        remarks.append(
            f"Short-period damping is {short_period.damping:.2f}, below the "
            "0.30 a pilot would call comfortable. More tailplane area or a "
            "longer tail arm raises cm_q, which is the term that damps it."
        )
    dutch = report.modes.get("dutch_roll")
    if dutch is not None and dutch.damping < 0.08:
        remarks.append(
            f"Dutch-roll damping is {dutch.damping:.2f}. It will wallow in "
            "turbulence. Raise cn_r (fin area or tail arm) or reduce cl_beta "
            "(less dihedral or less sweep) -- too much dihedral effect "
            "relative to weathercock stability is the usual cause."
        )
    spiral = report.modes.get("spiral")
    if spiral is not None and not spiral.stable and spiral.half_life < 20.0:
        remarks.append(
            f"The spiral mode doubles in {spiral.half_life:.0f} s. The "
            "aircraft will roll further into any bank it is left in. More "
            "dihedral effect or less fin fixes it, at the dutch roll's cost."
        )
    roll = report.modes.get("roll")
    if roll is not None and abs(roll.time_constant) > ROLL_TIME_CONSTANT_MAX:
        remarks.append(
            f"The roll mode takes {abs(roll.time_constant):.1f} s to settle, "
            "so it will feel sluggish in roll. Aileron power (cl_da) and roll "
            "damping (cl_p) set it between them."
        )

    if not remarks:
        remarks.append(
            "Nothing is outside its band. That is the point at which the "
            "design is answering the brief and the next question is what the "
            "brief should have been."
        )
    return remarks


# --------------------------------------------------------------------------


def format_report(report: DesignReport) -> str:
    """Render a report as fixed-width text."""
    marks = {"ok": "  ok ", "low": " LOW ", "high": "HIGH ", "--": "     "}
    lines = [
        "",
        f"  {report.display_name}   ({report.category})",
        f"  package {report.aircraft}",
        f"  graded at {report.mass / 1000.0:,.1f} t and "
        f"{to_ft(report.altitude):,.0f} ft",
        "",
    ]

    for section in report.sections:
        lines.append(f"  {section.title}")
        lines.append(f"  {'-' * (len(section.title))}")
        for figure in section.figures:
            band = figure.band()
            unit = f" {figure.unit}" if figure.unit else ""
            lines.append(
                f"   {marks[figure.verdict]} {figure.name:<26} "
                f"{figure.rendered():>10}{unit:<7} "
                f"{('target ' + band) if band else '':<22} {figure.note}"
            )
        lines.append("")

    if report.modes:
        lines.append("  Mode detail")
        lines.append("  -----------")
        for mode in report.modes.values():
            lines.append(f"   {mode.name:<18} {mode.describe()}")
        lines.append("")

    lines.append("  Notes")
    lines.append("  -----")
    for remark in report.remarks:
        wrapped = _wrap(remark, 72)
        lines.append(f"   * {wrapped[0]}")
        lines.extend(f"     {line}" for line in wrapped[1:])
    lines.append("")
    return "\n".join(lines)


def _wrap(text: str, width: int) -> list[str]:
    words, lines, current = text.split(), [], ""
    for word in words:
        if current and len(current) + 1 + len(word) > width:
            lines.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    if current:
        lines.append(current)
    return lines or [""]

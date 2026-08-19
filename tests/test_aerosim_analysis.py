"""Design analysis: linearised modes and the design report card.

The modes are obtained by finite-differencing the real force model, so the
tests below check them the way you check any numerical derivative: against a
closed form where one exists, against an independent perturbation of the
nonlinear model where one does not, and against the physical sign conventions
that must hold whatever the numbers are.
"""

from __future__ import annotations

import math
import os
import sys
from pathlib import Path

import numpy as np
import pytest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aerosim.analysis.design import (
    BUFFET_MARGIN,
    Figure,
    design_report,
    format_report,
)
from aerosim.analysis.modes import LATERAL, LONGITUDINAL, linearise, natural_modes
from aerosim.core.model_package import load_aircraft
from aerosim.core.orchestrator import Simulation
from aerosim.core.units import ft, kt
from aerosim.game.config import SimConditions, StartMode

DATA_ROOT = Path(__file__).resolve().parent.parent / "aerosim" / "data" / "aircraft"
PACKAGES = ["aeroliner_200", "aerofalcon_x"]


@pytest.fixture(scope="module", params=PACKAGES)
def model(request):
    return load_aircraft(DATA_ROOT / request.param)


@pytest.fixture(scope="module")
def trimmed(model):
    """A settled, wings-level cruise -- what a linearisation needs."""
    sim = Simulation(
        model,
        SimConditions(
            aircraft=model.name,
            start_mode=StartMode.AIRBORNE,
            altitude=ft(20000),
            airspeed=kt(300) if model.category == "military" else kt(280),
            flight_assist=False,
            cloud_cover=0.0,
        ),
    )
    for _ in range(400):
        sim.step()
    assert not sim.crashed
    return sim


# --------------------------------------------------------------------------
# Linearisation
# --------------------------------------------------------------------------


class TestLinearisation:
    def test_jacobian_matches_the_nonlinear_model_it_came_from(self, trimmed):
        # The whole point of finite-differencing the force build-up rather
        # than assembling derivatives by hand is that what is analysed is
        # what is flown. Check it: perturb the nonlinear model and compare
        # the response against the linear prediction.
        from aerosim.analysis.modes import (
            _full_to_reduced_rate,
            _reduced_to_full,
        )
        from aerosim.core import frames
        from aerosim.fdm.rigid_body import state_derivative

        fdm = trimmed.fdm
        matrix, z0 = linearise(fdm)
        _, _, yaw = frames.euler_from_quat(fdm.state.quaternion)
        mass_properties = fdm.mass_properties

        def nonlinear(z):
            air = fdm.atmosphere.sample(float(z[8]))
            x = _reduced_to_full(z, yaw)
            force, moment, _ = fdm._forces(
                x, fdm.controls, air, mass_properties, mass_properties.cg
            )
            dx = state_derivative(
                x,
                force,
                moment,
                mass_properties.mass,
                mass_properties.inertia,
                mass_properties.inertia_inverse,
            )
            return _full_to_reduced_rate(z, dx)

        base = nonlinear(z0)
        # A perturbation an order of magnitude larger than the differencing
        # step, so agreement is a statement about the model and not about
        # having reused the same arithmetic.
        delta = np.zeros(9)
        delta[0] = 0.5  # u
        delta[2] = 0.5  # w
        delta[4] = 0.01  # q
        predicted = base + matrix @ delta
        actual = nonlinear(z0 + delta)

        scale = max(1.0, float(np.abs(actual).max()))
        assert np.allclose(predicted, actual, atol=0.02 * scale)

    def test_longitudinal_and_lateral_decouple_in_wings_level_trim(self, trimmed):
        # A symmetric aircraft flying straight has no cross-coupling worth
        # the name. If the blocks were coupled, splitting them to name the
        # modes would be wrong -- so the split is checked, not assumed.
        matrix, _ = linearise(trimmed.fdm)
        cross = matrix[np.ix_(LONGITUDINAL, LATERAL)]
        within = matrix[np.ix_(LONGITUDINAL, LONGITUDINAL)]
        assert np.abs(cross).max() < 0.06 * np.abs(within).max()

    def test_linearisation_does_not_disturb_the_aircraft(self, trimmed):
        before = trimmed.fdm.state.x.copy()
        linearise(trimmed.fdm)
        assert np.array_equal(trimmed.fdm.state.x, before)


# --------------------------------------------------------------------------
# Modes
# --------------------------------------------------------------------------


class TestModes:
    def test_all_five_modes_are_found(self, trimmed):
        modes = natural_modes(trimmed.fdm)
        assert set(modes) == {
            "short_period",
            "phugoid",
            "dutch_roll",
            "roll",
            "spiral",
        }

    def test_the_short_period_is_faster_than_the_phugoid(self, trimmed):
        # This is what distinguishes them, and it is how they are named. If
        # it were ever false the labels would be swapped and every piece of
        # advice keyed to them would point at the wrong coefficient.
        modes = natural_modes(trimmed.fdm)
        assert modes["short_period"].period < modes["phugoid"].period
        assert modes["short_period"].period < 12.0
        assert modes["phugoid"].period > 25.0

    def test_the_aircraft_is_stable_in_every_mode(self, trimmed):
        # Not a general truth about aeroplanes -- a statement about these two
        # packages, and one worth failing loudly if a data edit breaks it.
        for mode in natural_modes(trimmed.fdm).values():
            assert mode.stable, f"{mode.name} is divergent: {mode.describe()}"

    def test_the_roll_mode_settles_faster_than_the_spiral(self, trimmed):
        modes = natural_modes(trimmed.fdm)
        assert abs(modes["roll"].time_constant) < abs(modes["spiral"].time_constant)
        assert abs(modes["roll"].time_constant) < 2.0

    def test_the_fighter_rolls_and_pitches_faster_than_the_transport(self):
        # A physical expectation independent of the numbers: less inertia and
        # a shorter tail arm put both the roll mode and the short period
        # further out.
        modes = {}
        for name, speed in (("aeroliner_200", kt(280)), ("aerofalcon_x", kt(300))):
            model = load_aircraft(DATA_ROOT / name)
            sim = Simulation(
                model,
                SimConditions(
                    aircraft=name,
                    start_mode=StartMode.AIRBORNE,
                    altitude=ft(20000),
                    airspeed=speed,
                    flight_assist=False,
                    cloud_cover=0.0,
                ),
            )
            for _ in range(400):
                sim.step()
            modes[name] = natural_modes(sim.fdm)

        assert (
            abs(modes["aerofalcon_x"]["roll"].time_constant)
            < abs(modes["aeroliner_200"]["roll"].time_constant)
        )
        assert (
            modes["aerofalcon_x"]["short_period"].period
            < modes["aeroliner_200"]["short_period"].period
        )

    def test_mode_arithmetic_is_self_consistent(self, trimmed):
        for mode in natural_modes(trimmed.fdm).values():
            if mode.oscillatory:
                # period, frequency and damping all describe the same root.
                assert mode.period == pytest.approx(
                    2.0 * math.pi / abs(mode.eigenvalue.imag)
                )
                assert mode.frequency == pytest.approx(abs(mode.eigenvalue))
                assert mode.damping == pytest.approx(
                    -mode.eigenvalue.real / mode.frequency
                )
            else:
                assert mode.time_constant == pytest.approx(1.0 / mode.eigenvalue.real)
            assert mode.half_life == pytest.approx(
                math.log(2.0) / abs(mode.eigenvalue.real)
            )


# --------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------


class TestDesignReport:
    def test_report_covers_every_section_and_renders(self, model, trimmed):
        report = design_report(model, altitude=ft(20000), modes=natural_modes(trimmed.fdm))
        titles = [section.title for section in report.sections]
        assert "Geometry and loading" in titles
        assert "Aerodynamics" in titles
        assert "Static stability" in titles
        assert any(title.startswith("Natural modes") for title in titles)

        text = format_report(report)
        assert report.display_name in text
        assert "Notes" in text
        assert report.remarks

    def test_report_needs_no_trimmed_aircraft(self, model):
        # Everything but the modes comes from the data package alone, so a
        # design that will not trim still gets graded on the rest.
        report = design_report(model)
        assert report.figures()
        assert not report.modes
        assert format_report(report)

    def test_verdicts_follow_the_bands(self):
        assert Figure("x", 1.0, "", 0.0, 2.0).verdict == "ok"
        assert Figure("x", -1.0, "", 0.0, 2.0).verdict == "low"
        assert Figure("x", 3.0, "", 0.0, 2.0).verdict == "high"
        assert Figure("x", 3.0, "").verdict == "--"
        assert Figure("x", -5.0, "", None, 2.0).verdict == "ok"
        assert Figure("x", 5.0, "", 2.0, None).verdict == "ok"

    def test_the_fighter_and_the_transport_are_graded_differently(self):
        # The bands are class-dependent on purpose: a fighter that passed the
        # transport's wing-loading band would be a bad fighter.
        transport = design_report(load_aircraft(DATA_ROOT / "aeroliner_200"))
        fighter = design_report(load_aircraft(DATA_ROOT / "aerofalcon_x"))

        def figure(report, name):
            return next(f for f in report.figures() if f.name == name)

        assert figure(fighter, "Aspect ratio").high < figure(transport, "Aspect ratio").low
        assert (
            figure(fighter, "Thrust / weight").low
            > figure(transport, "Thrust / weight").high
        )
        # And both are inside their own bands, which is what makes them
        # credible examples of their classes.
        assert figure(transport, "Aspect ratio").verdict == "ok"
        assert figure(fighter, "Aspect ratio").verdict == "ok"

    def test_both_packages_are_broadly_in_band(self, model):
        # A handful of deliberate exceptions is expected; a report that is
        # mostly red would mean the bands are wrong, not the aircraft.
        report = design_report(model, altitude=ft(20000))
        graded = [f for f in report.figures() if f.verdict != "--"]
        off = [f for f in graded if f.verdict != "ok"]
        assert len(off) <= 0.25 * len(graded), [f.name for f in off]

    def test_sustained_turn_respects_the_buffet_margin(self, model):
        # A turn held at maximum lift is one the wing is shaking through.
        from aerosim.analysis.design import _sustained_turn
        from aerosim.env.atmosphere import Atmosphere
        from aerosim.fdm.aero import AeroModel
        from aerosim.fdm.mass import MassModel
        from aerosim.fdm.propulsion import PropulsionModel

        aero = AeroModel(model)
        propulsion = PropulsionModel(model)
        mass_model = MassModel(model)
        mass_model.set_fuel(0.6 * mass_model.fuel_capacity)
        mass_model.set_payload(0.6 * mass_model.max_payload)
        mass = mass_model.compute().mass

        air = Atmosphere().sample(ft(20000))
        rate, load = _sustained_turn(
            aero, propulsion, mass, air.density, air.sound_speed, ft(20000)
        )
        assert rate > 0.0
        assert 1.0 < load < 10.0

        # The lift limit that bounds it is the buffet boundary, so the CL the
        # turn actually uses never reaches CLmax.
        speed = 9.80665 * math.sqrt(load * load - 1.0) / math.radians(rate)
        qbar = 0.5 * air.density * speed * speed
        cl = load * mass * 9.80665 / (qbar * aero.wing_area)
        assert cl <= BUFFET_MARGIN * aero.cl_max + 1e-6

    def test_remarks_name_the_coefficient_to_change(self, model, trimmed):
        # Advice that says "improve the handling" is not advice. Every remark
        # should point at something in the YAML.
        report = design_report(model, altitude=ft(20000), modes=natural_modes(trimmed.fdm))
        text = " ".join(report.remarks).lower()
        assert any(
            token in text
            for token in ("cd0", "cl_beta", "cn_r", "cm_q", "cl_p", "cl_da",
                          "delta_cl_max", "wing area", "aspect ratio", "thrust",
                          "static margin", "brief")
        )

"""Tables, aerodynamics, mass, propulsion, gear, integration and trim."""

from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aerosim.core.model_package import PackageError, available_aircraft, load_aircraft
from aerosim.core.state import State
from aerosim.core.units import G0, deg, ft
from aerosim.fdm.aero import AeroModel, synthesise_lift_curve
from aerosim.fdm.fdm import Controls, FlightDynamics
from aerosim.fdm.mass import MassModel, _parallel_axis
from aerosim.fdm.propulsion import PropulsionModel
from aerosim.fdm.rigid_body import rk4_step, state_derivative
from aerosim.fdm.tables import Table1D, Table2D, TableRangeError
from aerosim.fdm.trim import trim_level_flight

DATA_ROOT = Path(__file__).resolve().parent.parent / "aerosim" / "data" / "aircraft"
PACKAGES = ["aeroliner_200", "aerofalcon_x"]


@pytest.fixture(scope="module", params=PACKAGES)
def model(request):
    return load_aircraft(DATA_ROOT / request.param)


@pytest.fixture(scope="module")
def airliner():
    return load_aircraft(DATA_ROOT / "aeroliner_200")


@pytest.fixture(scope="module")
def fighter():
    return load_aircraft(DATA_ROOT / "aerofalcon_x")


# --------------------------------------------------------------------------
# Tables
# --------------------------------------------------------------------------


class TestTables:
    def test_interpolates_linearly(self):
        table = Table1D([0.0, 1.0, 2.0], [0.0, 10.0, 30.0])
        assert table(0.5) == pytest.approx(5.0)
        assert table(1.5) == pytest.approx(20.0)
        assert table(2.0) == pytest.approx(30.0)

    def test_clamp_policy_holds_the_end_value(self):
        table = Table1D([0.0, 1.0], [5.0, 7.0], policy="clamp")
        assert table(-10.0) == 5.0
        assert table(10.0) == 7.0

    def test_linear_policy_continues_the_slope(self):
        table = Table1D([0.0, 1.0], [0.0, 2.0], policy="linear")
        assert table(2.0) == pytest.approx(4.0)
        assert table(-1.0) == pytest.approx(-2.0)

    def test_error_policy_refuses_to_extrapolate(self):
        # Silent extrapolation of a stall curve to 60 degrees produces
        # plausible-looking numbers that mean nothing.
        table = Table1D([0.0, 1.0], [0.0, 1.0], policy="error")
        with pytest.raises(TableRangeError):
            table(1.5)

    def test_there_is_no_default_that_extrapolates_silently(self):
        with pytest.raises(ValueError):
            Table1D([0.0, 1.0], [0.0, 1.0], policy="whatever_looks_right")

    def test_rejects_malformed_tables(self):
        with pytest.raises(ValueError):
            Table1D([0.0, 1.0], [0.0])  # length mismatch
        with pytest.raises(ValueError):
            Table1D([1.0, 0.0], [0.0, 1.0])  # not increasing
        with pytest.raises(ValueError):
            Table1D([0.0], [0.0])  # too short

    def test_2d_bilinear(self):
        table = Table2D([0.0, 1.0], [0.0, 1.0], [[0.0, 1.0], [2.0, 3.0]])
        assert table(0.0, 0.0) == pytest.approx(0.0)
        assert table(1.0, 1.0) == pytest.approx(3.0)
        assert table(0.5, 0.5) == pytest.approx(1.5)


# --------------------------------------------------------------------------
# Model packages
# --------------------------------------------------------------------------


class TestModelPackage:
    def test_both_supplied_aircraft_load(self):
        assert len(available_aircraft(DATA_ROOT)) == 2

    def test_units_arrive_in_si(self, airliner):
        assert airliner.get("geometry", "wing_area") == pytest.approx(124.6)
        assert airliner.get("limitations", "vmo") == pytest.approx(174.911, abs=1e-2)
        assert airliner.get("aerodynamics", "pitch.cm_de") == pytest.approx(-1.30)

    def test_checksum_is_stable_and_distinct(self, airliner, fighter):
        assert airliner.checksum == load_aircraft(DATA_ROOT / "aeroliner_200").checksum
        assert airliner.checksum != fighter.checksum

    def test_categories_distinguish_the_two_aircraft(self, airliner, fighter):
        assert airliner.category == "passenger"
        assert fighter.category == "military"

    def test_missing_package_is_reported(self):
        with pytest.raises(PackageError):
            load_aircraft(DATA_ROOT / "no_such_aircraft")

    def test_bad_package_reports_every_problem_at_once(self, tmp_path):
        # A data author should fix one round of errors, not one error per run.
        package = tmp_path / "broken"
        package.mkdir()
        (package / "manifest.yaml").write_text(
            "name: broken\nversion: '1'\ncategory: passenger\n"
        )
        (package / "geometry.yaml").write_text(
            'wing_area: "10 furlongs2"\nwing_span: "10 m"\nmean_chord: "1 m"\n'
        )
        with pytest.raises(PackageError) as excinfo:
            load_aircraft(package)
        problems = excinfo.value.problems
        assert any("furlongs2" in p for p in problems)
        assert any("mass_properties.yaml" in p for p in problems)
        assert len(problems) > 3


class TestPhysicalAdmissibility:
    """Properties that must hold for any real aircraft.

    A transposed sign here produces an aircraft that flies and is wrong, which
    is far harder to diagnose later than a failed assertion now.
    """

    def test_pitch_stiffness_is_negative(self, model):
        assert model.get("aerodynamics", "pitch.cm_alpha") < 0.0

    def test_weathercock_stability_is_positive(self, model):
        assert model.get("aerodynamics", "lateral.cn_beta") > 0.0

    def test_dihedral_effect_is_negative(self, model):
        assert model.get("aerodynamics", "lateral.cl_beta") < 0.0

    def test_roll_and_yaw_damping_are_negative(self, model):
        assert model.get("aerodynamics", "lateral.cl_p") < 0.0
        assert model.get("aerodynamics", "lateral.cn_r") < 0.0
        assert model.get("aerodynamics", "pitch.cm_q") < 0.0

    def test_inertia_tensor_is_symmetric_positive_definite(self, model):
        properties = MassModel(model).compute()
        inertia = properties.inertia
        assert np.allclose(inertia, inertia.T, rtol=1e-12)
        assert np.all(np.linalg.eigvalsh(inertia) > 0.0)

    def test_principal_moments_satisfy_the_triangle_inequality(self, model):
        # Violate it and the tensor describes no rigid body that exists.
        a, b, c = sorted(np.linalg.eigvalsh(MassModel(model).compute().inertia))
        assert a + b >= c * (1.0 - 1e-9)

    def test_empty_mass_is_below_maximum(self, model):
        assert model.get("mass_properties", "empty_mass") < model.get(
            "mass_properties", "max_takeoff_mass"
        )


# --------------------------------------------------------------------------
# Aerodynamics
# --------------------------------------------------------------------------


class TestAerodynamics:
    def test_drag_polar_matches_the_closed_form(self, model):
        aero = AeroModel(model)
        for alpha_deg in (-2.0, 0.0, 3.0, 6.0):
            result = aero.compute(
                vtas=150.0,
                alpha=deg(alpha_deg),
                beta=0.0,
                rates=np.zeros(3),
                density=1.225,
                mach=0.0,
                elevator=0.0,
                aileron=0.0,
                rudder=0.0,
            )
            cl_wing = aero.cl_table.lookup(deg(alpha_deg))
            expected = aero.cd0 + cl_wing**2 / (
                math.pi * aero.aspect_ratio * aero.oswald
            )
            assert result.cd == pytest.approx(expected, rel=1e-9)

    def test_lift_curve_has_no_discontinuity(self, model):
        # A step in CL injects an impulsive force into the integrator.
        aero = AeroModel(model)
        previous = None
        for quarter_degree in range(-4 * 60, 4 * 60 + 1):
            cl = aero.cl_table.lookup(deg(quarter_degree * 0.25))
            if previous is not None:
                assert abs(cl - previous) < 0.08
            previous = cl

    def test_lift_curve_is_not_symmetric_about_zero(self, model):
        # Mirroring the upright peak onto the inverted side silently deletes
        # the camber term and misplaces the zero-lift angle.
        aero = AeroModel(model)
        assert aero.cl_table.lookup(deg(5.0)) != pytest.approx(
            -aero.cl_table.lookup(deg(-5.0)), rel=1e-3
        )

    def test_zero_lift_angle_follows_the_camber(self, model):
        aero = AeroModel(model)
        expected = -aero.cl0 / aero.cl_alpha
        # Bisect for where the synthesised curve actually crosses zero.
        low, high = -deg(12.0), deg(6.0)
        for _ in range(60):
            mid = 0.5 * (low + high)
            if aero.cl_table.lookup(mid) < 0.0:
                low = mid
            else:
                high = mid
        assert 0.5 * (low + high) == pytest.approx(expected, abs=deg(1.0))

    def test_lift_rises_then_falls_through_the_stall(self, model):
        aero = AeroModel(model)
        peak_alpha = max(
            (deg(d * 0.25) for d in range(0, 160)),
            key=lambda a: aero.cl_table.lookup(a),
        )
        assert aero.cl_table.lookup(peak_alpha) == pytest.approx(aero.cl_max, rel=1e-6)
        assert aero.cl_table.lookup(peak_alpha + deg(10.0)) < aero.cl_max

    def test_stall_flag_and_drag_rise(self, model):
        aero = AeroModel(model)
        kwargs = dict(
            vtas=100.0,
            beta=0.0,
            rates=np.zeros(3),
            density=1.225,
            mach=0.0,
            elevator=0.0,
            aileron=0.0,
            rudder=0.0,
        )
        cruise = aero.compute(alpha=deg(3.0), **kwargs)
        stalled = aero.compute(alpha=aero.alpha_stall + deg(8.0), **kwargs)
        assert not cruise.stalled
        assert stalled.stalled
        assert stalled.cd > cruise.cd

    def test_pitching_moment_opposes_alpha(self, model):
        aero = AeroModel(model)
        kwargs = dict(
            vtas=120.0,
            beta=0.0,
            rates=np.zeros(3),
            density=1.225,
            mach=0.0,
            elevator=0.0,
            aileron=0.0,
            rudder=0.0,
        )
        assert aero.compute(alpha=deg(6.0), **kwargs).cm < aero.compute(
            alpha=deg(2.0), **kwargs
        ).cm

    def test_compressibility_correction_cannot_go_singular(self, model):
        aero = AeroModel(model)
        assert math.isfinite(aero._prandtl_glauert(1.0))
        assert math.isfinite(aero._prandtl_glauert(3.0))
        assert aero._prandtl_glauert(0.0) == pytest.approx(1.0)
        assert aero._prandtl_glauert(0.8) > 1.0

    def test_envelope_flag_fires_beyond_the_validated_mach(self, model):
        aero = AeroModel(model)
        result = aero.compute(
            vtas=300.0,
            alpha=deg(2.0),
            beta=0.0,
            rates=np.zeros(3),
            density=0.4,
            mach=aero.mach_validated_max + 0.05,
            elevator=0.0,
            aileron=0.0,
            rudder=0.0,
        )
        assert result.out_of_envelope
        assert "M " in result.envelope_reason

    def test_envelope_flag_fires_beyond_beta_max(self, model):
        aero = AeroModel(model)
        result = aero.compute(
            vtas=120.0,
            alpha=0.0,
            beta=aero.beta_max + deg(5.0),
            rates=np.zeros(3),
            density=1.225,
            mach=0.2,
            elevator=0.0,
            aileron=0.0,
            rudder=0.0,
        )
        assert result.out_of_envelope

    def test_ground_effect_reduces_induced_drag_and_vanishes_one_span_up(self, model):
        aero = AeroModel(model)
        lift_near, induced_near = aero._ground_effect(0.05 * aero.wing_span)
        assert induced_near < 1.0 and lift_near > 1.0
        assert aero._ground_effect(aero.wing_span * 1.001) == (1.0, 1.0)

    def test_stall_speed_matches_the_independently_quoted_figure(self, model):
        # CL_max comes from aerodynamics.yaml, wing area from geometry.yaml and
        # the quoted stall speed from limitations.yaml. Three separately
        # authored files agreeing is evidence; a model agreeing with itself
        # would not be.
        aero = AeroModel(model)
        reference_mass = model.get("limitations", "stall_reference_mass")
        quoted = model.get("limitations", "stall_speed_clean")
        predicted = aero.stall_speed(reference_mass, 1.225)
        assert predicted == pytest.approx(quoted, rel=0.02)

    def test_flaps_add_lift_and_drag(self, model):
        aero = AeroModel(model)
        kwargs = dict(
            vtas=80.0,
            alpha=deg(4.0),
            beta=0.0,
            rates=np.zeros(3),
            density=1.225,
            mach=0.0,
            elevator=0.0,
            aileron=0.0,
            rudder=0.0,
        )
        clean = aero.compute(flap=0.0, **kwargs)
        landing = aero.compute(flap=1.0, **kwargs)
        assert landing.cl > clean.cl
        assert landing.cd > clean.cd

    def test_synthesised_curve_respects_its_inputs(self):
        table = synthesise_lift_curve(cl0=0.3, cl_alpha=5.7, alpha_stall=deg(14.0))
        assert table.lookup(0.0) == pytest.approx(0.3, abs=1e-9)
        assert table.lookup(deg(5.0)) == pytest.approx(0.3 + 5.7 * deg(5.0), rel=1e-6)


# --------------------------------------------------------------------------
# Mass
# --------------------------------------------------------------------------


class TestMass:
    def test_parallel_axis_matches_steiner(self):
        offset = np.array([2.0, -1.0, 0.5])
        transfer = _parallel_axis(100.0, offset)
        r2 = float(offset @ offset)
        assert transfer[0, 0] == pytest.approx(100.0 * (r2 - offset[0] ** 2))
        assert transfer[0, 1] == pytest.approx(-100.0 * offset[0] * offset[1])
        assert np.allclose(transfer, transfer.T)

    def test_zero_mass_contributes_nothing(self):
        assert np.allclose(_parallel_axis(0.0, np.array([5.0, 5.0, 5.0])), 0.0)

    def test_mass_is_the_sum_of_its_parts(self, model):
        mass_model = MassModel(model)
        mass_model.set_fuel(3000.0)
        mass_model.set_payload(2000.0)
        properties = mass_model.compute()
        assert properties.mass == pytest.approx(
            mass_model.empty_mass + 3000.0 + 2000.0
        )

    def test_fuel_load_is_capped_at_capacity(self, model):
        mass_model = MassModel(model)
        mass_model.set_fuel(1e9)
        assert mass_model.fuel_mass == pytest.approx(mass_model.fuel_capacity)

    def test_burning_fuel_reduces_mass_and_stops_at_empty(self, model):
        mass_model = MassModel(model)
        mass_model.set_fuel(100.0)
        assert mass_model.burn(30.0) == pytest.approx(30.0)
        assert mass_model.fuel_mass == pytest.approx(70.0)
        assert mass_model.burn(1000.0) == pytest.approx(70.0)
        assert mass_model.fuel_exhausted

    def test_cg_moves_when_the_load_moves(self, model):
        mass_model = MassModel(model)
        mass_model.set_fuel(0.0)
        mass_model.set_payload(0.0)
        empty_cg = mass_model.compute().cg[0]
        mass_model.set_payload(mass_model.max_payload)
        loaded_cg = mass_model.compute().cg[0]
        assert loaded_cg != pytest.approx(empty_cg)

    def test_inertia_grows_with_load(self, model):
        mass_model = MassModel(model)
        mass_model.set_fuel(0.0)
        light = MassModel(model).compute().inertia[0, 0]
        mass_model.set_fuel(mass_model.fuel_capacity)
        heavy = mass_model.compute().inertia[0, 0]
        assert heavy > light


# --------------------------------------------------------------------------
# Propulsion
# --------------------------------------------------------------------------


class TestPropulsion:
    def test_thrust_rises_with_throttle(self, model):
        propulsion = PropulsionModel(model)
        thrusts = [propulsion.steady_thrust(t, 1.0, 0.0) for t in (0.0, 0.3, 0.6, 1.0)]
        assert all(b > a for a, b in zip(thrusts, thrusts[1:]))

    def test_thrust_falls_with_altitude(self, model):
        propulsion = PropulsionModel(model)
        assert propulsion.steady_thrust(1.0, 0.3, 0.0) < propulsion.steady_thrust(
            1.0, 1.0, 0.0
        )

    def test_spool_lags_the_lever(self, model):
        propulsion = PropulsionModel(model)
        propulsion.reset()
        propulsion.update(0.01, throttle=1.0, afterburner=False, density_ratio=1.0, mach=0.0)
        assert propulsion.mean_n1 < 0.5  # nowhere near commanded after 10 ms
        for _ in range(9000):  # 90 s, several spool time constants
            propulsion.update(0.01, throttle=1.0, afterburner=False, density_ratio=1.0, mach=0.0)
        assert propulsion.mean_n1 == pytest.approx(1.0, abs=1e-4)

    def test_failed_engine_produces_no_thrust(self, model):
        propulsion = PropulsionModel(model)
        propulsion.reset()
        propulsion.fail_engine(0)
        for _ in range(2000):
            output = propulsion.update(
                0.01, throttle=1.0, afterburner=False, density_ratio=1.0, mach=0.0
            )
        assert propulsion.engines[0].thrust == pytest.approx(0.0, abs=1e-6)
        assert output.total_thrust < propulsion.max_thrust * len(propulsion.engines)

    def test_engine_out_yaws_a_twin_and_not_a_single(self, model):
        propulsion = PropulsionModel(model)
        propulsion.reset()
        propulsion.fail_engine(0)
        for _ in range(2000):
            output = propulsion.update(
                0.01, throttle=1.0, afterburner=False, density_ratio=1.0, mach=0.0
            )
        yaw_moment = abs(float(output.moment_body[2]))
        if propulsion.engine_count > 1:
            assert yaw_moment > 1000.0
        else:
            assert yaw_moment == pytest.approx(0.0, abs=1e-6)

    def test_afterburner_only_on_the_aircraft_that_declares_it(self, airliner, fighter):
        assert not PropulsionModel(airliner).has_afterburner
        assert PropulsionModel(fighter).has_afterburner

    def test_afterburner_adds_thrust_and_costs_fuel(self, fighter):
        propulsion = PropulsionModel(fighter)
        dry = propulsion.steady_output(1.0, 1.0, 0.0, afterburner=False)
        wet = propulsion.steady_output(1.0, 1.0, 0.0, afterburner=True)
        assert wet.total_thrust > dry.total_thrust
        # Fuel flow rises faster than thrust: that is what makes it expensive.
        assert wet.total_fuel_flow / dry.total_fuel_flow > wet.total_thrust / dry.total_thrust

    def test_afterburner_needs_the_detent(self, fighter):
        propulsion = PropulsionModel(fighter)
        assert propulsion.steady_thrust(0.5, 1.0, 0.0, True) == pytest.approx(
            propulsion.steady_thrust(0.5, 1.0, 0.0, False)
        )

    def test_steady_output_agrees_with_the_stepped_model(self, model):
        # The trim solver uses steady_output and the kernel uses update. If
        # they disagree, the solver finds the equilibrium of a model that does
        # not exist, and every airborne start begins with an oscillation.
        propulsion = PropulsionModel(model)
        propulsion.reset()
        for _ in range(15000):  # let the spools settle before comparing
            stepped = propulsion.update(
                0.01, throttle=0.7, afterburner=False, density_ratio=0.5, mach=0.6
            )
        steady = propulsion.steady_output(0.7, 0.5, 0.6, False)
        assert steady.total_thrust == pytest.approx(stepped.total_thrust, rel=1e-6)
        assert np.allclose(steady.force_body, stepped.force_body, rtol=1e-6)
        assert np.allclose(steady.moment_body, stepped.moment_body, rtol=1e-6)


# --------------------------------------------------------------------------
# Rigid body integration
# --------------------------------------------------------------------------


class TestRigidBody:
    def test_free_fall_matches_the_closed_form(self):
        # No aerodynamics: a dropped point mass, integrated, against s = gt^2/2.
        state = State.from_conditions(altitude=1000.0)
        mass, inertia = 1000.0, np.eye(3) * 1000.0
        inverse = np.linalg.inv(inertia)

        def derivative(x):
            from aerosim.core.frames import dcm_ned_to_body

            weight = dcm_ned_to_body(x[6:10]) @ np.array([0.0, 0.0, mass * G0])
            return state_derivative(x, weight, np.zeros(3), mass, inertia, inverse)

        x = state.x.copy()
        for _ in range(1000):  # 10 s
            x = rk4_step(x, 0.01, derivative)
        assert -x[2] == pytest.approx(1000.0 - 0.5 * G0 * 100.0, rel=1e-9)

    def test_quaternion_stays_normalised_through_a_roll(self):
        state = State.from_conditions(vtas=100.0)
        state.x[10] = 1.5  # rolling at 86 deg/s
        mass, inertia = 1000.0, np.diag([1000.0, 2000.0, 2500.0])
        inverse = np.linalg.inv(inertia)

        def derivative(x):
            return state_derivative(x, np.zeros(3), np.zeros(3), mass, inertia, inverse)

        x = state.x.copy()
        for _ in range(2000):
            x = rk4_step(x, 0.01, derivative)
            x[6:10] /= np.linalg.norm(x[6:10])
        assert np.linalg.norm(x[6:10]) == pytest.approx(1.0, abs=1e-12)

    def test_gyroscopic_coupling_is_present(self):
        # Spinning about two axes of unequal inertia must produce a moment
        # about the third. Drop the cross term and a tumbling aircraft rotates
        # like a sphere.
        state = State.from_conditions(vtas=100.0)
        state.x[10], state.x[11] = 2.0, 1.0
        inertia = np.diag([1000.0, 4000.0, 5000.0])
        dx = state_derivative(
            state.x, np.zeros(3), np.zeros(3), 1000.0, inertia, np.linalg.inv(inertia)
        )
        assert abs(dx[12]) > 1e-6

    def test_rk4_beats_euler_on_the_same_step(self):
        from aerosim.fdm.rigid_body import euler_step

        # Harmonic oscillator embedded in the state vector: exact solution known.
        def derivative(x):
            dx = np.zeros_like(x)
            dx[0] = x[1]
            dx[1] = -x[0]
            return dx

        rk, eu = np.array([1.0, 0.0]), np.array([1.0, 0.0])
        for _ in range(1000):
            rk = rk4_step(rk, 0.01, derivative)
            eu = euler_step(eu, 0.01, derivative)
        exact = math.cos(10.0)
        assert abs(rk[0] - exact) < abs(eu[0] - exact)


# --------------------------------------------------------------------------
# Trim and integrated flight
# --------------------------------------------------------------------------


def _build(model, **kwargs) -> FlightDynamics:
    fdm = FlightDynamics(model, **kwargs)
    fdm.initialise(
        fuel=0.5 * fdm.mass_model.fuel_capacity,
        payload=0.5 * fdm.mass_model.max_payload,
    )
    return fdm


class TestTrim:
    def test_converges_in_cruise(self, model):
        fdm = _build(model)
        altitude = ft(25000)
        vtas = 0.65 * fdm.atmosphere.sample(altitude).sound_speed
        trim = trim_level_flight(fdm, altitude=altitude, vtas=vtas)
        assert trim.converged
        assert trim.residual < 1e-6
        assert 0.0 <= trim.throttle <= 1.0
        assert abs(trim.alpha) < deg(15.0)

    def test_trim_is_an_actual_equilibrium_of_the_stepped_model(self, model):
        # Regression test. The solver originally omitted the engine-position
        # moment that the force assembly includes, so it balanced a model that
        # was not the one being flown. The residual was 1e-14 and every
        # airborne start still began with a 770 ft phugoid.
        from aerosim.fdm.trim import apply_trim

        fdm = _build(model)
        altitude = ft(25000)
        vtas = 0.65 * fdm.atmosphere.sample(altitude).sound_speed
        trim = trim_level_flight(fdm, altitude=altitude, vtas=vtas)
        controls = Controls(gear_down=False)
        apply_trim(fdm, trim, vtas=vtas, altitude=altitude, controls=controls)

        altitudes = []
        for _ in range(3000):  # 30 s hands off
            fdm.step(0.01, controls)
            altitudes.append(fdm.state.altitude)

        excursion = max(altitudes) - min(altitudes)
        assert excursion < ft(60.0), f"drifted {excursion:.0f} m from trim"

    def test_heavier_needs_more_alpha(self, model):
        fdm = _build(model)
        altitude = ft(20000)
        vtas = 0.6 * fdm.atmosphere.sample(altitude).sound_speed

        fdm.mass_model.set_fuel(0.1 * fdm.mass_model.fuel_capacity)
        light = trim_level_flight(fdm, altitude=altitude, vtas=vtas)
        fdm.mass_model.set_fuel(fdm.mass_model.fuel_capacity)
        heavy = trim_level_flight(fdm, altitude=altitude, vtas=vtas)

        assert heavy.alpha > light.alpha
        assert heavy.throttle > light.throttle

    def test_climb_needs_more_thrust_than_level(self, model):
        fdm = _build(model)
        altitude = ft(15000)
        vtas = 0.55 * fdm.atmosphere.sample(altitude).sound_speed
        level = trim_level_flight(fdm, altitude=altitude, vtas=vtas, gamma=0.0)
        climb = trim_level_flight(fdm, altitude=altitude, vtas=vtas, gamma=deg(3.0))
        assert climb.throttle > level.throttle


class TestIntegratedFlight:
    def test_derived_quantities_are_published_before_the_first_step(self, model):
        # The reference implementation left airspeed unpublished until after
        # the first integration step, so every flight began with the needle
        # reading zero while the aircraft was demonstrably moving.
        fdm = _build(model)
        state = State.from_conditions(altitude=ft(10000), vtas=180.0)
        fdm.reset(state)
        assert fdm.state.derived.vtas == pytest.approx(180.0, rel=1e-6)
        assert fdm.state.derived.mach > 0.0
        assert fdm.state.derived.qbar > 0.0

    def test_parked_aircraft_stays_parked(self, model):
        fdm = _build(model)
        state = State.from_conditions(altitude=0.0)
        fdm.place_on_ground(state)
        state.on_ground = True
        controls = Controls(gear_down=True, brake=1.0)
        fdm.reset(state, controls)

        for _ in range(2000):  # 20 s
            fdm.step(0.01, controls)

        assert not fdm.diagnostics.crashed
        assert fdm.state.derived.ground_speed < 1.0
        # And it has not sunk through the runway or bounced off it.
        assert abs(fdm.state.altitude - state.altitude) < 0.5

    def test_ground_reaction_supports_the_weight(self, model):
        fdm = _build(model)
        state = State.from_conditions(altitude=0.0)
        fdm.place_on_ground(state)
        controls = Controls(gear_down=True, brake=1.0)
        fdm.reset(state, controls)
        for _ in range(500):
            fdm.step(0.01, controls)
        weight = fdm.mass_properties.mass * G0
        total_load = sum(strut.load for strut in fdm.gear.struts)
        assert total_load == pytest.approx(weight, rel=0.05)

    def test_turbulence_is_reproducible_from_the_seed(self, model):
        from aerosim.env.wind import WindField

        def run(seed):
            fdm = _build(
                model, wind=WindField.uniform(5.0, 0.0, turbulence_intensity=3.5, seed=seed)
            )
            state = State.from_conditions(altitude=ft(5000), vtas=150.0)
            fdm.reset(state)
            controls = Controls(throttle=0.5, gear_down=False)
            for _ in range(500):
                fdm.step(0.01, controls)
            return fdm.state.x.copy()

        assert np.allclose(run(7), run(7), atol=0.0)  # bit-for-bit
        assert not np.allclose(run(7), run(8))

    def test_engine_failure_yaws_the_twin(self, airliner):
        fdm = _build(airliner)
        state = State.from_conditions(altitude=ft(10000), vtas=180.0)
        fdm.reset(state)
        controls = Controls(throttle=1.0, gear_down=False)
        for _ in range(300):
            fdm.step(0.01, controls)
        fdm.propulsion.fail_engine(0)
        for _ in range(1000):  # 10 s
            fdm.step(0.01, controls)
        assert abs(fdm.state.derived.beta) > deg(0.5)

    def test_touchdown_loads_are_not_reported_as_over_g(self, model):
        # A normal touchdown spikes the accelerometer to several g against a
        # 2 g flaps placard. That is a landing, not an exceedance: manoeuvring
        # limits apply in flight, and gear loads are checked by touchdown rate.
        fdm = _build(model)
        state = State.from_conditions(altitude=ft(60), vtas=75.0, pitch=deg(2.0))
        controls = Controls(gear_down=True, flap=1.0, throttle=0.2)
        fdm.reset(state, controls)

        peak = 0.0
        over_g = False
        for _ in range(4000):
            diagnostics = fdm.step(0.01, controls)
            if fdm.state.on_ground:
                peak = max(peak, fdm.state.derived.load_factor)
                over_g = over_g or any(
                    e.startswith("OVER-G") for e in diagnostics.events
                )
            if fdm.state.on_ground and fdm.state.derived.ground_speed < 5.0:
                break

        assert peak > 1.5, "the gear never loaded up, so the test proves nothing"
        assert not over_g

    def test_in_flight_over_g_is_still_flagged(self, model):
        fdm = _build(model)
        altitude = ft(5000)
        air = fdm.atmosphere.sample(altitude)
        mass = fdm.mass_properties.mass

        # Pick a speed at which the aircraft can actually reach its placard.
        # A fighter at cruise is lift-limited well below its 9 g limit, so a
        # fixed test speed proves nothing about the monitor -- only that the
        # wing ran out of lift first.
        target = 1.4 * fdm.load_limit_positive
        qbar = target * mass * G0 / (fdm.aero.wing_area * fdm.aero.cl_max)
        vtas = math.sqrt(2.0 * qbar / air.density)

        state = State.from_conditions(altitude=altitude, vtas=vtas)
        fdm.reset(state)
        controls = Controls(elevator=-1.0, throttle=1.0, gear_down=False)
        flagged = False
        for _ in range(400):
            diagnostics = fdm.step(0.01, controls)
            flagged = flagged or any(e.startswith("OVER-G") for e in diagnostics.events)
        assert flagged, f"never exceeded {fdm.load_limit_positive} g at {vtas:.0f} m/s"

    def test_flaps_lower_the_load_limit(self, model):
        clean = _build(model)
        assert clean.load_limit_positive_flaps <= clean.load_limit_positive

    def test_overspeed_is_flagged(self, model):
        fdm = _build(model)
        state = State.from_conditions(
            altitude=ft(2000), vtas=fdm.vmo * 1.35, pitch=0.0
        )
        fdm.reset(state)
        diagnostics = fdm.step(0.01, Controls(throttle=1.0, gear_down=False))
        assert diagnostics.overspeed

    def test_state_stays_finite_through_a_violent_manoeuvre(self, model):
        fdm = _build(model)
        state = State.from_conditions(altitude=ft(20000), vtas=200.0)
        fdm.reset(state)
        controls = Controls(elevator=-1.0, aileron=1.0, rudder=1.0, throttle=1.0)
        for _ in range(2000):
            fdm.step(0.01, controls)
            assert fdm.state.is_finite()

"""Units, frames, clock and atmosphere.

Three kinds of test, kept deliberately distinct:

* external validation -- against independently published reference data
* analytical comparison -- against the closed form the code claims to implement
* physical admissibility -- properties that must hold for any real aircraft

The atmosphere numbers below are the U.S. Standard Atmosphere 1976 table's,
not this code's output.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aerosim.core import frames
from aerosim.core.clock import Clock, FrameAccumulator
from aerosim.core.state import State
from aerosim.core.units import (
    UnitError,
    clamp,
    deg,
    ft,
    fpm,
    kt,
    to_ft,
    to_kt,
    to_si,
    wrap_pi,
)
from aerosim.env.atmosphere import Atmosphere, geometric_to_geopotential
from aerosim.env.terrain import PROFILES, Terrain, flat_terrain
from aerosim.env.weather import (
    PRECIPITATION_FLOOR,
    RUNWAY_STATES,
    Weather,
    default_runway_state,
    recovery_temperature,
)


# --------------------------------------------------------------------------
# Units
# --------------------------------------------------------------------------


class TestUnits:
    def test_known_conversions(self):
        assert to_si("340 kt") == pytest.approx(174.9111, abs=1e-3)
        assert to_si("30000 ft") == pytest.approx(9144.0, abs=1e-6)
        assert to_si("25 deg") == pytest.approx(math.radians(25.0))
        assert to_si("-1.30 1/rad") == pytest.approx(-1.30)
        assert to_si("120 kn") == pytest.approx(120000.0)  # kilonewtons, not knots

    def test_bare_number_is_already_si(self):
        assert to_si(124.6) == 124.6
        assert to_si(-3) == -3.0

    def test_unknown_unit_is_rejected(self):
        # A typo that is quietly accepted becomes an aircraft that flies and is
        # wrong, which is far harder to find than an exception at load time.
        with pytest.raises(UnitError):
            to_si("340 knots_per_fortnight")

    def test_temperature_carries_its_offset(self):
        assert to_si("15 degC") == pytest.approx(288.15)
        assert to_si("59 degF") == pytest.approx(288.15, abs=1e-9)

    def test_no_duplicated_conversion_factor(self):
        # fpm() computed as v * 0.3048 / 60 differs in the last bit from the
        # registry's v * (0.3048/60). Harmless alone; exactly the shape of
        # error that becomes expensive.
        for value in (1.0, 137.0, -2500.0, 1e6):
            assert fpm(value) == value * (0.3048 / 60.0)

    def test_round_trip(self):
        for value in (0.0, 1.0, 250.0, -137.5):
            assert to_kt(kt(value)) == pytest.approx(value)
            assert to_ft(ft(value)) == pytest.approx(value)

    def test_wrap_pi(self):
        assert wrap_pi(0.0) == pytest.approx(0.0)
        assert wrap_pi(math.pi) == pytest.approx(math.pi)
        assert wrap_pi(math.pi + 0.1) == pytest.approx(-math.pi + 0.1)
        assert wrap_pi(-3 * math.pi) == pytest.approx(math.pi)
        assert wrap_pi(7 * math.pi / 2) == pytest.approx(-math.pi / 2)

    def test_clamp_rejects_inverted_bounds(self):
        assert clamp(5.0, 0.0, 1.0) == 1.0
        with pytest.raises(ValueError):
            clamp(0.5, 1.0, 0.0)


# --------------------------------------------------------------------------
# Frames
# --------------------------------------------------------------------------


class TestFrames:
    def test_identity_quaternion_is_identity_rotation(self):
        assert np.allclose(frames.dcm_ned_to_body(frames.quat_identity()), np.eye(3))

    @pytest.mark.parametrize(
        "roll,pitch,yaw",
        [
            (0.0, 0.0, 0.0),
            (0.3, -0.2, 1.1),
            (-1.2, 0.4, -2.8),
            (math.pi - 0.01, 0.0, 0.0),
            (0.0, 1.5, 0.0),
        ],
    )
    def test_euler_quaternion_round_trip(self, roll, pitch, yaw):
        q = frames.quat_from_euler(roll, pitch, yaw)
        r, p, y = frames.euler_from_quat(q)
        assert r == pytest.approx(roll, abs=1e-9)
        assert p == pytest.approx(pitch, abs=1e-9)
        assert y == pytest.approx(yaw, abs=1e-9)

    def test_dcm_is_orthonormal(self):
        q = frames.quat_from_euler(0.7, -0.4, 2.2)
        dcm = frames.dcm_ned_to_body(q)
        assert np.allclose(dcm @ dcm.T, np.eye(3), atol=1e-12)
        assert np.linalg.det(dcm) == pytest.approx(1.0, abs=1e-12)

    def test_body_to_ned_is_the_transpose(self):
        q = frames.quat_from_euler(-0.2, 0.9, 0.5)
        assert np.allclose(
            frames.dcm_body_to_ned(q), frames.dcm_ned_to_body(q).T, atol=1e-14
        )

    def test_pitch_up_puts_gravity_forward_in_body_axes(self):
        # Nose up 30 degrees: the weight vector gains a component along -x,
        # which is what decelerates a climbing aircraft.
        q = frames.quat_from_euler(0.0, math.radians(30.0), 0.0)
        gravity_body = frames.dcm_ned_to_body(q) @ np.array([0.0, 0.0, 9.80665])
        assert gravity_body[0] == pytest.approx(-9.80665 * math.sin(math.radians(30)))
        assert gravity_body[2] == pytest.approx(9.80665 * math.cos(math.radians(30)))

    def test_gimbal_lock_is_survivable(self):
        # Straight up. Euler angles are degenerate here; the quaternion is not.
        q = frames.quat_from_euler(0.0, math.pi / 2 - 1e-9, 0.0)
        _, pitch, _ = frames.euler_from_quat(q)
        assert pitch == pytest.approx(math.pi / 2, abs=1e-6)
        assert np.all(np.isfinite(frames.dcm_ned_to_body(q)))

    def test_wind_angles(self):
        vtas, alpha, beta = frames.wind_angles(100.0, 0.0, 10.0)
        assert vtas == pytest.approx(math.hypot(100.0, 10.0))
        assert alpha == pytest.approx(math.atan2(10.0, 100.0))
        assert beta == pytest.approx(0.0)

        _, _, beta = frames.wind_angles(100.0, 10.0, 0.0)
        assert beta > 0.0  # velocity out the right wing is positive sideslip

    def test_wind_angles_are_zero_when_parked(self):
        # Dividing by an airspeed of nothing produces noise, not information.
        vtas, alpha, beta = frames.wind_angles(0.01, 0.005, -0.02)
        assert alpha == 0.0 and beta == 0.0

    def test_quaternion_derivative_matches_finite_difference(self):
        q = frames.quat_from_euler(0.1, 0.2, 0.3)
        omega = np.array([0.05, -0.03, 0.11])
        dt = 1e-7
        analytic = frames.quat_derivative(q, omega)
        numeric = (
            frames.quat_normalise(q + dt * frames.quat_derivative(q, omega)) - q
        ) / dt
        assert np.allclose(analytic, numeric, atol=1e-5)


# --------------------------------------------------------------------------
# Clock
# --------------------------------------------------------------------------


class TestClock:
    def test_time_is_exact_after_many_steps(self):
        # Accumulating 0.01 s 120 000 times lands on 1200.0000000000073 s.
        # Multiplying lands on 1200.0, and that is the whole reason two runs
        # of the same scenario can be compared at all.
        clock = Clock(dt=0.01)
        for _ in range(120_000):
            clock.advance()
        assert clock.time == 1200.0

    def test_rejects_non_positive_step(self):
        with pytest.raises(ValueError):
            Clock(dt=0.0)

    def test_warns_above_validated_step(self):
        assert Clock(dt=0.05).warnings
        assert not Clock(dt=0.01).warnings

    def test_accumulator_caps_catch_up_bursts(self):
        # A long stall must not produce a thousand-step burst that flies the
        # aircraft into the ground while the window was being dragged.
        accumulator = FrameAccumulator(0.01, max_steps_per_frame=8)
        assert accumulator.add(0.025) == 2
        assert accumulator.add(10.0) == 8
        assert accumulator.dropped_steps > 0


# --------------------------------------------------------------------------
# State
# --------------------------------------------------------------------------


class TestState:
    def test_altitude_is_negative_down(self):
        state = State.from_conditions(altitude=ft(30000))
        assert state.altitude == pytest.approx(ft(30000))
        assert state.x[2] == pytest.approx(-ft(30000))

    def test_velocity_decomposition_recovers_the_angles(self):
        state = State.from_conditions(vtas=200.0, alpha=deg(4.0), beta=deg(-2.0))
        vtas, alpha, beta = frames.wind_angles(*state.velocity_body)
        assert vtas == pytest.approx(200.0)
        assert alpha == pytest.approx(deg(4.0))
        assert beta == pytest.approx(deg(-2.0))

    def test_copy_is_independent(self):
        state = State.from_conditions(altitude=1000.0, vtas=100.0)
        clone = state.copy()
        clone.x[0] = 999.0
        assert state.x[0] != 999.0

    def test_detects_divergence(self):
        state = State()
        assert state.is_finite()
        state.x[3] = float("nan")
        assert not state.is_finite()


# --------------------------------------------------------------------------
# Atmosphere -- external validation against USSA 1976
# --------------------------------------------------------------------------

# Geopotential altitude (m): temperature (K), pressure (Pa), density (kg/m3).
USSA_1976 = {
    0: (288.150, 101325.0, 1.22500),
    2000: (275.154, 79501.4, 1.00655),
    5000: (255.676, 54048.2, 0.736429),
    11000: (216.650, 22632.1, 0.363918),
    15000: (216.650, 12044.6, 0.193674),
    20000: (216.650, 5474.89, 0.0880349),
    32000: (228.650, 868.019, 0.0132250),
}


class TestAtmosphere:
    @pytest.mark.parametrize("h,expected", sorted(USSA_1976.items()))
    def test_against_published_table(self, h, expected):
        temperature, pressure, density = expected
        # The table is indexed on geopotential altitude; sample() takes
        # geometric, so the conversion has to be applied to the query.
        from aerosim.env.atmosphere import geopotential_to_geometric

        air = Atmosphere().sample(geopotential_to_geometric(h))
        assert air.temperature == pytest.approx(temperature, abs=0.05)
        assert air.pressure == pytest.approx(pressure, rel=1e-3)
        assert air.density == pytest.approx(density, rel=1e-3)

    def test_geopotential_correction_is_not_negligible(self):
        # Treating geometric and geopotential altitude as identical costs 19 m
        # at 11 km. Small, but it moves the tropopause.
        assert 11000 - geometric_to_geopotential(11000.0) == pytest.approx(19.0, abs=1.0)

    def test_sea_level_sound_speed(self):
        assert Atmosphere().sample(0.0).sound_speed == pytest.approx(340.294, abs=0.01)

    def test_temperature_offset_changes_density_not_pressure(self):
        standard = Atmosphere().sample(0.0)
        hot = Atmosphere(temperature_offset=20.0).sample(0.0)
        assert hot.pressure == pytest.approx(standard.pressure)
        assert hot.temperature == pytest.approx(standard.temperature + 20.0)
        assert hot.density < standard.density

    def test_density_falls_monotonically(self):
        atmosphere = Atmosphere()
        densities = [atmosphere.sample(h).density for h in range(0, 30000, 250)]
        assert all(b < a for a, b in zip(densities, densities[1:]))

    def test_cas_equals_tas_at_sea_level(self):
        atmosphere = Atmosphere()
        assert atmosphere.cas_from_tas(100.0, 0.0) == pytest.approx(100.0, rel=1e-6)

    def test_cas_is_below_tas_at_altitude(self):
        # Thin air: the same true airspeed produces less impact pressure.
        atmosphere = Atmosphere()
        assert atmosphere.cas_from_tas(200.0, ft(35000)) < 200.0

    def test_cas_tas_round_trip(self):
        atmosphere = Atmosphere()
        for altitude in (0.0, ft(10000), ft(35000)):
            for vtas in (60.0, 150.0, 240.0):
                vcas = atmosphere.cas_from_tas(vtas, altitude)
                assert atmosphere.tas_from_cas(vcas, altitude) == pytest.approx(
                    vtas, rel=1e-4
                )

    def test_eas_relationship_is_the_closed_form(self):
        atmosphere = Atmosphere()
        altitude = ft(25000)
        air = atmosphere.sample(altitude)
        assert atmosphere.eas_from_tas(200.0, altitude) == pytest.approx(
            200.0 * math.sqrt(air.density_ratio)
        )


# --------------------------------------------------------------------------
# Terrain
# --------------------------------------------------------------------------


class TestTerrain:
    def test_flat_profile_is_the_field_elevation_everywhere(self):
        terrain = flat_terrain(field_elevation=250.0)
        assert terrain.is_flat
        for north, east in ((0.0, 0.0), (40000.0, -12000.0), (-90000.0, 90000.0)):
            assert terrain.height_at(north, east) == 250.0

    def test_airport_plateau_is_level(self):
        # The runway, the flare and the gear model all assume level ground near
        # the field. A hill in the touchdown zone would be a trap.
        terrain = Terrain(seed=3, profile="mountainous", field_elevation=120.0)
        for north in (-3000.0, 0.0, 3000.0):
            for east in (-2000.0, 0.0, 2000.0):
                assert terrain.height_at(north, east) == pytest.approx(120.0)

    def test_relief_appears_outside_the_plateau(self):
        terrain = Terrain(seed=3, profile="mountainous", field_elevation=120.0)
        far = [terrain.height_at(n, 0.0) for n in np.linspace(20000.0, 80000.0, 40)]
        assert max(far) - min(far) > 300.0

    def test_scalar_and_vectorised_paths_agree_exactly(self):
        # height_at() is a hand-written fast path, not a call into heights().
        # If the two ever disagree the aircraft stands on ground that is not
        # the ground being drawn, which is the one bug terrain must not have.
        rng = np.random.default_rng(11)
        north = rng.uniform(-70000.0, 70000.0, 250)
        east = rng.uniform(-70000.0, 70000.0, 250)
        for name in PROFILES:
            terrain = Terrain(seed=5, profile=name, field_elevation=64.0)
            grid = terrain.heights(north, east)
            one_at_a_time = np.array(
                [terrain.height_at(a, b) for a, b in zip(north, east)]
            )
            assert np.array_equal(grid, one_at_a_time)

    def test_field_is_deterministic_across_instances(self):
        a = Terrain(seed=42, profile="hilly")
        b = Terrain(seed=42, profile="hilly")
        assert a.height_at(31234.0, -8765.0) == b.height_at(31234.0, -8765.0)

    def test_different_seeds_give_different_landscapes(self):
        a = Terrain(seed=1, profile="hilly")
        b = Terrain(seed=2, profile="hilly")
        samples = np.linspace(20000.0, 60000.0, 25)
        assert any(
            a.height_at(n, 15000.0) != b.height_at(n, 15000.0) for n in samples
        )

    def test_amplitude_ranks_with_the_profile(self):
        def relief(name):
            terrain = Terrain(seed=8, profile=name)
            north, east = np.meshgrid(
                np.linspace(20000.0, 90000.0, 60),
                np.linspace(20000.0, 90000.0, 60),
            )
            heights = terrain.heights(north, east)
            return float(heights.max() - heights.min())

        ordered = ["flat", "gentle", "rolling", "hilly", "mountainous"]
        values = [relief(name) for name in ordered]
        assert all(a < b for a, b in zip(values, values[1:]))

    def test_highest_within_bounds_the_samples_it_covers(self):
        terrain = Terrain(seed=6, profile="mountainous")
        peak = terrain.highest_within(40000.0, 40000.0, 5000.0, samples=11)
        assert peak >= terrain.height_at(40000.0, 40000.0)
        assert peak >= terrain.height_at(43000.0, 42000.0)


# --------------------------------------------------------------------------
# Weather
# --------------------------------------------------------------------------


class TestWeather:
    def test_recovery_temperature_is_the_stagnation_rise(self):
        # The number that decides whether an airframe ices is not the outside
        # air temperature: it is what the leading edge feels after bringing
        # the flow to rest.
        assert recovery_temperature(288.15, 0.0) == pytest.approx(288.15)
        for mach in (0.2, 0.5, 0.8, 1.5):
            expected = 288.15 * (1.0 + 0.89 * 0.2 * mach * mach)
            assert recovery_temperature(288.15, mach) == pytest.approx(expected)
        # Monotone, and a real rise by the time it matters.
        assert recovery_temperature(263.15, 0.8) - 263.15 > 25.0

    def test_a_solid_deck_is_flown_into_and_a_broken_one_is_not(self):
        broken = Weather(cloud_cover=0.5, cloud_base=1000.0, cloud_thickness=800.0)
        assert not broken.has_deck
        assert not broken.in_cloud(1400.0)

        overcast = Weather(cloud_cover=1.0, cloud_base=1000.0, cloud_thickness=800.0)
        assert overcast.has_deck
        assert not overcast.in_cloud(900.0)  # below
        assert overcast.in_cloud(1400.0)  # inside
        assert not overcast.in_cloud(2000.0)  # on top

    def test_visibility_collapses_in_cloud_and_recovers_above_it(self):
        weather = Weather(
            cloud_cover=1.0, cloud_base=1000.0, cloud_thickness=800.0, visibility=40000.0
        )
        assert weather.visibility_at(500.0) == pytest.approx(40000.0)
        assert weather.visibility_at(1400.0) < 100.0
        assert weather.visibility_at(2500.0) == pytest.approx(40000.0)

    def test_precipitation_reduces_visibility_but_not_below_the_floor(self):
        clear = Weather(precipitation="none", visibility=40000.0)
        rain = Weather(precipitation="rain", visibility=40000.0)
        heavy = Weather(precipitation="heavy_snow", visibility=40000.0)
        assert clear.visibility_at(0.0) > rain.visibility_at(0.0) > heavy.visibility_at(0.0)

        # And a low reported visibility in heavy precipitation does not go to
        # zero: the simulator has no Cat III equipment and the pre-flight check
        # says so rather than the weather pretending.
        worst = Weather(precipitation="heavy_snow", visibility=2000.0)
        assert worst.visibility_at(0.0) >= PRECIPITATION_FLOOR

    def test_icing_needs_moisture(self):
        dry = Weather(cloud_cover=0.0, precipitation="none")
        assert dry.icing_severity(altitude=1500.0, temperature=263.15, mach=0.2) == 0.0

        wet = Weather(cloud_cover=1.0, cloud_base=1000.0, cloud_thickness=3000.0)
        assert wet.icing_severity(altitude=1500.0, temperature=263.15, mach=0.2) > 0.0

    def test_icing_window_is_bounded_at_both_ends(self):
        weather = Weather(cloud_cover=1.0, cloud_base=0.0, cloud_thickness=8000.0)

        def severity(celsius):
            return weather.icing_severity(
                altitude=2000.0, temperature=273.15 + celsius, mach=0.05
            )

        assert severity(+5.0) == 0.0  # too warm: liquid stays liquid
        assert severity(-30.0) == 0.0  # too cold: already ice crystals
        assert severity(-6.0) > 0.5  # the supercooled-water band
        assert severity(-6.0) > severity(-16.0) > 0.0

    def test_kinetic_heating_keeps_a_fast_aircraft_out_of_the_icing_band(self):
        # The same cloud, the same air: a slow aircraft ices in it and a fast
        # one does not, because the recovery temperature at the leading edge
        # is above freezing. This falls out of the physics rather than being
        # a special case for the fighter.
        weather = Weather(cloud_cover=1.0, cloud_base=0.0, cloud_thickness=8000.0)
        cold = 273.15 - 8.0
        slow = weather.icing_severity(altitude=2000.0, temperature=cold, mach=0.15)
        fast = weather.icing_severity(altitude=2000.0, temperature=cold, mach=0.85)
        assert slow > 0.9
        assert fast == 0.0

    def test_ice_accretes_with_airspeed_and_sheds_with_anti_ice(self):
        fast = Weather.ice_rate(1.0, 200.0, anti_ice=False)
        slow = Weather.ice_rate(1.0, 60.0, anti_ice=False)
        assert fast > slow > 0.0

        # Anti-ice sheds whatever the conditions, because a boot or a hot
        # leading edge removes ice that has already formed.
        assert Weather.ice_rate(1.0, 200.0, anti_ice=True) < 0.0
        assert Weather.ice_rate(0.0, 200.0, anti_ice=False) < 0.0

    def test_runway_states_rank_by_braking(self):
        order = ["dry", "damp", "wet", "standing_water", "snow", "ice"]
        braking = [RUNWAY_STATES[name].braking for name in order]
        assert braking == sorted(braking, reverse=True)
        assert RUNWAY_STATES["dry"].braking == 1.0
        for state in RUNWAY_STATES.values():
            assert 0.0 < state.braking <= 1.0
            assert 0.0 < state.cornering <= 1.0
            assert state.rolling >= 1.0

    def test_default_runway_state_follows_the_weather(self):
        warm, freezing = 288.15, 265.0
        assert default_runway_state("none", warm) == "dry"
        assert default_runway_state("rain", warm) == "wet"
        assert default_runway_state("heavy_rain", warm) == "standing_water"
        assert default_runway_state("rain", freezing) == "ice"
        assert default_runway_state("snow", warm) == "snow"
        assert default_runway_state("none", freezing) == "ice"

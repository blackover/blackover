"""Air combat: the BFM geometry, the yaw damper, and the adversary pilot.

The geometry layer is pure arithmetic on two states, so it is tested against
hand-constructed geometries where the right answer is known by inspection --
one aircraft directly behind another has zero angle off tail, and if the code
disagrees then every tactical decision built on it is wrong in a way no
amount of flying would make obvious.

The adversary is tested the way a flight control law has to be: not by
checking which manoeuvre it picks, which is a judgement, but by checking the
invariants it must never violate however it flies. It must not stall the
aircraft, it must not fly it into the ground, and it must not do anything a
human pilot could not do through the same controls.
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

from aerosim.combat.adversary import (
    ALPHA_LIMIT,
    SKILLS,
    Adversary,
    Manoeuvre,
)
from aerosim.combat.engagement import (
    DECISIVE_TRACK,
    TRACK_CONE,
    TRACK_MAX_LOS_RATE,
    TRACK_MAX_RANGE,
    TRACK_MIN_RANGE,
    Engagement,
    Score,
    Setup,
    in_envelope,
)
from aerosim.combat.geometry import (
    available_load_factor,
    corner_speed,
    instantaneous_turn,
    observe,
    specific_energy,
)
from aerosim.control.dampers import YawDamper
from aerosim.core.frames import quat_from_euler
from aerosim.core.model_package import load_aircraft
from aerosim.core.orchestrator import Simulation
from aerosim.core.state import State
from aerosim.core.units import G0, deg, ft, kt, to_deg
from aerosim.game.config import SimConditions, StartMode

DATA_ROOT = Path(__file__).resolve().parent.parent / "aerosim" / "data" / "aircraft"


def make_state(position, velocity_ned, *, roll=0.0, pitch=0.0, yaw=0.0) -> State:
    """A state placed and pointed by hand, for geometry that can be checked.

    Only the fields the geometry reads are set: position, attitude and the
    velocity. Nothing here is flown, so the rest of the state vector is
    irrelevant and left at its initialised value.
    """
    state = State()
    state.x[0:3] = position
    state.x[6:10] = quat_from_euler(roll, pitch, yaw)
    from aerosim.core.frames import dcm_ned_to_body

    state.x[3:6] = dcm_ned_to_body(state.quaternion) @ np.asarray(
        velocity_ned, dtype=float
    )
    state.derived.altitude = -position[2]
    return state


# --------------------------------------------------------------------------
# Geometry
# --------------------------------------------------------------------------


class TestGeometry:
    def test_directly_astern_is_zero_angle_off_tail(self):
        """The defining case: 0 deg AOT means dead behind the target."""
        speed = 200.0
        target = make_state([1000.0, 0.0, -3000.0], [speed, 0.0, 0.0])
        own = make_state([0.0, 0.0, -3000.0], [speed, 0.0, 0.0])

        geometry = observe(own, target)

        assert geometry.range == pytest.approx(1000.0)
        assert geometry.angle_off_tail == pytest.approx(0.0, abs=1.0e-9)
        # And it is also pointing straight at it, which is a different thing
        # that happens to coincide here.
        assert geometry.antenna_train == pytest.approx(0.0, abs=1.0e-9)
        # Aspect is measured at the target, looking back: it sees our nose
        # from behind, so 180 deg.
        assert geometry.aspect == pytest.approx(math.pi, abs=1.0e-9)
        # Matched speeds, so no closure at all.
        assert geometry.closure == pytest.approx(0.0, abs=1.0e-9)

    def test_antenna_train_and_angle_off_tail_are_independent(self):
        """Abeam the target, pointing at it: ATA zero, AOT ninety.

        The distinction the three pursuit curves rest on. If these two ever
        collapse into one number the tactics cannot tell "I am pointing at
        it" from "I am behind it".
        """
        target = make_state([0.0, 1000.0, -3000.0], [200.0, 0.0, 0.0])
        # Sitting at the origin, nose round to the right, looking at it.
        own = make_state([0.0, 0.0, -3000.0], [200.0, 0.0, 0.0], yaw=deg(90.0))

        geometry = observe(own, target)

        assert to_deg(geometry.antenna_train) == pytest.approx(0.0, abs=1.0e-6)
        assert to_deg(geometry.angle_off_tail) == pytest.approx(90.0, abs=1.0e-6)
        assert not geometry.offensive
        assert not geometry.defensive

    def test_head_on_is_fully_offset_aspect_and_hard_closure(self):
        own = make_state([0.0, 0.0, -3000.0], [200.0, 0.0, 0.0])
        # Pointed back at us, which is what makes it head-on: the aspect
        # angle is measured off the target's NOSE, not off its velocity, so
        # its heading has to be turned round as well as its velocity.
        target = make_state(
            [2000.0, 0.0, -3000.0], [-180.0, 0.0, 0.0], yaw=deg(180.0)
        )

        geometry = observe(own, target)

        assert to_deg(geometry.aspect) == pytest.approx(0.0, abs=1.0e-6)
        assert to_deg(geometry.angle_off_tail) == pytest.approx(180.0, abs=1.0e-6)
        assert geometry.closure == pytest.approx(-380.0)
        assert geometry.closing

    def test_closure_is_the_line_of_sight_component_only(self):
        """A target crossing the line of sight has no closure, only rate."""
        own = make_state([0.0, 0.0, -3000.0], [0.0, 0.0, 0.0])
        target = make_state([1000.0, 0.0, -3000.0], [0.0, 250.0, 0.0])

        geometry = observe(own, target)

        assert geometry.closure == pytest.approx(0.0, abs=1.0e-9)
        # All of the motion rotates the line of sight: omega = v / r.
        assert geometry.line_of_sight_rate == pytest.approx(0.25)

    def test_specific_energy_counts_height_and_speed_alike(self):
        """Energy is a height, so a zoom climb must conserve it."""
        fast_low = make_state([0.0, 0.0, -1000.0], [300.0, 0.0, 0.0])
        energy = specific_energy(fast_low)
        assert energy == pytest.approx(1000.0 + 300.0**2 / (2.0 * G0))

        # Trading all of that speed for height leaves the energy unchanged.
        slow_high = make_state([0.0, 0.0, -energy], [0.0, 0.0, 0.0])
        assert specific_energy(slow_high) == pytest.approx(energy)

    def test_energy_advantage_has_the_sign_of_the_aircraft_that_has_more(self):
        high = make_state([0.0, 0.0, -6000.0], [200.0, 0.0, 0.0])
        low = make_state([500.0, 0.0, -3000.0], [200.0, 0.0, 0.0])

        assert observe(high, low).energy_advantage == pytest.approx(3000.0)
        assert observe(low, high).energy_advantage == pytest.approx(-3000.0)
        assert observe(high, low).height_advantage == pytest.approx(3000.0)

    def test_offensive_needs_position_and_pointing_and_range_together(self):
        """Any one of the three alone is a different situation entirely."""
        speed = 200.0
        target = make_state([1000.0, 0.0, -3000.0], [speed, 0.0, 0.0])
        assert observe(
            make_state([0.0, 0.0, -3000.0], [speed, 0.0, 0.0]), target
        ).offensive

        # Behind it, but twelve kilometres behind it: not an attack.
        far = make_state([-11000.0, 0.0, -3000.0], [speed, 0.0, 0.0])
        assert not observe(far, target).offensive

        # Close and pointing, but in front of it: a head-on pass.
        ahead = make_state([2000.0, 0.0, -3000.0], [speed, 0.0, 0.0], yaw=deg(180.0))
        assert not observe(ahead, target).offensive


class TestTurnPerformance:
    def test_corner_speed_is_where_the_limit_load_meets_the_lift_limit(self):
        """At corner speed the lift limit equals the placard, by definition."""

        class Aero:
            wing_area = 28.0
            cl_max = 1.4

        mass, density, limit = 14000.0, 1.0, 9.0
        corner = corner_speed(Aero, mass, density, limit)

        # At corner the aircraft can just pull the limit, and no more.
        assert available_load_factor(Aero, mass, density, corner, limit) == pytest.approx(
            limit, rel=1.0e-9
        )
        # A knot slower and the wing gives out first.
        assert available_load_factor(Aero, mass, density, corner * 0.98, limit) < limit

    def test_load_factor_available_is_limited_by_lift_then_by_structure(self):
        class Aero:
            wing_area = 28.0
            cl_max = 1.4

        mass, density, limit = 14000.0, 1.0, 9.0
        slow = available_load_factor(Aero, mass, density, 100.0, limit)
        fast = available_load_factor(Aero, mass, density, 400.0, limit)

        assert 1.0 <= slow < limit  # lift limited
        assert fast == pytest.approx(limit)  # placard limited

    def test_turn_rate_falls_and_radius_grows_with_speed_at_fixed_g(self):
        slow_rate, slow_radius = instantaneous_turn(150.0, 5.0)
        fast_rate, fast_radius = instantaneous_turn(300.0, 5.0)

        assert slow_rate > fast_rate
        assert slow_radius < fast_radius
        # The closed form, checked once so the rest can be trusted.
        assert slow_rate == pytest.approx(G0 * math.sqrt(24.0) / 150.0)

    def test_a_one_g_turn_does_not_turn(self):
        rate, radius = instantaneous_turn(200.0, 1.0)
        assert rate == 0.0
        assert radius == float("inf")


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------


class TestEnvelope:
    def _geometry(self, **overrides):
        base = dict(
            range=400.0,
            closure=-20.0,
            antenna_train=deg(3.0),
            angle_off_tail=deg(10.0),
            aspect=deg(170.0),
            line_of_sight_rate=0.05,
            own_energy=5000.0,
            target_energy=5000.0,
            height_advantage=0.0,
            line_of_sight=np.array([1.0, 0.0, 0.0]),
        )
        base.update(overrides)
        from aerosim.combat.geometry import Geometry

        return Geometry(**base)

    def test_a_solution_needs_the_cone_the_range_and_the_rate(self):
        assert in_envelope(self._geometry())

        # Each condition alone is enough to break it.
        assert not in_envelope(self._geometry(antenna_train=TRACK_CONE * 1.2))
        assert not in_envelope(self._geometry(range=TRACK_MAX_RANGE * 1.1))
        assert not in_envelope(self._geometry(range=TRACK_MIN_RANGE * 0.5))
        assert not in_envelope(
            self._geometry(line_of_sight_rate=TRACK_MAX_LOS_RATE * 1.1)
        )

    def test_score_accumulates_only_unbroken_stretches_as_best(self):
        score = Score()
        for _ in range(10):
            score.update(True, 0.1)
        assert score.total == pytest.approx(1.0)
        assert score.best == pytest.approx(1.0)
        assert score.solutions == 1

        score.update(False, 0.1)
        assert score.current == 0.0
        assert score.best == pytest.approx(1.0)

        for _ in range(5):
            score.update(True, 0.1)
        assert score.solutions == 2
        assert score.total == pytest.approx(1.5)
        assert score.best == pytest.approx(1.0)  # the longer stretch stands

    def test_decisive_needs_the_full_hold_not_the_total(self):
        """Ten brushes with the envelope are not one gun solution."""
        score = Score()
        for _ in range(10):
            score.update(True, DECISIVE_TRACK * 0.5)
            score.update(False, 0.1)

        assert score.total > DECISIVE_TRACK * 4
        assert not score.decisive

        held = Score()
        held.update(True, DECISIVE_TRACK)
        assert held.decisive


# --------------------------------------------------------------------------
# The yaw damper
# --------------------------------------------------------------------------


class TestYawDamper:
    @pytest.fixture
    def damper(self):
        return YawDamper(load_aircraft(DATA_ROOT / "aeroliner_200"))

    def test_a_steady_yaw_rate_washes_out_to_nothing(self):
        """The property the whole design rests on.

        A plain rate feedback damps the dutch roll and also fights every
        turn the pilot asks for, holding standing rudder against a steady
        turn. Washing it out means the damper opposes oscillation and not
        the manoeuvre.
        """
        damper = YawDamper(load_aircraft(DATA_ROOT / "aeroliner_200"))
        damper.reset()
        rate = deg(3.0)  # a standard-rate turn
        command = 0.0
        for _ in range(int(60.0 / 0.01)):
            command = damper.update(0.01, rate)

        assert abs(command) < 0.005

    def test_an_oscillation_passes_through_at_the_gain_the_maths_predicts(self):
        """The discrete filter must match the continuous washout it stands for.

        Checked as a frequency response rather than as a sign, because the
        sign that constitutes "opposing" depends on the rudder convention
        and a test that encodes one reading of that convention proves only
        that the test and the code agree. The amplitude does not care: a
        first-order washout passes ``gain * wT / sqrt(1 + (wT)^2)``, and at
        the corner frequency that is the gain down by root two.

        Whether the loop actually damps the aeroplane is a separate question,
        and the test below answers it by flying one.
        """
        damper = YawDamper(load_aircraft(DATA_ROOT / "aeroliner_200"))
        damper.reset()
        frequency = 1.0 / damper.washout  # the corner, where the error shows
        amplitude = deg(5.0)

        dt = 0.001
        peak = 0.0
        for step in range(int(12.0 * damper.washout / dt)):
            t = step * dt
            command = damper.update(dt, amplitude * math.sin(frequency * t))
            if t > 6.0 * damper.washout:  # after the transient has died
                peak = max(peak, abs(command))

        expected = amplitude * damper.equivalent_gain(frequency)
        assert peak == pytest.approx(expected, rel=0.02)

    def test_it_damps_the_aeroplane_it_is_fitted_to(self):
        """The closed-loop test: fly a rudder doublet with it and without it.

        This is the only test here that can say the damper does its job,
        because it is the only one that involves the aircraft. The airliner's
        bare dutch roll is lightly damped -- that is the defect this damper
        was fitted to fix -- so the residual oscillation half a minute after
        a kick should be markedly smaller with the loop closed.
        """
        model = load_aircraft(DATA_ROOT / "aeroliner_200")
        conditions = SimConditions(
            aircraft="aeroliner_200",
            start_mode=StartMode.AIRBORNE,
            altitude=ft(20000),
            airspeed=kt(280),
            turbulence="none",
            wind_speed=0.0,
            cloud_cover=0.0,
            seed=11,
        )

        def residual(damper_on: bool) -> float:
            sim = Simulation(model, conditions)
            if not damper_on:
                sim.yaw_damper.gain = 0.0
            dt = sim.clock.dt
            # A doublet: full rudder one way, then the other, then hands off.
            for _ in range(int(1.5 / dt)):
                sim.pilot.yaw = 1.0
                sim.step()
            for _ in range(int(1.5 / dt)):
                sim.pilot.yaw = -1.0
                sim.step()
            sim.pilot.yaw = 0.0
            worst = 0.0
            for step in range(int(30.0 / dt)):
                sim.step()
                if step * dt > 12.0:  # well after the input
                    worst = max(worst, abs(sim.fdm.state.derived.beta))
            return worst

        damped, bare = residual(True), residual(False)
        assert damped < bare * 0.5, (
            f"residual sideslip {to_deg(damped):.3f} deg with the damper, "
            f"{to_deg(bare):.3f} deg without -- the damper is not damping"
        )

    def test_authority_is_bounded(self, damper):
        damper.reset()
        command = damper.update(0.01, deg(200.0))
        assert abs(command) <= damper.authority + 1.0e-12

    def test_equivalent_gain_rises_with_frequency(self, damper):
        """A washout is a high-pass: slow motion gets through less."""
        slow = damper.equivalent_gain(0.1)
        fast = damper.equivalent_gain(10.0)

        assert slow < fast <= damper.gain
        # At the washout corner the gain is down by the usual 1/sqrt(2).
        corner = damper.equivalent_gain(1.0 / damper.washout)
        assert corner == pytest.approx(damper.gain / math.sqrt(2.0), rel=1.0e-6)

    def test_reset_clears_the_filter(self, damper):
        damper.reset()
        for _ in range(500):
            damper.update(0.01, deg(10.0))
        damper.reset()
        assert damper.update(0.01, 0.0) == pytest.approx(0.0)


# --------------------------------------------------------------------------
# The adversary
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def fighter():
    return load_aircraft(DATA_ROOT / "aerofalcon_x")


def fight_conditions(**overrides) -> SimConditions:
    base = dict(
        aircraft="aerofalcon_x",
        start_mode=StartMode.AIRBORNE,
        altitude=ft(15000),
        airspeed=kt(400),
        cloud_cover=0.0,
        terrain="rolling",
        seed=7,
    )
    base.update(overrides)
    return SimConditions(**base)


def fly_engagement(fighter, setup, skill="veteran", seconds=90.0, player_skill=None):
    """Run a complete engagement headlessly and return everything it did."""
    conditions = fight_conditions()
    player = Simulation(fighter, conditions)
    engagement = Engagement(player, conditions, setup=setup, skill=skill)
    helper = (
        Adversary(fighter, skill=player_skill, seed=conditions.seed + 999)
        if player_skill
        else None
    )

    dt = player.clock.dt
    worst = {"alpha": 0.0, "speed": 1.0e9, "agl": 1.0e9, "bandit_agl": 1.0e9}
    for _ in range(int(seconds / dt)):
        if helper is not None:
            helper.update(player, engagement.bandit.fdm.state, dt)
        player.step()
        state = engagement.step(player, dt)

        bandit = engagement.bandit.fdm.state.derived
        worst["alpha"] = max(worst["alpha"], bandit.alpha)
        worst["speed"] = min(worst["speed"], bandit.vtas)
        worst["bandit_agl"] = min(worst["bandit_agl"], bandit.altitude_agl)
        worst["agl"] = min(worst["agl"], player.fdm.state.derived.altitude_agl)
        if state.finished:
            break
    return engagement, worst


class TestAdversaryInvariants:
    """What the adversary must never do, whatever it chooses to fly."""

    @pytest.fixture(
        scope="class", params=[Setup.NEUTRAL, Setup.OFFENSIVE, Setup.HEAD_ON]
    )
    @staticmethod
    def flown(request):
        fighter = load_aircraft(DATA_ROOT / "aerofalcon_x")
        return fly_engagement(fighter, request.param, skill="veteran")

    def test_it_does_not_stall_the_aircraft(self, flown):
        """The commanded alpha is limited, so the flown alpha stays sane.

        Not a hard guarantee -- a gust can push the wing past anything the
        pilot asked for -- so the margin allows for that while still failing
        if the law is pulling the aircraft through the stall, which is what
        an unscaled load-factor controller does.
        """
        engagement, worst = flown
        stall = engagement.bandit.fdm.aero.alpha_stall
        assert worst["alpha"] < stall, (
            f"bandit reached {to_deg(worst['alpha']):.1f} deg alpha, "
            f"stall is {to_deg(stall):.1f} deg"
        )

    def test_it_keeps_the_aircraft_flying(self, flown):
        """It must not fly itself into a stall-speed energy hole.

        An unmanaged BFM law bleeds a fighter to below its stall speed
        inside a minute of hard turning; the energy gate exists to stop it.
        """
        engagement, worst = flown
        aero = engagement.bandit.fdm.aero
        stall = aero.stall_speed(
            engagement.bandit.fdm.mass_properties.mass,
            engagement.bandit.fdm.state.derived.density,
        )
        assert worst["speed"] > stall, (
            f"bandit decayed to {worst['speed']:.0f} m/s, stall is {stall:.0f} m/s"
        )

    def test_it_does_not_fly_into_the_ground(self, flown):
        engagement, _ = flown
        assert not engagement.bandit.crashed, engagement.state.finished

    def test_it_only_uses_controls_a_pilot_has(self, flown):
        """No back door into the flight model.

        The adversary drives the same PilotInput a human does, and every
        channel of it is bounded the same way. A bandit that could command
        1.4 of stick would be a cheat, and an opponent that cheats teaches
        nothing.
        """
        engagement, _ = flown
        pilot = engagement.bandit.pilot
        for channel in ("pitch", "roll", "yaw"):
            assert -1.0 <= getattr(pilot, channel) <= 1.0
        assert 0.0 <= engagement.bandit.throttle <= 1.0


class TestAdversaryTactics:
    def test_it_breaks_when_it_is_about_to_be_shot(self, fighter):
        """The one geometry with only one right answer."""
        adversary = Adversary(fighter, skill="ace", seed=3)
        adversary._recovery_floor = kt(200.0)
        speed = 220.0
        # Someone directly astern at gun range, nose on.
        own = make_state([0.0, 0.0, -4000.0], [speed, 0.0, 0.0])
        attacker = make_state([-400.0, 0.0, -4000.0], [speed, 0.0, 0.0])
        own.derived.vtas = speed

        geometry = observe(own, attacker)
        assert geometry.defensive

        manoeuvre, reason = adversary._choose(geometry, own, attacker, None)
        assert manoeuvre is Manoeuvre.BREAK
        assert "gun" in reason or "break" in reason.lower()

    def test_it_does_not_scissors_at_five_hundred_knots(self, fighter):
        """A scissors is a slow fight; the speed test is what makes it one.

        Without it the trigger fires on a fast line-abreast merge, which
        throws away every joule the aircraft has for no reason -- the first
        thing the engagement logs showed it doing.
        """
        adversary = Adversary(fighter, skill="ace", seed=3)
        adversary._corner = kt(480.0)
        adversary._recovery_floor = kt(200.0)

        # Line abreast, 400 m apart, no closure: the geometry of a scissors.
        speed = kt(500.0)
        own = make_state([0.0, 0.0, -4000.0], [speed, 0.0, 0.0])
        own.derived.vtas = speed
        other = make_state([0.0, 400.0, -4000.0], [speed, 0.0, 0.0])
        geometry = observe(own, other)

        manoeuvre, _ = adversary._choose(geometry, own, other, None)
        assert manoeuvre is not Manoeuvre.SCISSORS

        # The same geometry slowly is a scissors.
        slow = kt(200.0)
        own_slow = make_state([0.0, 0.0, -4000.0], [slow, 0.0, 0.0])
        own_slow.derived.vtas = slow
        other_slow = make_state([0.0, 400.0, -4000.0], [slow, 0.0, 0.0])
        adversary._recovery_floor = kt(150.0)
        manoeuvre, _ = adversary._choose(
            observe(own_slow, other_slow), own_slow, other_slow, None
        )
        assert manoeuvre is Manoeuvre.SCISSORS

    def test_skill_changes_how_it_flies_and_not_what_it_flies(self):
        """Skill must not be a performance cheat.

        Every skill level shares one airframe and one set of limits. What
        differs is reaction time, decision rate, precision and discipline --
        the pilot, not the aeroplane.
        """
        for name, skill in SKILLS.items():
            assert 0.0 < skill.aggression <= 1.0, name
            assert skill.decision_interval > 0.0, name
            assert skill.reaction_time > 0.0, name
            assert 0.0 <= skill.discipline <= 1.0, name

        # And they are ordered: an ace reacts faster and flies tidier.
        assert SKILLS["ace"].reaction_time < SKILLS["novice"].reaction_time
        assert SKILLS["ace"].sloppiness < SKILLS["novice"].sloppiness
        assert SKILLS["ace"].discipline > SKILLS["novice"].discipline

    def test_the_alpha_limit_leaves_margin_to_the_stall(self):
        assert 0.0 < ALPHA_LIMIT < 1.0


class TestEngagementSetup:
    def test_both_aircraft_fly_the_same_world(self, fighter):
        """One terrain and one weather, not two rolls of the same dice.

        A bandit flying over different hills, or through a different gust
        field, is not in the same fight -- and the terrain is shared by
        reference so there is no way for the two to drift apart.
        """
        conditions = fight_conditions()
        player = Simulation(fighter, conditions)
        engagement = Engagement(player, conditions, setup=Setup.NEUTRAL)

        assert engagement.bandit.terrain is player.terrain
        assert engagement.bandit.fdm.terrain is player.terrain
        assert engagement.bandit.weather is player.weather

    @pytest.mark.parametrize(
        "setup", [Setup.NEUTRAL, Setup.OFFENSIVE, Setup.DEFENSIVE, Setup.HEAD_ON]
    )
    def test_every_setup_starts_in_the_geometry_it_is_named_for(self, fighter, setup):
        conditions = fight_conditions()
        player = Simulation(fighter, conditions)
        engagement = Engagement(player, conditions, setup=setup)
        state = engagement.step(player, player.clock.dt)
        geometry = state.geometry

        assert geometry is not None
        assert geometry.range > 0.0
        if setup == Setup.OFFENSIVE:
            # Behind it and pointing at it.
            assert geometry.angle_off_tail < deg(30.0)
            assert geometry.antenna_train < deg(30.0)
        elif setup == Setup.DEFENSIVE:
            # It is behind us.
            assert state.bandit_geometry.angle_off_tail < deg(30.0)
        elif setup == Setup.HEAD_ON:
            assert geometry.closing
            assert geometry.aspect < deg(30.0)
        else:  # NEUTRAL: line abreast, so each is off the other's beam
            assert deg(60.0) < geometry.angle_off_tail < deg(120.0)

    def test_the_bandit_starts_airborne_and_flying(self, fighter):
        conditions = fight_conditions()
        player = Simulation(fighter, conditions)
        engagement = Engagement(player, conditions, setup=Setup.NEUTRAL)

        bandit = engagement.bandit.fdm.state
        assert bandit.altitude > ft(1000)
        assert bandit.derived.vtas > kt(150)
        assert not engagement.bandit.fdm.state.on_ground

    def test_the_debrief_says_what_happened(self, fighter):
        engagement, _ = fly_engagement(fighter, Setup.NEUTRAL, seconds=20.0)
        lines = engagement.debrief()

        assert any("Engagement" in line for line in lines)
        assert any("Result:" in line for line in lines)
        assert all(isinstance(line, str) for line in lines)

    def test_a_fight_that_separates_ends(self, fighter):
        """The engagement must terminate rather than run for ever."""
        conditions = fight_conditions()
        player = Simulation(fighter, conditions)
        engagement = Engagement(player, conditions, setup=Setup.NEUTRAL)
        # Teleport the bandit well beyond the separation limit.
        engagement.bandit.fdm.state.x[0] = 60000.0
        engagement.step(player, player.clock.dt)

        assert engagement.state.finished

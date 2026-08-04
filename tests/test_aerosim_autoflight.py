"""Autoflight: auto take-off, climb, cruise and autoland.

These are slow because they fly whole flights, which is the only way to test
an autoland: a landing is the product of everything that happened before it.
"""

from __future__ import annotations

import math
import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aerosim.control.autoflight import AutolandConfig, Phase, Runway
from aerosim.core.model_package import load_aircraft
from aerosim.core.orchestrator import Simulation
from aerosim.core.units import deg, ft, kt
from aerosim.game.config import SimConditions, StartMode

DATA_ROOT = Path(__file__).resolve().parent.parent / "aerosim" / "data" / "aircraft"
PACKAGES = ["aeroliner_200", "aerofalcon_x"]


@pytest.fixture(scope="module", params=PACKAGES)
def model(request):
    return load_aircraft(DATA_ROOT / request.param)


def fly_until(sim, phase: Phase, limit: float = 1500.0) -> bool:
    """Step until the autoflight reaches ``phase``, or give up."""
    for _ in range(int(limit / sim.clock.dt)):
        sim.step()
        if sim.crashed:
            return False
        if sim.autoflight.phase is phase:
            return True
    return False


def land(sim, limit: float = 1500.0) -> dict:
    """Fly an armed autoland to a stop and report how it went."""
    touchdown = 0.0
    peak_g = 0.0
    max_bank = 0.0
    for _ in range(int(limit / sim.clock.dt)):
        sim.step()
        if not sim.fdm.state.on_ground:
            peak_g = max(peak_g, sim.fdm.state.derived.load_factor)
            max_bank = max(max_bank, abs(sim.fdm.state.derived.roll))
        if sim.fdm.diagnostics.touchdown_rate > 0.0 and touchdown == 0.0:
            touchdown = sim.fdm.diagnostics.touchdown_rate
        if sim.crashed or sim.autoflight.phase is Phase.DONE:
            break
    along, cross = sim.autoflight.runway.track(sim.fdm.state.position)
    return {
        "crashed": sim.crashed,
        "reason": sim.crash_reason,
        "phase": sim.autoflight.phase,
        "touchdown": touchdown,
        "peak_g": peak_g,
        "max_bank": max_bank,
        "along": along,
        "cross": cross,
        "ground_speed": sim.fdm.state.derived.ground_speed,
        "time": sim.time,
    }


# --------------------------------------------------------------------------
# Geometry
# --------------------------------------------------------------------------


class TestRunwayGeometry:
    def test_along_and_cross_track(self):
        runway = Runway(north=0.0, east=0.0, heading=0.0)
        along, cross = runway.track([-1000.0, 0.0, 0.0])
        assert along == pytest.approx(-1000.0)  # before the threshold
        assert cross == pytest.approx(0.0)

        along, cross = runway.track([-1000.0, 250.0, 0.0])
        assert cross == pytest.approx(250.0)  # right of the centreline

    def test_track_rotates_with_the_runway(self):
        runway = Runway(heading=math.pi / 2)  # pointing east
        along, cross = runway.track([0.0, -800.0, 0.0])
        assert along == pytest.approx(-800.0)
        assert cross == pytest.approx(0.0, abs=1e-9)

    def test_glidepath_height_falls_to_the_threshold(self):
        runway = Runway(elevation=100.0, glidepath=math.radians(3.0))
        assert runway.glidepath_altitude(0.0) == pytest.approx(100.0)
        far = runway.glidepath_altitude(-10000.0)
        assert far == pytest.approx(100.0 + 10000.0 * math.tan(math.radians(3.0)))
        # Past the threshold the path does not rise again.
        assert runway.glidepath_altitude(500.0) == pytest.approx(100.0)


class TestConfig:
    def test_defaults_load_without_an_autopilot_section(self, model):
        assert AutolandConfig.from_model(model).flare_height > 0.0

    def test_each_aircraft_declares_its_own_autoland(self, model):
        config = AutolandConfig.from_model(model)
        assert 1.1 < config.vref_factor < 1.5
        assert 10.0 < config.flare_height < 80.0
        assert config.capture_angle < math.radians(5.0)

    def test_the_two_aircraft_differ(self):
        airliner = AutolandConfig.from_model(load_aircraft(DATA_ROOT / "aeroliner_200"))
        fighter = AutolandConfig.from_model(load_aircraft(DATA_ROOT / "aerofalcon_x"))
        assert fighter.flare_height < airliner.flare_height


# --------------------------------------------------------------------------
# Autoland
# --------------------------------------------------------------------------


class TestAutoland:
    def test_lands_from_the_approach_start(self, model):
        sim = Simulation(
            model,
            SimConditions(aircraft=model.name, start_mode=StartMode.APPROACH, seed=4),
        )
        sim.engage_autoland()
        result = land(sim)

        assert not result["crashed"], result["reason"]
        assert result["phase"] is Phase.DONE
        assert 0.0 < result["touchdown"] < model.get("limitations", "max_touchdown_rate")
        assert abs(result["cross"]) < 15.0, "stopped off the centreline"
        assert 0.0 < result["along"] < 3200.0, "did not stop on the runway"
        assert result["ground_speed"] < kt(12.0)

    def test_touchdown_is_gentle_and_inside_the_load_limit(self, model):
        sim = Simulation(
            model,
            SimConditions(aircraft=model.name, start_mode=StartMode.APPROACH, seed=4),
        )
        sim.engage_autoland()
        result = land(sim)
        # A firm arrival still passes the gear check; this asserts the flare is
        # actually flaring rather than merely surviving.
        assert result["touchdown"] < 2.0
        limit = model.get("limitations", "load_factor_positive_flaps", 2.5)
        assert result["peak_g"] < limit

    def test_routes_to_the_runway_from_the_wrong_direction(self, model):
        # Over the threshold at altitude, pointing across the runway. The
        # aircraft has to fly away, line up and come back.
        sim = Simulation(
            model,
            SimConditions(
                aircraft=model.name,
                start_mode=StartMode.AIRBORNE,
                altitude=ft(10000),
                heading=deg(90.0),
                seed=6,
            ),
        )
        sim.autoflight.runway.heading = 0.0
        sim.engage_autoland()
        result = land(sim, limit=1500.0)

        assert not result["crashed"], result["reason"]
        assert result["phase"] is Phase.DONE
        assert abs(result["cross"]) < 15.0

    def test_does_not_bank_steeply_near_the_ground(self, model):
        # The bank limit closes down on short final. Without it a late
        # centreline correction puts a wingtip into the runway.
        sim = Simulation(
            model,
            SimConditions(
                aircraft=model.name,
                start_mode=StartMode.AIRBORNE,
                altitude=ft(10000),
                heading=deg(90.0),
                seed=6,
            ),
        )
        sim.autoflight.runway.heading = 0.0
        sim.engage_autoland()

        worst = 0.0
        for _ in range(int(1500.0 / sim.clock.dt)):
            sim.step()
            derived = sim.fdm.state.derived
            if 0.0 < derived.altitude_agl < 60.0 and not sim.fdm.state.on_ground:
                worst = max(worst, abs(derived.roll))
            if sim.crashed or sim.autoflight.phase is Phase.DONE:
                break
        assert worst < deg(10.0), f"banked {math.degrees(worst):.0f} deg on short final"

    def test_goes_around_rather_than_landing_off_the_side(self, model):
        # Dropped onto short final well off the centreline, which is exactly
        # the case that used to end as a wing strike.
        conditions = SimConditions(
            aircraft=model.name, start_mode=StartMode.APPROACH, seed=4
        )
        sim = Simulation(model, conditions)
        sim.engage_autoland()
        fly_until(sim, Phase.APPROACH, limit=60.0)

        # Displace it 300 m sideways at 60 m, below minimums and far too wide.
        state = sim.fdm.state
        state.x[1] += 300.0
        state.x[2] = -(sim.conditions.field_elevation + 60.0)

        went_around = False
        for _ in range(int(90.0 / sim.clock.dt)):
            sim.step()
            if sim.autoflight.phase is Phase.GO_AROUND:
                went_around = True
                break
            if sim.crashed:
                break
        assert went_around, "pressed on instead of going around"
        assert not sim.crashed

    def test_landing_is_reproducible(self, model):
        def once():
            sim = Simulation(
                model,
                SimConditions(
                    aircraft=model.name,
                    start_mode=StartMode.APPROACH,
                    turbulence="light",
                    seed=21,
                ),
            )
            sim.engage_autoland()
            return land(sim)["touchdown"]

        assert once() == pytest.approx(once(), abs=1e-9)


# --------------------------------------------------------------------------
# Auto take-off and the whole flight
# --------------------------------------------------------------------------


class TestAutoFlight:
    def test_takes_off_by_itself(self, model):
        sim = Simulation(
            model,
            SimConditions(aircraft=model.name, start_mode=StartMode.RUNWAY, seed=8),
        )
        sim.engage_autoflight()
        assert sim.autoflight.phase is Phase.TAKEOFF

        assert fly_until(sim, Phase.CLIMB, limit=180.0), "never got airborne"
        assert not sim.crashed
        assert sim.fdm.state.derived.altitude_agl > ft(30.0)
        assert not sim.pilot.gear_down, "gear left down after take-off"

    def test_climbs_to_cruise_and_levels(self, model):
        sim = Simulation(
            model,
            SimConditions(aircraft=model.name, start_mode=StartMode.RUNWAY, seed=8),
        )
        sim.engage_autoflight()
        sim.autoflight.cruise_altitude = ft(6000)
        assert fly_until(sim, Phase.CRUISE, limit=900.0), "never reached cruise"
        assert not sim.crashed
        assert abs(sim.fdm.state.derived.altitude - ft(6000)) < ft(400)

    def test_holds_the_runway_centreline_on_the_take_off_roll(self, model):
        sim = Simulation(
            model,
            SimConditions(
                aircraft=model.name,
                start_mode=StartMode.RUNWAY,
                wind_speed=kt(12.0),
                wind_direction=deg(60.0),
                seed=8,
            ),
        )
        sim.engage_autoflight()
        worst = 0.0
        for _ in range(int(120.0 / sim.clock.dt)):
            sim.step()
            if sim.fdm.state.on_ground:
                _, cross = sim.autoflight.runway.track(sim.fdm.state.position)
                worst = max(worst, abs(cross))
            elif sim.autoflight.phase is Phase.CLIMB:
                break
        assert not sim.crashed, sim.crash_reason
        assert worst < 25.0, f"wandered {worst:.0f} m off the centreline in a crosswind"

    def test_engaging_airborne_holds_present_condition(self, model):
        sim = Simulation(
            model,
            SimConditions(
                aircraft=model.name, start_mode=StartMode.AIRBORNE, altitude=ft(12000)
            ),
        )
        altitude = sim.fdm.state.derived.altitude
        sim.engage_autoflight()
        assert sim.autoflight.phase is Phase.CRUISE
        for _ in range(int(90.0 / sim.clock.dt)):
            sim.step()
        assert abs(sim.fdm.state.derived.altitude - altitude) < ft(300)

    def test_pilot_input_disconnects_it(self, model):
        sim = Simulation(
            model,
            SimConditions(aircraft=model.name, start_mode=StartMode.AIRBORNE),
        )
        sim.engage_autoflight()
        assert sim.autoflight.engaged
        sim.pilot.roll = 0.6
        sim.step()
        assert not sim.autoflight.engaged
        assert any("disconnected" in e.message for e in sim.events)

    def test_disengage_hands_back_cleanly(self, model):
        sim = Simulation(
            model,
            SimConditions(aircraft=model.name, start_mode=StartMode.AIRBORNE),
        )
        sim.engage_autoflight()
        for _ in range(int(30.0 / sim.clock.dt)):
            sim.step()

        elevator = sim.surfaces.elevator.position
        sim.disengage_autoflight()
        for _ in range(int(1.0 / sim.clock.dt)):
            sim.step()
        assert sim.surfaces.elevator.position == pytest.approx(elevator, abs=0.12)
        assert not sim.autopilot.engaged

    def test_annunciation_reports_the_phase(self, model):
        sim = Simulation(
            model,
            SimConditions(aircraft=model.name, start_mode=StartMode.RUNWAY),
        )
        assert sim.autoflight.annunciation() == ""
        sim.engage_autoflight()
        assert "T/O" in sim.autoflight.annunciation()

    def test_autoland_on_the_ground_is_refused(self, model):
        sim = Simulation(
            model,
            SimConditions(aircraft=model.name, start_mode=StartMode.RUNWAY),
        )
        sim.engage_autoland()
        assert not sim.autoflight.landing_armed
        assert any("nothing to land" in e.message for e in sim.events)

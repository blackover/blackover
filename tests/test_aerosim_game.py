"""Orchestrator, autopilot, pre-flight configuration and the renderer.

These exercise the layers the pilot actually touches. The renderer tests run
against SDL's dummy video driver, so they need no display.
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

from aerosim.control.actuators import Actuator, ActuatorFailure, ControlSurfaces
from aerosim.control.autopilot import PID, Autopilot, VerticalMode
from aerosim.core.model_package import load_aircraft
from aerosim.core.orchestrator import Simulation
from aerosim.core.units import deg, ft, kt
from aerosim.game.config import FailureMode, SimConditions, StartMode
from aerosim.env.wind import TURBULENCE_PRESETS, WindField, WindLayer

DATA_ROOT = Path(__file__).resolve().parent.parent / "aerosim" / "data" / "aircraft"
PACKAGES = ["aeroliner_200", "aerofalcon_x"]


@pytest.fixture(scope="module", params=PACKAGES)
def model(request):
    return load_aircraft(DATA_ROOT / request.param)


def run(sim: Simulation, seconds: float) -> None:
    for _ in range(int(seconds / sim.clock.dt)):
        sim.step()


# --------------------------------------------------------------------------
# Wind
# --------------------------------------------------------------------------


class TestWind:
    def test_wind_from_north_blows_south(self):
        # A wind *from* 000 is a velocity vector pointing south.
        field = WindField.uniform(10.0, 0.0)
        wind = field.mean_wind_ned(0.0)
        assert wind[0] == pytest.approx(-10.0)
        assert wind[1] == pytest.approx(0.0)

    def test_wind_from_east_blows_west(self):
        field = WindField.uniform(10.0, math.pi / 2)
        wind = field.mean_wind_ned(0.0)
        assert wind[1] == pytest.approx(-10.0, abs=1e-9)

    def test_layers_interpolate_and_hold_beyond_the_ends(self):
        field = WindField(
            [WindLayer(0.0, 0.0, 10.0), WindLayer(1000.0, 0.0, 30.0)]
        )
        assert np.linalg.norm(field.mean_wind_ned(500.0)) == pytest.approx(20.0)
        assert np.linalg.norm(field.mean_wind_ned(-100.0)) == pytest.approx(10.0)
        assert np.linalg.norm(field.mean_wind_ned(9999.0)) == pytest.approx(30.0)

    def test_direction_interpolates_the_short_way(self):
        # 350 deg to 010 deg must pass through north, not sweep through south.
        field = WindField(
            [
                WindLayer(0.0, deg(350.0), 10.0),
                WindLayer(1000.0, deg(10.0), 10.0),
            ]
        )
        wind = field.mean_wind_ned(500.0)
        assert wind[0] == pytest.approx(-10.0, abs=1e-6)  # still from the north

    def test_turbulence_is_bounded_and_seed_reproducible(self):
        def gusts(seed):
            field = WindField.uniform(0.0, 0.0, turbulence_intensity=3.5, seed=seed)
            return [field.update_turbulence(0.01, 150.0, 500.0).copy() for _ in range(4000)]

        a, b, c = gusts(3), gusts(3), gusts(4)
        assert np.allclose(np.array(a), np.array(b), atol=0.0)
        assert not np.allclose(np.array(a), np.array(c))
        # Bounded: a shaping filter that walks away is a shaping filter with a
        # sign error in it.
        assert np.max(np.abs(np.array(a))) < 8.0 * 3.5

    def test_turbulence_intensity_falls_with_altitude(self):
        field = WindField.uniform(0.0, 0.0, turbulence_intensity=5.0, seed=1)
        assert field._intensity_at(9144.0) < field._intensity_at(0.0)

    def test_no_turbulence_means_no_gust(self):
        field = WindField.uniform(5.0, 0.0, turbulence_intensity=0.0, seed=1)
        assert np.allclose(field.update_turbulence(0.01, 150.0, 500.0), 0.0)

    def test_presets_are_ordered(self):
        values = list(TURBULENCE_PRESETS.values())
        assert all(b >= a for a, b in zip(values, values[1:]))


# --------------------------------------------------------------------------
# Actuators
# --------------------------------------------------------------------------


class TestActuators:
    def test_rate_limit_is_respected(self):
        actuator = Actuator("test", max_rate=1.0, time_constant=0.0)
        actuator.update(0.1, 1.0)
        assert actuator.position == pytest.approx(0.1)

    def test_reaches_the_command_eventually(self):
        actuator = Actuator("test", max_rate=2.0)
        for _ in range(500):
            actuator.update(0.01, 0.7)
        assert actuator.position == pytest.approx(0.7, abs=1e-4)

    def test_position_limits_hold(self):
        actuator = Actuator("test", max_rate=100.0, lower_limit=-0.5, upper_limit=0.5)
        for _ in range(100):
            actuator.update(0.01, 5.0)
        assert actuator.position == pytest.approx(0.5)

    def test_jam_freezes_the_surface(self):
        actuator = Actuator("test", max_rate=2.0)
        for _ in range(100):
            actuator.update(0.01, 0.4)
        frozen = actuator.position
        actuator.failure = ActuatorFailure.JAMMED
        for _ in range(500):
            actuator.update(0.01, -1.0)
        assert actuator.position == pytest.approx(frozen)

    def test_runaway_drives_to_the_hardover(self):
        actuator = Actuator("test", max_rate=2.0)
        actuator.failure = ActuatorFailure.RUNAWAY
        for _ in range(500):
            actuator.update(0.01, 0.0)
        assert actuator.position == pytest.approx(actuator.runaway_target, abs=1e-3)

    def test_hydraulic_pressure_scales_the_rate(self, model):
        surfaces = ControlSurfaces(model)
        surfaces.set_hydraulic_pressure(0.3)
        assert all(a.rate_scale == pytest.approx(0.3) for a in surfaces.actuators)

        fast, slow = ControlSurfaces(model), ControlSurfaces(model)
        slow.set_hydraulic_pressure(0.3)
        for _ in range(20):
            fast.elevator.update(0.01, 1.0)
            slow.elevator.update(0.01, 1.0)
        assert slow.elevator.position < fast.elevator.position

    def test_gear_takes_its_declared_transit_time(self, model):
        surfaces = ControlSurfaces(model)
        surfaces.gear_position = 1.0
        steps = int(surfaces.gear_transit_time / 0.01)
        for _ in range(steps // 2):
            surfaces.update(0.01, elevator=0, aileron=0, rudder=0, flap=0,
                            speedbrake=0, gear_down=False)
        assert surfaces.gear_in_transit
        assert not surfaces.gear_effective_down
        for _ in range(steps):
            surfaces.update(0.01, elevator=0, aileron=0, rudder=0, flap=0,
                            speedbrake=0, gear_down=False)
        assert surfaces.gear_position == pytest.approx(0.0)


# --------------------------------------------------------------------------
# Autopilot
# --------------------------------------------------------------------------


class TestAutopilot:
    def test_pid_output_is_clamped(self):
        pid = PID(kp=100.0, output_min=-1.0, output_max=1.0)
        assert pid.update(50.0, 0.01) == pytest.approx(1.0)

    def test_integrator_does_not_wind_up_while_saturated(self):
        # A long climb at a pitch limit must not leave the integrator charged
        # with seconds of error that has to unwind before anything responds.
        pid = PID(kp=1.0, ki=1.0, output_min=-1.0, output_max=1.0)
        for _ in range(1000):
            pid.update(10.0, 0.01)
        assert pid.integral < 1.0

    def test_modes_annunciate(self, model):
        autopilot = Autopilot(model)
        assert autopilot.annunciation() == "AP OFF"
        autopilot.hold_altitude(3000.0)
        autopilot.hold_heading(deg(90.0))
        assert "ALT" in autopilot.annunciation()
        assert "HDG" in autopilot.annunciation()
        autopilot.disengage()
        assert not autopilot.engaged

    def test_altitude_hold_captures_and_holds(self, model):
        conditions = SimConditions(
            aircraft=model.name, start_mode=StartMode.AIRBORNE, altitude=ft(15000)
        )
        sim = Simulation(model, conditions)
        target = sim.fdm.state.derived.altitude + ft(1200)
        sim.autopilot.hold_altitude(target)
        sim.autopilot.hold_speed(sim.fdm.state.derived.vcas)
        run(sim, 200.0)
        assert not sim.crashed
        assert abs(sim.fdm.state.derived.altitude - target) < ft(180)

    def test_heading_hold_turns_onto_the_target(self, model):
        conditions = SimConditions(
            aircraft=model.name, start_mode=StartMode.AIRBORNE, altitude=ft(15000)
        )
        sim = Simulation(model, conditions)
        sim.autopilot.hold_altitude(sim.fdm.state.derived.altitude)
        sim.autopilot.hold_heading(deg(70.0))
        run(sim, 160.0)
        assert not sim.crashed
        error = abs(math.degrees(sim.fdm.state.derived.yaw) % 360 - 70.0)
        assert min(error, 360 - error) < 6.0

    def test_speed_hold_converges(self, model):
        conditions = SimConditions(
            aircraft=model.name, start_mode=StartMode.AIRBORNE, altitude=ft(15000)
        )
        sim = Simulation(model, conditions)
        target = sim.fdm.state.derived.vcas - kt(35)
        sim.autopilot.hold_altitude(sim.fdm.state.derived.altitude)
        sim.autopilot.hold_speed(target)
        run(sim, 240.0)
        assert abs(sim.fdm.state.derived.vcas - target) < kt(12)

    def test_handover_is_bumpless(self, model):
        # The autopilot's elevator is mostly integrator state. Dropping it on
        # disengage steps the surface by whatever had accumulated, and the
        # pilot feels a jolt at the exact moment they take over.
        conditions = SimConditions(
            aircraft=model.name, start_mode=StartMode.AIRBORNE, altitude=ft(12000)
        )
        sim = Simulation(model, conditions)
        sim.autopilot.hold_altitude(sim.fdm.state.derived.altitude + ft(700))
        sim.autopilot.hold_speed(sim.fdm.state.derived.vcas)
        run(sim, 40.0)  # let the integrator charge on the climb

        elevator_before = sim.surfaces.elevator.position
        sim.handover_trim()
        sim.autopilot.disengage()
        run(sim, 1.0)
        assert sim.surfaces.elevator.position == pytest.approx(
            elevator_before, abs=0.12
        ), "elevator stepped on handover"

    def test_handover_stores_what_the_autopilot_held(self, model):
        # The invariant the fix establishes. How large a step it avoids depends
        # on the aircraft and the manoeuvre -- on the fighter, whose trim
        # elevator is already near the autopilot's, it is small -- so the test
        # asserts what is always true rather than a difference that is not.
        conditions = SimConditions(
            aircraft=model.name, start_mode=StartMode.AIRBORNE, altitude=ft(12000)
        )
        sim = Simulation(model, conditions)
        sim.autopilot.hold_altitude(sim.fdm.state.derived.altitude + ft(4000))
        sim.autopilot.hold_speed(sim.fdm.state.derived.vcas)
        run(sim, 12.0)  # still climbing, integrator charged

        held = sim._autopilot_elevator
        sim.handover_trim()
        assert sim.pitch_trim == pytest.approx(held, abs=1e-9)

    def test_handover_is_a_no_op_when_vertical_is_off(self, model):
        # Nothing to hand over if the autopilot was not flying the pitch axis.
        sim = Simulation(model, SimConditions(aircraft=model.name))
        sim.pitch_trim = 0.123
        sim.handover_trim()
        assert sim.pitch_trim == pytest.approx(0.123)

    def test_pilot_input_disengages_the_axis(self, model):
        conditions = SimConditions(aircraft=model.name, start_mode=StartMode.AIRBORNE)
        sim = Simulation(model, conditions)
        sim.engage_altitude_hold()
        assert sim.autopilot.engaged
        sim.pilot.pitch = 0.5
        sim.step()
        assert sim.autopilot.vertical is VerticalMode.OFF


# --------------------------------------------------------------------------
# Pre-flight configuration
# --------------------------------------------------------------------------


class TestConditions:
    def test_defaults_are_inside_the_envelope(self, model):
        conditions = SimConditions(aircraft=model.name)
        assert conditions.validate(model) == []

    def test_overspeed_start_is_warned_not_blocked(self, model):
        conditions = SimConditions(
            aircraft=model.name, start_mode=StartMode.AIRBORNE, airspeed=kt(900)
        )
        warnings = conditions.validate(model)
        assert any("VMO" in w for w in warnings)
        # A warning, not a block: refusing to start would hide the very
        # behaviour someone setting up an unusual condition wants to see.
        assert Simulation(model, conditions) is not None

    def test_start_below_stall_speed_is_warned(self, model):
        conditions = SimConditions(
            aircraft=model.name, start_mode=StartMode.AIRBORNE, airspeed=kt(85)
        )
        assert any("stall" in w for w in conditions.validate(model))

    def test_above_ceiling_is_warned(self, model):
        conditions = SimConditions(
            aircraft=model.name, start_mode=StartMode.AIRBORNE, altitude=ft(60000)
        )
        assert any("ceiling" in w for w in conditions.validate(model))

    def test_coarse_step_is_warned(self, model):
        conditions = SimConditions(aircraft=model.name, dt=0.02)
        assert any("validated" in w for w in conditions.validate(model))

    def test_military_flag_matches_the_package(self):
        assert SimConditions(aircraft="aerofalcon_x").is_military
        assert not SimConditions(aircraft="aeroliner_200").is_military


# --------------------------------------------------------------------------
# Orchestrator
# --------------------------------------------------------------------------


class TestOrchestrator:
    @pytest.mark.parametrize(
        "start", [StartMode.RUNWAY, StartMode.AIRBORNE, StartMode.APPROACH]
    )
    def test_every_start_mode_is_survivable_hands_off(self, model, start):
        conditions = SimConditions(aircraft=model.name, start_mode=start)
        sim = Simulation(model, conditions)
        run(sim, 30.0)
        assert not sim.crashed, sim.crash_reason

    def test_runway_start_sits_still_and_reports_no_touchdown(self, model):
        conditions = SimConditions(aircraft=model.name, start_mode=StartMode.RUNWAY)
        sim = Simulation(model, conditions)
        sim.pilot.brake = 1.0
        run(sim, 20.0)
        assert sim.fdm.state.on_ground
        assert not any("TOUCHDOWN" in e.message for e in sim.events)
        assert sim.fdm.state.derived.ground_speed < 1.0

    def test_airborne_start_is_trimmed(self, model):
        conditions = SimConditions(
            aircraft=model.name, start_mode=StartMode.AIRBORNE, altitude=ft(20000)
        )
        sim = Simulation(model, conditions)
        start = sim.fdm.state.derived.altitude
        run(sim, 90.0)
        assert abs(sim.fdm.state.derived.altitude - start) < ft(120)

    def test_approach_start_descends_on_the_path(self, model):
        conditions = SimConditions(aircraft=model.name, start_mode=StartMode.APPROACH)
        sim = Simulation(model, conditions)
        run(sim, 25.0)
        assert sim.fdm.state.derived.vertical_speed < 0.0
        gamma = math.degrees(sim.fdm.state.derived.gamma)
        assert -6.0 < gamma < -1.0

    def test_approach_speed_is_computed_not_taken_from_cruise(self, model):
        # Using the cruise setting would put the aircraft over the threshold
        # far past VFE with full flap, and no trim solution but a dive.
        conditions = SimConditions(
            aircraft=model.name, start_mode=StartMode.APPROACH, airspeed=kt(300)
        )
        sim = Simulation(model, conditions)
        vfe = model.get("limitations", "vfe")
        assert sim.fdm.state.derived.vcas < vfe

    def test_takeoff_roll_accelerates_and_flies(self, model):
        conditions = SimConditions(aircraft=model.name, start_mode=StartMode.RUNWAY)
        sim = Simulation(model, conditions)
        rotate = 1.15 * sim.fdm.aero.stall_speed(
            sim.fdm.mass_properties.mass, 1.225, flap=sim.pilot.flap
        )
        for _ in range(int(90.0 / sim.clock.dt)):
            sim.throttle = 1.0
            if sim.fdm.state.derived.vcas > rotate:
                sim.pilot.pitch = min(0.45, sim.pilot.pitch + 0.01)
            sim.step()
            if sim.fdm.state.derived.altitude_agl > ft(200):
                break
        assert sim.fdm.state.derived.altitude_agl > ft(200), "never got airborne"
        assert not sim.crashed, sim.crash_reason

    def test_determinism_bit_for_bit(self, model):
        def fly():
            conditions = SimConditions(
                aircraft=model.name,
                start_mode=StartMode.AIRBORNE,
                turbulence="moderate",
                wind_speed=kt(25),
                seed=42,
            )
            sim = Simulation(model, conditions)
            for i in range(2000):
                sim.pilot.roll = 0.3 * math.sin(i * 0.01)
                sim.step()
            return sim.fdm.state.x.copy()

        assert np.array_equal(fly(), fly())

    def test_step_index_and_time_stay_exact(self, model):
        sim = Simulation(model, SimConditions(aircraft=model.name))
        run(sim, 60.0)
        assert sim.clock.step_index == 6000
        assert sim.time == 600 * 0.1  # 60.0 exactly, via multiplication

    def test_engine_failure_fires_at_the_scheduled_time(self, model):
        conditions = SimConditions(
            aircraft=model.name,
            start_mode=StartMode.AIRBORNE,
            failure=FailureMode.ENGINE_OUT,
            failure_time=5.0,
        )
        sim = Simulation(model, conditions)
        run(sim, 4.0)
        assert not any(e.failed for e in sim.fdm.propulsion.engines)
        run(sim, 3.0)
        assert sim.fdm.propulsion.engines[0].failed
        assert any("ENGINE 1 FAILURE" in e.message for e in sim.events)

    def test_hydraulic_failure_degrades_control_rate(self, model):
        conditions = SimConditions(
            aircraft=model.name,
            start_mode=StartMode.AIRBORNE,
            failure=FailureMode.HYDRAULIC,
            failure_time=1.0,
        )
        sim = Simulation(model, conditions)
        run(sim, 20.0)
        assert sim.hydraulic_pressure < 0.5
        assert sim.surfaces.elevator.rate_scale < 0.5

    def test_elevator_jam_freezes_the_surface(self, model):
        conditions = SimConditions(
            aircraft=model.name,
            start_mode=StartMode.AIRBORNE,
            failure=FailureMode.ELEVATOR_JAM,
            failure_time=1.0,
        )
        sim = Simulation(model, conditions)
        run(sim, 2.0)
        jammed = sim.surfaces.elevator.position
        for _ in range(500):
            sim.pilot.pitch = 1.0
            sim.step()
        assert sim.surfaces.elevator.position == pytest.approx(jammed)

    def test_fuel_leak_drains_the_tanks(self, model):
        conditions = SimConditions(
            aircraft=model.name,
            start_mode=StartMode.AIRBORNE,
            failure=FailureMode.FUEL_LEAK,
            failure_time=0.0,
        )
        sim = Simulation(model, conditions)
        before = sim.fdm.mass_model.fuel_mass
        run(sim, 60.0)
        assert sim.fdm.mass_model.fuel_mass < before * 0.98

    def test_no_failure_stays_healthy(self, model):
        sim = Simulation(model, SimConditions(aircraft=model.name))
        run(sim, 30.0)
        assert sim.hydraulic_pressure > 0.95
        assert not any(e.failed for e in sim.fdm.propulsion.engines)

    def test_event_log_throttles_repeats(self, model):
        # A stall lasting ten seconds is one event, not a thousand lines.
        sim = Simulation(model, SimConditions(aircraft=model.name))
        for _ in range(500):
            sim.log("WARNING", "STALL")
            sim.clock.advance()
        assert sum(1 for e in sim.events if e.message == "STALL") < 5

    def test_envelope_cautions_throttle_despite_varying_text(self, model):
        # The message quotes a Mach number, so no two are textually identical.
        sim = Simulation(model, SimConditions(aircraft=model.name))
        for i in range(500):
            sim.log("CAUTION", f"ENVELOPE: M 0.{850 + i}", key="envelope")
            sim.clock.advance()
        assert sum(1 for e in sim.events if "ENVELOPE" in e.message) < 5


# --------------------------------------------------------------------------
# Renderer -- viewer only, never a participant
# --------------------------------------------------------------------------


class TestRenderer:
    @pytest.fixture(scope="class")
    def screen(self):
        import pygame

        pygame.init()
        surface = pygame.display.set_mode((800, 600))
        yield surface
        pygame.quit()

    def test_near_plane_clip_drops_polygons_behind_the_eye(self):
        from aerosim.game.renderer import Renderer

        behind = np.array([[0.0, 0.0, -5.0], [1.0, 0.0, -5.0], [1.0, 1.0, -5.0]])
        assert Renderer.clip_near(behind) is None

    def test_near_plane_clip_splits_a_straddling_polygon(self):
        from aerosim.game.renderer import NEAR_PLANE, Renderer

        straddling = np.array([[0.0, 0.0, 5.0], [1.0, 0.0, -5.0], [1.0, 1.0, 5.0]])
        clipped = Renderer.clip_near(straddling)
        assert clipped is not None
        # Every surviving vertex is in front of the eye, which is what stops a
        # single behind-camera vertex painting a triangle across the screen.
        assert np.all(clipped[:, 2] >= NEAR_PLANE - 1e-9)

    def test_clip_leaves_a_fully_visible_polygon_alone(self):
        from aerosim.game.renderer import Renderer

        visible = np.array([[0.0, 0.0, 5.0], [1.0, 0.0, 5.0], [1.0, 1.0, 5.0]])
        assert np.array_equal(Renderer.clip_near(visible), visible)

    def test_projection_puts_the_axis_at_the_viewport_centre(self, screen):
        from aerosim.game.renderer import Renderer, Sky

        renderer = Renderer(screen, Sky(12.0, 30000.0))
        import pygame

        renderer.resize_view(pygame.Rect(0, 0, 800, 400))
        point = renderer.project(np.array([[0.0, 0.0, 100.0]]))[0]
        assert point[0] == pytest.approx(400.0)
        assert point[1] == pytest.approx(200.0)

    def test_meshes_build_for_both_aircraft(self, model):
        from aerosim.game.mesh import build_mesh, gear_facets

        facets = build_mesh(model)
        assert len(facets) > 20
        assert all(f.points.shape[1] == 3 for f in facets)
        assert all(np.all(np.isfinite(f.points)) for f in facets)
        assert gear_facets(model, 0.0) == []
        assert len(gear_facets(model, 1.0)) >= 6

    def test_mesh_spans_the_aircraft_it_describes(self, model):
        # The rendered wingtip must be the wingtip the crash check tested.
        from aerosim.game.mesh import build_mesh

        span = max(
            abs(float(p[1])) for f in build_mesh(model) for p in f.points
        )
        assert span == pytest.approx(0.5 * model.get("geometry", "wing_span"), rel=0.12)

    def test_a_full_frame_renders_without_error(self, screen, model):
        import pygame

        from aerosim.core.frames import dcm_body_to_ned
        from aerosim.game.mesh import build_mesh
        from aerosim.game.renderer import Renderer, Runway, Sky

        conditions = SimConditions(aircraft=model.name, start_mode=StartMode.AIRBORNE)
        sim = Simulation(model, conditions)
        run(sim, 2.0)

        renderer = Renderer(screen, Sky(conditions.time_of_day, conditions.visibility))
        renderer.resize_view(pygame.Rect(0, 0, 800, 420))
        renderer.camera.follow(sim.fdm.state, 1 / 60)
        renderer.draw_sky()
        renderer.draw_terrain(sim.fdm.state, sim.terrain, Runway())
        renderer.draw_aircraft(
            sim.fdm.state, build_mesh(model), dcm_body_to_ned(sim.fdm.state.quaternion)
        )
        assert screen.get_at((400, 100)) is not None

    def test_renderer_does_not_touch_the_state(self, screen, model):
        # The kernel is the sole authority on aircraft state; the animation is
        # a viewer, never a participant.
        import pygame

        from aerosim.core.frames import dcm_body_to_ned
        from aerosim.game.mesh import build_mesh
        from aerosim.game.renderer import Renderer, Runway, Sky

        sim = Simulation(model, SimConditions(aircraft=model.name))
        run(sim, 1.0)
        before = sim.fdm.state.x.copy()

        renderer = Renderer(screen, Sky(12.0, 30000.0))
        renderer.resize_view(pygame.Rect(0, 0, 800, 420))
        for _ in range(10):
            renderer.camera.follow(sim.fdm.state, 1 / 60)
            renderer.draw_sky()
            renderer.draw_terrain(sim.fdm.state, sim.terrain, Runway())
            renderer.draw_aircraft(
                sim.fdm.state,
                build_mesh(model),
                dcm_body_to_ned(sim.fdm.state.quaternion),
            )
        assert np.array_equal(sim.fdm.state.x, before)

    def test_terrain_rings_overlap_rather_than_leaving_a_gap(self):
        # Each ring snaps its grid to its own cell size, so two rings are never
        # aligned with each other. A hollow measured in the coarse ring's own
        # cells therefore lands up to a full coarse cell off the fine ring's
        # real footprint, and the base plane shows through the difference as a
        # band of bare ground straight across the middle distance.
        #
        # The invariant: everything the coarse ring hollows out is inside what
        # the fine ring actually paints, for every possible misalignment.
        from aerosim.game.renderer import RING_CELLS, RING_FACTOR, RING_LEVELS

        base = 137.0  # a cell size that snaps to nothing convenient
        for level in range(1, RING_LEVELS):
            fine = base * RING_FACTOR ** (level - 1)
            coarse = base * RING_FACTOR**level
            hole = RING_CELLS * fine - coarse

            for offset in np.linspace(0.0, coarse, 23):
                # Where the fine ring's vertices actually fall for this offset.
                fine_origin = math.floor(offset / fine)
                fine_low = (fine_origin - RING_CELLS) * fine
                fine_high = (fine_origin + RING_CELLS + 1) * fine

                # Every coarse cell whose whole extent is nearer than `hole` is
                # dropped; the furthest such cell must still be painted by the
                # fine ring.
                coarse_origin = math.floor(offset / coarse)
                for index in range(2 * RING_CELLS + 1):
                    low = (coarse_origin - RING_CELLS + index) * coarse
                    reach = max(abs(low - offset), abs(low + coarse - offset))
                    if reach < hole:
                        assert fine_low <= low
                        assert low + coarse <= fine_high

    def test_terrain_ring_grid_is_reused_until_the_aircraft_crosses_a_cell(
        self, screen, model
    ):
        import pygame

        from aerosim.game.renderer import Renderer, Sky

        sim = Simulation(
            model,
            SimConditions(aircraft=model.name, altitude=ft(9000), terrain="hilly"),
        )
        run(sim, 2.0)

        renderer = Renderer(screen, Sky(12.0, 30000.0))
        renderer.resize_view(pygame.Rect(0, 0, 800, 420))
        renderer.camera.follow(sim.fdm.state, 1 / 60)
        renderer.draw_terrain(sim.fdm.state, sim.terrain, None)
        grids = {key: value[1] for key, value in renderer._ring_cache.items()}
        assert grids

        renderer.draw_terrain(sim.fdm.state, sim.terrain, None)
        # Same position, same grid object -- the heightfield is not re-evaluated.
        for key, grid in grids.items():
            assert renderer._ring_cache[key][1] is grid

        # Far enough to cross every cell boundary: the grids must move with it.
        sim.fdm.state.x[0] += 400_000.0
        renderer.camera.follow(sim.fdm.state, 1 / 60)
        renderer.draw_terrain(sim.fdm.state, sim.terrain, None)
        for key, grid in grids.items():
            assert renderer._ring_cache[key][1] is not grid

    def test_panel_draws_for_both_aircraft(self, screen, model):
        import pygame

        from aerosim.game.instruments import Fonts, Panel

        sim = Simulation(model, SimConditions(aircraft=model.name))
        run(sim, 1.0)
        panel = Panel(pygame.Rect(0, 420, 800, 180), Fonts(1.0), model, sim)
        panel.draw(screen, sim)

    def test_setup_screen_draws_and_lists_both_aircraft(self, screen):
        from aerosim.game.setup_screen import SetupScreen

        setup = SetupScreen(screen, SimConditions())
        setup.draw()
        assert setup.model() is not None
        setup.conditions.aircraft = "aerofalcon_x"
        setup.draw()
        assert setup.model().category == "military"


# --------------------------------------------------------------------------
# Effects and strip charts -- also viewers, never participants
# --------------------------------------------------------------------------


class TestEffects:
    @pytest.fixture(scope="class")
    def screen(self):
        import pygame

        pygame.init()
        surface = pygame.display.set_mode((800, 600))
        yield surface
        pygame.quit()

    def test_trail_samples_by_distance_not_by_call(self):
        from aerosim.game.effects import TRAIL_SPACING, Trail

        trail = Trail()
        for step in range(40):
            trail.emit(np.array([step * TRAIL_SPACING / 4.0, 0.0, 0.0]), 1.0)
        # 40 calls covering ten spacings: a trail is a picture of a path, so
        # its point density must follow the path and not the frame rate.
        assert 9 <= len(trail.points) <= 12

    def test_trail_breaks_rather_than_bridging_a_gap(self):
        from aerosim.game.effects import TRAIL_SPACING, Trail

        trail = Trail()
        for step in range(6):
            trail.emit(np.array([step * TRAIL_SPACING, 0.0, 0.0]), 1.0)
        trail.emit(np.array([6 * TRAIL_SPACING, 0.0, 0.0]), 0.0)
        strengths = list(trail.strengths)
        assert strengths[-1] == 0.0
        assert all(value > 0.0 for value in strengths[:-1])

    def test_contrail_needs_cold_air(self, model):
        from aerosim.game.effects import Effects

        warm = Simulation(model, SimConditions(aircraft=model.name, altitude=ft(2000)))
        run(warm, 2.0)
        warm.throttle = 1.0
        run(warm, 8.0)
        low = Effects(model)
        for _ in range(60):
            low.update(warm.fdm.state, warm)
        assert not any(
            any(s > 0.0 for s in trail.strengths) for trail in low.contrails
        )

        cold = Simulation(model, SimConditions(aircraft=model.name, altitude=ft(37000)))
        run(cold, 2.0)
        cold.throttle = 1.0
        run(cold, 20.0)
        assert cold.fdm.state.derived.temperature < 233.15
        high = Effects(model)
        for _ in range(200):
            high.update(cold.fdm.state, cold)
            cold.step()
        assert any(any(s > 0.0 for s in trail.strengths) for trail in high.contrails)

    def test_trails_are_dropped_across_a_position_jump(self, model):
        from aerosim.game.effects import Effects

        sim = Simulation(model, SimConditions(aircraft=model.name, altitude=ft(37000)))
        run(sim, 2.0)
        sim.throttle = 1.0
        effects = Effects(model)
        for _ in range(300):
            effects.update(sim.fdm.state, sim)
            sim.step()
        assert any(len(trail.points) > 2 for trail in effects.contrails)

        # A restart puts the aircraft somewhere else; nothing flew between the
        # two positions, so there is no trail between them either.
        sim.fdm.state.x[0] += 50_000.0
        effects.update(sim.fdm.state, sim)
        assert all(len(trail.points) <= 1 for trail in effects.contrails)

    def test_effects_do_not_touch_the_state(self, screen, model):
        import pygame

        from aerosim.game.effects import Effects
        from aerosim.game.renderer import Renderer, Sky

        sim = Simulation(model, SimConditions(aircraft=model.name))
        run(sim, 2.0)
        sim.throttle = 1.0
        sim.pilot.afterburner = True
        run(sim, 4.0)

        renderer = Renderer(screen, Sky(12.0, 30000.0))
        renderer.resize_view(pygame.Rect(0, 0, 800, 420))
        renderer.camera.follow(sim.fdm.state, 1 / 60)
        effects = Effects(model)
        before = sim.fdm.state.x.copy()
        for _ in range(10):
            effects.update(sim.fdm.state, sim)
            effects.draw_trails(renderer)
            effects.draw_exhaust(renderer, sim.fdm.state, sim)
        assert np.array_equal(sim.fdm.state.x, before)


class TestCharts:
    @pytest.fixture(scope="class")
    def screen(self):
        import pygame

        pygame.init()
        surface = pygame.display.set_mode((900, 600))
        yield surface
        pygame.quit()

    def test_ring_keeps_the_newest_samples_in_order(self):
        from aerosim.game.charts import CAPACITY, Trace

        trace = Trace()
        for value in range(CAPACITY + 250):
            trace.push(float(value))
        window = trace.window(10)
        assert list(window) == [
            float(v) for v in range(CAPACITY + 240, CAPACITY + 250)
        ]
        assert trace.count == CAPACITY

    def test_sampling_follows_simulation_time_not_calls(self, model):
        from aerosim.game.charts import SAMPLE_HZ, FlightCharts
        from aerosim.game.instruments import Fonts

        import pygame

        pygame.init()
        charts = FlightCharts(model, Fonts(1.0))
        sim = Simulation(model, SimConditions(aircraft=model.name))
        for _ in range(int(4.0 / sim.clock.dt)):
            sim.step()
            charts.update(sim)  # called far more often than the sample rate
        count = charts.by_key["cas"].trace.count
        assert abs(count - 4.0 * SAMPLE_HZ) <= 2

    def test_history_is_dropped_when_time_runs_backwards(self, model):
        from aerosim.game.charts import FlightCharts
        from aerosim.game.instruments import Fonts

        import pygame

        pygame.init()
        charts = FlightCharts(model, Fonts(1.0))
        sim = Simulation(model, SimConditions(aircraft=model.name))
        for _ in range(int(6.0 / sim.clock.dt)):
            sim.step()
            charts.update(sim)
        assert charts.by_key["cas"].trace.count > 20

        # A replay seek or a restart: the buffer belongs to a different flight.
        sim.clock.step_index = 0
        charts.update(sim)
        assert charts.by_key["cas"].trace.count == 1

    def test_terrain_underlay_is_the_ground_below_the_flight(self, model):
        from aerosim.game.charts import FlightCharts
        from aerosim.game.instruments import Fonts
        from aerosim.core.units import to_ft

        import pygame

        pygame.init()
        charts = FlightCharts(model, Fonts(1.0))
        sim = Simulation(
            model,
            SimConditions(aircraft=model.name, altitude=ft(6000), terrain="mountainous"),
        )
        run(sim, 3.0)
        charts.update(sim)
        altitude = charts.by_key["altitude"]
        derived = sim.fdm.state.derived
        assert altitude.trace.window(1)[0] == pytest.approx(to_ft(derived.altitude))
        assert altitude.underlay.window(1)[0] == pytest.approx(
            to_ft(derived.altitude - derived.altitude_agl)
        )

    def test_charts_draw_and_do_not_touch_the_state(self, screen, model):
        import pygame

        from aerosim.game.charts import FlightCharts
        from aerosim.game.instruments import Fonts

        charts = FlightCharts(model, Fonts(1.0))
        sim = Simulation(model, SimConditions(aircraft=model.name))
        for _ in range(int(20.0 / sim.clock.dt)):
            sim.step()
            charts.update(sim)
        before = sim.fdm.state.x.copy()
        charts.draw_compact(screen, pygame.Rect(600, 420, 280, 170))
        charts.draw_overlay(screen, pygame.Rect(0, 0, 900, 420))
        assert np.array_equal(sim.fdm.state.x, before)

    def test_scale_shows_a_limit_the_trace_is_approaching(self, model):
        from aerosim.game.charts import FlightCharts
        from aerosim.game.instruments import Fonts

        import pygame

        pygame.init()
        charts = FlightCharts(model, Fonts(1.0))
        load = charts.by_key["load"]
        placard = model.get("limitations", "load_factor_positive", 2.5)

        # Pulling up to just short of the placard: it has to be on the axis,
        # or the chart cannot show how close the aircraft is to it.
        near = np.linspace(1.0, placard - 0.3, 50)
        low, high = charts._scale(load, [near])
        assert low <= placard <= high

        # A limit far from the trace does not stretch the axis to reach it.
        # Forcing a 9 g placard onto a 1 g cruise would squeeze the trace into
        # the bottom tenth of the box and hide the thing being plotted.
        steady = np.full(50, 1.0)
        low, high = charts._scale(load, [steady])
        reach = load.minimum_span * 1.4
        assert low <= 1.0 <= high
        assert high <= float(steady.max()) + reach
        assert low >= float(steady.min()) - reach


class TestPanelLayout:
    @pytest.fixture(scope="class")
    def screen(self):
        import pygame

        pygame.init()
        surface = pygame.display.set_mode((640, 480))
        yield surface
        pygame.quit()

    @pytest.mark.parametrize("width,height", [(800, 500), (960, 600), (1280, 720), (1920, 1080)])
    def test_panel_regions_never_overlap(self, screen, model, width, height):
        # The panel was laid out against one window size and placed the
        # attitude indicator at a fixed fraction of the width, so a smaller
        # window drew the status rows straight through the stick box. The
        # recorded demos run at 960x600, which is exactly where it showed.
        import pygame

        from aerosim.game.instruments import Fonts, Panel

        sim = Simulation(model, SimConditions(aircraft=model.name))
        run(sim, 1.0)
        panel = Panel(
            pygame.Rect(0, height - int(height * 0.28), width, int(height * 0.28)),
            Fonts(1.0),
            model,
            sim,
        )
        regions = [
            ("status", panel.status_rect),
            ("speed", panel.speed_rect),
            ("attitude", panel.ai_rect),
            ("altitude", panel.alt_rect),
            ("vsi", panel.vsi_rect),
            ("engines", panel.engine_rect),
        ]
        if panel.chart_rect.width:
            regions.append(("charts", panel.chart_rect))

        for i, (name_a, a) in enumerate(regions):
            assert a.width > 0 and a.height > 0, name_a
            for name_b, b in regions[i + 1 :]:
                assert not a.colliderect(b), f"{name_a} overlaps {name_b} at {width}x{height}"

        # The regions not colliding is not the whole invariant: the collision
        # was *inside* the status region, between its text rows and the stick
        # box drawn beside them. At 960 px it was 91 px wide and both were
        # drawn anyway. It has to be wide enough for what goes in it.
        assert panel.status_rect.width >= Panel.STATUS_WIDE

    @pytest.mark.parametrize("width", [700, 960, 1440])
    def test_panel_draws_at_any_width(self, screen, model, width):
        import pygame

        from aerosim.game.instruments import Fonts, Panel

        sim = Simulation(model, SimConditions(aircraft=model.name))
        run(sim, 1.0)
        surface = pygame.Surface((width, 500))
        panel = Panel(pygame.Rect(0, 360, width, 140), Fonts(1.0), model, sim)
        panel.draw(surface, sim)


class TestMeshGeometry:
    @pytest.mark.parametrize("aircraft", ["aeroliner_200", "aerofalcon_x"])
    def test_exhaust_ports_sit_at_the_back_of_the_drawn_engine(self, aircraft):
        # The propulsion package puts each engine at its *thrust* station,
        # which for a podded nacelle is in the middle of it -- two and a half
        # metres forward of the hole the mesh actually draws. A plume started
        # there comes out of the side of the nacelle.
        from aerosim.core.model_package import load_aircraft
        from aerosim.core.units import to_si
        from aerosim.game.config import DATA_ROOT
        from aerosim.game.mesh import build_mesh, exhaust_ports

        model = load_aircraft(DATA_ROOT / aircraft)
        ports = exhaust_ports(model)
        specs = model.raw("propulsion", "positions", []) or []
        assert len(ports) == len(specs)

        for (position, radius), spec in zip(ports, specs):
            assert radius > 0.0
            # Aft of the engine's own station.
            assert position[0] < to_si(spec.get("x", 0.0))

        # And inside the mesh's own extent, not floating behind it.
        points = np.concatenate([f.points for f in build_mesh(model)], axis=0)
        for position, _ in ports:
            assert points[:, 0].min() - 0.5 <= position[0] <= points[:, 0].max()
            assert points[:, 1].min() - 0.5 <= position[1] <= points[:, 1].max() + 0.5


class TestHorizon:
    @pytest.fixture(scope="class")
    def screen(self):
        import pygame

        pygame.init()
        surface = pygame.display.set_mode((800, 600))
        yield surface
        pygame.quit()

    def test_horizon_row_matches_a_projected_distant_point(self, screen, model):
        # The sky gradient is positioned on this row, so if it is wrong the sky
        # and the fully hazed far ground meet in two different colours.
        import pygame

        from aerosim.game.renderer import Renderer, Sky

        sim = Simulation(
            model, SimConditions(aircraft=model.name, altitude=ft(12000), heading=deg(37))
        )
        run(sim, 3.0)

        renderer = Renderer(screen, Sky(12.0, 45000.0))
        renderer.resize_view(pygame.Rect(0, 0, 800, 420))
        renderer.camera.follow(sim.fdm.state, 1 / 60)

        # A point 4 000 km away at the camera's own height is, to the accuracy
        # of a flat-earth projection, on the horizon.
        far = renderer.camera.position + np.array(
            [
                4.0e6 * math.cos(sim.fdm.state.derived.yaw),
                4.0e6 * math.sin(sim.fdm.state.derived.yaw),
                0.0,
            ]
        )
        projected = renderer.project(renderer.camera.to_camera(far[None, :]))
        assert renderer.horizon_y() == pytest.approx(float(projected[0, 1]), abs=0.5)

    @pytest.mark.parametrize("width,height", [(800, 420), (1920, 900), (3840, 1600)])
    def test_horizon_leaves_the_screen_the_right_way_when_looking_straight_down(
        self, screen, width, height
    ):
        import pygame

        from aerosim.game.renderer import Renderer, Sky

        surface = pygame.Surface((width, height))
        renderer = Renderer(surface, Sky(12.0, 45000.0))
        renderer.resize_view(pygame.Rect(0, 0, width, height))

        # Straight down: the horizon is above the top of the view, not below.
        renderer.camera.basis = np.array(
            [[0.0, 1.0, 0.0], [1.0, 0.0, 0.0], [0.0, 0.0, 1.0]]
        )
        assert renderer.horizon_y() < 0
        renderer.draw_sky()  # the sky offset becomes a pygame Rect, which is 32-bit

        # Straight up: below the bottom of it.
        renderer.camera.basis = np.array(
            [[0.0, 1.0, 0.0], [1.0, 0.0, 0.0], [0.0, 0.0, -1.0]]
        )
        assert renderer.horizon_y() > height
        renderer.draw_sky()


class TestChartEnvelope:
    @pytest.mark.parametrize(
        "count,width", [(2, 400), (50, 400), (400, 400), (401, 400), (2400, 300)]
    )
    def test_envelope_spans_the_plot_and_keeps_the_extremes(self, count, width):
        # A strip chart holds more samples than it has pixel columns. Taking
        # every nth sample would drop a 0.3 s g spike between two columns; the
        # min and max of each column keep it.
        from aerosim.game.charts import FlightCharts

        series = np.sin(np.linspace(0.0, 8.0, count))
        x, low, high = FlightCharts._envelope(series, width)

        assert len(x) == len(low) == len(high)
        assert x[0] == pytest.approx(0.0)
        assert x[-1] == pytest.approx(width - 1)
        assert np.all(np.diff(x) > 0.0)
        assert low.min() == pytest.approx(series.min())
        assert high.max() == pytest.approx(series.max())
        assert np.all(low <= high)


class TestTerrainDetail:
    def test_cell_size_rises_with_the_snapped_value_not_to_its_floor(self):
        # `10 ** round(log10(x))` sends anything above 3.16e(k) up a decade,
        # and x / magnitude then rounds to zero. The near cell was pinned to
        # its own floor for every altitude between about 900 m and 7 km, and
        # the outer ring lost three quarters of its reach exactly where the
        # horizon is furthest away.
        from aerosim.game.renderer import MAX_CELL, MIN_CELL, _snap

        for size in (120.0, 250.0, 420.0, 700.0, 1100.0, 1540.0):
            snapped = _snap(size)
            assert MIN_CELL <= snapped <= MAX_CELL
            # Within a factor of two of what was asked for, in both directions.
            assert 0.5 * size <= snapped <= 2.0 * size or snapped == MIN_CELL

        sizes = [_snap(v) for v in (120.0, 300.0, 600.0, 1200.0)]
        assert sizes == sorted(sizes)
        assert sizes[-1] > sizes[0]

    def test_snapping_gives_a_short_ladder_of_round_sizes(self):
        # Snapping exists so the size is *constant* over a range of altitudes:
        # a continuously varying cell slides the whole heightfield lattice
        # under the view every frame, and the ground crawls.
        from aerosim.game.renderer import MAX_CELL, MIN_CELL, _snap

        sizes = {_snap(float(v)) for v in np.linspace(50.0, 2500.0, 400)}
        assert len(sizes) <= 8, sorted(sizes)
        for snapped in sizes:
            if snapped in (MIN_CELL, MAX_CELL):
                continue  # the clamps, which are constant by construction
            mantissa = snapped / 10.0 ** math.floor(math.log10(snapped))
            assert mantissa in (1.0, 2.0, 5.0), snapped

    def test_snapping_honours_an_explicit_maximum(self):
        from aerosim.game.renderer import MIN_CELL, _snap

        for maximum in (144.0, 433.0, 865.0):
            for size in np.linspace(50.0, 2500.0, 80):
                snapped = _snap(float(size), maximum=maximum)
                assert MIN_CELL <= snapped <= max(maximum, MIN_CELL)

    def test_detail_reach_is_capped_by_visibility(self, model):
        # Ground twice the visibility away is fully hazed to the horizon
        # colour, so cells drawn out there cost a frame and paint nothing the
        # base plane was not painting already.
        import pygame

        from aerosim.game.renderer import (
            RING_CELLS,
            RING_FACTOR,
            RING_LEVELS,
            Renderer,
            Sky,
        )

        pygame.init()
        screen = pygame.display.set_mode((640, 480))
        outer = RING_CELLS * RING_FACTOR ** (RING_LEVELS - 1)

        for visibility in (8_000.0, 45_000.0, 90_000.0):
            renderer = Renderer(screen, Sky(12.0, visibility))
            renderer.resize_view(pygame.Rect(0, 0, 640, 300))
            sim = Simulation(
                model, SimConditions(aircraft=model.name, altitude=ft(38000))
            )
            run(sim, 2.0)
            renderer.camera.follow(sim.fdm.state, 1 / 60)
            renderer.draw_terrain(sim.fdm.state, sim.terrain, None)

            cell = min(key[0] for key in renderer._ring_cache)
            assert cell * outer <= max(30_000.0, 2.0 * visibility) + 1.0

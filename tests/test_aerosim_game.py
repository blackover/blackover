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
        renderer.draw_terrain(sim.fdm.state, 0.0, Runway())
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
            renderer.draw_terrain(sim.fdm.state, 0.0, Runway())
            renderer.draw_aircraft(
                sim.fdm.state,
                build_mesh(model),
                dcm_body_to_ned(sim.fdm.state.quaternion),
            )
        assert np.array_equal(sim.fdm.state.x, before)

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

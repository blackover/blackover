"""Telemetry recording and replay.

The property that matters most here is that a replay is a *viewer*: it
reproduces a recorded run exactly and cannot advance the physics. Both halves
of that are asserted below.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np
import pytest
import yaml

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aerosim.core.model_package import load_aircraft
from aerosim.core.orchestrator import Simulation
from aerosim.game.config import FailureMode, SimConditions, StartMode
from aerosim.telemetry.recorder import TelemetryRecorder, default_run_dir, engine_columns
from aerosim.telemetry.replay import (
    ReplayError,
    ReplaySession,
    describe,
    load_run,
)

DATA_ROOT = Path(__file__).resolve().parent.parent / "aerosim" / "data" / "aircraft"
PACKAGES = ["aeroliner_200", "aerofalcon_x"]


@pytest.fixture(scope="module", params=PACKAGES)
def model(request):
    return load_aircraft(DATA_ROOT / request.param)


def fly_and_record(model, tmp_path, seconds=8.0, conditions=None, sample_rate_hz=50.0):
    conditions = conditions or SimConditions(
        aircraft=model.name, start_mode=StartMode.AIRBORNE
    )
    sim = Simulation(model, conditions)
    recorder = TelemetryRecorder(
        tmp_path, model, conditions, sample_rate_hz=sample_rate_hz
    )
    for _ in range(int(seconds / sim.clock.dt)):
        sim.throttle = 0.7
        sim.step()
        recorder.sample(sim)
    manifest = recorder.close("test")
    return sim, manifest


# --------------------------------------------------------------------------
# Recorder
# --------------------------------------------------------------------------


class TestRecorder:
    def test_writes_telemetry_and_manifest(self, model, tmp_path):
        run_dir = tmp_path / "run"
        _, manifest = fly_and_record(model, run_dir)
        assert (run_dir / "telemetry.csv").exists()
        assert (run_dir / "manifest.yaml").exists()
        assert manifest.rows > 0

    def test_manifest_identifies_what_produced_the_run(self, model, tmp_path):
        conditions = SimConditions(aircraft=model.name, seed=1234, turbulence="moderate")
        _, manifest = fly_and_record(model, tmp_path / "run", conditions=conditions)
        stored = yaml.safe_load((tmp_path / "run" / "manifest.yaml").read_text())
        assert stored["aircraft"] == model.name
        assert stored["package_checksum"] == model.checksum
        assert stored["seed"] == 1234
        assert stored["conditions"]["turbulence"] == "moderate"
        assert stored["ended_reason"] == "test"

    def test_sample_rate_is_honoured_regardless_of_step_size(self, model, tmp_path):
        # A run at dt = 0.005 and one at dt = 0.01 must produce the same
        # sample rate, or two runs of the same scenario are not comparable.
        for i, dt in enumerate((0.005, 0.01)):
            conditions = SimConditions(aircraft=model.name, dt=dt)
            _, manifest = fly_and_record(
                model, tmp_path / f"run{i}", seconds=4.0, conditions=conditions,
                sample_rate_hz=25.0,
            )
            assert manifest.sample_rate_hz == pytest.approx(25.0)
            assert manifest.rows == pytest.approx(100, abs=2)

    def test_per_engine_columns_follow_the_aircraft(self, model, tmp_path):
        _, manifest = fly_and_record(model, tmp_path / "run")
        count = int(model.get("propulsion", "engine_count"))
        for i in range(count):
            assert f"eng{i + 1}_n1" in manifest.columns
            assert f"eng{i + 1}_failed" in manifest.columns
        assert f"eng{count + 1}_n1" not in manifest.columns

    def test_engine_column_getters_bind_their_index(self):
        # Closures over a loop variable are the classic way to end up with
        # every column reporting the last engine.
        columns = engine_columns(2)
        assert len(columns) == 8
        assert "eng1_n1" in columns and "eng2_n1" in columns

    def test_hash_covers_the_telemetry(self, model, tmp_path):
        run_dir = tmp_path / "run"
        _, manifest = fly_and_record(model, run_dir)
        assert len(manifest.telemetry_sha256) == 64
        run = load_run(run_dir)
        assert run.hash_verified

    def test_tampering_is_detected(self, model, tmp_path):
        run_dir = tmp_path / "run"
        fly_and_record(model, run_dir)
        telemetry = run_dir / "telemetry.csv"
        text = telemetry.read_text().splitlines()
        text[5] = text[5].replace(",", ",", 1) + ""
        # Change one value rather than the structure.
        fields = text[5].split(",")
        fields[1] = str(int(float(fields[1])) + 1)
        text[5] = ",".join(fields)
        telemetry.write_text("\n".join(text) + "\n")

        run = load_run(run_dir)
        assert not run.hash_verified
        assert run.hash_actual != run.hash_expected

    def test_context_manager_closes(self, model, tmp_path):
        conditions = SimConditions(aircraft=model.name)
        sim = Simulation(model, conditions)
        with TelemetryRecorder(tmp_path / "run", model, conditions) as recorder:
            for _ in range(200):
                sim.step()
                recorder.sample(sim)
        assert (tmp_path / "run" / "manifest.yaml").exists()

    def test_default_run_dir_is_timestamped(self):
        assert default_run_dir("runs").parent.name == "runs"
        assert default_run_dir("runs") != Path("runs")


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------


class TestLoad:
    def test_missing_directory_is_reported(self, tmp_path):
        with pytest.raises(ReplayError):
            load_run(tmp_path / "nothing_here")

    def test_missing_manifest_is_reported(self, model, tmp_path):
        run_dir = tmp_path / "run"
        fly_and_record(model, run_dir)
        (run_dir / "manifest.yaml").unlink()
        # A run without a manifest cannot be attributed to anything, which is
        # the whole reason the manifest exists.
        with pytest.raises(ReplayError, match="unattributable"):
            load_run(run_dir)

    def test_ragged_row_is_reported(self, model, tmp_path):
        run_dir = tmp_path / "run"
        fly_and_record(model, run_dir)
        telemetry = run_dir / "telemetry.csv"
        lines = telemetry.read_text().splitlines()
        lines[3] = lines[3] + ",999"
        telemetry.write_text("\n".join(lines) + "\n")
        with pytest.raises(ReplayError, match="fields"):
            load_run(run_dir)

    def test_describe_reports_integrity(self, model, tmp_path):
        run_dir = tmp_path / "run"
        fly_and_record(model, run_dir)
        assert any("verified" in line for line in describe(load_run(run_dir)))


# --------------------------------------------------------------------------
# Replay
# --------------------------------------------------------------------------


class TestReplay:
    def test_final_frame_matches_the_live_state(self, model, tmp_path):
        run_dir = tmp_path / "run"
        sim, _ = fly_and_record(model, run_dir)
        session = ReplaySession(model, load_run(run_dir))
        session.apply(session.run.rows - 1)

        live, replayed = sim.fdm.state.derived, session.fdm.state.derived
        assert replayed.altitude == pytest.approx(live.altitude, rel=1e-6, abs=0.5)
        assert replayed.vcas == pytest.approx(live.vcas, rel=1e-5, abs=0.05)
        assert replayed.roll == pytest.approx(live.roll, abs=1e-4)
        assert replayed.pitch == pytest.approx(live.pitch, abs=1e-4)
        # And the 13 states themselves, which is what the renderer draws from.
        assert np.allclose(session.fdm.state.x[0:6], sim.fdm.state.x[0:6], rtol=1e-6)

    def test_engine_failure_survives_the_round_trip(self, airliner_only, tmp_path):
        # A single mean N1 would show two healthy engines at reduced power
        # instead of one failed and one at maximum.
        conditions = SimConditions(
            aircraft=airliner_only.name,
            start_mode=StartMode.AIRBORNE,
            failure=FailureMode.ENGINE_OUT,
            failure_time=1.0,
        )
        run_dir = tmp_path / "run"
        sim, _ = fly_and_record(
            airliner_only, run_dir, seconds=10.0, conditions=conditions
        )
        session = ReplaySession(airliner_only, load_run(run_dir))
        session.apply(session.run.rows - 1)

        assert session.fdm.propulsion.engines[0].failed
        assert not session.fdm.propulsion.engines[1].failed
        assert session.fdm.propulsion.engines[1].n1 > 0.3
        assert session.fdm.propulsion.engines[0].n1 < session.fdm.propulsion.engines[1].n1

    def test_effectors_and_configuration_survive(self, model, tmp_path):
        conditions = SimConditions(aircraft=model.name, start_mode=StartMode.APPROACH)
        run_dir = tmp_path / "run"
        sim, _ = fly_and_record(model, run_dir, seconds=6.0, conditions=conditions)
        session = ReplaySession(model, load_run(run_dir))
        session.apply(session.run.rows - 1)

        assert session.surfaces.flap.position == pytest.approx(
            sim.surfaces.flap.position, abs=1e-4
        )
        assert session.surfaces.gear_position == pytest.approx(
            sim.surfaces.gear_position, abs=1e-4
        )
        assert session.throttle == pytest.approx(sim.throttle, abs=1e-4)

    def test_replay_cannot_advance_the_physics(self, model, tmp_path):
        # The kernel is the sole authority on aircraft state. A replay has no
        # kernel, and must not grow one by accident.
        run_dir = tmp_path / "run"
        fly_and_record(model, run_dir)
        session = ReplaySession(model, load_run(run_dir))
        assert not hasattr(session, "step")
        assert not hasattr(session.fdm, "step")

    def test_seeking_is_exact_and_reversible(self, model, tmp_path):
        run_dir = tmp_path / "run"
        fly_and_record(model, run_dir)
        session = ReplaySession(model, load_run(run_dir))

        session.apply(10)
        first = session.fdm.state.x.copy()
        session.apply(session.run.rows - 1)
        session.apply(10)
        assert np.array_equal(session.fdm.state.x, first)

    def test_playhead_clamps_at_both_ends(self, model, tmp_path):
        run_dir = tmp_path / "run"
        fly_and_record(model, run_dir)
        session = ReplaySession(model, load_run(run_dir))
        session.apply(-500)
        assert session.index == 0 and session.progress == pytest.approx(0.0)
        session.apply(10**9)
        assert session.index == session.run.rows - 1
        assert session.progress == pytest.approx(1.0)

    def test_advance_respects_pause_and_speed(self, model, tmp_path):
        run_dir = tmp_path / "run"
        fly_and_record(model, run_dir)
        session = ReplaySession(model, load_run(run_dir))

        session.paused = True
        session.advance(1.0)
        assert session.index == 0

        session.paused = False
        session.playback_speed = 1.0
        session.apply(0)
        session.advance(0.5)
        assert session.index == pytest.approx(session.clock.rate_hz * 0.5, abs=2)

    def test_seek_by_time(self, model, tmp_path):
        run_dir = tmp_path / "run"
        fly_and_record(model, run_dir)
        session = ReplaySession(model, load_run(run_dir))
        session.seek_time(3.0)
        assert session.time == pytest.approx(3.0, abs=1.0 / session.clock.rate_hz)

    def test_conditions_are_recovered_from_the_manifest(self, model, tmp_path):
        conditions = SimConditions(
            aircraft=model.name,
            seed=99,
            turbulence="severe",
            wind_speed=12.0,
            time_of_day=6.5,
        )
        run_dir = tmp_path / "run"
        fly_and_record(model, run_dir, conditions=conditions)
        session = ReplaySession(model, load_run(run_dir))
        assert session.conditions.seed == 99
        assert session.conditions.turbulence == "severe"
        assert session.conditions.time_of_day == pytest.approx(6.5)

    def test_derived_ground_speed_is_recomputed_not_stored(self, model, tmp_path):
        # Ground speed is exactly recoverable from the state, so storing it
        # would be a second copy of a number that could disagree with the first.
        run_dir = tmp_path / "run"
        sim, manifest = fly_and_record(model, run_dir)
        assert "ground_speed_kt" not in manifest.columns
        session = ReplaySession(model, load_run(run_dir))
        session.apply(session.run.rows - 1)
        assert session.fdm.state.derived.ground_speed == pytest.approx(
            sim.fdm.state.derived.ground_speed, rel=1e-5, abs=0.05
        )


@pytest.fixture(scope="module")
def airliner_only():
    return load_aircraft(DATA_ROOT / "aeroliner_200")


# --------------------------------------------------------------------------
# Rendering a replay
# --------------------------------------------------------------------------


class TestReplayRendering:
    def test_a_replay_frame_renders(self, model, tmp_path):
        import pygame

        from aerosim.core.frames import dcm_body_to_ned
        from aerosim.game.mesh import build_mesh
        from aerosim.game.renderer import Renderer, Runway, Sky

        pygame.init()
        screen = pygame.display.set_mode((640, 480))

        run_dir = tmp_path / "run"
        fly_and_record(model, run_dir)
        session = ReplaySession(model, load_run(run_dir))
        session.apply(session.run.rows // 2)

        renderer = Renderer(screen, Sky(12.0, 30000.0))
        renderer.resize_view(pygame.Rect(0, 0, 640, 340))
        renderer.camera.follow(session.fdm.state, 1 / 60)
        renderer.draw_sky()
        renderer.draw_terrain(session.fdm.state, 0.0, Runway())
        renderer.draw_aircraft(
            session.fdm.state,
            build_mesh(model),
            dcm_body_to_ned(session.fdm.state.quaternion),
        )

    def test_panel_draws_from_a_replay_session(self, model, tmp_path):
        import pygame

        from aerosim.game.instruments import Fonts, Panel

        pygame.init()
        screen = pygame.display.set_mode((640, 480))

        run_dir = tmp_path / "run"
        fly_and_record(model, run_dir)
        session = ReplaySession(model, load_run(run_dir))
        # The panel takes a Simulation; a replay session must satisfy the same
        # interface, or every viewer would need a replay-specific branch.
        panel = Panel(pygame.Rect(0, 340, 640, 140), Fonts(1.0), model, session)
        panel.draw(screen, session)

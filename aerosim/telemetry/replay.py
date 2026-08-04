"""Replay: animate a recorded run, feeding nothing back.

The simulation kernel is the sole authority on aircraft state. A replay has no
kernel at all -- it reads telemetry and nothing else, and there is deliberately
no code path from here into the flight model. That is what makes a replay
trustworthy as evidence: what you are watching is what was recorded, not a
re-simulation that might diverge from it.

The session below rebuilds the *real* component objects from the aircraft data
package -- the same ``ControlSurfaces``, ``PropulsionModel`` and ``MassModel``
the live simulation uses -- and drives their fields from the file. The panel
and the renderer then need no replay-specific branch, because they are looking
at the objects they always look at.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import yaml

from ..control.actuators import ControlSurfaces
from ..control.autopilot import Autopilot
from ..core.state import State
from ..env.atmosphere import Atmosphere
from ..env.terrain import Terrain
from ..env.wind import WindField
from ..fdm.aero import AeroModel
from ..fdm.fdm import Diagnostics
from ..fdm.mass import MassModel, MassProperties
from ..fdm.propulsion import PropulsionModel
from ..game.config import SimConditions


class ReplayError(Exception):
    """Raised when a run directory cannot be replayed."""


@dataclass
class ReplayClock:
    """Stands in for the kernel clock, driven by recorded time."""

    time: float = 0.0
    step_index: int = 0
    dt: float = 0.02
    rate_hz: float = 50.0


@dataclass
class Run:
    """A loaded telemetry file plus its manifest."""

    path: Path
    manifest: dict
    columns: list[str]
    data: np.ndarray  # (rows, columns)
    hash_verified: bool = False
    hash_expected: str = ""
    hash_actual: str = ""

    @property
    def rows(self) -> int:
        return int(self.data.shape[0])

    @property
    def duration(self) -> float:
        if self.rows == 0:
            return 0.0
        return float(self.data[-1, self.columns.index("time_s")])

    def column(self, name: str) -> np.ndarray:
        try:
            return self.data[:, self.columns.index(name)]
        except ValueError as exc:
            raise ReplayError(f"telemetry has no column {name!r}") from exc

    def has(self, name: str) -> bool:
        return name in self.columns


def load_run(run_dir: str | Path) -> Run:
    """Load a recorded run and check its telemetry against the stored hash."""
    root = Path(run_dir)
    telemetry = root / "telemetry.csv"
    manifest_path = root / "manifest.yaml"

    if not telemetry.exists():
        raise ReplayError(f"{root}: no telemetry.csv")
    if not manifest_path.exists():
        raise ReplayError(f"{root}: no manifest.yaml -- the run is unattributable")

    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}

    raw = telemetry.read_text(encoding="utf-8").splitlines()
    if len(raw) < 2:
        raise ReplayError(f"{root}: telemetry contains no samples")

    columns = raw[0].split(",")
    values = np.empty((len(raw) - 1, len(columns)), dtype=float)
    for row, line in enumerate(raw[1:]):
        parts = line.split(",")
        if len(parts) != len(columns):
            raise ReplayError(
                f"{root}: row {row + 1} has {len(parts)} fields, header has {len(columns)}"
            )
        for col, text in enumerate(parts):
            values[row, col] = float(text) if text else math.nan

    digest = hashlib.sha256()
    with telemetry.open("rb") as handle:
        for block in iter(lambda: handle.read(65536), b""):
            digest.update(block)
    actual = digest.hexdigest()
    expected = str(manifest.get("telemetry_sha256", ""))

    return Run(
        path=root,
        manifest=manifest,
        columns=columns,
        data=values,
        hash_verified=bool(expected) and actual == expected,
        hash_expected=expected,
        hash_actual=actual,
    )


class _ReplayFDM:
    """The subset of FlightDynamics a viewer reads, populated from a file.

    Deliberately not a real ``FlightDynamics``. Handing the renderer an object
    with a working ``step`` would make it possible for a replay to advance the
    physics, and the whole value of a replay is that it cannot.
    """

    def __init__(self, model, conditions: SimConditions) -> None:
        self.model = model
        self.aero = AeroModel(model)
        self.mass_model = MassModel(model)
        self.propulsion = PropulsionModel(model)
        self.atmosphere = Atmosphere(conditions.temperature_offset, conditions.qnh)
        self.field_elevation = conditions.field_elevation
        self.state = State()
        self.diagnostics = Diagnostics()
        self.vmo = model.get("limitations", "vmo")
        self.mmo = model.get("limitations", "mmo")
        self._mass_properties = self.mass_model.compute()

    @property
    def mass_properties(self) -> MassProperties:
        return self._mass_properties


class ReplaySession:
    """Quacks like a Simulation for the renderer and the panel, read-only."""

    def __init__(self, model, run: Run) -> None:
        self.model = model
        self.run = run
        self.conditions = _conditions_from(run, model)

        self.fdm = _ReplayFDM(model, self.conditions)
        self.surfaces = ControlSurfaces(model)
        self.autopilot = Autopilot(model)
        self.wind = WindField.uniform(
            self.conditions.wind_speed, self.conditions.wind_direction
        )
        # Rebuilt from the recorded seed and profile, not stored in the file:
        # the heightfield is a pure function of those two, so a replay lands in
        # the same valley the run took off from without carrying a landscape
        # around in the telemetry.
        self.terrain = Terrain(
            seed=self.conditions.seed,
            profile=self.conditions.terrain,
            field_elevation=self.conditions.field_elevation,
        )

        rate = float(run.manifest.get("sample_rate_hz", 50.0)) or 50.0
        self.clock = ReplayClock(dt=1.0 / rate, rate_hz=rate)

        self.events: list = []
        self.crashed = False
        self.crash_reason = ""
        self.paused = False
        self.throttle = 0.0
        self.hydraulic_pressure = 1.0
        self.pitch_trim = 0.0

        self.playback_speed = 1.0
        self.index = 0
        self._time = run.column("time_s")
        self.apply(0)

    # ---------------------------------------------------------------------

    @property
    def time(self) -> float:
        return self.clock.time

    @property
    def duration(self) -> float:
        return self.run.duration

    @property
    def progress(self) -> float:
        return self.index / max(1, self.run.rows - 1)

    @property
    def propulsion(self):
        """The engine states, so a viewer can read spool and reheat.

        A Simulation owns its propulsion model directly; here it lives on the
        replay FDM because that is what ``apply`` writes the recorded engine
        columns into. Exposing it under the same name is what lets the game
        draw the same exhaust plume for a replay as for a live flight.
        """
        return self.fdm.propulsion

    def seek_time(self, seconds: float) -> None:
        seconds = max(0.0, min(self.duration, seconds))
        self.apply(int(np.searchsorted(self._time, seconds)))

    def advance(self, wall_dt: float) -> None:
        """Move the playhead by wall time, scaled by the playback speed."""
        if self.paused:
            return
        step = wall_dt * self.playback_speed * self.clock.rate_hz
        self.apply(self.index + max(1, int(round(step))) if step > 0 else self.index)

    def step_frames(self, frames: int) -> None:
        self.apply(self.index + frames)

    # ---------------------------------------------------------------------

    def apply(self, index: int) -> None:
        """Load one recorded frame into the component objects."""
        run = self.run
        self.index = max(0, min(run.rows - 1, int(index)))
        row = run.data[self.index]

        def value(name: str, default: float = 0.0) -> float:
            if not run.has(name):
                return default
            v = row[run.columns.index(name)]
            return default if math.isnan(v) else float(v)

        state = self.fdm.state
        state.x[0:13] = [
            value("pn_m"), value("pe_m"), value("pd_m"),
            value("u_mps"), value("v_mps"), value("w_mps"),
            value("q0", 1.0), value("q1"), value("q2"), value("q3"),
            value("p_radps"), value("q_radps"), value("r_radps"),
        ]
        state.normalise()
        state.on_ground = value("on_ground") > 0.5

        derived = state.derived
        derived.vtas = value("vtas_kt") * 0.5144444444
        derived.vcas = value("vcas_kt") * 0.5144444444
        derived.mach = value("mach")
        derived.alpha = math.radians(value("alpha_deg"))
        derived.beta = math.radians(value("beta_deg"))
        derived.altitude = value("altitude_ft") * 0.3048
        derived.altitude_agl = value("agl_ft") * 0.3048
        derived.roll = math.radians(value("roll_deg"))
        derived.pitch = math.radians(value("pitch_deg"))
        derived.yaw = math.radians(value("yaw_deg"))
        derived.p, derived.q, derived.r = (
            value("p_radps"), value("q_radps"), value("r_radps")
        )
        derived.vertical_speed = value("vs_fpm") / 196.8503937
        derived.load_factor = value("load_factor_g", 1.0)

        # Ground speed and track are not recorded: they are exactly
        # recoverable from the state, so storing them would be a second copy
        # of a number that could disagree with the first.
        velocity_ned = state.velocity_ned
        derived.ground_speed = float(math.hypot(velocity_ned[0], velocity_ned[1]))
        derived.track = math.atan2(float(velocity_ned[1]), float(velocity_ned[0]))
        derived.gamma = math.atan2(
            -float(velocity_ned[2]), max(derived.ground_speed, 1e-9)
        )

        air = self.fdm.atmosphere.sample(derived.altitude)
        derived.density = air.density
        derived.temperature = air.temperature
        derived.pressure = air.pressure
        derived.sound_speed = air.sound_speed
        derived.qbar = 0.5 * air.density * derived.vtas**2
        derived.accel_body = np.array(
            [0.0, 0.0, -derived.load_factor * 9.80665 * value("mass_kg", 1.0)]
        )

        # Effectors.
        self.surfaces.elevator.position = value("elevator")
        self.surfaces.aileron.position = value("aileron")
        self.surfaces.rudder.position = value("rudder")
        self.surfaces.flap.position = value("flap")
        self.surfaces.speedbrake.position = value("speedbrake")
        self.surfaces.gear_position = value("gear", 1.0)
        for actuator in self.surfaces.actuators:
            actuator.commanded = actuator.position
        self.throttle = value("throttle")

        # Propulsion, per engine.
        for i, engine in enumerate(self.fdm.propulsion.engines):
            engine.n1 = value(f"eng{i + 1}_n1")
            engine.egt = value(f"eng{i + 1}_egt_k", 300.0)
            engine.failed = value(f"eng{i + 1}_failed") > 0.5
            engine.afterburner = value(f"eng{i + 1}_ab") > 0.5
            engine.running = not engine.failed
            engine.thrust = 0.0

        # Mass. The tanks are set from the recorded quantity so the fuel bar
        # and the CG both come from the file rather than from a fresh
        # calculation that would drift away from what was flown.
        self.fdm.mass_model.set_fuel(value("fuel_kg"))
        properties = self.fdm.mass_model.compute()
        self.fdm._mass_properties = MassProperties(
            mass=value("mass_kg", properties.mass),
            cg=np.array([value("cg_x_m"), 0.0, float(properties.cg[2])]),
            inertia=properties.inertia,
            inertia_inverse=properties.inertia_inverse,
            fuel_mass=value("fuel_kg"),
            payload_mass=properties.payload_mass,
            empty_mass=properties.empty_mass,
        )

        diagnostics = self.fdm.diagnostics
        diagnostics.thrust = value("thrust_n")
        diagnostics.fuel_flow = value("fuel_flow_kgps")
        diagnostics.lift_to_drag = value("lift_to_drag")
        diagnostics.stalled = value("stalled") > 0.5
        diagnostics.out_of_envelope = value("out_of_envelope") > 0.5
        diagnostics.on_ground = state.on_ground
        diagnostics.stall_speed = self.fdm.aero.stall_speed(
            self.fdm.mass_properties.mass, air.density, self.surfaces.flap.position
        )
        diagnostics.overspeed = (
            derived.vcas > self.fdm.vmo or derived.mach > self.fdm.mmo
        )

        self.hydraulic_pressure = value("hydraulic", 1.0)

        self.clock.time = value("time_s")
        self.clock.step_index = int(value("step"))


def _conditions_from(run: Run, model) -> SimConditions:
    """Rebuild the pre-flight conditions the run was flown under."""
    stored = run.manifest.get("conditions") or {}
    conditions = SimConditions()
    for key, value in stored.items():
        if hasattr(conditions, key):
            setattr(conditions, key, value)
    conditions.aircraft = str(run.manifest.get("aircraft", conditions.aircraft))
    return conditions


def describe(run: Run) -> list[str]:
    """Human-readable provenance, for the replay banner."""
    manifest = run.manifest
    integrity = (
        "verified"
        if run.hash_verified
        else ("NOT VERIFIED" if run.hash_expected else "no hash recorded")
    )
    return [
        f"{manifest.get('aircraft', '?')} v{manifest.get('aircraft_version', '?')}",
        f"package {str(manifest.get('package_checksum', ''))[:16]}",
        f"seed {manifest.get('seed', '?')}   dt {manifest.get('dt', '?')} s",
        f"{run.rows} samples at {manifest.get('sample_rate_hz', '?')} Hz",
        f"{run.duration:.1f} s   ended: {manifest.get('ended_reason', '?')}",
        f"telemetry {integrity}",
    ]

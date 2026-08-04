"""Buffered CSV telemetry with a run manifest and an integrity hash.

Every run writes two files:

    telemetry.csv   one row per sample, header names carrying their units
    manifest.yaml   what produced it -- aircraft, package checksum, seed,
                    every pre-flight condition, step size, sample rate, row
                    count, and a SHA-256 over the CSV

The manifest is the point. A telemetry file on its own is a pile of numbers
that cannot be attributed to anything; a telemetry file next to a manifest
naming the model version, the scenario and the seed can be reproduced, and a
hash says whether it is the file that was written.

Writes are buffered because a flush per step at 100 Hz turns the recorder into
the slowest thing in the loop.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import yaml

from ..core.units import to_deg, to_ft, to_kt

SCHEMA_VERSION = 1

# Column name -> how to get it from a live Simulation. Units are named in the
# header so a reader never has to guess, and the aviation-unit columns are
# derived here at the boundary rather than stored twice in the kernel.
COLUMNS: dict[str, callable] = {
    "time_s": lambda s: s.clock.time,
    "step": lambda s: s.clock.step_index,
    # 13 continuous states, SI, exactly as the integrator holds them
    "pn_m": lambda s: s.fdm.state.x[0],
    "pe_m": lambda s: s.fdm.state.x[1],
    "pd_m": lambda s: s.fdm.state.x[2],
    "u_mps": lambda s: s.fdm.state.x[3],
    "v_mps": lambda s: s.fdm.state.x[4],
    "w_mps": lambda s: s.fdm.state.x[5],
    "q0": lambda s: s.fdm.state.x[6],
    "q1": lambda s: s.fdm.state.x[7],
    "q2": lambda s: s.fdm.state.x[8],
    "q3": lambda s: s.fdm.state.x[9],
    "p_radps": lambda s: s.fdm.state.x[10],
    "q_radps": lambda s: s.fdm.state.x[11],
    "r_radps": lambda s: s.fdm.state.x[12],
    # derived, for analysis without re-deriving
    "vtas_kt": lambda s: to_kt(s.fdm.state.derived.vtas),
    "vcas_kt": lambda s: to_kt(s.fdm.state.derived.vcas),
    "mach": lambda s: s.fdm.state.derived.mach,
    "alpha_deg": lambda s: to_deg(s.fdm.state.derived.alpha),
    "beta_deg": lambda s: to_deg(s.fdm.state.derived.beta),
    "altitude_ft": lambda s: to_ft(s.fdm.state.derived.altitude),
    "agl_ft": lambda s: to_ft(s.fdm.state.derived.altitude_agl),
    "roll_deg": lambda s: to_deg(s.fdm.state.derived.roll),
    "pitch_deg": lambda s: to_deg(s.fdm.state.derived.pitch),
    "yaw_deg": lambda s: to_deg(s.fdm.state.derived.yaw) % 360.0,
    "vs_fpm": lambda s: s.fdm.state.derived.vertical_speed * 196.8503937,
    "load_factor_g": lambda s: s.fdm.state.derived.load_factor,
    # effectors, as they actually are rather than as commanded
    "elevator": lambda s: s.surfaces.elevator.position,
    "aileron": lambda s: s.surfaces.aileron.position,
    "rudder": lambda s: s.surfaces.rudder.position,
    "flap": lambda s: s.surfaces.flap.position,
    "speedbrake": lambda s: s.surfaces.speedbrake.position,
    "gear": lambda s: s.surfaces.gear_position,
    "throttle": lambda s: s.throttle,
    # propulsion and mass
    "thrust_n": lambda s: s.fdm.diagnostics.thrust,
    "fuel_flow_kgps": lambda s: s.fdm.diagnostics.fuel_flow,
    "fuel_kg": lambda s: s.fdm.mass_properties.fuel_mass,
    "mass_kg": lambda s: s.fdm.mass_properties.mass,
    "cg_x_m": lambda s: s.fdm.mass_properties.cg[0],
    "lift_to_drag": lambda s: s.fdm.diagnostics.lift_to_drag,
    # health
    "on_ground": lambda s: 1 if s.fdm.state.on_ground else 0,
    "stalled": lambda s: 1 if s.fdm.diagnostics.stalled else 0,
    "out_of_envelope": lambda s: 1 if s.fdm.diagnostics.out_of_envelope else 0,
    "hydraulic": lambda s: s.hydraulic_pressure,
}


def engine_columns(count: int) -> dict[str, callable]:
    """Per-engine columns, generated from the aircraft's own engine count.

    A single mean N1 would be smaller than either engine during an engine-out,
    and a replay of that run would show two healthy engines at reduced power
    rather than one failed and one at maximum -- which is the entire point of
    the scenario.
    """
    columns: dict[str, callable] = {}
    for i in range(count):
        columns[f"eng{i + 1}_n1"] = lambda s, i=i: s.fdm.propulsion.engines[i].n1
        columns[f"eng{i + 1}_egt_k"] = lambda s, i=i: s.fdm.propulsion.engines[i].egt
        columns[f"eng{i + 1}_failed"] = (
            lambda s, i=i: 1 if s.fdm.propulsion.engines[i].failed else 0
        )
        columns[f"eng{i + 1}_ab"] = (
            lambda s, i=i: 1 if s.fdm.propulsion.engines[i].afterburner else 0
        )
    return columns


@dataclass
class RunManifest:
    """Everything needed to say what produced a telemetry file."""

    schema_version: int = SCHEMA_VERSION
    started_utc: str = ""
    aircraft: str = ""
    aircraft_version: str = ""
    package_checksum: str = ""
    seed: int = 0
    dt: float = 0.01
    sample_rate_hz: float = 50.0
    rows: int = 0
    duration_s: float = 0.0
    conditions: dict = field(default_factory=dict)
    columns: list[str] = field(default_factory=list)
    telemetry_sha256: str = ""
    ended_reason: str = ""

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "started_utc": self.started_utc,
            "aircraft": self.aircraft,
            "aircraft_version": self.aircraft_version,
            "package_checksum": self.package_checksum,
            "seed": self.seed,
            "dt": self.dt,
            "sample_rate_hz": self.sample_rate_hz,
            "rows": self.rows,
            "duration_s": self.duration_s,
            "ended_reason": self.ended_reason,
            "conditions": self.conditions,
            "columns": self.columns,
            "telemetry_sha256": self.telemetry_sha256,
        }


class TelemetryRecorder:
    """Records a run to ``<run_dir>/telemetry.csv`` plus a manifest."""

    def __init__(
        self,
        run_dir: str | Path,
        model,
        conditions,
        *,
        sample_rate_hz: float = 50.0,
        buffer_rows: int = 512,
    ) -> None:
        self.run_dir = Path(run_dir)
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.run_dir / "telemetry.csv"

        self._getters = dict(COLUMNS)
        self._getters.update(
            engine_columns(int(model.get("propulsion", "engine_count")))
        )
        self.columns = list(self._getters)
        self.sample_rate_hz = max(1.0, float(sample_rate_hz))

        # Decimation is computed against the kernel rate so a run recorded at
        # dt = 0.005 and one at dt = 0.01 produce the same sample rate.
        kernel_hz = 1.0 / conditions.dt
        self.decimation = max(1, int(round(kernel_hz / self.sample_rate_hz)))
        self.effective_rate_hz = kernel_hz / self.decimation

        self.manifest = RunManifest(
            started_utc=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            aircraft=model.name,
            aircraft_version=model.version,
            package_checksum=model.checksum,
            seed=conditions.seed,
            dt=conditions.dt,
            sample_rate_hz=self.effective_rate_hz,
            conditions=conditions.to_dict(),
            columns=self.columns,
        )

        self._buffer: list[str] = [",".join(self.columns)]
        self._buffer_rows = buffer_rows
        self._rows = 0
        self._last_time = 0.0
        self._closed = False
        self._handle = self.path.open("w", encoding="utf-8", newline="\n")

    # ---------------------------------------------------------------------

    @property
    def rows(self) -> int:
        return self._rows

    def sample(self, sim) -> bool:
        """Record one row if this step falls on the sample interval."""
        if self._closed:
            return False
        if sim.clock.step_index % self.decimation != 0:
            return False

        values = []
        for name in self.columns:
            value = self._getters[name](sim)
            if isinstance(value, float):
                # A NaN in a telemetry file is a silent corruption of every
                # analysis downstream; write it as empty so a reader notices.
                values.append("" if not math.isfinite(value) else f"{value:.9g}")
            else:
                values.append(str(value))
        self._buffer.append(",".join(values))
        self._rows += 1
        self._last_time = sim.clock.time

        if len(self._buffer) >= self._buffer_rows:
            self._flush()
        return True

    def _flush(self) -> None:
        if self._buffer:
            self._handle.write("\n".join(self._buffer) + "\n")
            self._buffer.clear()

    def close(self, reason: str = "completed") -> RunManifest:
        """Flush, hash the telemetry and write the manifest beside it."""
        if self._closed:
            return self.manifest

        self._flush()
        self._handle.close()
        self._closed = True

        digest = hashlib.sha256()
        with self.path.open("rb") as handle:
            for block in iter(lambda: handle.read(65536), b""):
                digest.update(block)

        self.manifest.rows = self._rows
        self.manifest.duration_s = self._last_time
        self.manifest.telemetry_sha256 = digest.hexdigest()
        self.manifest.ended_reason = reason

        (self.run_dir / "manifest.yaml").write_text(
            yaml.safe_dump(self.manifest.to_dict(), sort_keys=False, default_flow_style=False),
            encoding="utf-8",
        )
        return self.manifest

    def __enter__(self) -> "TelemetryRecorder":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close("aborted" if exc_type else "completed")


def default_run_dir(root: str | Path = "runs") -> Path:
    """A timestamped directory, so consecutive runs never overwrite."""
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M%S")
    return Path(root) / stamp

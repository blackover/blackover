"""Aircraft data package loading, validation and checksum.

An aircraft is a directory of YAML files, not a class in the source tree.
Adding an aircraft requires no code change. The loader validates the package
against a schema, computes a SHA-256 over its contents, and refuses to run if
anything mandatory is missing or carries a unit it does not recognise.

Validation reports *every* problem at once, naming the file, the key and what
was expected, so a data author fixes one round of errors rather than one error
per run.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .units import UnitError, to_si

# Sections every package must supply, and the keys each must contain.
REQUIRED_SECTIONS: dict[str, tuple[str, ...]] = {
    "manifest": ("name", "version", "category"),
    "geometry": ("wing_area", "wing_span", "mean_chord"),
    "mass_properties": ("empty_mass", "max_takeoff_mass", "inertia"),
    "aerodynamics": ("lift", "drag", "pitch", "lateral"),
    "propulsion": ("engine_count", "max_thrust_per_engine"),
    "flight_controls": ("elevator", "aileron", "rudder"),
    "landing_gear": ("gear",),
    "limitations": ("vmo", "mmo", "stall_speed_clean"),
}

OPTIONAL_SECTIONS = ("autopilot", "systems")


class PackageError(Exception):
    """Raised when a data package is missing, malformed or inconsistent."""

    def __init__(self, path: Path, problems: list[str]) -> None:
        self.path = path
        self.problems = problems
        listing = "\n".join(f"  - {p}" for p in problems)
        super().__init__(f"aircraft package {path} is invalid:\n{listing}")


@dataclass
class AircraftModel:
    """A loaded, validated aircraft data package."""

    path: Path
    sections: dict[str, dict] = field(default_factory=dict)
    checksum: str = ""

    @property
    def name(self) -> str:
        return str(self.sections["manifest"]["name"])

    @property
    def version(self) -> str:
        return str(self.sections["manifest"]["version"])

    @property
    def category(self) -> str:
        """``passenger`` or ``military`` -- drives which cockpit is drawn."""
        return str(self.sections["manifest"]["category"])

    @property
    def display_name(self) -> str:
        return str(self.sections["manifest"].get("display_name", self.name))

    @property
    def description(self) -> str:
        return str(self.sections["manifest"].get("description", ""))

    def get(self, section: str, key: str, default: Any = None) -> Any:
        """Fetch a dotted key from a section, converted to SI.

        ``model.get("limitations", "vmo")`` returns 174.9 from ``"340 kt"``
        regardless of how the data file chose to write it.
        """
        node: Any = self.sections.get(section)
        if node is None:
            if default is not None:
                return default
            raise KeyError(f"no section {section!r} in {self.path}")

        for part in key.split("."):
            if not isinstance(node, dict) or part not in node:
                if default is not None:
                    return default
                raise KeyError(f"{section}.{key} not found in {self.path}")
            node = node[part]

        if isinstance(node, (dict, list)):
            return node
        try:
            return to_si(node)
        except UnitError as exc:
            raise PackageError(self.path, [f"{section}.{key}: {exc}"]) from exc

    def raw(self, section: str, key: str, default: Any = None) -> Any:
        """Fetch without unit conversion, for strings and structures."""
        node: Any = self.sections.get(section, {})
        for part in key.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def has(self, section: str) -> bool:
        return section in self.sections


def _walk_units(node: Any, trail: str, problems: list[str]) -> None:
    """Recursively verify every scalar string parses as a physical quantity."""
    if isinstance(node, dict):
        for key, value in node.items():
            _walk_units(value, f"{trail}.{key}" if trail else str(key), problems)
    elif isinstance(node, list):
        for i, value in enumerate(node):
            _walk_units(value, f"{trail}[{i}]", problems)
    elif isinstance(node, str):
        # Free-text fields carry prose, not quantities; skip them.
        leaf = trail.rsplit(".", 1)[-1].split("[")[0]
        if leaf in _PROSE_KEYS:
            return
        try:
            to_si(node)
        except UnitError as exc:
            problems.append(f"{trail}: {exc}")


_PROSE_KEYS = {
    "name",
    "display_name",
    "description",
    "category",
    "source",
    "confidence",
    "assumptions",
    "notes",
    "version",
    "author",
    "range",
    "role",
    "engine_type",
    "gear_type",
    "position",
    "units",
}


def load_aircraft(path: str | Path) -> AircraftModel:
    """Load and validate an aircraft package directory."""
    root = Path(path)
    problems: list[str] = []

    if not root.is_dir():
        raise PackageError(root, ["package directory does not exist"])

    sections: dict[str, dict] = {}
    hasher = hashlib.sha256()

    for name in list(REQUIRED_SECTIONS) + list(OPTIONAL_SECTIONS):
        file_path = root / f"{name}.yaml"
        if not file_path.exists():
            if name in REQUIRED_SECTIONS:
                problems.append(f"{name}.yaml: mandatory file is missing")
            continue

        text = file_path.read_text(encoding="utf-8")
        hasher.update(name.encode("utf-8"))
        hasher.update(text.encode("utf-8"))

        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            problems.append(f"{name}.yaml: not valid YAML -- {exc}")
            continue

        if not isinstance(data, dict):
            problems.append(f"{name}.yaml: top level must be a mapping")
            continue

        sections[name] = data

    # Required keys.
    for name, keys in REQUIRED_SECTIONS.items():
        section = sections.get(name)
        if section is None:
            continue
        for key in keys:
            if key not in section:
                problems.append(f"{name}.yaml: required key {key!r} is missing")

    # Every quantity string must resolve to SI. The manifest is exempt: it
    # carries identity, provenance and prose, not physics, and running a unit
    # parser over an assumptions paragraph only produces false alarms.
    for name, section in sections.items():
        if name == "manifest":
            continue
        unit_problems: list[str] = []
        _walk_units(section, "", unit_problems)
        problems.extend(f"{name}.yaml: {problem}" for problem in unit_problems)

    if problems:
        raise PackageError(root, problems)

    model = AircraftModel(path=root, sections=sections, checksum=hasher.hexdigest())
    _validate_physics(model, problems)
    if problems:
        raise PackageError(root, problems)

    return model


def _validate_physics(model: AircraftModel, problems: list[str]) -> None:
    """Physical admissibility checks -- properties any real aircraft has.

    A transposed sign here produces an aircraft that flies and is wrong, which
    is far harder to diagnose later than a failed check now.
    """
    try:
        span = model.get("geometry", "wing_span")
        area = model.get("geometry", "wing_area")
        chord = model.get("geometry", "mean_chord")
    except (KeyError, PackageError):
        return

    if area <= 0 or span <= 0 or chord <= 0:
        problems.append("geometry: area, span and chord must all be positive")

    inertia = model.raw("mass_properties", "inertia", {})
    try:
        ixx = to_si(inertia["ixx"])
        iyy = to_si(inertia["iyy"])
        izz = to_si(inertia["izz"])
    except (KeyError, UnitError, TypeError):
        problems.append("mass_properties.inertia: needs ixx, iyy and izz")
        return

    if min(ixx, iyy, izz) <= 0.0:
        problems.append("mass_properties.inertia: principal moments must be positive")
    # The triangle inequality on principal moments: violate it and the tensor
    # describes no rigid body that exists.
    for a, b, c, label in (
        (ixx, iyy, izz, "ixx + iyy >= izz"),
        (iyy, izz, ixx, "iyy + izz >= ixx"),
        (izz, ixx, iyy, "izz + ixx >= iyy"),
    ):
        if a + b < c:
            problems.append(f"mass_properties.inertia: {label} is violated")

    try:
        cm_alpha = model.get("aerodynamics", "pitch.cm_alpha")
        if cm_alpha >= 0.0:
            problems.append(
                "aerodynamics.pitch.cm_alpha must be negative or the aircraft "
                "diverges in pitch"
            )
    except KeyError:
        pass

    try:
        cn_beta = model.get("aerodynamics", "lateral.cn_beta")
        if cn_beta <= 0.0:
            problems.append(
                "aerodynamics.lateral.cn_beta must be positive or the aircraft "
                "has no weathercock stability"
            )
    except KeyError:
        pass

    try:
        cl_beta = model.get("aerodynamics", "lateral.cl_beta")
        if cl_beta >= 0.0:
            problems.append(
                "aerodynamics.lateral.cl_beta must be negative for a stable "
                "dihedral effect"
            )
    except KeyError:
        pass

    try:
        empty = model.get("mass_properties", "empty_mass")
        mtom = model.get("mass_properties", "max_takeoff_mass")
        if empty >= mtom:
            problems.append("mass_properties: empty_mass must be below max_takeoff_mass")
    except KeyError:
        pass


def available_aircraft(root: str | Path) -> list[Path]:
    """List candidate package directories under ``root``."""
    base = Path(root)
    if not base.is_dir():
        return []
    return sorted(p for p in base.iterdir() if p.is_dir() and (p / "manifest.yaml").exists())

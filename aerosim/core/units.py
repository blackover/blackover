"""SI unit registry and boundary conversions.

Everything inside the simulator is metre, kilogram, second, radian, newton,
pascal and kelvin. Feet, knots, degrees and pounds exist only where data is
read from a file, where a value is shown to the pilot, and in test
expectations.

A single table defines every conversion factor. Nothing restates a factor
locally -- the duplicated-factor defect is easy to write and hard to find.
"""

from __future__ import annotations

import math
import re

# --------------------------------------------------------------------------
# Fundamental constants
# --------------------------------------------------------------------------

G0 = 9.80665  # standard gravity, m/s^2
R_AIR = 287.05287  # specific gas constant, dry air, J/(kg K)
GAMMA_AIR = 1.4  # ratio of specific heats
EARTH_RADIUS = 6356766.0  # ISA 1976 effective earth radius, m

# --------------------------------------------------------------------------
# Conversion registry: unit token -> multiplier that takes it TO SI
# --------------------------------------------------------------------------

_TO_SI: dict[str, float] = {
    # dimensionless / already SI
    "": 1.0,
    "-": 1.0,
    "1": 1.0,
    # length
    "m": 1.0,
    "km": 1000.0,
    "ft": 0.3048,
    "in": 0.0254,
    "nm": 1852.0,
    "sm": 1609.344,
    # area
    "m2": 1.0,
    "ft2": 0.3048**2,
    # volume
    "m3": 1.0,
    "l": 1.0e-3,
    "usgal": 3.785411784e-3,
    # speed
    "m/s": 1.0,
    "kt": 1852.0 / 3600.0,
    "kts": 1852.0 / 3600.0,
    "km/h": 1000.0 / 3600.0,
    "mph": 1609.344 / 3600.0,
    "ft/s": 0.3048,
    "fpm": 0.3048 / 60.0,
    "ft/min": 0.3048 / 60.0,
    # acceleration
    "m/s2": 1.0,
    "g": G0,
    # mass
    "kg": 1.0,
    "t": 1000.0,
    "lb": 0.45359237,
    "lbm": 0.45359237,
    # force
    "n": 1.0,
    "kn": 1000.0,  # kilonewton -- note: NOT knots, which is "kt"
    "lbf": 4.4482216152605,
    # moment of inertia
    "kgm2": 1.0,
    "slugft2": 1.3558179619,
    # pressure
    "pa": 1.0,
    "hpa": 100.0,
    "kpa": 1000.0,
    "mbar": 100.0,
    "inhg": 3386.389,
    "psi": 6894.757293168,
    # temperature (offsets handled separately; these are scale-only)
    "k": 1.0,
    "degc": 1.0,  # as a DIFFERENCE / offset, 1 degC == 1 K
    "degf": 5.0 / 9.0,  # as a DIFFERENCE
    # angle
    "rad": 1.0,
    "deg": math.pi / 180.0,
    # angular rate
    "rad/s": 1.0,
    "deg/s": math.pi / 180.0,
    "rpm": 2.0 * math.pi / 60.0,
    # time
    "s": 1.0,
    "sec": 1.0,
    "min": 60.0,
    "h": 3600.0,
    "hr": 3600.0,
    # per-angle derivatives
    "1/rad": 1.0,
    "1/deg": 180.0 / math.pi,
    # mass flow
    "kg/s": 1.0,
    "kg/h": 1.0 / 3600.0,
    "lb/h": 0.45359237 / 3600.0,
    # density
    "kg/m3": 1.0,
}


class UnitError(ValueError):
    """Raised when a unit string cannot be resolved."""


_VALUE_RE = re.compile(
    r"^\s*(?P<num>[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)\s*(?P<unit>.*?)\s*$"
)


def _normalise(unit: str) -> str:
    """Fold a unit token to its registry key."""
    u = unit.strip().lower()
    u = u.replace("^", "").replace("·", "").replace("*", "")
    u = u.replace("degrees", "deg").replace("degree", "deg")
    u = u.replace("radians", "rad").replace("radian", "rad")
    u = u.replace("meters", "m").replace("metres", "m")
    u = u.replace("seconds", "s").replace("second", "s")
    u = u.replace("°c", "degc").replace("°f", "degf").replace("°", "deg")
    return u


def to_si(value: float | int | str, expected_dim: str | None = None) -> float:
    """Convert a data-file value to SI.

    Accepts a bare number (assumed already SI) or a string carrying its unit,
    e.g. ``"340 kt"``, ``"25 deg"``, ``"-1.30 1/rad"``.

    An unknown unit is an error, never a silent pass-through: a typo that is
    quietly accepted becomes a wrong aircraft that still flies.
    """
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)

    if not isinstance(value, str):
        raise UnitError(f"cannot interpret {value!r} as a physical quantity")

    match = _VALUE_RE.match(value)
    if match is None:
        raise UnitError(f"malformed quantity {value!r}")

    number = float(match.group("num"))
    unit = _normalise(match.group("unit"))

    if unit not in _TO_SI:
        raise UnitError(
            f"unrecognised unit {match.group('unit')!r} in {value!r}; "
            f"known units: {', '.join(sorted(k for k in _TO_SI if k))}"
        )

    # Absolute temperature needs an offset, not just a scale.
    if unit == "degc":
        return number + 273.15
    if unit == "degf":
        return (number - 32.0) * 5.0 / 9.0 + 273.15

    return number * _TO_SI[unit]


def factor(unit: str) -> float:
    """Return the multiplier that converts ``unit`` to SI."""
    u = _normalise(unit)
    if u not in _TO_SI:
        raise UnitError(f"unrecognised unit {unit!r}")
    return _TO_SI[u]


# --------------------------------------------------------------------------
# Boundary helpers -- read as "this many feet, in SI"
# --------------------------------------------------------------------------


def ft(value: float) -> float:
    """Feet to metres."""
    return value * _TO_SI["ft"]


def kt(value: float) -> float:
    """Knots to metres per second."""
    return value * _TO_SI["kt"]


def fpm(value: float) -> float:
    """Feet per minute to metres per second."""
    return value * _TO_SI["fpm"]


def deg(value: float) -> float:
    """Degrees to radians."""
    return value * _TO_SI["deg"]


def lb(value: float) -> float:
    """Pounds mass to kilograms."""
    return value * _TO_SI["lb"]


def nm(value: float) -> float:
    """Nautical miles to metres."""
    return value * _TO_SI["nm"]


# --------------------------------------------------------------------------
# Display helpers -- SI out to aviation units, for the instrument panel
# --------------------------------------------------------------------------


def to_ft(value: float) -> float:
    return value / _TO_SI["ft"]


def to_kt(value: float) -> float:
    return value / _TO_SI["kt"]


def to_fpm(value: float) -> float:
    return value / _TO_SI["fpm"]


def to_deg(value: float) -> float:
    return value / _TO_SI["deg"]


def to_lb(value: float) -> float:
    return value / _TO_SI["lb"]


def to_nm(value: float) -> float:
    return value / _TO_SI["nm"]


def to_degc(value: float) -> float:
    """Kelvin to degrees Celsius."""
    return value - 273.15


def to_inhg(value: float) -> float:
    return value / _TO_SI["inhg"]


# --------------------------------------------------------------------------
# Angle handling
# --------------------------------------------------------------------------


def wrap_pi(angle: float) -> float:
    """Wrap an angle to (-pi, pi]."""
    wrapped = math.fmod(angle + math.pi, 2.0 * math.pi)
    if wrapped <= 0.0:
        wrapped += 2.0 * math.pi
    return wrapped - math.pi


def wrap_2pi(angle: float) -> float:
    """Wrap an angle to [0, 2pi)."""
    wrapped = math.fmod(angle, 2.0 * math.pi)
    if wrapped < 0.0:
        wrapped += 2.0 * math.pi
    return wrapped


def clamp(value: float, low: float, high: float) -> float:
    """Constrain a value to [low, high]."""
    if low > high:
        raise ValueError(f"clamp bounds inverted: {low} > {high}")
    return low if value < low else (high if value > high else value)

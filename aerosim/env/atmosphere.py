"""U.S. Standard Atmosphere 1976, with an ISA temperature offset.

The layer equations are defined on *geopotential* altitude; aircraft measure
*geometric* altitude. Treating the two as identical is worth 19 m at 11 km and
250 m at 40 km, so the conversion is done explicitly here and nowhere else.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ..core.units import EARTH_RADIUS, G0, GAMMA_AIR, R_AIR

# Base of each layer: geopotential altitude (m), temperature (K), lapse (K/m).
_LAYERS: list[tuple[float, float, float]] = [
    (0.0, 288.15, -0.0065),
    (11000.0, 216.65, 0.0),
    (20000.0, 216.65, 0.001),
    (32000.0, 228.65, 0.0028),
    (47000.0, 270.65, 0.0),
    (51000.0, 270.65, -0.0028),
    (71000.0, 214.65, -0.002),
]
_TOP = 84852.0

SEA_LEVEL_PRESSURE = 101325.0
SEA_LEVEL_DENSITY = 1.225
SEA_LEVEL_TEMPERATURE = 288.15
SEA_LEVEL_SOUND_SPEED = math.sqrt(GAMMA_AIR * R_AIR * SEA_LEVEL_TEMPERATURE)


def _base_pressures() -> list[float]:
    """Integrate the hydrostatic equation once, at import, up the layer stack."""
    pressures = [SEA_LEVEL_PRESSURE]
    for i in range(len(_LAYERS) - 1):
        h0, t0, lapse = _LAYERS[i]
        h1, _, _ = _LAYERS[i + 1]
        p0 = pressures[i]
        if abs(lapse) < 1.0e-12:
            p1 = p0 * math.exp(-G0 * (h1 - h0) / (R_AIR * t0))
        else:
            t1 = t0 + lapse * (h1 - h0)
            p1 = p0 * (t1 / t0) ** (-G0 / (R_AIR * lapse))
        pressures.append(p1)
    return pressures


_BASE_PRESSURE = _base_pressures()


def geometric_to_geopotential(h: float) -> float:
    """Geometric altitude (what an aircraft flies at) to geopotential."""
    return EARTH_RADIUS * h / (EARTH_RADIUS + h)


def geopotential_to_geometric(h: float) -> float:
    return EARTH_RADIUS * h / (EARTH_RADIUS - h)


@dataclass(frozen=True)
class AtmosphereSample:
    """Air state at one altitude."""

    altitude: float  # geometric, m
    temperature: float  # K
    pressure: float  # Pa
    density: float  # kg/m^3
    sound_speed: float  # m/s

    @property
    def density_ratio(self) -> float:
        return self.density / SEA_LEVEL_DENSITY

    @property
    def pressure_ratio(self) -> float:
        return self.pressure / SEA_LEVEL_PRESSURE

    @property
    def temperature_ratio(self) -> float:
        return self.temperature / SEA_LEVEL_TEMPERATURE


class Atmosphere:
    """ISA 1976 with a constant temperature offset.

    The offset changes temperature and therefore density, but leaves the
    pressure column unchanged. Pressure-altitude relationships on a hot day are
    consequently not strictly correct; the density effect -- the part that
    governs aircraft performance -- is.
    """

    def __init__(self, temperature_offset: float = 0.0, sea_level_pressure: float = SEA_LEVEL_PRESSURE) -> None:
        self.temperature_offset = float(temperature_offset)
        self.sea_level_pressure = float(sea_level_pressure)
        self._qnh_ratio = self.sea_level_pressure / SEA_LEVEL_PRESSURE

    def sample(self, altitude: float) -> AtmosphereSample:
        """Air state at a geometric altitude in metres."""
        h = geometric_to_geopotential(max(-5000.0, min(altitude, _TOP)))

        index = 0
        for i, (base_h, _, _) in enumerate(_LAYERS):
            if h >= base_h:
                index = i
            else:
                break

        base_h, base_t, lapse = _LAYERS[index]
        base_p = _BASE_PRESSURE[index]

        temperature = base_t + lapse * (h - base_h)
        if abs(lapse) < 1.0e-12:
            pressure = base_p * math.exp(-G0 * (h - base_h) / (R_AIR * base_t))
        else:
            pressure = base_p * (temperature / base_t) ** (-G0 / (R_AIR * lapse))

        pressure *= self._qnh_ratio
        temperature += self.temperature_offset

        density = pressure / (R_AIR * temperature)
        sound_speed = math.sqrt(GAMMA_AIR * R_AIR * temperature)

        return AtmosphereSample(
            altitude=altitude,
            temperature=temperature,
            pressure=pressure,
            density=density,
            sound_speed=sound_speed,
        )

    # -- airspeed relationships -------------------------------------------

    def eas_from_tas(self, vtas: float, altitude: float) -> float:
        """Equivalent airspeed: TAS scaled by the square root of density ratio."""
        return vtas * math.sqrt(self.sample(altitude).density_ratio)

    def tas_from_eas(self, veas: float, altitude: float) -> float:
        ratio = self.sample(altitude).density_ratio
        return veas / math.sqrt(ratio) if ratio > 0.0 else 0.0

    def cas_from_tas(self, vtas: float, altitude: float) -> float:
        """Calibrated airspeed via compressible (subsonic) impact pressure.

        Above M 1 the subsonic relation is wrong, but this simulator does not
        claim supersonic validity, so the subsonic form is used throughout and
        the envelope monitor flags the region where it stops meaning anything.
        """
        air = self.sample(altitude)
        if vtas <= 0.0 or air.pressure <= 0.0:
            return 0.0

        mach = vtas / air.sound_speed
        exponent = GAMMA_AIR / (GAMMA_AIR - 1.0)

        # Impact pressure from static pressure and Mach.
        qc = air.pressure * ((1.0 + 0.2 * mach * mach) ** exponent - 1.0)

        # Invert at sea level to recover CAS.
        inner = qc / SEA_LEVEL_PRESSURE + 1.0
        bracket = inner ** (1.0 / exponent) - 1.0
        if bracket < 0.0:
            return 0.0
        return SEA_LEVEL_SOUND_SPEED * math.sqrt(5.0 * bracket)

    def tas_from_cas(self, vcas: float, altitude: float) -> float:
        """Invert ``cas_from_tas`` by bisection.

        Bisection rather than a closed form because the closed form has to be
        rederived if the CAS relation ever changes, and one of the two would
        then be quietly wrong.
        """
        if vcas <= 0.0:
            return 0.0
        low, high = 0.0, max(400.0, vcas * 4.0)
        for _ in range(60):
            mid = 0.5 * (low + high)
            if self.cas_from_tas(mid, altitude) < vcas:
                low = mid
            else:
                high = mid
        return 0.5 * (low + high)

    def pressure_altitude(self, altitude: float) -> float:
        """Altitude a pressure altimeter set to 1013.25 hPa would indicate."""
        p = self.sample(altitude).pressure
        # Invert the troposphere relation; adequate below the tropopause and
        # this is an indication, not a navigation source.
        ratio = p / SEA_LEVEL_PRESSURE
        h_geopotential = (SEA_LEVEL_TEMPERATURE / 0.0065) * (1.0 - ratio ** (1.0 / 5.255876))
        return geopotential_to_geometric(h_geopotential)

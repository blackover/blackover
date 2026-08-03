"""Wind field and turbulence.

Wind is horizontally uniform and varies with altitude only: no microbursts, no
shear fronts, no terrain-induced flow. Layer interpolation is linear and the
outermost layers are held constant beyond the ends of the table.

Turbulence is a Dryden-*like* gust model realised as three first-order shaping
filters rather than the full second-order transfer functions of MIL-F-8785C.
The objective is a plausible, bounded and above all *reproducible* disturbance
for handling and autopilot work, not spectral fidelity. The power spectral
density consequently rolls off at 20 dB/decade instead of 30, so high-frequency
gust energy is overstated relative to the standard.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np


@dataclass
class WindLayer:
    """Wind at one altitude: direction is where the wind blows *from*."""

    altitude: float  # m
    direction: float  # rad, meteorological (from), true
    speed: float  # m/s


# Turbulence intensity presets, as an RMS gust velocity in m/s at low level.
TURBULENCE_PRESETS: dict[str, float] = {
    "none": 0.0,
    "light": 1.5,
    "moderate": 3.5,
    "severe": 6.5,
    "extreme": 10.0,
}


class WindField:
    """Layered mean wind plus optional seeded turbulence.

    Every stochastic source draws from its own generator seeded from the run
    seed, so adding a consumer in one subsystem cannot perturb another's
    sequence. Two runs with the same seed produce the same gusts, which is what
    makes a turbulence-sensitive result reproducible at all.
    """

    def __init__(
        self,
        layers: list[WindLayer] | None = None,
        turbulence_intensity: float = 0.0,
        seed: int = 1,
        scale_length: float = 533.4,
    ) -> None:
        self.layers = sorted(layers or [], key=lambda layer: layer.altitude)
        self.turbulence_intensity = max(0.0, float(turbulence_intensity))
        self.scale_length = scale_length  # Dryden L, m (1750 ft above 1750 ft)
        self._rng = np.random.default_rng(seed)
        self._gust = np.zeros(3)
        self.gust_ned = np.zeros(3)

    # -- mean wind ---------------------------------------------------------

    def mean_wind_ned(self, altitude: float) -> np.ndarray:
        """Mean wind velocity vector in NED, m/s.

        Returned as the velocity *of the air*, so a 20 kt wind *from* the north
        is a vector pointing south.
        """
        if not self.layers:
            return np.zeros(3)

        if altitude <= self.layers[0].altitude:
            layer = self.layers[0]
            speed, direction = layer.speed, layer.direction
        elif altitude >= self.layers[-1].altitude:
            layer = self.layers[-1]
            speed, direction = layer.speed, layer.direction
        else:
            upper_index = next(
                i for i, layer in enumerate(self.layers) if layer.altitude > altitude
            )
            lower = self.layers[upper_index - 1]
            upper = self.layers[upper_index]
            span = upper.altitude - lower.altitude
            t = 0.0 if span <= 0.0 else (altitude - lower.altitude) / span
            speed = lower.speed + t * (upper.speed - lower.speed)
            # Interpolate through the shorter arc so 350 deg -> 010 deg does
            # not sweep the long way round through south.
            delta = math.atan2(
                math.sin(upper.direction - lower.direction),
                math.cos(upper.direction - lower.direction),
            )
            direction = lower.direction + t * delta

        # From-direction to air velocity vector.
        return np.array(
            [-speed * math.cos(direction), -speed * math.sin(direction), 0.0]
        )

    # -- turbulence --------------------------------------------------------

    def _intensity_at(self, altitude: float) -> float:
        """Linear altitude scaling of gust intensity.

        Not the MIL-F-8785C probability-of-exceedance model: turbulence fades
        linearly from full strength at the surface to a third of it at 30 000
        ft, which is a serviceable approximation and is documented as one.
        """
        if self.turbulence_intensity <= 0.0:
            return 0.0
        fade = 1.0 - (2.0 / 3.0) * min(1.0, max(0.0, altitude / 9144.0))
        return self.turbulence_intensity * fade

    def update_turbulence(self, dt: float, vtas: float, altitude: float) -> np.ndarray:
        """Advance the gust filters one step, returning the gust in NED."""
        sigma = self._intensity_at(altitude)
        if sigma <= 0.0 or vtas < 1.0:
            self._gust[:] = 0.0
            self.gust_ned = self._gust.copy()
            return self.gust_ned

        # First-order shaping filter: tau = L / V, driven by white noise scaled
        # so the steady-state standard deviation of the output is sigma.
        tau = max(self.scale_length / max(vtas, 1.0), 5.0 * dt)
        beta = math.exp(-dt / tau)
        drive = sigma * math.sqrt(1.0 - beta * beta)

        noise = self._rng.standard_normal(3)
        self._gust = beta * self._gust + drive * noise

        # Vertical gusts in a real atmosphere are weaker than horizontal ones.
        self.gust_ned = self._gust * np.array([1.0, 1.0, 0.7])
        return self.gust_ned

    def wind_ned(self, altitude: float) -> np.ndarray:
        """Total air velocity in NED: mean wind plus the current gust."""
        return self.mean_wind_ned(altitude) + self.gust_ned

    @classmethod
    def uniform(
        cls,
        speed: float,
        direction: float,
        turbulence_intensity: float = 0.0,
        seed: int = 1,
    ) -> "WindField":
        """A single-layer field: the same wind at every altitude."""
        return cls(
            layers=[WindLayer(0.0, direction, speed)],
            turbulence_intensity=turbulence_intensity,
            seed=seed,
        )

    @classmethod
    def sheared(
        cls,
        surface_speed: float,
        surface_direction: float,
        aloft_speed: float,
        aloft_direction: float,
        aloft_altitude: float,
        turbulence_intensity: float = 0.0,
        seed: int = 1,
    ) -> "WindField":
        """Surface wind veering and strengthening with height."""
        return cls(
            layers=[
                WindLayer(0.0, surface_direction, surface_speed),
                WindLayer(aloft_altitude, aloft_direction, aloft_speed),
            ],
            turbulence_intensity=turbulence_intensity,
            seed=seed,
        )

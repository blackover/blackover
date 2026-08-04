"""Procedural terrain elevation.

One heightfield, used by both the renderer and the flight model. That is the
whole point of putting it here rather than in the game layer: hills you can see
but cannot hit are worse than no hills at all, because the picture then
contradicts the simulation it is supposed to be showing.

The field is deterministic -- a hash of the integer lattice, no stored state --
so the same seed produces the same landscape on every machine and in every
process, and a replay of a run lands in the same valley it took off from.

The airport sits on a flat plateau. Approach guidance, gear reaction and the
autoland flare all assume level ground near the runway, and a hill in the
touchdown zone would be a trap rather than a feature.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

# Large primes for the lattice hash. Any decent mix works; these are the ones
# used elsewhere in this project for the ground patchwork, kept the same so the
# two never disagree about which cell is which.
_HASH_X = 73856093
_HASH_Y = 19349663
_HASH_Z = 83492791

_UINT64 = (1 << 64) - 1


def _lattice_noise(ix: np.ndarray, iy: np.ndarray, seed: int) -> np.ndarray:
    """Deterministic pseudo-random value in [-1, 1] at integer lattice points."""
    h = (ix.astype(np.int64) * _HASH_X) ^ (iy.astype(np.int64) * _HASH_Y)
    h ^= (seed + 1) * _HASH_Z
    h = (h ^ (h >> 13)) * 1274126177
    h = h ^ (h >> 16)
    return ((h & 0xFFFF).astype(np.float64) / 32767.5) - 1.0


def _wrap64(value: int) -> int:
    """Signed 64-bit wraparound, which is what the numpy path does implicitly."""
    value &= _UINT64
    return value - (1 << 64) if value >> 63 else value


def _lattice_noise_scalar(ix: int, iy: int, seed: int) -> float:
    """Scalar twin of :func:`_lattice_noise`, bit-for-bit identical.

    Python's ``>>`` on negative integers is arithmetic and its ``^`` and ``&``
    behave as infinite-width two's complement, so both agree with int64 on the
    low bits; only multiplication has to be wrapped by hand.
    """
    h = _wrap64(ix * _HASH_X) ^ _wrap64(iy * _HASH_Y)
    h ^= _wrap64((seed + 1) * _HASH_Z)
    h = _wrap64((h ^ (h >> 13)) * 1274126177)
    h ^= h >> 16
    return ((h & 0xFFFF) / 32767.5) - 1.0


def _smoothstep(t: np.ndarray) -> np.ndarray:
    return t * t * (3.0 - 2.0 * t)


@dataclass
class TerrainProfile:
    """How dramatic the landscape is."""

    name: str = "rolling"
    amplitude: float = 220.0  # m, peak-to-trough of the largest octave
    base_wavelength: float = 9000.0  # m
    octaves: int = 4
    roughness: float = 0.5  # amplitude falloff per octave
    ridge: float = 0.0  # 0 rolling hills, 1 sharp ridges


PROFILES: dict[str, TerrainProfile] = {
    "flat": TerrainProfile("flat", amplitude=0.0, octaves=1),
    "gentle": TerrainProfile("gentle", amplitude=90.0, base_wavelength=11000.0),
    "rolling": TerrainProfile("rolling", amplitude=260.0, base_wavelength=9000.0),
    "hilly": TerrainProfile("hilly", amplitude=620.0, base_wavelength=8000.0, ridge=0.35),
    "mountainous": TerrainProfile(
        "mountainous", amplitude=1500.0, base_wavelength=12000.0, octaves=5, ridge=0.6
    ),
}


class Terrain:
    """Ground elevation as a function of position.

    ``height`` is the authority: the renderer draws it and the gear stands on
    it. There is deliberately no second copy of this surface anywhere.
    """

    def __init__(
        self,
        *,
        seed: int = 1,
        profile: str | TerrainProfile = "rolling",
        field_elevation: float = 0.0,
        airport_radius: float = 6000.0,
        airport_blend: float = 9000.0,
    ) -> None:
        self.seed = int(seed)
        self.profile = (
            profile if isinstance(profile, TerrainProfile) else PROFILES[str(profile)]
        )
        self.field_elevation = float(field_elevation)
        self.airport_radius = float(airport_radius)
        self.airport_blend = max(1.0, float(airport_blend))

    @property
    def is_flat(self) -> bool:
        return self.profile.amplitude <= 0.0

    # ---------------------------------------------------------------------

    def _octave(self, north: np.ndarray, east: np.ndarray, wavelength: float, index: int) -> np.ndarray:
        """One octave of smoothed value noise."""
        x = north / wavelength
        y = east / wavelength
        ix = np.floor(x)
        iy = np.floor(y)
        fx = _smoothstep(x - ix)
        fy = _smoothstep(y - iy)

        n00 = _lattice_noise(ix, iy, self.seed + index)
        n10 = _lattice_noise(ix + 1, iy, self.seed + index)
        n01 = _lattice_noise(ix, iy + 1, self.seed + index)
        n11 = _lattice_noise(ix + 1, iy + 1, self.seed + index)

        top = n00 + (n10 - n00) * fx
        bottom = n01 + (n11 - n01) * fx
        return top + (bottom - top) * fy

    def _airport_blend(self, north: np.ndarray, east: np.ndarray) -> np.ndarray:
        """1 out in the hills, 0 on the airport plateau."""
        distance = np.hypot(north, east)
        t = (distance - self.airport_radius) / self.airport_blend
        return _smoothstep(np.clip(t, 0.0, 1.0))

    def heights(self, north, east) -> np.ndarray:
        """Ground elevation at an array of positions, metres."""
        north = np.asarray(north, dtype=float)
        east = np.asarray(east, dtype=float)

        if self.is_flat:
            return np.full(north.shape, self.field_elevation)

        total = np.zeros(north.shape)
        amplitude = self.profile.amplitude
        wavelength = self.profile.base_wavelength
        norm = 0.0

        for octave in range(self.profile.octaves):
            value = self._octave(north, east, wavelength, octave)
            if self.profile.ridge > 0.0:
                # Ridged noise: fold the field about zero so valleys become
                # crests. Blended rather than switched, so a profile can be
                # part rolling and part ridged.
                ridged = 1.0 - 2.0 * np.abs(value)
                value = value * (1.0 - self.profile.ridge) + ridged * self.profile.ridge
            total += value * amplitude
            norm += amplitude
            amplitude *= self.profile.roughness
            wavelength *= 0.5

        if norm > 0.0:
            total *= self.profile.amplitude / norm * 1.6

        # Lift the whole field so the airport is not sitting in a hole.
        total = np.maximum(total, -self.profile.amplitude * 0.55)
        return self.field_elevation + total * self._airport_blend(north, east)

    def height_at(self, north: float, east: float) -> float:
        """Ground elevation at one position, metres.

        Deliberately not ``heights()`` on a one-element array. This is the
        hot path -- every strut queries it inside every RK4 stage -- and numpy
        on single scalars is all overhead: the array version measured 278 us
        per call, which put 5 ms of pure dispatch into every rendered frame
        with the gear down. The plain-Python version below is ~40x faster and
        is tested to agree with the vectorised one to the last bit.
        """
        if self.is_flat:
            return self.field_elevation

        profile = self.profile
        amplitude = profile.amplitude
        wavelength = profile.base_wavelength
        ridge = profile.ridge
        seed = self.seed
        total = 0.0
        norm = 0.0

        for octave in range(profile.octaves):
            x = north / wavelength
            y = east / wavelength
            ix = math.floor(x)
            iy = math.floor(y)
            fx = x - ix
            fy = y - iy
            fx = fx * fx * (3.0 - 2.0 * fx)
            fy = fy * fy * (3.0 - 2.0 * fy)

            octave_seed = seed + octave
            n00 = _lattice_noise_scalar(ix, iy, octave_seed)
            n10 = _lattice_noise_scalar(ix + 1, iy, octave_seed)
            n01 = _lattice_noise_scalar(ix, iy + 1, octave_seed)
            n11 = _lattice_noise_scalar(ix + 1, iy + 1, octave_seed)

            top = n00 + (n10 - n00) * fx
            bottom = n01 + (n11 - n01) * fx
            value = top + (bottom - top) * fy

            if ridge > 0.0:
                value = value * (1.0 - ridge) + (1.0 - 2.0 * abs(value)) * ridge

            total += value * amplitude
            norm += amplitude
            amplitude *= profile.roughness
            wavelength *= 0.5

        if norm > 0.0:
            total *= profile.amplitude / norm * 1.6
        total = max(total, -profile.amplitude * 0.55)

        distance = math.hypot(north, east)
        blend = (distance - self.airport_radius) / self.airport_blend
        blend = 0.0 if blend < 0.0 else (1.0 if blend > 1.0 else blend)
        blend = blend * blend * (3.0 - 2.0 * blend)

        return self.field_elevation + total * blend

    def slope_at(self, north: float, east: float, step: float = 30.0) -> tuple[float, float]:
        """Ground gradient, for a future slope-aware gear model."""
        if self.is_flat:
            return 0.0, 0.0
        dn = (self.height_at(north + step, east) - self.height_at(north - step, east)) / (
            2.0 * step
        )
        de = (self.height_at(north, east + step) - self.height_at(north, east - step)) / (
            2.0 * step
        )
        return dn, de

    def highest_within(self, north: float, east: float, radius: float, samples: int = 9) -> float:
        """Highest ground within a radius -- for terrain awareness."""
        if self.is_flat:
            return self.field_elevation
        offsets = np.linspace(-radius, radius, samples)
        grid_n, grid_e = np.meshgrid(offsets + north, offsets + east, indexing="ij")
        return float(self.heights(grid_n, grid_e).max())


def flat_terrain(field_elevation: float = 0.0) -> Terrain:
    """The classic single flat plane, for scenarios that assume one."""
    return Terrain(profile="flat", field_elevation=field_elevation)

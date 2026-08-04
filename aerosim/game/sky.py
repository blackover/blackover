"""Sky, sun and cloud rendering.

Split out of the renderer because it is the part with no geometry in it: a
gradient, a light source, and a field of billboards. Everything here is
precomputed once and blitted, because a per-pixel sky costs more per frame than
the entire terrain.
"""

from __future__ import annotations

import math

import numpy as np
import pygame


def lerp(a, b, t: float):
    t = max(0.0, min(1.0, t))
    return tuple(int(round(x + (y - x) * t)) for x, y in zip(a, b))


class Sky:
    """Sky colours, sun direction and a cloud field, for one time of day."""

    def __init__(
        self,
        time_of_day: float,
        visibility: float,
        *,
        seed: int = 1,
        cloud_cover: float = 0.45,
        cloud_base: float = 1800.0,
    ) -> None:
        self.time_of_day = time_of_day
        self.visibility = max(1000.0, visibility)
        self.cloud_cover = max(0.0, min(1.0, cloud_cover))
        self.cloud_base = cloud_base

        # Sun elevation: a crude but continuous day cycle. Noon overhead,
        # sunrise and sunset near 06:00 and 18:00.
        self.sun_elevation = math.radians(
            72.0 * math.sin(math.pi * (time_of_day - 6.0) / 12.0)
        )
        self.sun_azimuth = math.radians(90.0 + 15.0 * (time_of_day - 12.0))

        # Direction FROM the sun toward the scene, in NED.
        self.sun_ned = np.array(
            [
                -math.cos(self.sun_elevation) * math.cos(self.sun_azimuth),
                -math.cos(self.sun_elevation) * math.sin(self.sun_azimuth),
                math.sin(self.sun_elevation),
            ]
        )
        # And the direction TOWARD the sun, which is what a billboard needs.
        self.to_sun_ned = -self.sun_ned

        daylight = max(0.0, min(1.0, (math.degrees(self.sun_elevation) + 6.0) / 18.0))
        # Low sun reddens everything; the band is narrow so noon is unaffected.
        low = max(0.0, 1.0 - abs(math.degrees(self.sun_elevation)) / 14.0)
        self.daylight = daylight
        self.golden = low * daylight

        night_top, night_bottom = (7, 10, 24), (20, 26, 46)
        day_top, day_bottom = (44, 104, 190), (150, 194, 238)
        golden_bottom = (238, 152, 88)
        golden_top = (86, 96, 170)

        self.zenith = lerp(night_top, day_top, daylight)
        self.zenith = lerp(self.zenith, golden_top, self.golden * 0.5)
        horizon = lerp(night_bottom, day_bottom, daylight)
        self.horizon = lerp(horizon, golden_bottom, self.golden * 0.8)

        # Ground palette by elevation band. Low ground is greener, high ground
        # turns to rock and then snow -- which is what makes relief readable at
        # a glance rather than a uniform sheet with a wobbly horizon.
        self.ground_low = lerp((16, 22, 18), (74, 104, 56), daylight)
        self.ground_mid = lerp((22, 22, 18), (104, 108, 66), daylight)
        self.ground_high = lerp((26, 25, 24), (128, 118, 104), daylight)
        self.ground_peak = lerp((44, 46, 54), (232, 236, 242), daylight)
        self.water = lerp((10, 14, 24), (58, 96, 132), daylight)

        self.ambient = 0.32 + 0.26 * daylight
        self.sun_colour = lerp((255, 244, 214), (255, 176, 108), self.golden)

        self._gradient: pygame.Surface | None = None
        self._gradient_size: tuple[int, int] = (0, 0)
        self._sun_sprite: pygame.Surface | None = None
        self._cloud_sprites: list[pygame.Surface] = []
        self._clouds: np.ndarray | None = None
        self._cloud_seed = seed

    # -- colour helpers ----------------------------------------------------

    # Haze as a stretched exponential rather than a plain one:
    #
    #     t = 1 - exp(-(d / (visibility * SCALE)) ** POWER)
    #
    # A plain exponential has to choose. Tuned so the far ground actually
    # reaches the horizon colour, it washes the middle distance out to a flat
    # sheet and the relief that took all the work to compute stops being
    # visible; tuned to keep the middle distance clear, the ground meets the
    # sky along a hard line that reads as the edge of a painted backdrop.
    # A power above one is flat near the eye and saturates hard beyond the
    # stated visibility, which is both of those at once. At 45 km visibility
    # the numbers are 5 % blended at 10 km, 35 % at 20 km, 80 % at 45 km and
    # indistinguishable from sky past 100 km.
    HAZE_SCALE = 0.75
    HAZE_POWER = 1.6
    HAZE_MAX = 1.0

    def haze(self, colour, distance: float):
        """Blend a colour toward the horizon with distance."""
        reduced = distance / (self.visibility * self.HAZE_SCALE)
        t = 1.0 - math.exp(-(reduced**self.HAZE_POWER))
        return lerp(colour, self.horizon, t * self.HAZE_MAX)

    def haze_array(self, colours: np.ndarray, distance: np.ndarray) -> np.ndarray:
        """Vectorised haze, for the whole terrain grid at once."""
        reduced = distance / (self.visibility * self.HAZE_SCALE)
        t = (1.0 - np.exp(-(reduced**self.HAZE_POWER))) * self.HAZE_MAX
        horizon = np.array(self.horizon, dtype=float)
        return np.clip(colours + (horizon - colours) * t[:, None], 0, 255)

    def shade(self, colour, normal_ned: np.ndarray):
        """Lambert shading against the sun, floored at the ambient level."""
        lit = float(-(normal_ned @ self.sun_ned))
        level = max(0.0, min(1.25, self.ambient + (1.0 - self.ambient) * max(0.0, lit)))
        return tuple(int(max(0, min(255, round(c * level)))) for c in colour)

    # -- gradient ----------------------------------------------------------

    def gradient(self, width: int, height: int) -> pygame.Surface:
        """The sky backdrop, built once per viewport size.

        Twice the viewport tall, with the horizon colour exactly at the middle
        row: the caller blits it offset so that row lands on the projected
        horizon. Anchoring it to the screen instead leaves the sky still half
        zenith-blue where the fully hazed ground has already reached the
        horizon colour, and the two meet along a hard line that reads as the
        edge of a painted backdrop rather than as distance.

        Below the horizon it stays horizon-coloured, which is what shows
        through any gap the terrain rings leave.

        One pixel wide and scaled up: a per-pixel gradient over the whole
        viewport every frame costs more than the entire terrain does.
        """
        if self._gradient is not None and self._gradient_size == (width, height):
            return self._gradient

        span = max(2, height)
        strip = pygame.Surface((1, span * 2))
        for y in range(span):
            # Above the horizon: 0 at the horizon, 1 a full viewport higher.
            t = (span - y) / span
            strip.set_at((0, y), lerp(self.horizon, self.zenith, t**0.72))
        for y in range(span, span * 2):
            strip.set_at((0, y), self.horizon)

        self._gradient = pygame.transform.scale(strip, (width, span * 2))
        self._gradient_size = (width, height)
        return self._gradient

    # -- sun ---------------------------------------------------------------

    def sun_sprite(self) -> pygame.Surface:
        """Sun disc with a soft halo, drawn once into a surface with alpha."""
        if self._sun_sprite is not None:
            return self._sun_sprite

        size = 320
        sprite = pygame.Surface((size, size), pygame.SRCALPHA)
        centre = size // 2
        colour = self.sun_colour

        # Halo: concentric circles with falling alpha. Cheaper than a per-pixel
        # radial gradient and indistinguishable once it is on screen.
        for i in range(46, 0, -1):
            radius = int(centre * i / 46)
            alpha = int(120 * (1.0 - i / 46) ** 2.4)
            if alpha <= 0:
                continue
            pygame.draw.circle(sprite, (*colour, alpha), (centre, centre), radius)
        pygame.draw.circle(sprite, (*colour, 255), (centre, centre), int(size * 0.055))

        self._sun_sprite = sprite
        return sprite

    # -- clouds ------------------------------------------------------------

    def cloud_sprites(self) -> list[pygame.Surface]:
        """A ladder of pre-scaled puffs.

        Pre-scaling rather than scaling per frame: a smoothscale per puff per
        frame is the difference between clouds costing nothing and clouds
        costing more than the aircraft.
        """
        if self._cloud_sprites:
            return self._cloud_sprites

        base = 256
        master = pygame.Surface((base, base), pygame.SRCALPHA)
        rng = np.random.default_rng(self._cloud_seed)

        lit = lerp((255, 255, 255), self.sun_colour, 0.35)
        lit = lerp(lit, (120, 126, 140), 1.0 - self.daylight)
        shadowed = lerp(lit, (96, 104, 124), 0.45)

        # A puff is a handful of overlapping blobs, brighter on the sunward
        # side, so it reads as volume rather than as a disc.
        for i in range(9):
            angle = rng.uniform(0, 2 * math.pi)
            radius = rng.uniform(0.10, 0.32) * base
            distance = rng.uniform(0.0, 0.26) * base
            cx = base / 2 + math.cos(angle) * distance
            cy = base / 2 + math.sin(angle) * distance * 0.55
            colour = lit if cy < base / 2 else shadowed
            for layer in range(7, 0, -1):
                alpha = int(62 * (1.0 - layer / 8.0) ** 1.4)
                pygame.draw.circle(
                    master, (*colour, max(10, alpha)),
                    (int(cx), int(cy)), int(radius * layer / 7.0),
                )

        self._cloud_sprites = [
            pygame.transform.smoothscale(master, (size, size))
            for size in (16, 24, 36, 54, 80, 120, 180, 256, 384)
        ]
        return self._cloud_sprites

    def clouds(self, extent: float = 90000.0, count: int = 260) -> np.ndarray:
        """Puff positions in NED, laid out once and reused every frame.

        Two layers at different heights rather than one deck. A single deck is
        realistic and almost never on screen: from below it sits overhead,
        which a level chase camera cannot see, and from above it is a floor you
        only notice at the horizon. Spreading the puffs vertically keeps some
        of them in the view wherever the aircraft happens to be.
        """
        if self._clouds is not None:
            return self._clouds
        if self.cloud_cover <= 0.0:
            self._clouds = np.zeros((0, 4))
            return self._clouds

        rng = np.random.default_rng(self._cloud_seed + 977)
        rows = []

        # Cumulus layer: lumpy, near the aircraft's usual working altitudes.
        n = max(1, int(count * 0.62 * self.cloud_cover))
        base = self.cloud_base
        rows.append(
            np.stack(
                [
                    rng.uniform(-extent, extent, n),
                    rng.uniform(-extent, extent, n),
                    -(base * rng.uniform(0.7, 2.2, n)),
                    rng.uniform(700.0, 2600.0, n),
                ],
                axis=1,
            )
        )

        # High, thin layer, which is what gives the sky depth at altitude.
        n = max(1, int(count * 0.38 * self.cloud_cover))
        rows.append(
            np.stack(
                [
                    rng.uniform(-extent * 1.6, extent * 1.6, n),
                    rng.uniform(-extent * 1.6, extent * 1.6, n),
                    -(base * rng.uniform(3.4, 5.2, n)),
                    rng.uniform(2600.0, 7000.0, n),
                ],
                axis=1,
            )
        )

        self._clouds = np.concatenate(rows, axis=0)
        return self._clouds

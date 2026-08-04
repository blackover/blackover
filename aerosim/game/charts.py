"""Strip charts: the flight as a time history rather than as a needle.

An instrument tells you what a quantity is now. A trace tells you what it has
been doing, which is a different and often more useful thing: a speed five
knots above the stall is fine if it is steady and an emergency if it has been
falling for ten seconds, and the two look identical on a tape.

The charts sample on *simulation* time, not wall time, so pausing pauses them,
a replay draws the same picture the run did, and the horizontal axis means
seconds of flight rather than seconds of watching. Sampling is decoupled from
both the physics rate and the frame rate for the same reason the renderer is.

Nothing here writes to the simulation.
"""

from __future__ import annotations

import math

import numpy as np
import pygame

from ..core.units import to_deg, to_ft, to_fpm, to_kt
from .instruments import (
    AMBER,
    CYAN,
    DIM,
    GREEN,
    INSTRUMENT_BG,
    PANEL_EDGE,
    RED,
    WHITE,
    draw_text,
)

SAMPLE_HZ = 10.0
HISTORY_SECONDS = 240.0
CAPACITY = int(SAMPLE_HZ * HISTORY_SECONDS)

GRID = (34, 39, 48)
BAND_ALPHA = 58


class Trace:
    """One channel's history, in a fixed-size ring buffer.

    A ring rather than a growing list: a long flight is hours, the chart only
    ever shows the last few minutes, and an unbounded buffer would quietly turn
    a flight simulator into a memory leak.
    """

    __slots__ = ("values", "count", "_head")

    def __init__(self) -> None:
        self.values = np.zeros(CAPACITY)
        self.count = 0
        self._head = 0

    def push(self, value: float) -> None:
        self.values[self._head] = value
        self._head = (self._head + 1) % CAPACITY
        self.count = min(self.count + 1, CAPACITY)

    def clear(self) -> None:
        self.count = 0
        self._head = 0

    def window(self, samples: int) -> np.ndarray:
        """The most recent ``samples`` values, oldest first."""
        take = min(samples, self.count)
        if take == 0:
            return np.zeros(0)
        start = (self._head - take) % CAPACITY
        if start + take <= CAPACITY:
            return self.values[start : start + take]
        return np.concatenate([self.values[start:], self.values[: start + take - CAPACITY]])


class Channel:
    """A trace plus how to draw it: units, colour, limits, preferred scale."""

    def __init__(
        self,
        key: str,
        label: str,
        unit: str,
        colour,
        *,
        fmt: str = "{:.0f}",
        floor: float | None = None,
        ceiling: float | None = None,
        minimum_span: float = 1.0,
        fill: bool = False,
    ) -> None:
        self.key = key
        self.label = label
        self.unit = unit
        self.colour = colour
        self.fmt = fmt
        self.floor = floor  # forced lower bound of the y axis, if any
        self.ceiling = ceiling
        self.minimum_span = minimum_span
        self.fill = fill
        self.trace = Trace()
        # Limit bands, as (low, high, colour) in the channel's own unit.
        self.bands: list[tuple[float, float, tuple[int, int, int]]] = []
        # A second trace drawn underneath, e.g. the ground below the altitude.
        self.underlay: Trace | None = None
        self.underlay_colour = (58, 62, 52)


class FlightCharts:
    """The chart set for one flight: sampling, scaling and drawing."""

    def __init__(self, model, fonts) -> None:
        self.fonts = fonts
        self.model = model
        self._next_sample = 0.0
        self._last_time = 0.0
        self.window_seconds = 90.0

        vmo = model.get("limitations", "vmo", 0.0)
        load_positive = model.get("limitations", "load_factor_positive", 2.5)
        load_negative = model.get("limitations", "load_factor_negative", -1.0)
        alpha_stall = to_deg(model.get("aerodynamics", "alpha_stall", math.radians(15.0)))

        speed = Channel(
            "cas", "AIRSPEED", "kt", CYAN, floor=0.0, minimum_span=40.0
        )
        if vmo > 0.0:
            speed.bands.append((to_kt(vmo), 1.0e6, RED))

        altitude = Channel(
            "altitude", "ALTITUDE", "ft", WHITE, floor=0.0, minimum_span=500.0, fill=True
        )
        # The ground under the flight path, from the same heightfield the gear
        # stands on: the gap between the two lines is the AGL the radio
        # altimeter reads, drawn rather than tabulated.
        altitude.underlay = Trace()

        load = Channel(
            "load", "LOAD FACTOR", "g", GREEN, fmt="{:+.2f}", minimum_span=2.0
        )
        load.bands.append((load_positive, 1.0e6, RED))
        load.bands.append((-1.0e6, load_negative, RED))

        alpha = Channel("alpha", "ANGLE OF ATTACK", "deg", AMBER, fmt="{:+.1f}", minimum_span=8.0)
        alpha.bands.append((alpha_stall, 1.0e6, RED))

        vertical = Channel(
            "vs", "VERTICAL SPEED", "fpm", (150, 190, 255), fmt="{:+,.0f}", minimum_span=1000.0
        )

        power = Channel(
            "n1", "N1", "%", (232, 150, 96), floor=0.0, ceiling=110.0, minimum_span=110.0
        )

        self.channels = [speed, altitude, load, alpha, vertical, power]
        self.by_key = {channel.key: channel for channel in self.channels}
        self._band_layer: pygame.Surface | None = None

    # -- sampling ----------------------------------------------------------

    def reset(self) -> None:
        for channel in self.channels:
            channel.trace.clear()
            if channel.underlay is not None:
                channel.underlay.clear()
        self._next_sample = 0.0

    def update(self, sim) -> None:
        """Take a sample if enough simulation time has passed."""
        time = sim.time
        # Time running backwards means a restart or a replay seek; the history
        # in the buffer belongs to a different flight than the one now running.
        if time < self._last_time - 1.0e-9:
            self.reset()
        self._last_time = time
        if time + 1.0e-9 < self._next_sample:
            return
        # Anchor the next sample to the grid rather than to now, so a slow
        # frame does not shift the whole time base.
        self._next_sample = (math.floor(time * SAMPLE_HZ) + 1.0) / SAMPLE_HZ

        derived = sim.fdm.state.derived
        engines = sim.fdm.propulsion.engines
        self.by_key["cas"].trace.push(to_kt(derived.vcas))
        self.by_key["altitude"].trace.push(to_ft(derived.altitude))
        self.by_key["altitude"].underlay.push(
            to_ft(derived.altitude - derived.altitude_agl)
        )
        self.by_key["load"].trace.push(derived.load_factor)
        self.by_key["alpha"].trace.push(to_deg(derived.alpha))
        self.by_key["vs"].trace.push(to_fpm(derived.vertical_speed))
        self.by_key["n1"].trace.push(
            100.0 * max((engine.n1 for engine in engines), default=0.0)
        )

    # -- drawing -----------------------------------------------------------

    @property
    def samples(self) -> int:
        return max(2, int(self.window_seconds * SAMPLE_HZ))

    def _scale(self, channel: Channel, series: list[np.ndarray]) -> tuple[float, float]:
        """Pick the y range: the data, the limits it must show, and a margin."""
        low = min(float(s.min()) for s in series if len(s))
        high = max(float(s.max()) for s in series if len(s))

        # A limit the trace is anywhere near must stay on the chart, or the
        # chart cannot show you that you are approaching it.
        for band_low, band_high, _ in channel.bands:
            if abs(band_low) < 1.0e5 and band_low <= high + channel.minimum_span:
                low, high = min(low, band_low), max(high, band_low)
            if abs(band_high) < 1.0e5 and band_high >= low - channel.minimum_span:
                low, high = min(low, band_high), max(high, band_high)

        span = max(high - low, channel.minimum_span)
        centre = 0.5 * (low + high)
        low, high = centre - 0.58 * span, centre + 0.58 * span
        if channel.floor is not None:
            low = max(low, channel.floor) if high > channel.floor else channel.floor
            low = min(low, high - 0.5 * channel.minimum_span)
        if channel.ceiling is not None:
            high = min(high, channel.ceiling)
        if high - low < 1.0e-6:
            high = low + 1.0
        return low, high

    @staticmethod
    def _envelope(series: np.ndarray, width: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Reduce a series to one min/max pair per pixel column.

        Drawing every sample would be both slower and less honest at this
        scale: a 0.3 s g spike between two pixel columns should show as a
        spike, not vanish because the sample that captured it was skipped.
        """
        count = len(series)
        if count <= width:
            x = np.arange(count, dtype=float) * (width - 1) / max(1, count - 1)
            return x, series, series
        edges = np.unique(np.linspace(0, count, width + 1).astype(int)[:-1])
        low = np.minimum.reduceat(series, edges)
        high = np.maximum.reduceat(series, edges)
        # One column per segment, spread across the full plot width. Mapping
        # the *sample* index instead leaves the trace a couple of pixels short
        # of the right edge, so the newest-sample marker floats off the line.
        x = np.arange(len(edges), dtype=float) * (width - 1) / max(1, len(edges) - 1)
        return x, low, high

    def draw_chart(self, surface, rect: pygame.Rect, channel: Channel, *, compact=False) -> None:
        """One trace in its own box, with grid, limit bands and a readout."""
        pygame.draw.rect(surface, INSTRUMENT_BG, rect)
        pygame.draw.rect(surface, PANEL_EDGE, rect, 1)

        header = 15 if compact else 19
        plot = pygame.Rect(
            rect.x + 1, rect.y + header, rect.width - 2, rect.height - header - 1
        )
        series = channel.trace.window(self.samples)
        under = (
            channel.underlay.window(self.samples)
            if channel.underlay is not None
            else np.zeros(0)
        )

        font = self.fonts.tiny
        draw_text(surface, font, channel.label, (rect.x + 6, rect.y + 3), DIM)
        if len(series):
            draw_text(
                surface,
                font,
                f"{channel.fmt.format(series[-1])} {channel.unit}",
                (rect.right - 6, rect.y + 3),
                channel.colour,
                "topright",
            )
        if len(series) < 2 or plot.height < 8 or plot.width < 8:
            return

        low, high = self._scale(channel, [series, under] if len(under) else [series])
        span = high - low

        def to_y(value):
            return plot.bottom - (np.clip(value, low, high) - low) / span * plot.height

        # Limit bands first, so the trace is drawn over them.
        for band_low, band_high, colour in channel.bands:
            top = float(to_y(min(band_high, high)))
            bottom = float(to_y(max(band_low, low)))
            if bottom - top < 1.0:
                continue
            band = pygame.Surface((plot.width, int(bottom - top)), pygame.SRCALPHA)
            band.fill((*colour, BAND_ALPHA))
            surface.blit(band, (plot.x, int(top)))

        # Horizontal grid at a round interval, and the zero line if it is on.
        step = _nice_step(span / (2.0 if compact else 3.0))
        value = math.ceil(low / step) * step
        while value <= high:
            y = int(to_y(value))
            pygame.draw.line(surface, GRID, (plot.x, y), (plot.right, y))
            if not compact:
                draw_text(surface, font, f"{value:,.0f}", (plot.x + 3, y - 11), (72, 78, 90))
            value += step

        x, series_low, series_high = self._envelope(series, plot.width)
        xs = plot.x + x
        y_low, y_high = to_y(series_low), to_y(series_high)

        if channel.fill:
            polygon = [(float(a), float(b)) for a, b in zip(xs, y_high)]
            polygon += [(float(xs[-1]), plot.bottom), (float(xs[0]), plot.bottom)]
            if len(polygon) >= 3:
                shade = pygame.Surface((plot.width, plot.height), pygame.SRCALPHA)
                pygame.draw.polygon(
                    shade,
                    (*channel.colour, 40),
                    [(a - plot.x, b - plot.y) for a, b in polygon],
                )
                surface.blit(shade, plot.topleft)

        # The ground profile, as solid terrain rather than a line, and drawn
        # over the shading so it is not seen through it: the gap between this
        # and the altitude trace is the clearance the flight actually had.
        if len(under) >= 2:
            gx, _, ground = self._envelope(under, plot.width)
            gxs = plot.x + gx
            polygon = [(float(a), float(b)) for a, b in zip(gxs, to_y(ground))]
            polygon += [(float(gxs[-1]), plot.bottom), (float(gxs[0]), plot.bottom)]
            if len(polygon) >= 3:
                pygame.draw.polygon(surface, channel.underlay_colour, polygon)

        # Where min and max differ by more than a pixel the column is drawn as
        # a vertical bar, which is what preserves a spike narrower than one
        # column instead of averaging it away.
        points = [(float(a), float(b)) for a, b in zip(xs, y_high)]
        for a, top, bottom in zip(xs, y_high, y_low):
            if bottom - top > 1.5:
                pygame.draw.line(
                    surface, channel.colour, (float(a), float(top)), (float(a), float(bottom))
                )
        if len(points) >= 2:
            pygame.draw.lines(surface, channel.colour, False, points)

        # A marker on the newest sample, so the eye finds "now" immediately.
        pygame.draw.circle(
            surface, channel.colour, (int(xs[-1]), int(to_y(series[-1]))), 2
        )

    # -- layouts -----------------------------------------------------------

    def draw_compact(self, surface, rect: pygame.Rect) -> None:
        """Two traces in the panel, for the flight in progress."""
        if rect.width < 90 or rect.height < 60:
            return
        keys = ("cas", "altitude")
        gap = 4
        height = (rect.height - gap * (len(keys) - 1)) // len(keys)
        for index, key in enumerate(keys):
            box = pygame.Rect(rect.x, rect.y + index * (height + gap), rect.width, height)
            self.draw_chart(surface, box, self.by_key[key], compact=True)

    def draw_overlay(self, surface, view: pygame.Rect) -> None:
        """The full set, over the chase view, on request."""
        margin = max(16, int(view.width * 0.04))
        panel = view.inflate(-2 * margin, -2 * margin)
        if panel.width < 320 or panel.height < 240:
            panel = view

        backdrop = pygame.Surface(panel.size, pygame.SRCALPHA)
        backdrop.fill((8, 10, 14, 232))
        surface.blit(backdrop, panel.topleft)
        pygame.draw.rect(surface, PANEL_EDGE, panel, 1)

        header = 30
        draw_text(
            surface,
            self.fonts.small,
            f"FLIGHT TRACES     last {self.window_seconds:.0f} s"
            f"     , and . zoom, C to close",
            (panel.x + 12, panel.y + 8),
            DIM,
        )

        columns, rows = 2, 3
        gap = 8
        body = pygame.Rect(
            panel.x + gap, panel.y + header, panel.width - 2 * gap, panel.height - header - gap
        )
        cell_w = (body.width - gap * (columns - 1)) // columns
        cell_h = (body.height - gap * (rows - 1)) // rows
        for index, channel in enumerate(self.channels):
            column, row = index % columns, index // columns
            box = pygame.Rect(
                body.x + column * (cell_w + gap),
                body.y + row * (cell_h + gap),
                cell_w,
                cell_h,
            )
            self.draw_chart(surface, box, channel)


def _nice_step(raw: float) -> float:
    """A round grid interval near ``raw``: 1, 2, 5 times a power of ten."""
    if raw <= 0.0 or not math.isfinite(raw):
        return 1.0
    magnitude = 10.0 ** math.floor(math.log10(raw))
    for multiple in (1.0, 2.0, 5.0, 10.0):
        if raw <= multiple * magnitude:
            return multiple * magnitude
    return 10.0 * magnitude

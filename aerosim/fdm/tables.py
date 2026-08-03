"""Interpolated lookup tables with an explicit out-of-range policy.

Every table declares what happens past its ends: ``clamp``, ``linear`` or
``error``. There is deliberately no default that silently extrapolates.
Extrapolating a stall curve to 60 degrees angle of attack produces
plausible-looking numbers that mean nothing, and is one of the most common ways
a flight model becomes quietly wrong while continuing to run.
"""

from __future__ import annotations

from bisect import bisect_right

import numpy as np

RANGE_POLICIES = ("clamp", "linear", "error")


class TableRangeError(ValueError):
    """Raised when a lookup falls outside a table declared ``error``."""


class Table1D:
    """One-dimensional piecewise-linear table."""

    def __init__(
        self,
        breakpoints,
        values,
        policy: str = "clamp",
        name: str = "table",
    ) -> None:
        if policy not in RANGE_POLICIES:
            raise ValueError(
                f"{name}: range policy must be one of {RANGE_POLICIES}, got {policy!r}"
            )

        self.x = [float(v) for v in breakpoints]
        self.y = [float(v) for v in values]
        self.policy = policy
        self.name = name

        if len(self.x) != len(self.y):
            raise ValueError(
                f"{name}: {len(self.x)} breakpoints but {len(self.y)} values"
            )
        if len(self.x) < 2:
            raise ValueError(f"{name}: a table needs at least two points")
        if any(b <= a for a, b in zip(self.x, self.x[1:])):
            raise ValueError(f"{name}: breakpoints must be strictly increasing")

    def __call__(self, x: float) -> float:
        return self.lookup(x)

    def lookup(self, x: float) -> float:
        x = float(x)
        lo, hi = self.x[0], self.x[-1]

        if x < lo or x > hi:
            if self.policy == "error":
                raise TableRangeError(
                    f"{self.name}: {x:g} outside [{lo:g}, {hi:g}]"
                )
            if self.policy == "clamp":
                return self.y[0] if x < lo else self.y[-1]
            # linear: continue the slope of the nearest interval
            if x < lo:
                slope = (self.y[1] - self.y[0]) / (self.x[1] - self.x[0])
                return self.y[0] + slope * (x - lo)
            slope = (self.y[-1] - self.y[-2]) / (self.x[-1] - self.x[-2])
            return self.y[-1] + slope * (x - hi)

        i = bisect_right(self.x, x) - 1
        if i >= len(self.x) - 1:
            return self.y[-1]

        span = self.x[i + 1] - self.x[i]
        t = (x - self.x[i]) / span
        return self.y[i] + t * (self.y[i + 1] - self.y[i])

    @property
    def domain(self) -> tuple[float, float]:
        return self.x[0], self.x[-1]

    @classmethod
    def from_spec(cls, spec: dict, name: str = "table", convert=float) -> "Table1D":
        """Build from a data-package mapping.

        Expects ``{breakpoints: [...], values: [...], range: clamp|linear|error}``.
        ``convert`` is applied to each breakpoint so a table indexed in degrees
        arrives in radians.
        """
        try:
            breakpoints = [convert(v) for v in spec["breakpoints"]]
            values = [float(v) for v in spec["values"]]
        except KeyError as exc:
            raise ValueError(
                f"{name}: table needs 'breakpoints' and 'values', missing {exc}"
            ) from exc
        return cls(breakpoints, values, spec.get("range", "clamp"), name)


class Table2D:
    """Two-dimensional bilinear table, ``z = f(x, y)``."""

    def __init__(
        self,
        x_breakpoints,
        y_breakpoints,
        values,
        policy: str = "clamp",
        name: str = "table2d",
    ) -> None:
        if policy not in RANGE_POLICIES:
            raise ValueError(f"{name}: bad range policy {policy!r}")

        self.x = [float(v) for v in x_breakpoints]
        self.y = [float(v) for v in y_breakpoints]
        self.z = np.asarray(values, dtype=float)
        self.policy = policy
        self.name = name

        if self.z.shape != (len(self.x), len(self.y)):
            raise ValueError(
                f"{name}: values shape {self.z.shape} does not match "
                f"({len(self.x)}, {len(self.y)}) breakpoints"
            )
        if any(b <= a for a, b in zip(self.x, self.x[1:])):
            raise ValueError(f"{name}: x breakpoints must be strictly increasing")
        if any(b <= a for a, b in zip(self.y, self.y[1:])):
            raise ValueError(f"{name}: y breakpoints must be strictly increasing")

    def __call__(self, x: float, y: float) -> float:
        return self.lookup(x, y)

    def _axis_index(self, axis: list[float], value: float, label: str) -> tuple[int, float]:
        lo, hi = axis[0], axis[-1]
        if value < lo or value > hi:
            if self.policy == "error":
                raise TableRangeError(
                    f"{self.name}: {label}={value:g} outside [{lo:g}, {hi:g}]"
                )
            if self.policy == "clamp":
                return (0, 0.0) if value < lo else (len(axis) - 2, 1.0)

        i = min(max(bisect_right(axis, value) - 1, 0), len(axis) - 2)
        span = axis[i + 1] - axis[i]
        return i, (value - axis[i]) / span

    def lookup(self, x: float, y: float) -> float:
        i, tx = self._axis_index(self.x, float(x), "x")
        j, ty = self._axis_index(self.y, float(y), "y")

        z00 = self.z[i, j]
        z10 = self.z[i + 1, j]
        z01 = self.z[i, j + 1]
        z11 = self.z[i + 1, j + 1]

        return float(
            z00 * (1 - tx) * (1 - ty)
            + z10 * tx * (1 - ty)
            + z01 * (1 - tx) * ty
            + z11 * tx * ty
        )

"""Generate synthetic OHLCV candles for offline testing and demos.

The series is a geometric random walk with alternating trend regimes, so
it looks vaguely like a crypto chart.  It is NOT real XRP data.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def make_ohlcv(n: int = 900, start_price: float = 2.40, seed: int = 7,
               interval_hours: int = 1) -> pd.DataFrame:
    rng = np.random.default_rng(seed)

    # alternating bull/bear/flat regimes of 50-150 bars
    drift = np.zeros(n)
    i = 0
    while i < n:
        length = int(rng.integers(50, 150))
        mu = rng.choice([-0.0012, -0.0004, 0.0, 0.0005, 0.0014])
        drift[i:i + length] = mu
        i += length

    vol = 0.008 + 0.006 * np.abs(np.sin(np.arange(n) / 37.0))
    log_returns = drift + rng.normal(0.0, 1.0, n) * vol
    closes = start_price * np.exp(np.cumsum(log_returns))

    opens = np.empty(n)
    opens[0] = start_price
    opens[1:] = closes[:-1]
    spread = np.abs(rng.normal(0.0, vol, n)) * closes
    highs = np.maximum(opens, closes) + spread
    lows = np.minimum(opens, closes) - spread
    volume = 1e6 * (1.0 + 4.0 * np.abs(log_returns) / vol.mean()
                    + rng.random(n))

    end = pd.Timestamp.now(tz="UTC").floor("h")
    times = pd.date_range(end=end, periods=n, freq=f"{interval_hours}h")

    return pd.DataFrame({
        "time": times,
        "open": opens.round(6),
        "high": highs.round(6),
        "low": lows.round(6),
        "close": closes.round(6),
        "volume": volume.round(2),
    })


if __name__ == "__main__":
    import sys
    out = sys.argv[1] if len(sys.argv) > 1 else "sample_data/synthetic_demo.csv"
    df = make_ohlcv()
    df.to_csv(out, index=False)
    print(f"wrote {len(df)} synthetic candles to {out}")

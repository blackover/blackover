"""Technical indicators used by the strategy and the ML model.

All functions take/return pandas Series aligned to the input index.
`add_indicators` decorates an OHLCV frame with every column the rest of
the app needs.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def sma(series: pd.Series, window: int) -> pd.Series:
    return series.rolling(window).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Relative Strength Index with Wilder's smoothing."""
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    out = 100.0 - 100.0 / (1.0 + rs)
    # all-gain windows: avg_loss == 0 -> RSI is 100 by definition
    out = out.where(avg_loss != 0.0, 100.0)
    out[avg_gain.isna() | avg_loss.isna()] = np.nan
    return out


def macd(close: pd.Series, fast: int = 12, slow: int = 26,
         signal: int = 9) -> tuple[pd.Series, pd.Series, pd.Series]:
    macd_line = ema(close, fast) - ema(close, slow)
    signal_line = ema(macd_line, signal)
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def bollinger(close: pd.Series, window: int = 20,
              num_std: float = 2.0) -> tuple[pd.Series, pd.Series, pd.Series, pd.Series]:
    mid = sma(close, window)
    std = close.rolling(window).std(ddof=0)
    upper = mid + num_std * std
    lower = mid - num_std * std
    width = (upper - lower).replace(0.0, np.nan)
    pct_b = (close - lower) / width
    return mid, upper, lower, pct_b


def stochastic(high: pd.Series, low: pd.Series, close: pd.Series,
               k_period: int = 14, d_period: int = 3) -> tuple[pd.Series, pd.Series]:
    lowest = low.rolling(k_period).min()
    highest = high.rolling(k_period).max()
    span = (highest - lowest).replace(0.0, np.nan)
    k = 100.0 * (close - lowest) / span
    d = k.rolling(d_period).mean()
    return k, d


def atr(high: pd.Series, low: pd.Series, close: pd.Series,
        period: int = 14) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def roc(close: pd.Series, window: int) -> pd.Series:
    return close.pct_change(window)


def obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    direction = np.sign(close.diff()).fillna(0.0)
    return (direction * volume).cumsum()


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Return a copy of the OHLCV frame with indicator columns added."""
    out = df.copy()
    close, high, low, volume = out["close"], out["high"], out["low"], out["volume"]

    out["ema_12"] = ema(close, 12)
    out["ema_26"] = ema(close, 26)
    out["sma_50"] = sma(close, 50)
    out["rsi_14"] = rsi(close, 14)
    out["macd"], out["macd_signal"], out["macd_hist"] = macd(close)
    (out["bb_mid"], out["bb_upper"], out["bb_lower"],
     out["bb_pct_b"]) = bollinger(close)
    out["stoch_k"], out["stoch_d"] = stochastic(high, low, close)
    out["atr_14"] = atr(high, low, close)
    out["roc_1"] = roc(close, 1)
    out["roc_3"] = roc(close, 3)
    out["roc_6"] = roc(close, 6)
    out["roc_12"] = roc(close, 12)
    out["obv"] = obv(close, volume)
    vol_mean = volume.rolling(20).mean()
    vol_std = volume.rolling(20).std(ddof=0).replace(0.0, np.nan)
    out["volume_z"] = (volume - vol_mean) / vol_std
    return out


# Number of leading rows that contain NaN warm-up values for some indicator.
WARMUP_ROWS = 60

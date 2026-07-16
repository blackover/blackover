"""Market data fetching for XRP/USD.

Tries several free public APIs in order (no API key required):

1. Binance  - XRPUSDT klines (full OHLCV, up to 1000 candles)
2. Kraken   - XRPUSD OHLC (full OHLCV, last ~720 candles)
3. CoinGecko - close prices + volume (open/high/low approximated)

All sources are normalized to a DataFrame with columns:
    time (UTC datetime), open, high, low, close, volume
sorted oldest -> newest.  The last row is the candle currently forming.
"""

from __future__ import annotations

import time as _time

import pandas as pd
import requests

USER_AGENT = "xrp-predictor/1.0 (educational)"

# interval name -> (binance interval, kraken minutes, seconds per candle)
INTERVALS = {
    "15m": ("15m", 15, 15 * 60),
    "1h": ("1h", 60, 60 * 60),
    "4h": ("4h", 240, 4 * 60 * 60),
    "1d": ("1d", 1440, 24 * 60 * 60),
}

COLUMNS = ["time", "open", "high", "low", "close", "volume"]


class DataError(RuntimeError):
    """Raised when no data source could provide candles."""


def interval_seconds(interval: str) -> int:
    return INTERVALS[interval][2]


def _get(url: str, params: dict | None = None, timeout: int = 20) -> dict | list:
    resp = requests.get(url, params=params, timeout=timeout,
                        headers={"User-Agent": USER_AGENT})
    resp.raise_for_status()
    return resp.json()


def fetch_binance(interval: str = "1h", limit: int = 720) -> pd.DataFrame:
    binance_interval = INTERVALS[interval][0]
    raw = _get(
        "https://api.binance.com/api/v3/klines",
        {"symbol": "XRPUSDT", "interval": binance_interval,
         "limit": min(limit, 1000)},
    )
    rows = [
        {
            "time": pd.Timestamp(int(k[0]), unit="ms", tz="UTC"),
            "open": float(k[1]),
            "high": float(k[2]),
            "low": float(k[3]),
            "close": float(k[4]),
            "volume": float(k[5]),
        }
        for k in raw
    ]
    return pd.DataFrame(rows, columns=COLUMNS)


def fetch_kraken(interval: str = "1h", limit: int = 720) -> pd.DataFrame:
    minutes = INTERVALS[interval][1]
    raw = _get("https://api.kraken.com/0/public/OHLC",
               {"pair": "XRPUSD", "interval": minutes})
    if raw.get("error"):
        raise DataError(f"Kraken error: {raw['error']}")
    result = raw["result"]
    key = next(k for k in result if k != "last")
    rows = [
        {
            "time": pd.Timestamp(int(c[0]), unit="s", tz="UTC"),
            "open": float(c[1]),
            "high": float(c[2]),
            "low": float(c[3]),
            "close": float(c[4]),
            "volume": float(c[6]),
        }
        for c in result[key]
    ]
    df = pd.DataFrame(rows, columns=COLUMNS)
    return df.tail(limit).reset_index(drop=True)


def fetch_coingecko(interval: str = "1h", limit: int = 720) -> pd.DataFrame:
    # CoinGecko market_chart gives close prices + volumes.  Granularity is
    # automatic: days<=1 -> 5min, days<=90 -> hourly, otherwise daily.
    seconds = interval_seconds(interval)
    days = max(2, min(90, (limit * seconds) // 86400 + 1))
    if interval == "1d":
        days = min(365, limit + 1)
    raw = _get(
        "https://api.coingecko.com/api/v3/coins/ripple/market_chart",
        {"vs_currency": "usd", "days": days},
    )
    prices = pd.DataFrame(raw["prices"], columns=["ms", "close"])
    vols = pd.DataFrame(raw["total_volumes"], columns=["ms", "volume"])
    df = prices.merge(vols, on="ms", how="left")
    df["time"] = pd.to_datetime(df["ms"], unit="ms", utc=True)
    df = df.set_index("time").sort_index()

    freq = {"15m": "15min", "1h": "1h", "4h": "4h", "1d": "1D"}[interval]
    close = df["close"].resample(freq).last().ffill()
    high = df["close"].resample(freq).max().ffill()
    low = df["close"].resample(freq).min().ffill()
    volume = df["volume"].resample(freq).mean().fillna(0.0)

    out = pd.DataFrame({
        "time": close.index,
        "open": close.shift(1),
        "high": high.values,
        "low": low.values,
        "close": close.values,
        "volume": volume.values,
    }).dropna().reset_index(drop=True)
    return out.tail(limit).reset_index(drop=True)


FETCHERS = {
    "binance": fetch_binance,
    "kraken": fetch_kraken,
    "coingecko": fetch_coingecko,
}


def fetch_ohlcv(interval: str = "1h", limit: int = 720,
                source: str = "auto", csv_path: str | None = None,
                retries: int = 2) -> tuple[pd.DataFrame, str]:
    """Fetch candles.  Returns (dataframe, source_name_used)."""
    if interval not in INTERVALS:
        raise ValueError(f"interval must be one of {sorted(INTERVALS)}")

    if source == "csv" or csv_path:
        if not csv_path:
            raise ValueError("--csv PATH is required when source is csv")
        return load_csv(csv_path).tail(limit).reset_index(drop=True), "csv"

    order = list(FETCHERS) if source == "auto" else [source]
    errors = []
    for name in order:
        fetcher = FETCHERS.get(name)
        if fetcher is None:
            raise ValueError(f"unknown source '{name}' "
                             f"(choose from auto/csv/{'/'.join(FETCHERS)})")
        for attempt in range(retries + 1):
            try:
                df = fetcher(interval=interval, limit=limit)
                if len(df) < 60:
                    raise DataError(f"{name} returned only {len(df)} candles")
                return df, name
            except Exception as exc:  # noqa: BLE001 - fall through to next source
                errors.append(f"{name}: {exc}")
                if attempt < retries:
                    _time.sleep(2 ** attempt)
    raise DataError("all data sources failed:\n  " + "\n  ".join(errors))


def load_csv(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    missing = [c for c in COLUMNS if c not in df.columns]
    if missing:
        raise DataError(f"CSV is missing columns: {missing}")
    df["time"] = pd.to_datetime(df["time"], utc=True)
    for col in COLUMNS[1:]:
        df[col] = df[col].astype(float)
    return df[COLUMNS].sort_values("time").reset_index(drop=True)


def save_csv(df: pd.DataFrame, path: str) -> None:
    df[COLUMNS].to_csv(path, index=False)

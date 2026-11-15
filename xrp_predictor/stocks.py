"""Stock market data: Yahoo Finance (US + Borsa Istanbul) with a Stooq
daily fallback for US tickers.

No API key is needed for either source.  Parsed into the same OHLCV frame
as crypto data:  time (UTC), open, high, low, close, volume.

Stock candles only exist for exchange trading hours, so 720 hourly bars
span several calendar months, and the newest candle is from the most
recent session.
"""

from __future__ import annotations

import io
import time as _time

import pandas as pd
import requests

from .assets import Asset
from .data import COLUMNS, DataError

# Browser-like UA: Yahoo rejects obvious bot agents.
HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) query-client/1.0"}

# interval -> (yahoo interval, max lookback days Yahoo allows)
YAHOO_INTERVALS = {"15m": ("15m", 59), "1h": ("60m", 729), "1d": ("1d", 3650)}


def parse_yahoo_chart(payload: dict) -> pd.DataFrame:
    chart = payload.get("chart", {})
    if chart.get("error"):
        raise DataError(f"yahoo: {chart['error']}")
    results = chart.get("result") or []
    if not results:
        raise DataError("yahoo: empty chart result")
    result = results[0]
    stamps = result.get("timestamp") or []
    quote = (result.get("indicators", {}).get("quote") or [{}])[0]
    rows = []
    for i, ts in enumerate(stamps):
        close = quote.get("close", [None])[i] if i < len(quote.get("close", [])) else None
        if close is None:
            continue  # holidays / not-yet-filled bars
        def val(key, default):
            arr = quote.get(key) or []
            v = arr[i] if i < len(arr) else None
            return default if v is None else float(v)
        rows.append({
            "time": pd.Timestamp(int(ts), unit="s", tz="UTC"),
            "open": val("open", float(close)),
            "high": val("high", float(close)),
            "low": val("low", float(close)),
            "close": float(close),
            "volume": val("volume", 0.0),
        })
    if not rows:
        raise DataError("yahoo: no usable candles")
    df = pd.DataFrame(rows, columns=COLUMNS)
    return df.drop_duplicates("time").sort_values("time").reset_index(drop=True)


def fetch_yahoo(yahoo_symbol: str, interval: str = "1h",
                limit: int = 720) -> pd.DataFrame:
    if interval not in YAHOO_INTERVALS:
        raise DataError(f"stocks support intervals "
                        f"{'/'.join(YAHOO_INTERVALS)} (not {interval})")
    yi, max_days = YAHOO_INTERVALS[interval]
    per_day = 1 if interval == "1d" else (7 if interval == "1h" else 26)
    days = min(max_days, max(7, (limit // per_day) * 2 + 14))
    now = int(_time.time())
    resp = requests.get(
        f"https://query1.finance.yahoo.com/v8/finance/chart/{yahoo_symbol}",
        params={"interval": yi, "period1": now - days * 86400, "period2": now,
                "events": "history"},
        headers=HEADERS, timeout=20)
    resp.raise_for_status()
    return parse_yahoo_chart(resp.json()).tail(limit).reset_index(drop=True)


def parse_stooq(csv_text: str) -> pd.DataFrame:
    df = pd.read_csv(io.StringIO(csv_text))
    expected = {"Date", "Open", "High", "Low", "Close"}
    if not expected.issubset(df.columns):
        raise DataError("stooq: unexpected CSV format")
    out = pd.DataFrame({
        "time": pd.to_datetime(df["Date"], utc=True),
        "open": df["Open"].astype(float),
        "high": df["High"].astype(float),
        "low": df["Low"].astype(float),
        "close": df["Close"].astype(float),
        "volume": df.get("Volume", 0.0),
    })
    out["volume"] = out["volume"].fillna(0.0).astype(float)
    return out.sort_values("time").reset_index(drop=True)


def fetch_stooq(symbol: str, interval: str = "1d",
                limit: int = 720) -> pd.DataFrame:
    if interval != "1d":
        raise DataError("stooq fallback provides daily candles only")
    resp = requests.get("https://stooq.com/q/d/l/",
                        params={"s": f"{symbol.lower()}.us", "i": "d"},
                        headers=HEADERS, timeout=20)
    resp.raise_for_status()
    if "No data" in resp.text[:200]:
        raise DataError(f"stooq: no data for {symbol}")
    return parse_stooq(resp.text).tail(limit).reset_index(drop=True)


def fetch_stock_ohlcv(asset: Asset, interval: str = "1h", limit: int = 720,
                      source: str = "auto",
                      retries: int = 2) -> tuple[pd.DataFrame, str]:
    """Fetch stock candles.  Returns (dataframe, source_name_used)."""
    attempts: list[tuple[str, callable]] = []
    if source in ("auto", "yahoo"):
        attempts.append(("yahoo",
                         lambda: fetch_yahoo(asset.yahoo, interval, limit)))
    if (source in ("auto", "stooq") and asset.kind == "us_stock"
            and interval == "1d"):
        attempts.append(("stooq",
                         lambda: fetch_stooq(asset.symbol, interval, limit)))
    if not attempts:
        raise DataError(f"source '{source}' cannot serve {asset.kind} "
                        f"{asset.symbol} at {interval}")

    errors = []
    for name, fetch in attempts:
        for attempt in range(retries + 1):
            try:
                df = fetch()
                if len(df) < 60:
                    raise DataError(f"{name} returned only {len(df)} candles")
                return df, name
            except Exception as exc:  # noqa: BLE001 - try next source
                errors.append(f"{name}: {exc}")
                if attempt < retries:
                    _time.sleep(2 ** attempt)
    raise DataError(f"all stock sources failed for {asset.symbol}:\n  "
                    + "\n  ".join(errors))

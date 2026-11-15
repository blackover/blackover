"""Asset catalog: crypto coins, US stocks and Turkish (BIST) stocks.

A symbol string resolves to an Asset with a kind:

- "crypto"   - from the COINS catalog (XRP, BTC, ...), priced in USD
- "us_stock" - any US ticker (AAPL, MSFT, ...), priced in USD
- "tr_stock" - Borsa Istanbul ticker (THYAO, ASELS, ... or THYAO.IS),
               priced in TRY (Turkish lira)

Stocks are fetched from Yahoo Finance (with Stooq as a daily fallback for
US tickers).  Which markets the scan covers is chosen in settings.json
(see settings.py / the `settings` command).
"""

from __future__ import annotations

from dataclasses import dataclass

from .data import COINS

US_STOCKS_DEFAULT = ["AAPL", "MSFT", "NVDA", "TSLA", "AMZN",
                     "GOOGL", "META", "JPM"]
TR_STOCKS_DEFAULT = ["THYAO", "ASELS", "GARAN", "AKBNK", "EREGL",
                     "TUPRS", "SISE", "BIMAS"]

MARKETS = ("crypto", "us_stocks", "tr_stocks")

CURRENCY_SYMBOL = {"USD": "$", "TRY": "₺"}


@dataclass(frozen=True)
class Asset:
    symbol: str          # XRP, AAPL, THYAO ...
    kind: str            # crypto | us_stock | tr_stock
    yahoo: str | None    # Yahoo Finance symbol (None for crypto)
    currency: str        # USD or TRY

    @property
    def market(self) -> str:
        return {"crypto": "crypto", "us_stock": "us_stocks",
                "tr_stock": "tr_stocks"}[self.kind]


def cur(currency: str) -> str:
    """Currency symbol for display."""
    return CURRENCY_SYMBOL.get(currency, currency + " ")


def resolve_asset(symbol: str, settings=None) -> Asset:
    """Map a user-typed symbol to an Asset.

    Rules: explicit ``.IS`` suffix -> BIST; known coin -> crypto; symbol on
    the Turkish watchlist -> BIST; anything else -> US stock.
    """
    raw = symbol.strip().upper()
    if not raw or not raw.replace(".", "").replace("-", "").isalnum():
        raise ValueError(f"'{symbol}' is not a valid symbol")

    if raw.endswith(".IS"):
        base = raw[:-3]
        return Asset(base, "tr_stock", f"{base}.IS", "TRY")

    if raw in COINS:
        return Asset(raw, "crypto", None, "USD")

    tr = set(TR_STOCKS_DEFAULT)
    us = set(US_STOCKS_DEFAULT)
    if settings is not None:
        tr |= {s.upper().removesuffix(".IS")
               for s in settings.watchlist("tr_stocks")}
        us |= {s.upper() for s in settings.watchlist("us_stocks")}

    if raw in tr and raw not in us:
        return Asset(raw, "tr_stock", f"{raw}.IS", "TRY")
    return Asset(raw, "us_stock", raw, "USD")

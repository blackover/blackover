"""User settings: which markets are enabled and which symbols to scan.

Stored in ``settings.json`` next to where you run the app.  Crypto is on
by default; US and Turkish stocks are OPTIONAL and must be enabled:

    python -m xrp_predictor settings show
    python -m xrp_predictor settings enable us_stocks
    python -m xrp_predictor settings enable tr_stocks
    python -m xrp_predictor settings add us_stocks AMD
    python -m xrp_predictor settings remove tr_stocks SISE
"""

from __future__ import annotations

import json
from pathlib import Path

from .assets import MARKETS, TR_STOCKS_DEFAULT, US_STOCKS_DEFAULT
from .data import COINS

SETTINGS_FILE = "settings.json"


def _defaults() -> dict:
    return {
        "markets": {"crypto": True, "us_stocks": False, "tr_stocks": False},
        "watchlist": {
            "crypto": list(COINS),
            "us_stocks": list(US_STOCKS_DEFAULT),
            "tr_stocks": list(TR_STOCKS_DEFAULT),
        },
    }


class Settings:
    def __init__(self, data: dict, path: Path):
        base = _defaults()
        base["markets"].update(data.get("markets", {}))
        for market, symbols in data.get("watchlist", {}).items():
            if market in base["watchlist"] and isinstance(symbols, list):
                base["watchlist"][market] = [str(s).upper() for s in symbols]
        self.data = base
        self.path = path

    @classmethod
    def load(cls, path: str = SETTINGS_FILE) -> "Settings":
        p = Path(path)
        if p.exists():
            try:
                return cls(json.loads(p.read_text()), p)
            except (json.JSONDecodeError, OSError):
                pass
        return cls({}, p)

    def save(self) -> None:
        self.path.write_text(json.dumps(self.data, indent=2) + "\n")

    # -- queries --------------------------------------------------------

    def market_enabled(self, market: str) -> bool:
        return bool(self.data["markets"].get(market, False))

    def enabled_markets(self) -> list[str]:
        return [m for m in MARKETS if self.market_enabled(m)]

    def watchlist(self, market: str) -> list[str]:
        return list(self.data["watchlist"].get(market, []))

    def scan_symbols(self) -> list[str]:
        """All symbols from every enabled market, in order."""
        out: list[str] = []
        for market in MARKETS:
            if self.market_enabled(market):
                out.extend(s for s in self.watchlist(market) if s not in out)
        return out

    # -- edits ----------------------------------------------------------

    def set_market(self, market: str, enabled: bool) -> None:
        if market not in MARKETS:
            raise ValueError(f"unknown market '{market}' "
                             f"(choose from {', '.join(MARKETS)})")
        self.data["markets"][market] = enabled
        self.save()

    def add_symbol(self, market: str, symbol: str) -> None:
        if market not in MARKETS:
            raise ValueError(f"unknown market '{market}'")
        symbol = symbol.strip().upper()
        wl = self.data["watchlist"][market]
        if symbol not in wl:
            wl.append(symbol)
        self.save()

    def remove_symbol(self, market: str, symbol: str) -> None:
        if market not in MARKETS:
            raise ValueError(f"unknown market '{market}'")
        symbol = symbol.strip().upper()
        self.data["watchlist"][market] = [
            s for s in self.data["watchlist"][market] if s != symbol]
        self.save()

    def describe(self) -> str:
        lines = [f"settings file: {self.path}"]
        for market in MARKETS:
            state = "ON " if self.market_enabled(market) else "off"
            symbols = ", ".join(self.watchlist(market))
            lines.append(f"  [{state}] {market:<10} {symbols}")
        lines.append("scan covers: " + (", ".join(self.scan_symbols()) or "nothing"))
        return "\n".join(lines)

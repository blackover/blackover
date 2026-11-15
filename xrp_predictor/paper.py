"""Paper-trading portfolio: executes signals with SIMULATED money.

State is persisted to a small JSON file so the portfolio survives between
runs (e.g. hourly cron invocations).  No real orders are ever placed.
One portfolio file tracks one coin (default file: paper_portfolio_XRP.json).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .strategy import Signal

STARTING_USD = 1000.0


def default_path(coin: str = "XRP") -> str:
    return f"paper_portfolio_{coin}.json"


@dataclass
class PaperPortfolio:
    coin: str
    usd: float
    units: float
    fee: float
    history: list[dict]
    path: Path
    starting_usd: float = STARTING_USD

    @classmethod
    def load(cls, path: str | None = None, coin: str = "XRP",
             starting_usd: float = STARTING_USD,
             fee: float = 0.001) -> "PaperPortfolio":
        p = Path(path or default_path(coin))
        if p.exists():
            raw = json.loads(p.read_text())
            return cls(coin=raw.get("coin", coin), usd=raw["usd"],
                       units=raw.get("units", raw.get("xrp", 0.0)),
                       fee=raw.get("fee", fee), history=raw.get("history", []),
                       path=p,
                       starting_usd=raw.get("starting_usd", starting_usd))
        return cls(coin=coin, usd=starting_usd, units=0.0, fee=fee,
                   history=[], path=p, starting_usd=starting_usd)

    def save(self) -> None:
        self.path.write_text(json.dumps({
            "coin": self.coin,
            "usd": round(self.usd, 8),
            "units": round(self.units, 8),
            "fee": self.fee,
            "starting_usd": self.starting_usd,
            "history": self.history,
        }, indent=2))

    def value(self, price: float) -> float:
        return self.usd + self.units * price

    def apply_signal(self, signal: Signal) -> str:
        """Execute a signal as a simulated market order.  Returns a message."""
        if signal.coin != self.coin:
            return (f"skipped: portfolio tracks {self.coin}, "
                    f"signal is for {signal.coin}")
        price = signal.price
        if signal.action == "BUY" and self.usd > 1e-9:
            bought = self.usd * (1.0 - self.fee) / price
            self.history.append({
                "time": signal.time.isoformat(), "action": "BUY",
                "coin": self.coin, "price": price, "units": round(bought, 6),
                "usd_spent": round(self.usd, 2),
            })
            self.units += bought
            self.usd = 0.0
            msg = f"PAPER BUY  {bought:.4f} {self.coin} @ ${price:.4f}"
        elif signal.action == "SELL" and self.units > 1e-9:
            proceeds = self.units * price * (1.0 - self.fee)
            self.history.append({
                "time": signal.time.isoformat(), "action": "SELL",
                "coin": self.coin, "price": price,
                "units": round(self.units, 6),
                "usd_received": round(proceeds, 2),
            })
            self.usd += proceeds
            sold = self.units
            self.units = 0.0
            msg = (f"PAPER SELL {sold:.4f} {self.coin} @ ${price:.4f} "
                   f"-> ${proceeds:.2f}")
        else:
            msg = (f"no trade ({signal.action}; usd=${self.usd:.2f}, "
                   f"{self.coin.lower()}={self.units:.4f})")
        self.save()
        return msg

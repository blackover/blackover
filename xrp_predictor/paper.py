"""Paper-trading portfolio: executes signals with SIMULATED money.

State is persisted to a small JSON file so the portfolio survives between
runs (e.g. hourly cron invocations).  No real orders are ever placed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .strategy import Signal

DEFAULT_PATH = "paper_portfolio.json"
STARTING_USD = 1000.0


@dataclass
class PaperPortfolio:
    usd: float
    xrp: float
    fee: float
    history: list[dict]
    path: Path

    @classmethod
    def load(cls, path: str = DEFAULT_PATH, starting_usd: float = STARTING_USD,
             fee: float = 0.001) -> "PaperPortfolio":
        p = Path(path)
        if p.exists():
            raw = json.loads(p.read_text())
            return cls(usd=raw["usd"], xrp=raw["xrp"],
                       fee=raw.get("fee", fee), history=raw.get("history", []),
                       path=p)
        return cls(usd=starting_usd, xrp=0.0, fee=fee, history=[], path=p)

    def save(self) -> None:
        self.path.write_text(json.dumps({
            "usd": round(self.usd, 8),
            "xrp": round(self.xrp, 8),
            "fee": self.fee,
            "history": self.history,
        }, indent=2))

    def value(self, price: float) -> float:
        return self.usd + self.xrp * price

    def apply_signal(self, signal: Signal) -> str:
        """Execute a signal as a simulated market order.  Returns a message."""
        price = signal.price
        if signal.action == "BUY" and self.usd > 1e-9:
            bought = self.usd * (1.0 - self.fee) / price
            self.history.append({
                "time": signal.time.isoformat(), "action": "BUY",
                "price": price, "xrp": round(bought, 6),
                "usd_spent": round(self.usd, 2),
            })
            self.xrp += bought
            self.usd = 0.0
            msg = f"PAPER BUY  {bought:.4f} XRP @ ${price:.4f}"
        elif signal.action == "SELL" and self.xrp > 1e-9:
            proceeds = self.xrp * price * (1.0 - self.fee)
            self.history.append({
                "time": signal.time.isoformat(), "action": "SELL",
                "price": price, "xrp": round(self.xrp, 6),
                "usd_received": round(proceeds, 2),
            })
            self.usd += proceeds
            sold = self.xrp
            self.xrp = 0.0
            msg = f"PAPER SELL {sold:.4f} XRP @ ${price:.4f} -> ${proceeds:.2f}"
        else:
            msg = f"no trade ({signal.action}; usd=${self.usd:.2f}, xrp={self.xrp:.4f})"
        self.save()
        return msg

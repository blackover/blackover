"""Automatic trade execution driven by the prediction signals.

Two executors share the same interface:

- ``PaperPortfolio`` (paper.py)  - simulated money, always safe (default)
- ``LiveTrader`` (this module)   - REAL orders on a real exchange via ccxt

LiveTrader safety rails:

- It only ever manages a fixed ``budget`` slice (default $100), never your
  whole account balance.
- Every single order is capped at ``max_usd`` (default $50).
- API keys are read ONLY from environment variables
  (``EXCHANGE_API_KEY`` / ``EXCHANGE_API_SECRET``), never from files or
  command-line arguments.
- Orders below the exchange minimum (default $10) are skipped.
- State (remaining budget, held units, order history) is persisted to
  ``live_state_<COIN>.json`` and every order is written to the run log.

Live trading can lose real money quickly.  Run paper mode for a long time
first, and only trade money you can afford to lose entirely.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from .strategy import Signal

API_KEY_ENV = "EXCHANGE_API_KEY"
API_SECRET_ENV = "EXCHANGE_API_SECRET"
MIN_ORDER_USD = 10.0


class LiveTraderError(RuntimeError):
    pass


def make_exchange_client(exchange_id: str):
    """Build an authenticated ccxt client from environment variables."""
    try:
        import ccxt  # imported lazily: only live trading needs it
    except ImportError as exc:
        raise LiveTraderError(
            "ccxt is not installed; run: pip install ccxt") from exc

    api_key = os.environ.get(API_KEY_ENV, "")
    secret = os.environ.get(API_SECRET_ENV, "")
    if not api_key or not secret:
        raise LiveTraderError(
            f"set {API_KEY_ENV} and {API_SECRET_ENV} environment variables "
            f"with your exchange API key (trade permission only — NEVER "
            f"enable withdrawal permission for bot keys)")
    if not hasattr(ccxt, exchange_id):
        raise LiveTraderError(f"unknown exchange '{exchange_id}' "
                              f"(use a ccxt id like binance, kraken, bybit)")
    client = getattr(ccxt, exchange_id)({
        "apiKey": api_key,
        "secret": secret,
        "enableRateLimit": True,
    })
    return client


class LiveTrader:
    """Places REAL market orders for one coin within a fixed USD budget."""

    def __init__(self, coin: str, exchange_id: str = "binance",
                 quote: str = "USDT", budget: float = 100.0,
                 max_usd: float = 50.0, min_usd: float = MIN_ORDER_USD,
                 state_path: str | None = None, client=None, log=None):
        self.coin = coin
        self.symbol = f"{coin}/{quote}"
        self.exchange_id = exchange_id
        self.max_usd = max_usd
        self.min_usd = min_usd
        self.log = log
        self.client = client if client is not None else make_exchange_client(exchange_id)
        try:
            self.client.load_markets()
        except Exception as exc:  # noqa: BLE001
            raise LiveTraderError(f"could not load markets from "
                                  f"{exchange_id}: {exc}") from exc
        if self.symbol not in getattr(self.client, "markets", {self.symbol: {}}):
            raise LiveTraderError(f"{self.symbol} is not traded on {exchange_id}")

        self.path = Path(state_path or f"live_state_{coin}.json")
        if self.path.exists():
            state = json.loads(self.path.read_text())
            self.cash = float(state["cash_usd"])
            self.units = float(state["units"])
            self.orders = state.get("orders", [])
        else:
            self.cash = float(budget)
            self.units = 0.0
            self.orders = []
            self._save()

    def _save(self) -> None:
        self.path.write_text(json.dumps({
            "coin": self.coin,
            "symbol": self.symbol,
            "exchange": self.exchange_id,
            "cash_usd": round(self.cash, 8),
            "units": round(self.units, 8),
            "orders": self.orders,
        }, indent=2))

    def _amount_to_precision(self, amount: float) -> float:
        try:
            return float(self.client.amount_to_precision(self.symbol, amount))
        except Exception:  # noqa: BLE001 - precision helper is best-effort
            return float(f"{amount:.6f}")

    def _record(self, action: str, order: dict, amount: float,
                price: float, cost: float, when) -> None:
        entry = {
            "time": str(when), "action": action, "symbol": self.symbol,
            "amount": amount, "price": price, "cost_usd": round(cost, 4),
            "exchange_order_id": str(order.get("id", "")),
        }
        self.orders.append(entry)
        self._save()
        if self.log is not None:
            self.log.event(
                "ORDER",
                f"LIVE {action} {amount:.6f} {self.symbol} "
                f"~${cost:.2f} (order id {entry['exchange_order_id'] or 'n/a'})",
                {"order": entry},
            )

    def execute(self, signal: Signal) -> str:
        """Act on a signal with a REAL market order.  Returns a message."""
        if signal.coin != self.coin:
            return f"skipped: live trader is for {self.coin}, not {signal.coin}"
        price = signal.price

        if signal.action == "BUY":
            spend = min(self.cash, self.max_usd)
            if spend < self.min_usd:
                return (f"no live buy: remaining budget ${self.cash:.2f} is "
                        f"below the ${self.min_usd:.0f} exchange minimum")
            amount = self._amount_to_precision(spend / price)
            order = self.client.create_order(self.symbol, "market", "buy", amount)
            filled = float(order.get("filled") or amount)
            cost = float(order.get("cost") or spend)
            self.units += filled
            self.cash -= cost
            self._record("BUY", order, filled, price, cost, signal.time)
            return (f"LIVE BUY  {filled:.6f} {self.coin} ~${cost:.2f} "
                    f"(budget left ${self.cash:.2f})")

        if signal.action == "SELL":
            if self.units <= 0:
                return "no live sell: nothing held"
            amount = self._amount_to_precision(self.units)
            if amount * price < self.min_usd:
                return (f"no live sell: position ~${amount * price:.2f} is "
                        f"below the ${self.min_usd:.0f} exchange minimum")
            order = self.client.create_order(self.symbol, "market", "sell", amount)
            filled = float(order.get("filled") or amount)
            proceeds = float(order.get("cost") or filled * price)
            self.units -= filled
            self.cash += proceeds
            self._record("SELL", order, filled, price, proceeds, signal.time)
            return (f"LIVE SELL {filled:.6f} {self.coin} ~${proceeds:.2f} "
                    f"(budget now ${self.cash:.2f})")

        return f"no live trade (signal is {signal.action})"

    def value(self, price: float) -> float:
        return self.cash + self.units * price

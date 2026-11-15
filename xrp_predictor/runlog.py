"""Run logging: every signal, scan, simulated trade and error is recorded.

Two files are written side by side:

- ``predictor.log``        - human-readable, one line per event
- ``predictor_log.jsonl``  - one JSON object per line with full detail
                             (share this one when tuning the model)
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

TEXT_LOG = "predictor.log"
JSON_LOG = "predictor_log.jsonl"


class RunLog:
    def __init__(self, directory: str = "logs", enabled: bool = True):
        self.enabled = enabled
        self.dir = Path(directory)
        if enabled:
            self.dir.mkdir(parents=True, exist_ok=True)

    def _now(self) -> str:
        return f"{pd.Timestamp.now(tz='UTC'):%Y-%m-%d %H:%M:%S}"

    def event(self, kind: str, text: str, payload: dict | None = None) -> None:
        """Log one event: short `text` to the text log, full `payload` to JSONL."""
        if not self.enabled:
            return
        stamp = self._now()
        with (self.dir / TEXT_LOG).open("a", encoding="utf-8") as fh:
            fh.write(f"{stamp} UTC | {kind:<9} | {text}\n")
        record = {"time": f"{stamp}Z", "kind": kind}
        if payload:
            record.update(payload)
        with (self.dir / JSON_LOG).open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, default=str) + "\n")

    # -- convenience wrappers -------------------------------------------

    def signal(self, sig) -> None:
        d = sig.to_dict()
        prob = "n/a" if d["prob_up"] is None else f"{d['prob_up'] * 100:.0f}%"
        self.event(
            "SIGNAL",
            f"{d.get('coin', 'XRP')} {d['interval']} {d['action']:<4} "
            f"${d['price']:.4f} score {d['score']:+.3f} "
            f"conf {d['confidence']:.0f}% p_up {prob} src {d['source']}",
            {"signal": d},
        )

    def trade(self, coin: str, action: str, price: float, amount: float,
              value_usd: float, when, note: str = "") -> None:
        self.event(
            "TRADE",
            f"{coin} {action:<4} {amount:.4f} @ ${price:.4f} "
            f"(${value_usd:.2f}) {note}".rstrip(),
            {"trade": {"coin": coin, "action": action, "price": price,
                       "amount": amount, "value_usd": value_usd,
                       "candle_time": str(when), "note": note}},
        )

    def backtest(self, coin: str, result) -> None:
        wr = "n/a" if result.win_rate is None else f"{result.win_rate:.0%}"
        self.event(
            "BACKTEST",
            f"{coin} {result.interval} {result.bars} bars: strategy "
            f"{result.total_return:+.2%} vs hold {result.buy_hold_return:+.2%}, "
            f"dd {result.max_drawdown:.1%}, sharpe {result.sharpe:.2f}, "
            f"{result.n_trades} trips, win {wr}",
            {"backtest": {
                "coin": coin, "interval": result.interval, "bars": result.bars,
                "start": str(result.start), "end": str(result.end),
                "fee": result.fee, "strategy_return": result.total_return,
                "buy_hold_return": result.buy_hold_return,
                "max_drawdown": result.max_drawdown, "sharpe": result.sharpe,
                "n_trades": result.n_trades, "win_rate": result.win_rate,
            }},
        )

    def scan(self, rows: list[dict]) -> None:
        if rows:
            best = rows[0]
            text = (f"{len(rows)} coins; top: {best['coin']} "
                    f"{best['action']} score {best['score']:+.3f}")
        else:
            text = "no coins scanned"
        self.event("SCAN", text, {"scan": rows})

    def error(self, where: str, exc: Exception) -> None:
        self.event("ERROR", f"{where}: {exc}",
                   {"error": {"where": where, "message": str(exc),
                              "type": type(exc).__name__}})

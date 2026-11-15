"""Evidence-checked recommendations.

A raw BUY/SELL signal is only half the story.  Before recommending
anything, this module checks how the strategy and the ML model have
ACTUALLY performed on this coin recently (walk-forward, no look-ahead):

- recent strategy return vs just holding
- recent win rate of completed round trips
- recent directional hit rate of the ML model

From that it assigns a trust level (HIGH / MEDIUM / LOW), may downgrade a
BUY to WAIT when the evidence is weak, and adds risk-management numbers:
ATR-based stop-loss / take-profit and a position size for a chosen
risk-per-trade.  The same evidence check powers ``watch --gated``, which
suppresses automatic buys while recent evidence is poor.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .backtest import BacktestResult
from .strategy import Signal

# evidence window: how much recent history to judge the strategy on
EVIDENCE_BARS = {"15m": 672, "1h": 720, "4h": 180, "1d": 30}   # ~30 days

RISK_PER_TRADE = 0.01       # recommend risking 1% of capital per trade
STOP_ATR_MULT = 2.0         # stop-loss distance: 2 x ATR
TAKE_ATR_MULT = 3.0         # take-profit distance: 3 x ATR


@dataclass
class Evidence:
    window_bars: int
    strategy_return: float
    hold_return: float
    n_trades: int
    win_rate: float | None
    model_hit_rate: float | None

    def beats_market(self) -> bool:
        return self.strategy_return > self.hold_return

    def to_dict(self) -> dict:
        return {
            "window_bars": self.window_bars,
            "strategy_return": round(self.strategy_return, 5),
            "hold_return": round(self.hold_return, 5),
            "n_trades": self.n_trades,
            "win_rate": None if self.win_rate is None else round(self.win_rate, 3),
            "model_hit_rate": (None if self.model_hit_rate is None
                               else round(self.model_hit_rate, 3)),
        }


@dataclass
class Advice:
    signal: Signal
    evidence: Evidence
    trust: str                   # HIGH / MEDIUM / LOW
    recommended_action: str      # signal action, or WAIT when evidence is weak
    stop_loss: float | None
    take_profit: float | None
    position_usd: float | None   # suggested size for the given capital & risk
    capital: float
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "signal": self.signal.to_dict(),
            "evidence": self.evidence.to_dict(),
            "trust": self.trust,
            "recommended_action": self.recommended_action,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "position_usd": self.position_usd,
            "capital": self.capital,
            "notes": self.notes,
        }


def recent_evidence(result: BacktestResult, interval: str) -> Evidence:
    """Judge the strategy on the most recent window of the backtest."""
    window = min(EVIDENCE_BARS.get(interval, 720), len(result.equity))
    eq = result.equity.tail(window)
    strategy_return = float(eq["equity"].iloc[-1] / eq["equity"].iloc[0] - 1.0)
    hold_return = float(eq["buy_hold"].iloc[-1] / eq["buy_hold"].iloc[0] - 1.0)

    start_time = eq["time"].iloc[0]
    finished = [t for t in result.trades
                if t.exit_time is not None and t.exit_time >= start_time]
    wins = sum(1 for t in finished if t.pnl_pct > 2 * result.fee)
    win_rate = wins / len(finished) if finished else None

    sig = result.signals.tail(window).reset_index(drop=True)
    hit_rate = None
    if len(sig) > 2:
        probs = sig["prob_up"].to_numpy(dtype=float)[:-1]
        closes = sig["close"].to_numpy(dtype=float)
        actual_up = closes[1:] > closes[:-1]
        mask = ~np.isnan(probs)
        if mask.sum() >= 20:
            hit_rate = float(((probs[mask] > 0.5) == actual_up[mask]).mean())

    return Evidence(window_bars=window, strategy_return=strategy_return,
                    hold_return=hold_return, n_trades=len(finished),
                    win_rate=win_rate, model_hit_rate=hit_rate)


def trust_level(ev: Evidence) -> str:
    good_model = ev.model_hit_rate is not None and ev.model_hit_rate >= 0.52
    bad_model = ev.model_hit_rate is not None and ev.model_hit_rate < 0.48
    if ev.beats_market() and ev.strategy_return > 0 and not bad_model:
        return "HIGH" if good_model else "MEDIUM"
    if ev.strategy_return < 0 and not ev.beats_market():
        return "LOW"
    if bad_model:
        return "LOW"
    return "MEDIUM"


def make_advice(signal: Signal, ind: pd.DataFrame, result: BacktestResult,
                capital: float = 1000.0,
                risk_per_trade: float = RISK_PER_TRADE) -> Advice:
    ev = recent_evidence(result, signal.interval)
    trust = trust_level(ev)

    recommended = signal.action
    notes: list[str] = []

    pct = lambda v: f"{v:+.2%}"  # noqa: E731
    notes.append(
        f"Recent evidence (last {ev.window_bars} candles): strategy "
        f"{pct(ev.strategy_return)} vs just holding {pct(ev.hold_return)}"
        + (f", win rate {ev.win_rate:.0%} over {ev.n_trades} trades"
           if ev.win_rate is not None else f", {ev.n_trades} trades")
        + (f", model direction correct {ev.model_hit_rate:.0%} of the time."
           if ev.model_hit_rate is not None else "."))

    if trust == "LOW" and signal.action == "BUY":
        recommended = "WAIT"
        notes.append("Signal says BUY, but the strategy has NOT been working "
                     "on this coin recently — recommendation downgraded to "
                     "WAIT. Do not buy on a cold model.")
    elif trust == "LOW" and signal.action == "SELL":
        notes.append("Evidence is weak, but SELL only exits risk — if you "
                     "hold this coin, exiting is still the cautious move.")
    elif trust == "HIGH":
        notes.append("The strategy has been beating the market on this coin "
                     "recently, so the signal deserves more weight. It can "
                     "still be wrong.")
    else:
        notes.append("Mixed recent evidence — treat the signal with caution "
                     "and keep positions small.")

    atr = float(ind["atr_14"].iloc[-1])
    price = signal.price
    stop_loss = take_profit = position_usd = None
    if recommended == "BUY" and atr > 0:
        stop_loss = round(price - STOP_ATR_MULT * atr, 6)
        take_profit = round(price + TAKE_ATR_MULT * atr, 6)
        stop_frac = STOP_ATR_MULT * atr / price
        position_usd = round(min(capital, capital * risk_per_trade / stop_frac), 2)
        notes.append(
            f"Risk plan: buy about ${position_usd:,.2f} "
            f"({position_usd / capital:.0%} of ${capital:,.0f}), stop-loss "
            f"${stop_loss:.4f} ({-stop_frac:.1%}), take-profit "
            f"${take_profit:.4f} (+{TAKE_ATR_MULT * atr / price:.1%}). "
            f"That risks about {risk_per_trade:.0%} of capital if the stop "
            f"is hit.")
        notes.append("Never buy with money you cannot afford to lose, and "
                     "always keep the stop-loss.")
    elif recommended == "WAIT":
        notes.append("While waiting: re-run this advice after a few candles, "
                     "or scan other coins for better evidence.")

    return Advice(signal=signal, evidence=ev, trust=trust,
                  recommended_action=recommended, stop_loss=stop_loss,
                  take_profit=take_profit, position_usd=position_usd,
                  capital=capital, notes=notes)


def gate_allows_buy(result: BacktestResult, interval: str) -> tuple[bool, str]:
    """Evidence gate for automatic trading: may we act on BUY signals?"""
    ev = recent_evidence(result, interval)
    trust = trust_level(ev)
    reason = (f"trust {trust}: recent strategy {ev.strategy_return:+.2%} vs "
              f"hold {ev.hold_return:+.2%}"
              + (f", hit rate {ev.model_hit_rate:.0%}"
                 if ev.model_hit_rate is not None else ""))
    return trust != "LOW", reason

"""Walk-forward backtest of the signal engine.

The model is retrained periodically on data available *up to that point*
(no look-ahead), signals are generated bar by bar, and a simple long/flat
portfolio is simulated: BUY moves everything into XRP, SELL moves
everything back to USD.  Trading fees are charged on every side.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .indicators import WARMUP_ROWS
from .model import DirectionModel, build_features, build_labels
from .strategy import action_for, combined_score, rule_scores

BARS_PER_YEAR = {"15m": 35040, "1h": 8760, "4h": 2190, "1d": 365}


@dataclass
class Trade:
    entry_time: pd.Timestamp
    entry_price: float
    exit_time: pd.Timestamp | None = None
    exit_price: float | None = None

    @property
    def pnl_pct(self) -> float | None:
        if self.exit_price is None:
            return None
        return self.exit_price / self.entry_price - 1.0


@dataclass
class BacktestResult:
    interval: str
    start: pd.Timestamp
    end: pd.Timestamp
    bars: int
    fee: float
    total_return: float
    buy_hold_return: float
    max_drawdown: float
    sharpe: float
    n_trades: int
    win_rate: float | None
    trades: list[Trade] = field(default_factory=list)
    equity: pd.DataFrame | None = None      # time, equity, buy_hold
    signals: pd.DataFrame | None = None     # time, close, action, score, prob_up

    def summary(self) -> str:
        wr = "n/a" if self.win_rate is None else f"{self.win_rate:.1%}"
        return "\n".join([
            f"Period          : {self.start:%Y-%m-%d %H:%M} -> {self.end:%Y-%m-%d %H:%M} UTC",
            f"Bars ({self.interval:>3})      : {self.bars}",
            f"Strategy return : {self.total_return:+.2%}   (fees {self.fee:.2%}/side)",
            f"Buy & hold      : {self.buy_hold_return:+.2%}",
            f"Max drawdown    : {self.max_drawdown:.2%}",
            f"Sharpe (annual) : {self.sharpe:.2f}",
            f"Round trips     : {self.n_trades}   win rate: {wr}",
        ])


def run_backtest(ind: pd.DataFrame, interval: str = "1h", fee: float = 0.001,
                 retrain_every: int = 24, min_train: int = 150,
                 use_model: bool = True) -> BacktestResult:
    """Simulate the strategy over an indicator-decorated OHLCV frame."""
    features = build_features(ind)
    labels = build_labels(ind)

    start_idx = max(WARMUP_ROWS, min_train)
    if len(ind) <= start_idx + 20:
        raise ValueError(
            f"not enough history: {len(ind)} bars, need > {start_idx + 20}")

    model: DirectionModel | None = None
    last_trained = -10**9

    cash, units = 1.0, 0.0
    equity_rows, signal_rows = [], []
    trades: list[Trade] = []

    closes = ind["close"].to_numpy()
    times = ind["time"]

    for i in range(start_idx, len(ind)):
        # Retrain on the past only (rows < i; labels use row i-1's future = row i).
        if use_model and i - last_trained >= retrain_every:
            try:
                model = DirectionModel().fit(features.iloc[:i], labels.iloc[:i])
                last_trained = i
            except ValueError:
                model = None

        prob_up = None
        if model is not None and not features.iloc[[i]].isna().any(axis=1).iloc[0]:
            prob_up = float(model.predict_proba(features.iloc[[i]])[0])

        row = ind.iloc[i]
        score = combined_score(rule_scores(row), prob_up)
        action = action_for(score)
        price = closes[i]

        if action == "BUY" and cash > 0:
            units = cash * (1.0 - fee) / price
            cash = 0.0
            trades.append(Trade(entry_time=times.iloc[i], entry_price=price))
        elif action == "SELL" and units > 0:
            cash = units * price * (1.0 - fee)
            units = 0.0
            trades[-1].exit_time = times.iloc[i]
            trades[-1].exit_price = price

        equity_rows.append({
            "time": times.iloc[i],
            "equity": cash + units * price,
            "buy_hold": price / closes[start_idx],
        })
        signal_rows.append({
            "time": times.iloc[i], "close": price, "action": action,
            "score": score, "prob_up": prob_up,
        })

    equity = pd.DataFrame(equity_rows)
    signals = pd.DataFrame(signal_rows)

    # Close any open position on paper for metrics (not logged as a trade).
    final_equity = float(equity["equity"].iloc[-1])

    curve = equity["equity"].to_numpy()
    running_max = np.maximum.accumulate(curve)
    max_dd = float(((curve - running_max) / running_max).min())

    returns = pd.Series(curve).pct_change().dropna()
    per_year = BARS_PER_YEAR.get(interval, 8760)
    sharpe = 0.0
    if len(returns) > 1 and returns.std(ddof=0) > 0:
        sharpe = float(returns.mean() / returns.std(ddof=0) * np.sqrt(per_year))

    finished = [t for t in trades if t.pnl_pct is not None]
    wins = sum(1 for t in finished if t.pnl_pct > 2 * fee)
    win_rate = wins / len(finished) if finished else None

    return BacktestResult(
        interval=interval,
        start=times.iloc[start_idx],
        end=times.iloc[-1],
        bars=len(ind) - start_idx,
        fee=fee,
        total_return=final_equity - 1.0,
        buy_hold_return=float(closes[-1] / closes[start_idx] - 1.0),
        max_drawdown=max_dd,
        sharpe=sharpe,
        n_trades=len(finished),
        win_rate=win_rate,
        trades=trades,
        equity=equity,
        signals=signals,
    )

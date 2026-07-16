"""Signal engine: combines indicator rules with the ML model probability.

Every component produces a score in [-1, +1] (positive = bullish).  The
weighted rule score is blended with the model's probability of an up-move
and mapped to BUY / SELL / HOLD.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

BUY_THRESHOLD = 0.20
SELL_THRESHOLD = -0.20

RULE_WEIGHTS = {
    "trend": 0.25,      # EMA 12/26 trend
    "macd": 0.20,       # MACD histogram momentum
    "rsi": 0.20,        # overbought / oversold
    "bollinger": 0.15,  # mean reversion
    "stochastic": 0.10,
    "sma50": 0.10,      # position vs long moving average
}

MODEL_BLEND = 0.40  # final = 60% rules + 40% ML model


@dataclass
class Signal:
    time: pd.Timestamp
    price: float
    action: str                 # BUY / SELL / HOLD
    score: float                # [-1, +1]
    confidence: float           # 0..100
    prob_up: float | None       # ML model P(next candle up), None if unavailable
    reasons: list[str] = field(default_factory=list)
    interval: str = "1h"
    source: str = ""

    def to_dict(self) -> dict:
        return {
            "time": self.time.isoformat(),
            "price": round(self.price, 6),
            "action": self.action,
            "score": round(self.score, 4),
            "confidence": round(self.confidence, 1),
            "prob_up": None if self.prob_up is None else round(self.prob_up, 4),
            "interval": self.interval,
            "source": self.source,
            "reasons": self.reasons,
        }


def _clip(value: float) -> float:
    return float(np.clip(value, -1.0, 1.0))


def rule_scores(row: pd.Series) -> dict[str, float]:
    """Per-indicator scores in [-1, +1] for one row of the indicator frame."""
    scores: dict[str, float] = {}

    # Trend: EMA12 above EMA26 is bullish.  +/-0.5% spread saturates.
    spread = row["ema_12"] / row["ema_26"] - 1.0
    scores["trend"] = _clip(spread / 0.005)

    # MACD histogram, normalized by ATR so it works at any price level.
    atr_val = row["atr_14"]
    scores["macd"] = _clip(row["macd_hist"] / (0.25 * atr_val)) if atr_val > 0 else 0.0

    # RSI: below 30 strongly bullish (oversold), above 70 strongly bearish.
    scores["rsi"] = _clip((50.0 - row["rsi_14"]) / 20.0)

    # Bollinger %B: near the lower band -> bullish mean reversion.
    scores["bollinger"] = _clip((0.5 - row["bb_pct_b"]) * 2.0)

    # Stochastic %D.
    scores["stochastic"] = _clip((50.0 - row["stoch_d"]) / 30.0)

    # Above/below the 50-period average; +/-2% saturates.
    sma_dist = row["close"] / row["sma_50"] - 1.0
    scores["sma50"] = _clip(sma_dist / 0.02)

    return scores


def _describe(row: pd.Series, scores: dict[str, float],
              prob_up: float | None) -> list[str]:
    def lean(s: float) -> str:
        if s >= 0.3:
            return "bullish"
        if s <= -0.3:
            return "bearish"
        return "neutral"

    reasons = [
        f"EMA trend: EMA12 {'above' if scores['trend'] >= 0 else 'below'} EMA26 "
        f"-> {lean(scores['trend'])}",
        f"MACD histogram {row['macd_hist']:+.5f} -> {lean(scores['macd'])}",
        f"RSI(14) {row['rsi_14']:.1f} "
        f"({'oversold' if row['rsi_14'] < 30 else 'overbought' if row['rsi_14'] > 70 else 'mid-range'}) "
        f"-> {lean(scores['rsi'])}",
        f"Bollinger %B {row['bb_pct_b']:.2f} -> {lean(scores['bollinger'])}",
        f"Stochastic %D {row['stoch_d']:.1f} -> {lean(scores['stochastic'])}",
        f"Price {row['close'] / row['sma_50'] - 1.0:+.2%} vs SMA50 "
        f"-> {lean(scores['sma50'])}",
    ]
    if prob_up is not None:
        reasons.append(f"ML model: P(next candle up) = {prob_up:.1%} "
                       f"-> {lean((prob_up - 0.5) * 4)}")
    return reasons


def combined_score(scores: dict[str, float], prob_up: float | None) -> float:
    rule = sum(RULE_WEIGHTS[k] * v for k, v in scores.items())
    if prob_up is None:
        return _clip(rule)
    model_score = _clip((prob_up - 0.5) * 4.0)  # 25%..75% maps to -1..+1
    return _clip((1.0 - MODEL_BLEND) * rule + MODEL_BLEND * model_score)


def action_for(score: float) -> str:
    if score >= BUY_THRESHOLD:
        return "BUY"
    if score <= SELL_THRESHOLD:
        return "SELL"
    return "HOLD"


def make_signal(ind: pd.DataFrame, prob_up: float | None,
                interval: str = "1h", source: str = "",
                row_index: int = -1) -> Signal:
    """Build the Signal for one row (default: the most recent candle)."""
    row = ind.iloc[row_index]
    scores = rule_scores(row)
    score = combined_score(scores, prob_up)
    return Signal(
        time=row["time"],
        price=float(row["close"]),
        action=action_for(score),
        score=score,
        confidence=float(min(abs(score), 1.0) * 100.0),
        prob_up=prob_up,
        reasons=_describe(row, scores, prob_up),
        interval=interval,
        source=source,
    )

"""Small machine-learning model that predicts short-term direction.

A regularized logistic regression (implemented in plain numpy, no heavy
dependencies) is trained on engineered indicator features to estimate

    P(next candle closes higher than this one)

The model is retrained on recent history every time it is used, so it
adapts to current market conditions ("walk-forward" style).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

FEATURE_COLUMNS = [
    "f_rsi", "f_macd", "f_ema_trend", "f_bb", "f_stoch",
    "f_roc_1", "f_roc_3", "f_roc_6", "f_roc_12",
    "f_vol_z", "f_atr", "f_sma_dist",
]


def build_features(ind: pd.DataFrame) -> pd.DataFrame:
    """Map indicator columns to normalized model features."""
    close = ind["close"]
    f = pd.DataFrame(index=ind.index)
    f["f_rsi"] = ind["rsi_14"] / 100.0 - 0.5
    f["f_macd"] = (ind["macd_hist"] / close).clip(-0.05, 0.05) * 100.0
    f["f_ema_trend"] = ((ind["ema_12"] / ind["ema_26"]) - 1.0).clip(-0.05, 0.05) * 100.0
    f["f_bb"] = (ind["bb_pct_b"] - 0.5).clip(-1.5, 1.5)
    f["f_stoch"] = ind["stoch_k"] / 100.0 - 0.5
    f["f_roc_1"] = ind["roc_1"].clip(-0.2, 0.2) * 25.0
    f["f_roc_3"] = ind["roc_3"].clip(-0.3, 0.3) * 15.0
    f["f_roc_6"] = ind["roc_6"].clip(-0.4, 0.4) * 10.0
    f["f_roc_12"] = ind["roc_12"].clip(-0.5, 0.5) * 8.0
    f["f_vol_z"] = ind["volume_z"].clip(-4.0, 4.0)
    f["f_atr"] = (ind["atr_14"] / close).clip(0.0, 0.2) * 25.0
    f["f_sma_dist"] = ((close / ind["sma_50"]) - 1.0).clip(-0.2, 0.2) * 20.0
    return f


def build_labels(ind: pd.DataFrame) -> pd.Series:
    """1 if the NEXT candle closes above this candle's close, else 0."""
    nxt = ind["close"].shift(-1)
    return (nxt > ind["close"]).astype(float).where(nxt.notna())


class DirectionModel:
    """Logistic regression with L2 regularization, trained by gradient descent."""

    def __init__(self, l2: float = 1e-2, lr: float = 0.3, iterations: int = 400):
        self.l2 = l2
        self.lr = lr
        self.iterations = iterations
        self.weights: np.ndarray | None = None
        self.mean: np.ndarray | None = None
        self.std: np.ndarray | None = None

    @staticmethod
    def _sigmoid(z: np.ndarray) -> np.ndarray:
        return np.where(z >= 0,
                        1.0 / (1.0 + np.exp(-np.clip(z, -500, 500))),
                        np.exp(np.clip(z, -500, 500)) /
                        (1.0 + np.exp(np.clip(z, -500, 500))))

    def _standardize(self, x: np.ndarray) -> np.ndarray:
        return (x - self.mean) / self.std

    def fit(self, features: pd.DataFrame, labels: pd.Series) -> "DirectionModel":
        mask = features.notna().all(axis=1) & labels.notna()
        x = features.loc[mask].to_numpy(dtype=float)
        y = labels.loc[mask].to_numpy(dtype=float)
        if len(y) < 50:
            raise ValueError(f"not enough training rows ({len(y)}); need >= 50")

        self.mean = x.mean(axis=0)
        self.std = x.std(axis=0)
        self.std[self.std < 1e-9] = 1.0
        xs = self._standardize(x)
        xs = np.hstack([np.ones((len(xs), 1)), xs])  # bias column

        w = np.zeros(xs.shape[1])
        n = len(y)
        for _ in range(self.iterations):
            p = self._sigmoid(xs @ w)
            grad = xs.T @ (p - y) / n
            grad[1:] += self.l2 * w[1:]
            w -= self.lr * grad
        self.weights = w
        return self

    def predict_proba(self, features: pd.DataFrame) -> np.ndarray:
        if self.weights is None:
            raise RuntimeError("model is not fitted")
        x = features.to_numpy(dtype=float)
        xs = self._standardize(x)
        xs = np.hstack([np.ones((len(xs), 1)), xs])
        return self._sigmoid(xs @ self.weights)

    def predict_last(self, features: pd.DataFrame) -> float | None:
        """Probability that the candle after the last row closes higher."""
        last = features.iloc[[-1]]
        if last.isna().any(axis=1).iloc[0]:
            return None
        return float(self.predict_proba(last)[0])


def train_for_live(ind: pd.DataFrame) -> tuple[DirectionModel, float | None]:
    """Train on all history except the last row and predict for the last row."""
    features = build_features(ind)
    labels = build_labels(ind)
    model = DirectionModel()
    model.fit(features.iloc[:-1], labels.iloc[:-1])
    return model, model.predict_last(features)

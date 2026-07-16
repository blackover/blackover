"""End-to-end tests on synthetic data (no network needed).

Run with:  python -m pytest tests/  -or-  python tests/test_app.py
"""

from __future__ import annotations

import os
import sys
import tempfile

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.synthetic import make_ohlcv                      # noqa: E402
from xrp_predictor.backtest import run_backtest             # noqa: E402
from xrp_predictor.indicators import (add_indicators, ema,  # noqa: E402
                                      rsi, sma)
from xrp_predictor.model import (DirectionModel, build_features,  # noqa: E402
                                 build_labels, train_for_live)
from xrp_predictor.paper import PaperPortfolio              # noqa: E402
from xrp_predictor.report import generate_report            # noqa: E402
from xrp_predictor.strategy import (action_for, combined_score,  # noqa: E402
                                    make_signal, rule_scores)


def _ind():
    return add_indicators(make_ohlcv(n=900))


def test_indicator_basics():
    s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
    assert sma(s, 3).iloc[-1] == 4.0
    assert abs(ema(s, 3).iloc[-1] - 4.0625) < 1e-9  # hand-computed

    up_only = pd.Series(np.linspace(1, 2, 40))
    assert rsi(up_only).iloc[-1] == 100.0
    down_only = pd.Series(np.linspace(2, 1, 40))
    assert rsi(down_only).iloc[-1] < 1.0

    ind = _ind()
    valid = ind.iloc[60:]
    assert valid["rsi_14"].between(0, 100).all()
    assert (valid["atr_14"] > 0).all()
    assert valid["bb_pct_b"].notna().all()
    assert valid[["macd", "macd_signal", "macd_hist"]].notna().all().all()


def test_model_learns_and_predicts():
    ind = _ind()
    features, labels = build_features(ind), build_labels(ind)
    model = DirectionModel().fit(features.iloc[:-1], labels.iloc[:-1])
    probs = model.predict_proba(features.iloc[100:200])
    assert ((probs >= 0) & (probs <= 1)).all()
    assert probs.std() > 0.001  # actually discriminates

    _, prob = train_for_live(ind)
    assert prob is None or 0.0 <= prob <= 1.0


def test_strategy_signal():
    ind = _ind()
    scores = rule_scores(ind.iloc[-1])
    assert all(-1.0 <= v <= 1.0 for v in scores.values())
    assert action_for(0.5) == "BUY"
    assert action_for(-0.5) == "SELL"
    assert action_for(0.0) == "HOLD"
    assert combined_score(scores, prob_up=None) == combined_score(scores, None)

    signal = make_signal(ind, prob_up=0.62, interval="1h", source="test")
    assert signal.action in ("BUY", "SELL", "HOLD")
    assert 0 <= signal.confidence <= 100
    assert len(signal.reasons) >= 6
    d = signal.to_dict()
    assert d["action"] == signal.action and d["prob_up"] == 0.62


def test_backtest_runs_and_is_consistent():
    ind = _ind()
    result = run_backtest(ind, interval="1h", fee=0.001)
    assert result.bars > 500
    assert np.isfinite(result.total_return)
    assert -1.0 <= result.max_drawdown <= 0.0
    assert result.equity is not None and len(result.equity) == result.bars
    # equity must equal 1 + total return at the end
    assert abs(result.equity["equity"].iloc[-1] - (1.0 + result.total_return)) < 1e-9
    # trades must alternate buy -> sell
    for t in result.trades[:-1]:
        assert t.exit_time is not None and t.exit_time > t.entry_time


def test_paper_portfolio_roundtrip():
    ind = _ind()
    buy = make_signal(ind, prob_up=0.99, source="test")
    buy.action = "BUY"
    sell = make_signal(ind, prob_up=0.01, source="test")
    sell.action = "SELL"

    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "pf.json")
        pf = PaperPortfolio.load(path, starting_usd=1000.0, fee=0.001)
        pf.apply_signal(buy)
        assert pf.usd == 0.0 and pf.xrp > 0
        pf2 = PaperPortfolio.load(path)          # persisted?
        assert abs(pf2.xrp - pf.xrp) < 1e-9
        pf2.apply_signal(sell)
        assert pf2.xrp == 0.0
        # two fee sides lost, otherwise round trip at same price
        assert abs(pf2.usd - 1000.0 * (1 - 0.001) ** 2) < 1e-6
        assert len(pf2.history) == 2


def test_report_generation():
    ind = _ind()
    result = run_backtest(ind, interval="1h")
    signal = make_signal(ind, prob_up=0.55, interval="1h", source="test")
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "report.html")
        generate_report(signal, ind, result, out_path=out)
        text = open(out, encoding="utf-8").read()
        assert "<svg" in text and "XRP short-term signal" in text
        assert signal.action in text
        assert "Disclaimer" in text


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as exc:
                failures += 1
                print(f"FAIL {name}: {exc}")
    sys.exit(1 if failures else 0)

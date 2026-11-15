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
        assert pf.usd == 0.0 and pf.units > 0
        pf2 = PaperPortfolio.load(path)          # persisted?
        assert abs(pf2.units - pf.units) < 1e-9
        pf2.apply_signal(sell)
        assert pf2.units == 0.0
        # two fee sides lost, otherwise round trip at same price
        assert abs(pf2.usd - 1000.0 * (1 - 0.001) ** 2) < 1e-6
        assert len(pf2.history) == 2

        # a signal for a different coin must be skipped
        other = make_signal(ind, prob_up=0.99, source="test", coin="BTC")
        other.action = "BUY"
        msg = pf2.apply_signal(other)
        assert "skipped" in msg and pf2.usd > 0


def test_coins_catalog():
    from xrp_predictor.data import COINS
    assert "XRP" in COINS and len(COINS) >= 8
    for coin, ids in COINS.items():
        assert set(ids) == {"binance", "kraken", "coingecko"}, coin
    # per-source identifiers must be unique
    for key in ("binance", "kraken", "coingecko"):
        vals = [ids[key] for ids in COINS.values()]
        assert len(vals) == len(set(vals)), key


def test_runlog_writes_both_files():
    import json as _json

    from xrp_predictor.runlog import RunLog

    ind = _ind()
    sig = make_signal(ind, prob_up=0.6, source="test", coin="XRP")
    result = run_backtest(ind, interval="1h")
    with tempfile.TemporaryDirectory() as tmp:
        log = RunLog(directory=tmp)
        log.signal(sig)
        log.backtest("XRP", result)
        log.trade("XRP", "BUY", 2.5, 400.0, 1000.0, sig.time, note="test")
        log.error("unit-test", ValueError("boom"))
        text = open(os.path.join(tmp, "predictor.log")).read()
        assert "SIGNAL" in text and "BACKTEST" in text
        assert "TRADE" in text and "ERROR" in text
        lines = open(os.path.join(tmp, "predictor_log.jsonl")).read().splitlines()
        assert len(lines) == 4
        records = [_json.loads(line) for line in lines]
        assert records[0]["signal"]["coin"] == "XRP"
        assert records[1]["backtest"]["bars"] == result.bars
        assert records[3]["error"]["type"] == "ValueError"


def test_live_trader_with_fake_exchange():
    from xrp_predictor.strategy import Signal
    from xrp_predictor.trader import LiveTrader

    class FakeExchange:
        price = 2.0

        def __init__(self):
            self.markets = {"XRP/USDT": {}}
            self.orders = []

        def load_markets(self):
            return self.markets

        def amount_to_precision(self, symbol, amount):
            return f"{amount:.4f}"

        def create_order(self, symbol, order_type, side, amount):
            assert order_type == "market"
            self.orders.append((symbol, side, amount))
            return {"id": f"o{len(self.orders)}", "filled": amount,
                    "cost": amount * self.price}

    def sig(action, price=2.0, coin="XRP"):
        return Signal(time=pd.Timestamp("2026-11-15", tz="UTC"), price=price,
                      action=action, score=0.5, confidence=50.0,
                      prob_up=None, coin=coin)

    with tempfile.TemporaryDirectory() as tmp:
        state = os.path.join(tmp, "live.json")
        fake = FakeExchange()
        tr = LiveTrader(coin="XRP", budget=100.0, max_usd=50.0,
                        state_path=state, client=fake)

        # BUY is capped at max_usd per order
        msg = tr.execute(sig("BUY"))
        assert "LIVE BUY" in msg and abs(tr.cash - 50.0) < 1e-6
        assert fake.orders[-1][1] == "buy" and fake.orders[-1][2] == 25.0

        # second buy uses the remaining budget slice
        tr.execute(sig("BUY"))
        assert tr.cash < 1e-6 and abs(tr.units - 50.0) < 1e-6

        # below-minimum buy is skipped, not sent to the exchange
        n_orders = len(fake.orders)
        msg = tr.execute(sig("BUY"))
        assert "no live buy" in msg and len(fake.orders) == n_orders

        # wrong coin is never traded
        msg = tr.execute(sig("BUY", coin="BTC"))
        assert "skipped" in msg and len(fake.orders) == n_orders

        # HOLD does nothing
        assert "no live trade" in tr.execute(sig("HOLD"))

        # SELL dumps the whole position
        msg = tr.execute(sig("SELL"))
        assert "LIVE SELL" in msg and tr.units == 0.0
        assert abs(tr.cash - 100.0) < 1e-6  # fake exchange charges no fee

        # state persisted and reloadable
        tr2 = LiveTrader(coin="XRP", budget=999.0, max_usd=50.0,
                         state_path=state, client=FakeExchange())
        assert abs(tr2.cash - 100.0) < 1e-6   # budget from state, not arg
        assert len(tr2.orders) == 3


def test_cli_simulate_and_scan_offline():
    from xrp_predictor.cli import main

    csv_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "..", "sample_data", "synthetic_demo.csv")
    with tempfile.TemporaryDirectory() as tmp:
        trades_csv = os.path.join(tmp, "trades.csv")
        rc = main(["simulate", "--source", "csv", "--csv", csv_path,
                   "--trades-csv", trades_csv, "--log-dir", tmp])
        assert rc == 0
        assert os.path.exists(trades_csv)
        assert os.path.exists(os.path.join(tmp, "predictor_log.jsonl"))
        # scan offline: every coin reads the same CSV, but the pipeline runs
        rc = main(["scan", "--coins", "XRP", "--source", "csv",
                   "--csv", csv_path, "--log-dir", tmp, "--json"])
        assert rc == 0


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

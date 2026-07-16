"""Command line interface.

    python -m xrp_predictor signal    # one prediction now
    python -m xrp_predictor backtest  # how the strategy did historically
    python -m xrp_predictor report    # write report.html with charts
    python -m xrp_predictor watch     # run automatically every candle
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path

from . import __version__
from .backtest import run_backtest
from .data import INTERVALS, fetch_ohlcv, interval_seconds
from .indicators import add_indicators
from .model import train_for_live
from .paper import PaperPortfolio
from .report import generate_report
from .strategy import Signal, make_signal

BANNER = ("XRP predictor v{v} — educational tool, NOT financial advice. "
          "All trading is simulated.")


def _load(args) -> tuple:
    df, source = fetch_ohlcv(interval=args.interval, limit=args.limit,
                             source=args.source, csv_path=args.csv)
    return add_indicators(df), source


def _live_signal(args) -> tuple[Signal, "object"]:
    ind, source = _load(args)
    try:
        _, prob_up = train_for_live(ind)
    except ValueError:
        prob_up = None
    signal = make_signal(ind, prob_up, interval=args.interval, source=source)
    return signal, ind


def _print_signal(signal: Signal, as_json: bool) -> None:
    if as_json:
        print(json.dumps(signal.to_dict(), indent=2))
        return
    print(f"\n=== {signal.action} ===  XRP ${signal.price:.4f}  "
          f"(confidence {signal.confidence:.0f}%)")
    print(f"candle: {signal.time:%Y-%m-%d %H:%M} UTC  interval: {signal.interval}"
          f"  source: {signal.source}  score: {signal.score:+.3f}")
    if signal.prob_up is not None:
        print(f"ML model: P(next candle up) = {signal.prob_up:.1%}")
    print("reasons:")
    for r in signal.reasons:
        print(f"  - {r}")


def _append_log(signal: Signal, path: str) -> None:
    p = Path(path)
    new = not p.exists()
    with p.open("a", newline="") as fh:
        writer = csv.writer(fh)
        if new:
            writer.writerow(["time", "interval", "price", "action",
                             "score", "confidence", "prob_up", "source"])
        d = signal.to_dict()
        writer.writerow([d["time"], d["interval"], d["price"], d["action"],
                         d["score"], d["confidence"], d["prob_up"], d["source"]])


def cmd_signal(args) -> int:
    signal, _ = _live_signal(args)
    _print_signal(signal, args.json)
    if args.log:
        _append_log(signal, args.log)
    return 0


def cmd_backtest(args) -> int:
    ind, source = _load(args)
    result = run_backtest(ind, interval=args.interval, fee=args.fee,
                          retrain_every=args.retrain_every,
                          use_model=not args.no_model)
    print(f"data source: {source}\n")
    print(result.summary())
    return 0


def cmd_report(args) -> int:
    ind, source = _load(args)
    try:
        _, prob_up = train_for_live(ind)
    except ValueError:
        prob_up = None
    signal = make_signal(ind, prob_up, interval=args.interval, source=source)
    result = None
    try:
        result = run_backtest(ind, interval=args.interval, fee=args.fee)
    except ValueError as exc:
        print(f"(backtest skipped: {exc})", file=sys.stderr)
    path = generate_report(signal, ind, result, out_path=args.out)
    _print_signal(signal, as_json=False)
    print(f"\nreport written to {path}")
    return 0


def cmd_watch(args) -> int:
    """Run forever: one prediction per candle (hourly/daily/...)."""
    portfolio = PaperPortfolio.load(args.portfolio, fee=args.fee) if args.trade else None
    seconds = interval_seconds(args.interval)
    print(f"watching XRP every {args.interval} candle "
          f"(paper trading: {'ON' if portfolio else 'off'}) — Ctrl+C to stop")
    while True:
        try:
            signal, ind = _live_signal(args)
            _print_signal(signal, args.json)
            if args.log:
                _append_log(signal, args.log)
            if portfolio is not None:
                print(portfolio.apply_signal(signal))
                print(f"portfolio value: ${portfolio.value(signal.price):.2f} "
                      f"(usd ${portfolio.usd:.2f} + {portfolio.xrp:.4f} XRP)")
            if args.report_out:
                try:
                    result = run_backtest(ind, interval=args.interval, fee=args.fee)
                except ValueError:
                    result = None
                generate_report(signal, ind, result, out_path=args.report_out)
                print(f"report updated: {args.report_out}")
        except KeyboardInterrupt:
            raise
        except Exception as exc:  # noqa: BLE001 - keep the loop alive
            print(f"[watch] error: {exc}", file=sys.stderr)

        # sleep until shortly after the next candle closes
        now = time.time()
        wake = (int(now // seconds) + 1) * seconds + 30
        try:
            time.sleep(max(wake - now, 10))
        except KeyboardInterrupt:
            print("\nstopped")
            return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="xrp_predictor",
        description=BANNER.format(v=__version__))
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p):
        p.add_argument("--interval", default="1h", choices=sorted(INTERVALS),
                       help="candle size (default 1h)")
        p.add_argument("--limit", type=int, default=720,
                       help="number of candles of history (default 720)")
        p.add_argument("--source", default="auto",
                       choices=["auto", "binance", "kraken", "coingecko", "csv"],
                       help="data source (default: try each in order)")
        p.add_argument("--csv", default=None, metavar="PATH",
                       help="read candles from a CSV file instead of an API")
        p.add_argument("--fee", type=float, default=0.001,
                       help="simulated fee per trade side (default 0.001 = 0.1%%)")

    p_signal = sub.add_parser("signal", help="print one BUY/SELL/HOLD signal now")
    common(p_signal)
    p_signal.add_argument("--json", action="store_true", help="output JSON")
    p_signal.add_argument("--log", default=None, metavar="CSV",
                          help="append the signal to this CSV log")
    p_signal.set_defaults(func=cmd_signal)

    p_back = sub.add_parser("backtest", help="simulate the strategy on history")
    common(p_back)
    p_back.add_argument("--retrain-every", type=int, default=24,
                        help="retrain the ML model every N bars (default 24)")
    p_back.add_argument("--no-model", action="store_true",
                        help="rules only, skip the ML model")
    p_back.set_defaults(func=cmd_backtest)

    p_report = sub.add_parser("report", help="write an HTML report with charts")
    common(p_report)
    p_report.add_argument("--out", default="report.html", help="output file")
    p_report.set_defaults(func=cmd_report)

    p_watch = sub.add_parser("watch",
                             help="run automatically once per candle, forever")
    common(p_watch)
    p_watch.add_argument("--json", action="store_true", help="output JSON")
    p_watch.add_argument("--log", default="signals.csv", metavar="CSV",
                         help="append signals to this CSV (default signals.csv)")
    p_watch.add_argument("--trade", action="store_true",
                         help="paper-trade the signals (simulated money)")
    p_watch.add_argument("--portfolio", default="paper_portfolio.json",
                         help="paper portfolio state file")
    p_watch.add_argument("--report-out", default=None, metavar="HTML",
                         help="also regenerate an HTML report every candle")
    p_watch.set_defaults(func=cmd_watch)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    print(BANNER.format(v=__version__), file=sys.stderr)
    return args.func(args)

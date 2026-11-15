"""Command line interface.

    python -m xrp_predictor signal     # one prediction now (default coin XRP)
    python -m xrp_predictor scan       # scan ALL coins for opportunities
    python -m xrp_predictor simulate   # replay strategy on real history, trade by trade
    python -m xrp_predictor backtest   # summary statistics of the strategy
    python -m xrp_predictor report     # write report.html with charts
    python -m xrp_predictor watch      # run automatically every candle

Every command appends to logs/predictor.log (human-readable) and
logs/predictor_log.jsonl (full detail, good for sharing/tuning).
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
from .data import COINS, INTERVALS, fetch_ohlcv, interval_seconds
from .indicators import add_indicators
from .model import train_for_live
from .paper import PaperPortfolio
from .report import generate_report
from .runlog import RunLog
from .strategy import Signal, make_signal

BANNER = ("Coin predictor v{v} — educational tool, NOT financial advice. "
          "All trading is simulated.")


def _runlog(args) -> RunLog:
    return RunLog(directory=args.log_dir, enabled=not args.no_log_file)


def _load(args, coin: str | None = None) -> tuple:
    coin = coin or args.coin
    df, source = fetch_ohlcv(interval=args.interval, limit=args.limit,
                             source=args.source, csv_path=args.csv, coin=coin)
    return add_indicators(df), source


def _live_signal(args, coin: str | None = None) -> tuple[Signal, "object"]:
    coin = coin or args.coin
    ind, source = _load(args, coin)
    try:
        _, prob_up = train_for_live(ind)
    except ValueError:
        prob_up = None
    signal = make_signal(ind, prob_up, interval=args.interval, source=source,
                         coin=coin)
    return signal, ind


def _print_signal(signal: Signal, as_json: bool) -> None:
    if as_json:
        print(json.dumps(signal.to_dict(), indent=2))
        return
    print(f"\n=== {signal.action} ===  {signal.coin} ${signal.price:.4f}  "
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
            writer.writerow(["time", "coin", "interval", "price", "action",
                             "score", "confidence", "prob_up", "source"])
        d = signal.to_dict()
        writer.writerow([d["time"], d["coin"], d["interval"], d["price"],
                         d["action"], d["score"], d["confidence"],
                         d["prob_up"], d["source"]])


def cmd_signal(args) -> int:
    log = _runlog(args)
    try:
        signal, _ = _live_signal(args)
    except Exception as exc:
        log.error(f"signal {args.coin}", exc)
        raise
    _print_signal(signal, args.json)
    log.signal(signal)
    if args.log:
        _append_log(signal, args.log)
    return 0


def cmd_scan(args) -> int:
    """Check every coin and rank buy/sell opportunities."""
    log = _runlog(args)
    coins = ([c.strip().upper() for c in args.coins.split(",")]
             if args.coins else list(COINS))
    unknown = [c for c in coins if c not in COINS]
    if unknown:
        print(f"unknown coins: {', '.join(unknown)} "
              f"(supported: {', '.join(COINS)})", file=sys.stderr)
        return 2

    rows, failures = [], []
    for coin in coins:
        try:
            signal, _ = _live_signal(args, coin)
            rows.append(signal.to_dict())
            log.signal(signal)
        except Exception as exc:  # noqa: BLE001 - keep scanning other coins
            failures.append(f"{coin}: {exc}")
            log.error(f"scan {coin}", exc)

    rows.sort(key=lambda r: r["score"], reverse=True)
    log.scan(rows)

    if args.json:
        print(json.dumps(rows, indent=2))
    else:
        print(f"\nOpportunity scan — {args.interval} candles, "
              f"{len(rows)} coins (sorted most bullish first)\n")
        print(f"{'COIN':<6}{'ACTION':<8}{'PRICE':>14}{'SCORE':>8}"
              f"{'CONF':>6}{'P(UP)':>7}")
        for r in rows:
            prob = "  n/a" if r["prob_up"] is None else f"{r['prob_up']*100:4.0f}%"
            print(f"{r['coin']:<6}{r['action']:<8}{r['price']:>14.4f}"
                  f"{r['score']:>+8.3f}{r['confidence']:>5.0f}%{prob:>7}")
        buys = [r for r in rows if r["action"] == "BUY"]
        sells = [r for r in rows if r["action"] == "SELL"]
        print()
        if buys:
            print("BUY opportunities : " + ", ".join(
                f"{r['coin']} ({r['score']:+.2f})" for r in buys))
        if sells:
            print("SELL signals      : " + ", ".join(
                f"{r['coin']} ({r['score']:+.2f})" for r in sells))
        if not buys and not sells:
            print("No strong opportunity right now — everything is HOLD.")
    for f in failures:
        print(f"failed: {f}", file=sys.stderr)
    return 0


def cmd_backtest(args) -> int:
    log = _runlog(args)
    ind, source = _load(args)
    result = run_backtest(ind, interval=args.interval, fee=args.fee,
                          retrain_every=args.retrain_every,
                          use_model=not args.no_model)
    print(f"coin: {args.coin}   data source: {source}\n")
    print(result.summary())
    log.backtest(args.coin, result)
    return 0


def cmd_simulate(args) -> int:
    """Replay the strategy over real history and show every buy/sell."""
    log = _runlog(args)
    ind, source = _load(args)
    result = run_backtest(ind, interval=args.interval, fee=args.fee)
    log.backtest(args.coin, result)

    capital = args.capital
    cash, units = capital, 0.0
    print(f"\nSimulation — {args.coin}, {args.interval} candles, source "
          f"{source}, starting capital ${capital:.2f}, "
          f"fee {args.fee:.2%}/side\n")

    trade_rows = []
    for t in result.trades:
        units = cash * (1.0 - args.fee) / t.entry_price
        cash = 0.0
        value = units * t.entry_price
        print(f"{t.entry_time:%Y-%m-%d %H:%M}  BUY  "
              f"{units:>12.4f} {args.coin} @ ${t.entry_price:<10.4f} "
              f"value ${value:,.2f}")
        log.trade(args.coin, "BUY", t.entry_price, units, value, t.entry_time,
                  note="simulation")
        trade_rows.append({"time": t.entry_time.isoformat(), "action": "BUY",
                           "price": t.entry_price, "units": round(units, 6),
                           "value_usd": round(value, 2), "pnl_pct": ""})
        if t.exit_time is None:
            print(f"{'':18}(position still open at end of data)")
            cash = units * ind["close"].iloc[-1]  # mark to market
            continue
        cash = units * t.exit_price * (1.0 - args.fee)
        pnl = t.pnl_pct
        print(f"{t.exit_time:%Y-%m-%d %H:%M}  SELL "
              f"{units:>12.4f} {args.coin} @ ${t.exit_price:<10.4f} "
              f"value ${cash:,.2f}  ({pnl:+.2%})")
        log.trade(args.coin, "SELL", t.exit_price, units, cash, t.exit_time,
                  note=f"simulation pnl {pnl:+.2%}")
        trade_rows.append({"time": t.exit_time.isoformat(), "action": "SELL",
                           "price": t.exit_price, "units": round(units, 6),
                           "value_usd": round(cash, 2),
                           "pnl_pct": round(pnl * 100, 3)})
        units = 0.0

    final = capital * (1.0 + result.total_return)
    print(f"\n{result.summary()}")
    print(f"\nFinal value of ${capital:,.2f}: ${final:,.2f} "
          f"({result.total_return:+.2%})  |  buy & hold would be "
          f"${capital * (1 + result.buy_hold_return):,.2f} "
          f"({result.buy_hold_return:+.2%})")

    if args.trades_csv:
        p = Path(args.trades_csv)
        with p.open("w", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=["time", "action", "price",
                                                    "units", "value_usd",
                                                    "pnl_pct"])
            writer.writeheader()
            writer.writerows(trade_rows)
        print(f"trades written to {p}")
    return 0


def cmd_report(args) -> int:
    log = _runlog(args)
    ind, source = _load(args)
    try:
        _, prob_up = train_for_live(ind)
    except ValueError:
        prob_up = None
    signal = make_signal(ind, prob_up, interval=args.interval, source=source,
                         coin=args.coin)
    result = None
    try:
        result = run_backtest(ind, interval=args.interval, fee=args.fee)
        log.backtest(args.coin, result)
    except ValueError as exc:
        print(f"(backtest skipped: {exc})", file=sys.stderr)
    path = generate_report(signal, ind, result, out_path=args.out)
    _print_signal(signal, as_json=False)
    log.signal(signal)
    print(f"\nreport written to {path}")
    return 0


def cmd_watch(args) -> int:
    """Run forever: one prediction per candle (hourly/daily/...)."""
    log = _runlog(args)
    if args.live and args.trade:
        print("choose ONE of --trade (paper) or --live (real orders)",
              file=sys.stderr)
        return 2

    live_trader = None
    if args.live:
        from .trader import LiveTrader, LiveTraderError
        print("\n" + "!" * 68)
        print("!!  LIVE TRADING MODE — this places REAL orders with REAL money.")
        print(f"!!  Exchange: {args.exchange}   pair: {args.coin}/{args.quote}")
        print(f"!!  Managed budget: ${args.budget:.2f}   "
              f"max per order: ${args.max_usd:.2f}")
        print("!!  The prediction model WILL be wrong regularly; you can lose")
        print("!!  this entire budget. Paper-trade first (--trade).")
        print("!" * 68)
        if not args.yes:
            try:
                answer = input("Type LIVE to continue, anything else aborts: ")
            except EOFError:
                answer = ""
            if answer.strip() != "LIVE":
                print("aborted — nothing was traded. "
                      "Use --trade for safe paper trading.")
                return 1
        try:
            live_trader = LiveTrader(
                coin=args.coin, exchange_id=args.exchange, quote=args.quote,
                budget=args.budget, max_usd=args.max_usd, log=log)
        except LiveTraderError as exc:
            print(f"live trading setup failed: {exc}", file=sys.stderr)
            log.error("live setup", exc)
            return 1

    portfolio = (PaperPortfolio.load(args.portfolio, coin=args.coin,
                                     fee=args.fee)
                 if args.trade else None)
    seconds = interval_seconds(args.interval)
    mode = "LIVE" if live_trader else ("paper" if portfolio else "signals only")
    print(f"watching {args.coin} every {args.interval} candle "
          f"(trading: {mode}) — Ctrl+C to stop")
    while True:
        try:
            signal, ind = _live_signal(args)
            _print_signal(signal, args.json)
            log.signal(signal)
            if args.log:
                _append_log(signal, args.log)
            if live_trader is not None:
                msg = live_trader.execute(signal)
                print(msg)
                print(f"live budget value: "
                      f"${live_trader.value(signal.price):.2f} "
                      f"(cash ${live_trader.cash:.2f} + "
                      f"{live_trader.units:.6f} {args.coin})")
            if portfolio is not None:
                before_units = portfolio.units
                print(portfolio.apply_signal(signal))
                print(f"portfolio value: ${portfolio.value(signal.price):.2f} "
                      f"(usd ${portfolio.usd:.2f} + "
                      f"{portfolio.units:.4f} {args.coin})")
                if portfolio.units != before_units:
                    log.trade(args.coin, signal.action, signal.price,
                              abs(portfolio.units - before_units),
                              portfolio.value(signal.price), signal.time,
                              note="paper")
            if args.report_out:
                try:
                    result = run_backtest(ind, interval=args.interval,
                                          fee=args.fee)
                except ValueError:
                    result = None
                generate_report(signal, ind, result, out_path=args.report_out)
                print(f"report updated: {args.report_out}")
        except KeyboardInterrupt:
            raise
        except Exception as exc:  # noqa: BLE001 - keep the loop alive
            print(f"[watch] error: {exc}", file=sys.stderr)
            log.error(f"watch {args.coin}", exc)

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

    def common(p, coin_flag=True):
        if coin_flag:
            p.add_argument("--coin", default="XRP", type=str.upper,
                           choices=list(COINS), metavar="COIN",
                           help=f"coin to analyze (default XRP; "
                                f"choices: {', '.join(COINS)})")
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
        p.add_argument("--log-dir", default="logs",
                       help="directory for predictor.log / predictor_log.jsonl "
                            "(default: logs)")
        p.add_argument("--no-log-file", action="store_true",
                       help="do not write the run log files")

    p_signal = sub.add_parser("signal", help="print one BUY/SELL/HOLD signal now")
    common(p_signal)
    p_signal.add_argument("--json", action="store_true", help="output JSON")
    p_signal.add_argument("--log", default=None, metavar="CSV",
                          help="append the signal to this CSV log")
    p_signal.set_defaults(func=cmd_signal)

    p_scan = sub.add_parser("scan",
                            help="scan all coins and rank opportunities")
    common(p_scan, coin_flag=False)
    p_scan.add_argument("--coins", default=None, metavar="LIST",
                        help="comma-separated coins (default: all supported)")
    p_scan.add_argument("--json", action="store_true", help="output JSON")
    p_scan.set_defaults(func=cmd_scan)

    p_back = sub.add_parser("backtest", help="simulate the strategy on history")
    common(p_back)
    p_back.add_argument("--retrain-every", type=int, default=24,
                        help="retrain the ML model every N bars (default 24)")
    p_back.add_argument("--no-model", action="store_true",
                        help="rules only, skip the ML model")
    p_back.set_defaults(func=cmd_backtest)

    p_sim = sub.add_parser("simulate",
                           help="replay the strategy on real history and "
                                "print every simulated buy/sell")
    common(p_sim)
    p_sim.add_argument("--capital", type=float, default=1000.0,
                       help="starting simulated capital in USD (default 1000)")
    p_sim.add_argument("--trades-csv", default="simulation_trades.csv",
                       metavar="CSV", help="write executed trades to this CSV "
                                           "(default simulation_trades.csv)")
    p_sim.set_defaults(func=cmd_simulate)

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
    p_watch.add_argument("--live", action="store_true",
                         help="place REAL orders on a real exchange "
                              "(requires EXCHANGE_API_KEY/EXCHANGE_API_SECRET "
                              "env vars; read the README first)")
    p_watch.add_argument("--exchange", default="binance",
                         help="ccxt exchange id for --live (default binance)")
    p_watch.add_argument("--quote", default="USDT",
                         help="quote currency for --live pairs (default USDT)")
    p_watch.add_argument("--budget", type=float, default=100.0,
                         help="total USD the live trader may manage "
                              "(default 100)")
    p_watch.add_argument("--max-usd", type=float, default=50.0,
                         help="maximum USD per live order (default 50)")
    p_watch.add_argument("--yes", action="store_true",
                         help="skip the interactive LIVE confirmation "
                              "(for running under nohup/systemd)")
    p_watch.add_argument("--portfolio", default=None,
                         help="paper portfolio state file "
                              "(default paper_portfolio_<COIN>.json)")
    p_watch.add_argument("--report-out", default=None, metavar="HTML",
                         help="also regenerate an HTML report every candle")
    p_watch.set_defaults(func=cmd_watch)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    print(BANNER.format(v=__version__), file=sys.stderr)
    return args.func(args)

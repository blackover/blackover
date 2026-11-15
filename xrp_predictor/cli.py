"""Command line interface.

    python -m xrp_predictor signal     # one prediction now (default XRP)
    python -m xrp_predictor scan       # scan enabled markets for opportunities
    python -m xrp_predictor advise     # evidence-checked recommendation + risk plan
    python -m xrp_predictor news       # recent headlines + sentiment for a symbol
    python -m xrp_predictor simulate   # replay strategy on real history, trade by trade
    python -m xrp_predictor backtest   # summary statistics of the strategy
    python -m xrp_predictor report     # write report.html with charts
    python -m xrp_predictor watch      # run automatically every candle
    python -m xrp_predictor portfolio  # combined view of all paper/live holdings
    python -m xrp_predictor settings   # enable/disable markets, edit watchlists

Symbols can be crypto (XRP, BTC, ...), US stocks (AAPL, NVDA, ...) or
Borsa Istanbul stocks (THYAO, ASELS, ... — priced in TRY).  US and
Turkish stocks are optional: enable them with
`settings enable us_stocks` / `settings enable tr_stocks`.

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
from .advice import gate_allows_buy, make_advice
from .assets import Asset, cur, resolve_asset
from .backtest import periods_per_year, run_backtest
from .data import COINS, INTERVALS, fetch_ohlcv, interval_seconds
from .indicators import add_indicators
from .model import train_for_live
from .paper import PaperPortfolio
from .report import generate_report
from .runlog import RunLog
from .settings import Settings
from .strategy import Signal, make_signal

BANNER = ("Market predictor v{v} — educational tool, NOT financial advice. "
          "All trading is simulated unless you explicitly go --live.")

CRYPTO_SOURCES = ("binance", "kraken", "coingecko")
STOCK_SOURCES = ("yahoo", "stooq")


def _runlog(args) -> RunLog:
    return RunLog(directory=args.log_dir, enabled=not args.no_log_file)


def _load(args, symbol: str | None = None) -> tuple:
    """Fetch candles for any asset.  Returns (indicator_df, source, asset)."""
    settings = Settings.load()
    asset = resolve_asset(symbol or args.symbol, settings)

    if args.source == "csv" or args.csv:
        df, source = fetch_ohlcv(interval=args.interval, limit=args.limit,
                                 source="csv", csv_path=args.csv, coin="XRP")
    elif asset.kind == "crypto":
        source_arg = args.source if args.source in ("auto",) + CRYPTO_SOURCES \
            else "auto"
        df, source = fetch_ohlcv(interval=args.interval, limit=args.limit,
                                 source=source_arg, coin=asset.symbol)
    else:
        from .stocks import fetch_stock_ohlcv
        source_arg = args.source if args.source in ("auto",) + STOCK_SOURCES \
            else "auto"
        df, source = fetch_stock_ohlcv(asset, interval=args.interval,
                                       limit=args.limit, source=source_arg)
    return add_indicators(df), source, asset


def _live_signal(args, symbol: str | None = None) -> tuple[Signal, object, Asset]:
    ind, source, asset = _load(args, symbol)
    try:
        _, prob_up = train_for_live(ind)
    except ValueError:
        prob_up = None
    signal = make_signal(ind, prob_up, interval=args.interval, source=source,
                         coin=asset.symbol, currency=asset.currency)
    return signal, ind, asset


def _print_signal(signal: Signal, as_json: bool) -> None:
    if as_json:
        print(json.dumps(signal.to_dict(), indent=2))
        return
    c = cur(signal.currency)
    print(f"\n=== {signal.action} ===  {signal.coin} {c}{signal.price:.4f}  "
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
            writer.writerow(["time", "symbol", "currency", "interval", "price",
                             "action", "score", "confidence", "prob_up",
                             "source"])
        d = signal.to_dict()
        writer.writerow([d["time"], d["coin"], d["currency"], d["interval"],
                         d["price"], d["action"], d["score"], d["confidence"],
                         d["prob_up"], d["source"]])


def cmd_signal(args) -> int:
    log = _runlog(args)
    try:
        signal, _, _ = _live_signal(args)
    except Exception as exc:
        log.error(f"signal {args.symbol}", exc)
        raise
    _print_signal(signal, args.json)
    log.signal(signal)
    if args.log:
        _append_log(signal, args.log)
    return 0


def cmd_scan(args) -> int:
    """Check symbols across the enabled markets and rank opportunities."""
    log = _runlog(args)
    settings = Settings.load()
    if args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    else:
        symbols = settings.scan_symbols()
    if not symbols:
        print("nothing to scan — enable a market first, e.g.:\n"
              "  python -m xrp_predictor settings enable us_stocks",
              file=sys.stderr)
        return 2

    rows, failures = [], []
    for symbol in symbols:
        try:
            signal, _, asset = _live_signal(args, symbol)
            row = signal.to_dict()
            row["kind"] = asset.kind
            rows.append(row)
            log.signal(signal)
        except Exception as exc:  # noqa: BLE001 - keep scanning the rest
            failures.append(f"{symbol}: {exc}")
            log.error(f"scan {symbol}", exc)

    rows.sort(key=lambda r: r["score"], reverse=True)
    log.scan(rows)

    if args.json:
        print(json.dumps(rows, indent=2))
    else:
        print(f"\nOpportunity scan — {args.interval} candles, "
              f"{len(rows)} symbols (sorted most bullish first)\n")
        print(f"{'SYMBOL':<8}{'MARKET':<10}{'ACTION':<8}{'PRICE':>14}"
              f"{'SCORE':>8}{'CONF':>6}{'P(UP)':>7}")
        for r in rows:
            prob = "  n/a" if r["prob_up"] is None else f"{r['prob_up']*100:4.0f}%"
            market = {"crypto": "crypto", "us_stock": "US",
                      "tr_stock": "BIST"}.get(r.get("kind", ""), "?")
            print(f"{r['coin']:<8}{market:<10}{r['action']:<8}"
                  f"{cur(r['currency'])}{r['price']:>13.4f}"
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
    ind, source, asset = _load(args)
    result = run_backtest(ind, interval=args.interval, fee=args.fee,
                          retrain_every=args.retrain_every,
                          use_model=not args.no_model,
                          annualize_periods=periods_per_year(args.interval,
                                                             asset.kind))
    print(f"symbol: {asset.symbol} ({asset.kind})   data source: {source}\n")
    print(result.summary())
    log.backtest(asset.symbol, result)
    return 0


def cmd_simulate(args) -> int:
    """Replay the strategy over real history and show every buy/sell."""
    log = _runlog(args)
    ind, source, asset = _load(args)
    result = run_backtest(ind, interval=args.interval, fee=args.fee,
                          annualize_periods=periods_per_year(args.interval,
                                                             asset.kind))
    log.backtest(asset.symbol, result)
    c = cur(asset.currency)

    capital = args.capital
    cash, units = capital, 0.0
    print(f"\nSimulation — {asset.symbol} ({asset.kind}), {args.interval} "
          f"candles, source {source}, starting capital {c}{capital:,.2f}, "
          f"fee {args.fee:.2%}/side\n")

    trade_rows = []
    for t in result.trades:
        units = cash * (1.0 - args.fee) / t.entry_price
        cash = 0.0
        value = units * t.entry_price
        print(f"{t.entry_time:%Y-%m-%d %H:%M}  BUY  "
              f"{units:>12.4f} {asset.symbol} @ {c}{t.entry_price:<10.4f} "
              f"value {c}{value:,.2f}")
        log.trade(asset.symbol, "BUY", t.entry_price, units, value,
                  t.entry_time, note="simulation")
        trade_rows.append({"time": t.entry_time.isoformat(), "action": "BUY",
                           "price": t.entry_price, "units": round(units, 6),
                           "value": round(value, 2), "pnl_pct": ""})
        if t.exit_time is None:
            print(f"{'':18}(position still open at end of data)")
            cash = units * ind["close"].iloc[-1]  # mark to market
            continue
        cash = units * t.exit_price * (1.0 - args.fee)
        pnl = t.pnl_pct
        print(f"{t.exit_time:%Y-%m-%d %H:%M}  SELL "
              f"{units:>12.4f} {asset.symbol} @ {c}{t.exit_price:<10.4f} "
              f"value {c}{cash:,.2f}  ({pnl:+.2%})")
        log.trade(asset.symbol, "SELL", t.exit_price, units, cash, t.exit_time,
                  note=f"simulation pnl {pnl:+.2%}")
        trade_rows.append({"time": t.exit_time.isoformat(), "action": "SELL",
                           "price": t.exit_price, "units": round(units, 6),
                           "value": round(cash, 2),
                           "pnl_pct": round(pnl * 100, 3)})
        units = 0.0

    final = capital * (1.0 + result.total_return)
    print(f"\n{result.summary()}")
    print(f"\nFinal value of {c}{capital:,.2f}: {c}{final:,.2f} "
          f"({result.total_return:+.2%})  |  buy & hold would be "
          f"{c}{capital * (1 + result.buy_hold_return):,.2f} "
          f"({result.buy_hold_return:+.2%})")

    if args.trades_csv:
        p = Path(args.trades_csv)
        with p.open("w", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=["time", "action", "price",
                                                    "units", "value",
                                                    "pnl_pct"])
            writer.writeheader()
            writer.writerows(trade_rows)
        print(f"trades written to {p}")
    return 0


def _news_section(asset: Asset, limit: int = 15) -> tuple[list[dict], dict]:
    from .news import fetch_news, summarize
    items = fetch_news(asset, limit=limit)
    return items, summarize(items)


def cmd_news(args) -> int:
    """Recent headlines + keyword sentiment for one symbol or a market."""
    from .news import sentiment_tag
    log = _runlog(args)
    settings = Settings.load()

    if args.market:
        symbols = settings.watchlist(args.market)
        if not symbols:
            print(f"no symbols in market '{args.market}'", file=sys.stderr)
            return 2
        print(f"\nNews mood — {args.market} ({len(symbols)} symbols)\n")
        for symbol in symbols:
            asset = resolve_asset(symbol, settings)
            try:
                items, summary = _news_section(asset, limit=args.headlines)
            except Exception as exc:  # noqa: BLE001
                print(f"{symbol:<8} news fetch failed: {exc}")
                log.error(f"news {symbol}", exc)
                continue
            log.event("NEWS", f"{symbol}: {summary['label']} "
                              f"({summary['positive']}+ / {summary['negative']}-)",
                      {"news": {"symbol": symbol, "summary": summary}})
            print(f"{symbol:<8} {summary['label']:<18} "
                  f"({summary['positive']} positive / {summary['negative']} "
                  f"negative of {summary['n']})")
        print("\n(keyword-based mood scan — read the actual articles before "
              "acting on any of this)")
        return 0

    asset = resolve_asset(args.symbol, settings)
    items, summary = _news_section(asset, limit=args.headlines)
    log.event("NEWS", f"{asset.symbol}: {summary['label']} "
                      f"({summary['positive']}+ / {summary['negative']}-)",
              {"news": {"symbol": asset.symbol, "summary": summary,
                        "headlines": items}})
    if args.json:
        print(json.dumps({"summary": summary, "headlines": items}, indent=2))
        return 0
    print(f"\nNews for {asset.symbol} — {summary['label']} "
          f"({summary['positive']} positive, {summary['negative']} negative, "
          f"{summary['neutral']} neutral)\n")
    for item in items:
        src = f"  — {item['source']}" if item.get("source") else ""
        print(f"  {sentiment_tag(item['sentiment'])} {item['title']}{src}")
    print("\n(keyword-based mood scan, not deep analysis — read the articles "
          "before acting)")
    return 0


def cmd_advise(args) -> int:
    """Signal + evidence check (+ optional news) = an honest recommendation."""
    log = _runlog(args)
    ind, source, asset = _load(args)
    try:
        _, prob_up = train_for_live(ind)
    except ValueError:
        prob_up = None
    signal = make_signal(ind, prob_up, interval=args.interval, source=source,
                         coin=asset.symbol, currency=asset.currency)
    result = run_backtest(ind, interval=args.interval, fee=args.fee,
                          annualize_periods=periods_per_year(args.interval,
                                                             asset.kind))
    advice = make_advice(signal, ind, result, capital=args.capital,
                         risk_per_trade=args.risk)

    news_items, news_summary = [], None
    if args.news:
        try:
            news_items, news_summary = _news_section(asset)
            advice.notes.append(
                f"News mood ({news_summary['n']} recent headlines): "
                f"{news_summary['positive']} positive, "
                f"{news_summary['negative']} negative -> "
                f"{news_summary['label']}. Keyword scan only — read the "
                f"articles before trusting it.")
        except Exception as exc:  # noqa: BLE001 - news is optional garnish
            advice.notes.append(f"News check failed ({exc}) — "
                                f"recommendation is price-data only.")
            log.error(f"news {asset.symbol}", exc)

    log.signal(signal)
    log.backtest(asset.symbol, result)
    payload = advice.to_dict()
    if news_summary:
        payload["news_summary"] = news_summary
    log.event("ADVICE",
              f"{asset.symbol} {signal.interval} recommend "
              f"{advice.recommended_action} (signal {signal.action}, "
              f"trust {advice.trust})",
              {"advice": payload})

    if args.json:
        print(json.dumps(payload, indent=2))
        return 0

    c = cur(asset.currency)
    print(f"\n=== RECOMMENDATION: {advice.recommended_action} ===  "
          f"{asset.symbol} {c}{signal.price:.4f}")
    print(f"raw signal: {signal.action} (score {signal.score:+.3f}, "
          f"confidence {signal.confidence:.0f}%)   "
          f"evidence trust: {advice.trust}")
    if signal.prob_up is not None:
        print(f"ML model: P(next candle up) = {signal.prob_up:.1%}")
    print("\nsignal reasons:")
    for r in signal.reasons:
        print(f"  - {r}")
    print("\nrecommendation notes:")
    for n in advice.notes:
        print(f"  * {n}")
    if news_items:
        from .news import sentiment_tag
        print("\ntop headlines:")
        for item in news_items[:5]:
            print(f"  {sentiment_tag(item['sentiment'])} {item['title']}")
    if advice.stop_loss is not None:
        print(f"\nrisk plan: position {c}{advice.position_usd:,.2f}  "
              f"stop-loss {c}{advice.stop_loss:.4f}  "
              f"take-profit {c}{advice.take_profit:.4f}")
    return 0


def cmd_report(args) -> int:
    log = _runlog(args)
    ind, source, asset = _load(args)
    try:
        _, prob_up = train_for_live(ind)
    except ValueError:
        prob_up = None
    signal = make_signal(ind, prob_up, interval=args.interval, source=source,
                         coin=asset.symbol, currency=asset.currency)
    result = None
    try:
        result = run_backtest(ind, interval=args.interval, fee=args.fee,
                              annualize_periods=periods_per_year(
                                  args.interval, asset.kind))
        log.backtest(asset.symbol, result)
    except ValueError as exc:
        print(f"(backtest skipped: {exc})", file=sys.stderr)
    path = generate_report(signal, ind, result, out_path=args.out)
    _print_signal(signal, as_json=False)
    log.signal(signal)
    print(f"\nreport written to {path}")
    return 0


def cmd_portfolio(args) -> int:
    """Combined view of every paper portfolio and live budget found here."""
    log = _runlog(args)
    settings = Settings.load()
    holdings = []

    for path in sorted(Path(".").glob("paper_portfolio_*.json")):
        try:
            raw = json.loads(path.read_text())
            holdings.append({"mode": "paper", "symbol": raw.get("coin", "?"),
                             "cash": float(raw.get("usd", 0.0)),
                             "units": float(raw.get("units", raw.get("xrp", 0.0))),
                             "start": raw.get("starting_usd"),
                             "history": raw.get("history", [])})
        except (json.JSONDecodeError, OSError) as exc:
            print(f"could not read {path}: {exc}", file=sys.stderr)
    for path in sorted(Path(".").glob("live_state_*.json")):
        try:
            raw = json.loads(path.read_text())
            holdings.append({"mode": "LIVE", "symbol": raw.get("coin", "?"),
                             "cash": float(raw.get("cash_usd", 0.0)),
                             "units": float(raw.get("units", 0.0)),
                             "start": raw.get("starting_usd"),
                             "history": raw.get("orders", [])})
        except (json.JSONDecodeError, OSError) as exc:
            print(f"could not read {path}: {exc}", file=sys.stderr)

    if not holdings:
        print("no portfolios found here yet — start one with:\n"
              "  python -m xrp_predictor watch --symbol XRP --trade")
        return 0

    print(f"\n{'SYMBOL':<8}{'MODE':<7}{'CASH':>12}{'UNITS':>14}{'PRICE':>13}"
          f"{'VALUE':>13}{'P/L':>9}")
    rows = []
    for h in holdings:
        asset = resolve_asset(h["symbol"], settings)
        price, stale = None, ""
        if h["units"] > 0:
            try:
                ind, _, _ = _load(args, h["symbol"])
                price = float(ind["close"].iloc[-1])
            except Exception:  # noqa: BLE001 - offline fallback
                for entry in reversed(h["history"]):
                    if "price" in entry:
                        price = float(entry["price"])
                        stale = "~"
                        break
        value = h["cash"] + h["units"] * (price or 0.0)
        pnl = (f"{(value / h['start'] - 1):+.1%}"
               if h.get("start") else "n/a")
        c = cur(asset.currency)
        price_txt = f"{stale}{c}{price:,.4f}" if price is not None else "-"
        cash_txt = f"{c}{h['cash']:,.2f}"
        value_txt = f"{c}{value:,.2f}"
        print(f"{h['symbol']:<8}{h['mode']:<7}{cash_txt:>12}"
              f"{h['units']:>14.4f}{price_txt:>13}{value_txt:>13}{pnl:>9}")
        rows.append({"symbol": h["symbol"], "mode": h["mode"],
                     "cash": h["cash"], "units": h["units"], "price": price,
                     "value": round(value, 2), "currency": asset.currency,
                     "start": h.get("start")})
    print("\n(~ price is the last trade price — live quote unavailable; "
          "currencies are NOT converted or summed together)")
    log.event("PORTFOLIO", f"{len(rows)} holdings listed",
              {"portfolio": rows})
    return 0


def cmd_settings(args) -> int:
    settings = Settings.load()
    action = args.action
    try:
        if action == "show" or action is None:
            print(settings.describe())
        elif action in ("enable", "disable"):
            if not args.market:
                print("usage: settings enable|disable "
                      "crypto|us_stocks|tr_stocks", file=sys.stderr)
                return 2
            settings.set_market(args.market, action == "enable")
            print(settings.describe())
        elif action in ("add", "remove"):
            if not args.market or not args.value:
                print(f"usage: settings {action} MARKET SYMBOL",
                      file=sys.stderr)
                return 2
            if action == "add":
                settings.add_symbol(args.market, args.value)
            else:
                settings.remove_symbol(args.market, args.value)
            print(settings.describe())
        else:
            print(f"unknown settings action '{action}'", file=sys.stderr)
            return 2
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0


def cmd_watch(args) -> int:
    """Run forever: one prediction per candle (hourly/daily/...)."""
    log = _runlog(args)
    if args.live and args.trade:
        print("choose ONE of --trade (paper) or --live (real orders)",
              file=sys.stderr)
        return 2

    settings = Settings.load()
    asset = resolve_asset(args.symbol, settings)
    if args.live and asset.kind != "crypto":
        print("live trading is only implemented for crypto (via exchange "
              "APIs). Stocks run in paper mode: use --trade instead.",
              file=sys.stderr)
        return 2

    live_trader = None
    if args.live:
        from .trader import LiveTrader, LiveTraderError
        print("\n" + "!" * 68)
        print("!!  LIVE TRADING MODE — this places REAL orders with REAL money.")
        print(f"!!  Exchange: {args.exchange}   pair: {asset.symbol}/{args.quote}")
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
                coin=asset.symbol, exchange_id=args.exchange, quote=args.quote,
                budget=args.budget, max_usd=args.max_usd, log=log)
        except LiveTraderError as exc:
            print(f"live trading setup failed: {exc}", file=sys.stderr)
            log.error("live setup", exc)
            return 1

    portfolio = (PaperPortfolio.load(args.portfolio, coin=asset.symbol,
                                     fee=args.fee)
                 if args.trade else None)
    seconds = interval_seconds(args.interval)
    mode = "LIVE" if live_trader else ("paper" if portfolio else "signals only")
    print(f"watching {asset.symbol} ({asset.kind}) every {args.interval} "
          f"candle (trading: {mode}) — Ctrl+C to stop")
    if asset.kind != "crypto":
        print("note: stock candles only move during exchange trading hours")
    while True:
        try:
            signal, ind, _ = _live_signal(args)
            _print_signal(signal, args.json)
            log.signal(signal)
            if args.log:
                _append_log(signal, args.log)

            # Evidence gate: refuse automatic BUYs while the strategy has
            # not been working on this symbol recently.  SELL always passes
            # (exiting risk is the cautious move).
            suppress_buy = False
            if (args.gated and signal.action == "BUY"
                    and (live_trader is not None or portfolio is not None)):
                try:
                    gate_bt = run_backtest(ind, interval=args.interval,
                                           fee=args.fee)
                    allowed, reason = gate_allows_buy(gate_bt, args.interval)
                except ValueError as exc:
                    allowed, reason = False, f"not enough history ({exc})"
                log.event("GATE",
                          f"{asset.symbol} BUY "
                          f"{'allowed' if allowed else 'SUPPRESSED'} — {reason}",
                          {"gate": {"symbol": asset.symbol, "allowed": allowed,
                                    "reason": reason}})
                if not allowed:
                    suppress_buy = True
                    print(f"[gate] BUY suppressed — {reason}")

            if live_trader is not None and not suppress_buy:
                msg = live_trader.execute(signal)
                print(msg)
                print(f"live budget value: "
                      f"${live_trader.value(signal.price):.2f} "
                      f"(cash ${live_trader.cash:.2f} + "
                      f"{live_trader.units:.6f} {asset.symbol})")
            if portfolio is not None and not suppress_buy:
                before_units = portfolio.units
                print(portfolio.apply_signal(signal))
                c = cur(asset.currency)
                print(f"portfolio value: {c}{portfolio.value(signal.price):.2f} "
                      f"(cash {c}{portfolio.usd:.2f} + "
                      f"{portfolio.units:.4f} {asset.symbol})")
                if portfolio.units != before_units:
                    log.trade(asset.symbol, signal.action, signal.price,
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
            log.error(f"watch {args.symbol}", exc)

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

    def common(p, symbol_flag=True):
        if symbol_flag:
            p.add_argument("--symbol", "--coin", dest="symbol", default="XRP",
                           type=str.upper, metavar="SYMBOL",
                           help="what to analyze: a coin (XRP, BTC, ...), a "
                                "US stock (AAPL, ...) or a BIST stock "
                                "(THYAO, ...). Default XRP")
        p.add_argument("--interval", default="1h", choices=sorted(INTERVALS),
                       help="candle size (default 1h; stocks support "
                            "15m/1h/1d)")
        p.add_argument("--limit", type=int, default=720,
                       help="number of candles of history (default 720)")
        p.add_argument("--source", default="auto",
                       choices=["auto", "binance", "kraken", "coingecko",
                                "yahoo", "stooq", "csv"],
                       help="data source (default: pick automatically)")
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
                            help="scan the enabled markets and rank "
                                 "opportunities")
    common(p_scan, symbol_flag=False)
    p_scan.add_argument("--symbols", "--coins", dest="symbols", default=None,
                        metavar="LIST",
                        help="comma-separated symbols (default: the "
                             "watchlists of every enabled market)")
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
                       help="starting simulated capital (default 1000)")
    p_sim.add_argument("--trades-csv", default="simulation_trades.csv",
                       metavar="CSV", help="write executed trades to this CSV "
                                           "(default simulation_trades.csv)")
    p_sim.set_defaults(func=cmd_simulate)

    p_adv = sub.add_parser("advise",
                           help="evidence-checked recommendation with risk "
                                "plan (stop-loss, take-profit, size)")
    common(p_adv)
    p_adv.add_argument("--capital", type=float, default=1000.0,
                       help="your capital for position sizing (default 1000)")
    p_adv.add_argument("--risk", type=float, default=0.01,
                       help="fraction of capital to risk per trade "
                            "(default 0.01 = 1%%)")
    p_adv.add_argument("--news", action="store_true",
                       help="also check recent news headlines and include "
                            "the mood in the recommendation")
    p_adv.add_argument("--json", action="store_true", help="output JSON")
    p_adv.set_defaults(func=cmd_advise)

    p_news = sub.add_parser("news",
                            help="recent internet headlines + sentiment for "
                                 "a symbol or a whole market")
    common(p_news)
    p_news.add_argument("--market", default=None,
                        choices=["crypto", "us_stocks", "tr_stocks"],
                        help="summarize news mood for every symbol in this "
                             "market's watchlist instead of one symbol")
    p_news.add_argument("--headlines", type=int, default=15,
                        help="headlines per symbol (default 15)")
    p_news.add_argument("--json", action="store_true", help="output JSON")
    p_news.set_defaults(func=cmd_news)

    p_report = sub.add_parser("report", help="write an HTML report with charts")
    common(p_report)
    p_report.add_argument("--out", default="report.html", help="output file")
    p_report.set_defaults(func=cmd_report)

    p_pf = sub.add_parser("portfolio",
                          help="combined view of all paper/live holdings "
                               "with current values and P/L")
    common(p_pf, symbol_flag=False)
    p_pf.set_defaults(func=cmd_portfolio)

    p_set = sub.add_parser("settings",
                           help="enable/disable markets and edit watchlists")
    p_set.add_argument("action", nargs="?", default="show",
                       choices=["show", "enable", "disable", "add", "remove"])
    p_set.add_argument("market", nargs="?", default=None,
                       help="crypto | us_stocks | tr_stocks")
    p_set.add_argument("value", nargs="?", default=None,
                       help="symbol for add/remove")
    p_set.set_defaults(func=cmd_settings)

    p_watch = sub.add_parser("watch",
                             help="run automatically once per candle, forever")
    common(p_watch)
    p_watch.add_argument("--json", action="store_true", help="output JSON")
    p_watch.add_argument("--log", default="signals.csv", metavar="CSV",
                         help="append signals to this CSV (default signals.csv)")
    p_watch.add_argument("--trade", action="store_true",
                         help="paper-trade the signals (simulated money)")
    p_watch.add_argument("--live", action="store_true",
                         help="place REAL orders on a real exchange — crypto "
                              "only (requires EXCHANGE_API_KEY/"
                              "EXCHANGE_API_SECRET env vars; read the README)")
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
    p_watch.add_argument("--gated", action="store_true",
                         help="only auto-BUY while recent evidence shows the "
                              "strategy working on this symbol (recommended)")
    p_watch.add_argument("--portfolio", default=None,
                         help="paper portfolio state file "
                              "(default paper_portfolio_<SYMBOL>.json)")
    p_watch.add_argument("--report-out", default=None, metavar="HTML",
                         help="also regenerate an HTML report every candle")
    p_watch.set_defaults(func=cmd_watch)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    print(BANNER.format(v=__version__), file=sys.stderr)
    return args.func(args)

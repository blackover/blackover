> This repository holds two independent projects:
> **[AeroSim Lab](aerosim/README.md)** — a flight simulator you fly like a game
> (`python -m aerosim`), and the XRP signal app documented below.

---

# XRP Buy/Sell Predictor

An automatic short-term **BUY / SELL / HOLD** signal app for XRP.

Every hour (or every day — you choose), it:

1. downloads the latest XRP price candles from a free public API
   (Binance → Kraken → CoinGecko, whichever answers first — no API key needed),
2. computes classic technical indicators (RSI, MACD, EMA trend, Bollinger
   Bands, Stochastic, ATR, volume),
3. trains a small machine-learning model on recent history to estimate the
   probability that the **next candle closes higher**,
4. combines everything into one signal: **BUY**, **SELL** or **HOLD**, with a
   confidence score and plain-language reasons,
5. optionally **paper-trades** the signal (simulated money, never real orders)
   and writes an HTML report with charts.

> ⚠️ **Disclaimer:** this is an educational tool, **not financial advice**.
> Short-term crypto prediction is extremely hard; signals will often be wrong.
> The app never touches real money — all trading is simulated. Never risk
> money you cannot afford to lose.

## Setup

Needs Python 3.10+.

```bash
pip install -r requirements.txt
```

## Get a signal right now

```bash
python -m xrp_predictor signal                 # hourly candles (default)
python -m xrp_predictor signal --interval 1d   # daily candles
python -m xrp_predictor signal --json          # machine-readable output
```

Example output:

```
=== BUY ===  XRP $2.1834  (confidence 46%)
candle: 2026-07-16 15:00 UTC  interval: 1h  source: binance  score: +0.463
ML model: P(next candle up) = 61.3%
reasons:
  - EMA trend: EMA12 above EMA26 -> bullish
  - RSI(14) 28.4 (oversold) -> bullish
  ...
```

## Run automatically (hourly or daily)

The `watch` command runs forever and produces one signal per candle, right
after each candle closes:

```bash
# every hour, paper-trade the signals, keep an HTML report updated:
python -m xrp_predictor watch --interval 1h --trade --report-out report.html

# every day instead:
python -m xrp_predictor watch --interval 1d --trade
```

Every signal is appended to `signals.csv`, and the simulated portfolio is
stored in `paper_portfolio.json` so it survives restarts.

Prefer cron? Run one signal per invocation instead:

```cron
# hourly, 2 minutes past the hour
2 * * * *  cd /path/to/repo && python -m xrp_predictor signal --log signals.csv >> predictor.log 2>&1

# or daily at 00:02 UTC on daily candles
2 0 * * *  cd /path/to/repo && python -m xrp_predictor signal --interval 1d --log signals.csv >> predictor.log 2>&1
```

## Check the strategy on history (backtest)

Before trusting any signal, see how it would have done on past data —
walk-forward, so the model never sees the future:

```bash
python -m xrp_predictor backtest --interval 1h --limit 1000
```

```
Strategy return : +4.21%   (fees 0.10%/side)
Buy & hold      : -1.35%
Max drawdown    : -9.80%
Round trips     : 12   win rate: 58.3%
```

## HTML report with charts

```bash
python -m xrp_predictor report --out report.html
```

Open `report.html` in a browser: latest signal, price chart with buy/sell
markers, RSI panel, strategy-vs-buy-&-hold equity curve, backtest stats and
the recent signal table. Light and dark mode both supported.

## How the prediction works

Two layers, blended 60/40:

- **Rule layer** — six weighted indicator votes, each scored −1 (bearish) to
  +1 (bullish): EMA 12/26 trend, MACD histogram, RSI overbought/oversold,
  Bollinger %B mean reversion, Stochastic %D, and price vs SMA50.
- **ML layer** — a logistic-regression model (pure numpy) trained on the last
  ~700 candles of engineered indicator features, predicting the probability
  the next candle closes higher. It is retrained on every run, so it adapts
  to current market conditions.

The blended score maps to the signal: **score ≥ +0.20 → BUY**,
**≤ −0.20 → SELL**, otherwise **HOLD**.

## Offline / testing

No internet? Use CSV data (a synthetic demo file is included):

```bash
python -m xrp_predictor report --source csv --csv sample_data/synthetic_demo.csv
python tests/test_app.py        # run the test suite
```

`sample_data/synthetic_demo.csv` is randomly generated demo data, **not** real
XRP prices.

## Project layout

| File | What it does |
|---|---|
| `xrp_predictor/data.py` | fetch OHLCV candles (Binance / Kraken / CoinGecko / CSV) |
| `xrp_predictor/indicators.py` | RSI, MACD, EMA, Bollinger, Stochastic, ATR, OBV… |
| `xrp_predictor/model.py` | numpy logistic regression → P(next candle up) |
| `xrp_predictor/strategy.py` | blends rules + model into BUY/SELL/HOLD |
| `xrp_predictor/backtest.py` | walk-forward historical simulation |
| `xrp_predictor/paper.py` | simulated (paper) trading portfolio |
| `xrp_predictor/report.py` | self-contained HTML report with charts |
| `xrp_predictor/cli.py` | `signal`, `backtest`, `report`, `watch` commands |

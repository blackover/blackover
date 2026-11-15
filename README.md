# Coin Buy/Sell Predictor (XRP + more)

An automatic short-term **BUY / SELL / HOLD** signal app. **XRP is the
default coin**; it can also analyze and scan BTC, ETH, SOL, ADA, DOGE, LINK,
LTC, DOT and AVAX for opportunities.

Every hour (or every day — you choose), it:

1. downloads the latest price candles from a free public API
   (Binance → Kraken → CoinGecko, whichever answers first — no API key needed),
2. computes classic technical indicators (RSI, MACD, EMA trend, Bollinger
   Bands, Stochastic, ATR, volume),
3. trains a small machine-learning model on recent history to estimate the
   probability that the **next candle closes higher**,
4. combines everything into one signal: **BUY**, **SELL** or **HOLD**, with a
   confidence score and plain-language reasons,
5. optionally **paper-trades** the signal (simulated money, never real orders)
   and writes an HTML report with charts,
6. **logs everything** to `logs/predictor.log` and `logs/predictor_log.jsonl`
   so you can review and tune the model later.

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
python -m xrp_predictor signal                   # XRP, hourly candles
python -m xrp_predictor signal --coin BTC        # another coin
python -m xrp_predictor signal --interval 1d     # daily candles
python -m xrp_predictor signal --json            # machine-readable output
```

## Scan all coins for opportunities

```bash
python -m xrp_predictor scan                     # all 10 supported coins
python -m xrp_predictor scan --coins XRP,BTC,SOL # just these
```

```
Opportunity scan — 1h candles, 10 coins (sorted most bullish first)

COIN  ACTION          PRICE   SCORE  CONF  P(UP)
SOL   BUY          142.3100  +0.311   31%   63%
XRP   HOLD           2.1834  +0.142   14%   55%
...
BUY opportunities : SOL (+0.31)
```

## Simulation mode — test the strategy on real data

Replays the strategy over real downloaded history (walk-forward, the model
never sees the future) and prints **every simulated buy and sell**:

```bash
python -m xrp_predictor simulate --coin XRP --interval 1h --limit 1000
python -m xrp_predictor simulate --capital 5000       # start with $5000
```

```
2026-10-02 14:00  BUY      448.2774 XRP @ $2.2285     value $999.00
2026-10-03 10:00  SELL     448.2774 XRP @ $2.2362     value $1,001.43  (+0.34%)
...
Final value of $1,000.00: $1,042.10 (+4.21%)  |  buy & hold would be $986.50 (-1.35%)
trades written to simulation_trades.csv
```

`backtest` gives the same simulation as summary statistics only.

## Run automatically (hourly or daily)

The `watch` command runs forever and produces one signal per candle, right
after each candle closes:

```bash
# every hour, paper-trade the signals, keep an HTML report updated:
python -m xrp_predictor watch --coin XRP --interval 1h --trade --report-out report.html

# every day instead:
python -m xrp_predictor watch --interval 1d --trade
```

Every signal is appended to `signals.csv`, and the simulated portfolio is
stored in `paper_portfolio_XRP.json` so it survives restarts.

Prefer cron? Run one signal per invocation instead:

```cron
# hourly, 2 minutes past the hour
2 * * * *  cd /path/to/repo && python -m xrp_predictor signal --log signals.csv >> cron.log 2>&1

# or daily at 00:02 UTC on daily candles
2 0 * * *  cd /path/to/repo && python -m xrp_predictor signal --interval 1d --log signals.csv >> cron.log 2>&1
```

## Log files (for reviewing and tuning the model)

Every command writes to the `logs/` directory:

- **`logs/predictor.log`** — human-readable: one line per signal, trade,
  backtest and error.
- **`logs/predictor_log.jsonl`** — one JSON object per line with full detail
  (all indicator reasons, scores, model probability, backtest metrics).
  **Share this file when you want help tuning the model** — it contains
  everything needed to see what the model predicted and why.

Use `--log-dir DIR` to change the location, `--no-log-file` to disable.

## HTML report with charts

```bash
python -m xrp_predictor report --coin XRP --out report.html
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
prices.

## Project layout

| File | What it does |
|---|---|
| `xrp_predictor/data.py` | coin catalog + OHLCV fetch (Binance / Kraken / CoinGecko / CSV) |
| `xrp_predictor/indicators.py` | RSI, MACD, EMA, Bollinger, Stochastic, ATR, OBV… |
| `xrp_predictor/model.py` | numpy logistic regression → P(next candle up) |
| `xrp_predictor/strategy.py` | blends rules + model into BUY/SELL/HOLD |
| `xrp_predictor/backtest.py` | walk-forward historical simulation |
| `xrp_predictor/paper.py` | simulated (paper) trading portfolio |
| `xrp_predictor/runlog.py` | predictor.log + predictor_log.jsonl writers |
| `xrp_predictor/report.py` | self-contained HTML report with charts |
| `xrp_predictor/cli.py` | signal / scan / simulate / backtest / report / watch |

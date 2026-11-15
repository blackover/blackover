# Market Buy/Sell Predictor — crypto, US stocks & Borsa Istanbul

An automatic short-term **BUY / SELL / HOLD** signal app. **XRP (crypto) is
the default**; it also analyzes:

- **crypto**: BTC, ETH, SOL, ADA, DOGE, LINK, LTC, DOT, AVAX (Binance →
  Kraken → CoinGecko, no API key)
- **US stocks** *(optional)*: AAPL, MSFT, NVDA, TSLA, ... (Yahoo Finance,
  Stooq fallback)
- **Türkiye / Borsa Istanbul stocks** *(optional, priced in TRY)*: THYAO,
  ASELS, GARAN, AKBNK, ... (Yahoo Finance)

Every hour (or every day — you choose), it:

1. downloads the latest price candles for the symbol,
2. computes classic technical indicators (RSI, MACD, EMA trend, Bollinger
   Bands, Stochastic, ATR, volume),
3. trains a small machine-learning model on recent history to estimate the
   probability that the **next candle closes higher**,
4. combines everything into one signal: **BUY**, **SELL** or **HOLD**, with a
   confidence score and plain-language reasons,
5. can check **recent internet news headlines** and fold the mood into the
   recommendation,
6. optionally **paper-trades** the signal (simulated money) and writes an
   HTML report with charts,
7. **logs everything** to `logs/predictor.log` and `logs/predictor_log.jsonl`
   so you can review and tune the model later.

> ⚠️ **Disclaimer:** this is an educational tool, **not financial advice**.
> Short-term prediction is extremely hard; signals will often be wrong.
> Trading is simulated unless you explicitly enable live mode. Never risk
> money you cannot afford to lose.

## Setup

Needs Python 3.10+.

```bash
pip install -r requirements.txt
```

## Choose your markets (settings)

Crypto is on by default. US and Turkish stocks are **optional** — turn them
on once and every `scan` includes them:

```bash
python -m xrp_predictor settings show
python -m xrp_predictor settings enable us_stocks
python -m xrp_predictor settings enable tr_stocks
python -m xrp_predictor settings add us_stocks AMD       # extend watchlists
python -m xrp_predictor settings remove tr_stocks SISE
```

Choices are saved in `settings.json`. You can always analyze any single
symbol directly without enabling its market, e.g.
`signal --symbol AAPL` or `advise --symbol THYAO`.

## Get a signal right now

```bash
python -m xrp_predictor signal                   # XRP, hourly candles
python -m xrp_predictor signal --symbol BTC      # another coin
python -m xrp_predictor signal --symbol AAPL     # US stock
python -m xrp_predictor signal --symbol THYAO    # BIST stock (TRY)
python -m xrp_predictor signal --interval 1d     # daily candles
```

Stocks support `15m`, `1h` and `1d` candles, and their candles only move
during exchange trading hours.

## Scan all your markets for opportunities

```bash
python -m xrp_predictor scan                     # every enabled market
python -m xrp_predictor scan --symbols XRP,AAPL,THYAO
```

```
Opportunity scan — 1h candles, 19 symbols (sorted most bullish first)

SYMBOL  MARKET    ACTION          PRICE   SCORE  CONF  P(UP)
SOL     crypto    BUY         $142.3100  +0.311   31%   63%
THYAO   BIST      HOLD        ₺308.5000  +0.146   15%   56%
AAPL    US        HOLD        $271.4400  +0.058    6%   52%
...
BUY opportunities : SOL (+0.31)
```

## Get an honest recommendation (signal + evidence + news + risk plan)

A raw signal is not enough — `advise` first checks how the strategy and
model have **actually performed on this symbol recently** (walk-forward),
assigns a trust level, and only then recommends. A BUY with weak evidence
is downgraded to **WAIT**. It also gives a risk plan: ATR-based stop-loss
and take-profit, and a position size for your capital at 1% risk per trade.
Add `--news` and it also scans recent internet headlines (English for
US/crypto, Turkish for BIST) and reports the news mood:

```bash
python -m xrp_predictor advise --symbol XRP --capital 1000
python -m xrp_predictor advise --symbol AAPL --capital 5000 --news
python -m xrp_predictor advise --symbol THYAO --news
```

```
=== RECOMMENDATION: BUY ===  XRP $2.1834
raw signal: BUY (score +0.46, confidence 46%)   evidence trust: HIGH
...
  * Recent evidence (last 720 candles): strategy +4.2% vs just holding -1.3%,
    win rate 58% over 12 trades, model direction correct 54% of the time.
  * News mood (15 recent headlines): 6 positive, 2 negative -> leaning
    positive. Keyword scan only — read the articles before trusting it.
  * Risk plan: buy about $312.00 (31% of $1,000), stop-loss $2.0510 (-6.1%),
    take-profit $2.3820 (+9.1%). That risks about 1% of capital if the stop is hit.
```

## News from the internet (US + Türkiye)

```bash
python -m xrp_predictor news --symbol AAPL              # headlines + mood
python -m xrp_predictor news --symbol THYAO             # Turkish edition
python -m xrp_predictor news --market us_stocks         # mood of a whole market
python -m xrp_predictor news --market tr_stocks
```

Headlines come from Google News RSS (no API key). Sentiment is a simple
positive/negative keyword scan in English and Turkish — a quick mood
check, **not** deep analysis. Always read the actual articles.

## Simulation mode — test the strategy on real data

Replays the strategy over real downloaded history (walk-forward, the model
never sees the future) and prints **every simulated buy and sell** — works
for crypto and stocks:

```bash
python -m xrp_predictor simulate --symbol XRP --interval 1h --limit 1000
python -m xrp_predictor simulate --symbol AAPL --interval 1d --capital 5000
python -m xrp_predictor simulate --symbol THYAO --interval 1h
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
python -m xrp_predictor watch --symbol XRP --interval 1h --trade --report-out report.html

# a stock instead (candles move during market hours only):
python -m xrp_predictor watch --symbol AAPL --interval 1h --trade

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

## Automatic LIVE trading (real money — read this twice)

The watch loop can also place **real market orders** on a real crypto
exchange, driven by the model's BUY/SELL signals. **Live trading is
crypto-only** (stocks always stay in paper mode — connecting a stock
broker is not implemented). It is **off by default** and wrapped in
safety rails:

- the bot manages only a fixed **budget slice** (default **$100**), never
  your whole account;
- each single order is capped (default **$50**, `--max-usd`);
- API keys come **only** from environment variables, never files or flags;
- you must type `LIVE` at startup to confirm (or pass `--yes` for
  unattended runs);
- add `--gated` (recommended!) and the bot refuses automatic BUYs while
  recent evidence shows the strategy losing on that coin;
- every order is appended to `live_state_<COIN>.json` and the run log.

Setup:

```bash
pip install ccxt                      # exchange connector (100+ exchanges)
export EXCHANGE_API_KEY="...your key..."
export EXCHANGE_API_SECRET="...your secret..."

python -m xrp_predictor watch --symbol XRP --interval 1h --live --gated \
    --exchange binance --budget 100 --max-usd 50
```

**Before you do this:**

1. Run `simulate` and paper `watch --trade` for **at least a few weeks** and
   check in the logs whether the strategy actually makes money for your coin
   and interval. In many market regimes it does not.
2. Create a **dedicated API key with trade permission only** — never enable
   withdrawals on a bot key — and ideally restrict it to your IP.
3. Start with a budget you are fully prepared to lose. The model is a
   statistical guess, not a promise; a string of losing trades is normal.

The author takes no responsibility for losses; use entirely at your own risk.

## Your whole budget in one view

Every paper portfolio and live budget you started is a small JSON file.
The `portfolio` command gathers them all — crypto and stocks together —
with current prices and profit/loss:

```bash
python -m xrp_predictor portfolio
```

```
SYMBOL  MODE          CASH         UNITS        PRICE        VALUE      P/L
XRP     paper        $0.00      448.2774      $2.2362    $1,002.36    +0.2%
AAPL    paper      $512.10        1.8000     $271.44      $1,000.69   +0.1%
THYAO   paper        ₺0.00       32.0000     ₺308.50      ₺9,872.00   -1.3%
BTC     LIVE        $50.00        0.0005   $96,210.00       $98.11    -1.9%
```

(Currencies are shown per asset and not converted/summed — a combined
multi-currency HTML dashboard is a good next step.)

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
python -m xrp_predictor report --symbol XRP --out report.html
python -m xrp_predictor report --symbol THYAO --out thyao.html
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
| `xrp_predictor/data.py` | coin catalog + crypto OHLCV (Binance / Kraken / CoinGecko / CSV) |
| `xrp_predictor/assets.py` | symbol resolution: crypto / US stock / BIST stock |
| `xrp_predictor/stocks.py` | stock OHLCV via Yahoo Finance (+ Stooq fallback) |
| `xrp_predictor/settings.py` | optional markets + watchlists (settings.json) |
| `xrp_predictor/news.py` | Google News headlines + EN/TR keyword sentiment |
| `xrp_predictor/indicators.py` | RSI, MACD, EMA, Bollinger, Stochastic, ATR, OBV… |
| `xrp_predictor/model.py` | numpy logistic regression → P(next candle up) |
| `xrp_predictor/strategy.py` | blends rules + model into BUY/SELL/HOLD |
| `xrp_predictor/backtest.py` | walk-forward historical simulation |
| `xrp_predictor/advice.py` | evidence check, trust level, risk plan, BUY gate |
| `xrp_predictor/paper.py` | simulated (paper) trading portfolio |
| `xrp_predictor/trader.py` | live crypto order execution via ccxt (budget-capped) |
| `xrp_predictor/runlog.py` | predictor.log + predictor_log.jsonl writers |
| `xrp_predictor/report.py` | self-contained HTML report with charts |
| `xrp_predictor/cli.py` | signal / scan / advise / news / simulate / backtest / report / watch / portfolio / settings |

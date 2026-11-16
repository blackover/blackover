# Real-data check

Checked on **2026-11-16, 14:47-14:52 UTC** (a Monday; NASDAQ and Borsa Istanbul
were both in session) from a cloud container whose network allows only
api.binance.com, api.kraken.com, api.coingecko.com, query1.finance.yahoo.com,
stooq.com and news.google.com. Everything below is real captured output; the
`watch` command and `--live` were not run.

## Result per data source

| Source | Result | Latest candle (UTC) / close, or headline count | Error (exact) |
|---|---|---|---|
| Binance (XRP 1h) | **FAIL** | - | `451 Client Error:  for url: https://api.binance.com/api/v3/klines?symbol=XRPUSDT&interval=1h&limit=720` - body: `"Service unavailable from a restricted location according to 'b. Eligibility' in https://www.binance.com/en/terms."` |
| Kraken (XRP 1h) | **PASS** | 2026-11-16 14:00, $0.9694 (720 candles, no gaps) | |
| CoinGecko (XRP 1h) | **PASS** | 2026-11-16 14:00, $0.9698 (720 candles) | |
| Yahoo US (AAPL 1h) | **PASS** | 2026-11-16 14:30, $354.88 (720 candles, currency USD) | |
| Yahoo US (AAPL 1d) | **PASS** | 2026-11-16 (session open 14:30), $354.88 (720 candles) | |
| Yahoo Borsa Istanbul (THYAO.IS 1h) | **PASS** | 2026-11-16 14:30, ₺322.50 (720 candles, currency TRY) | |
| Yahoo Borsa Istanbul (THYAO.IS 1d) | **PASS** | 2026-11-16 (session open 06:30), ₺322.50 (720 candles) | |
| Stooq (AAPL 1d) | **FAIL** | - | `('Connection aborted.', ConnectionResetError(104, 'Connection reset by peer'))` on all 3 attempts |
| Google News English (AAPL) | **PASS** | 15 headlines | |
| Google News Turkish (THYAO) | **PASS** | 15 headlines (Turkish) | |

Notes on the two failures - neither is a bug in this code, and neither was
worked around:

- **Binance** answers HTTP 451: it refuses service to the region this
  container's traffic comes from (the response is served from a US CloudFront
  edge). The request itself is well-formed. From a location Binance serves it
  may work, but that is **untested**. In `auto` mode the app retries Binance
  three times (about 3 s) and then falls back to Kraken, which is what
  happened in every crypto run below.
- **Stooq** resets the connection for the app's request. A plain `curl` to the
  same URL got HTTP 200 with a JavaScript challenge page instead of CSV, i.e.
  the site now rejects automated clients. The Stooq fallback is therefore
  **not working**; US daily data comes from Yahoo.

Plausibility checks: Kraken and CoinGecko agree on XRP to within 0.1%; Yahoo
reports `currency: USD` for AAPL and `currency: TRY` for THYAO.IS; the newest
candle of every passing source is from the minute/hour/day of the check.

Known limitations seen in the real data (not changed):

- CoinGecko only provides close prices, so open/high/low are approximations,
  and its volume is a rolling 24h figure rather than per-candle volume.
- Stock candles exist only for trading hours (gaps overnight/weekends), as the
  README already says.

## Code fixes

1. `xrp_predictor/stocks.py` - **Yahoo intraday duplicate last candle.** While
   the market is open, Yahoo returns the forming candle (e.g. 14:30) *plus* an
   extra row stamped with the live quote time (e.g. 14:48:31, volume 0,
   open=high=low=close). The parser kept both, so 15m/1h data ended with a
   bogus zero-volume candle and the signal showed `candle: 14:47 UTC`.
   `parse_yahoo_chart` now folds that trailing row into the candle it belongs
   to (close = live price, high/low widened). Daily data is untouched.
   Offline test added to `test_stock_parsers` using the timestamps seen live.
2. `xrp_predictor/data.py` - **CoinGecko candles could have high < open.**
   Open is taken from the previous close while high/low came only from the
   current bucket, so real data produced rows such as open 0.9557 / high
   0.9486. High/low are now widened to include open and close.

`python3 tests/test_app.py` passes (15 PASS) after the changes.

## Captured output

Individual source runs (after the fixes):

```
$ python3 -m xrp_predictor signal --symbol XRP --source kraken
Market predictor v1.3.0 — educational tool, NOT financial advice. All trading is simulated unless you explicitly go --live.

=== HOLD ===  XRP $0.9694  (confidence 15%)
candle: 2026-11-16 14:00 UTC  interval: 1h  source: kraken  score: -0.155
ML model: P(next candle up) = 44.5%
reasons:
  - EMA trend: EMA12 below EMA26 -> neutral
  - MACD histogram +0.00048 -> neutral
  - RSI(14) 56.6 (mid-range) -> bearish
  - Bollinger %B 0.85 -> bearish
  - Stochastic %D 44.3 -> neutral
  - Price +1.57% vs SMA50 -> bullish
  - ML model: P(next candle up) = 44.5% -> neutral

$ python3 -m xrp_predictor signal --symbol XRP --source coingecko
Market predictor v1.3.0 — educational tool, NOT financial advice. All trading is simulated unless you explicitly go --live.

=== SELL ===  XRP $0.9698  (confidence 26%)
candle: 2026-11-16 14:00 UTC  interval: 1h  source: coingecko  score: -0.259
ML model: P(next candle up) = 38.0%
reasons:
  - EMA trend: EMA12 below EMA26 -> neutral
  - MACD histogram +0.00037 -> neutral
  - RSI(14) 56.1 (mid-range) -> bearish
  - Bollinger %B 0.86 -> bearish
  - Stochastic %D 48.0 -> neutral
  - Price +1.49% vs SMA50 -> bullish
  - ML model: P(next candle up) = 38.0% -> bearish

$ python3 -m xrp_predictor signal --symbol AAPL --interval 1h
Market predictor v1.3.0 — educational tool, NOT financial advice. All trading is simulated unless you explicitly go --live.

=== BUY ===  AAPL $354.8800  (confidence 28%)
candle: 2026-11-16 14:30 UTC  interval: 1h  source: yahoo  score: +0.282
ML model: P(next candle up) = 58.4%
reasons:
  - EMA trend: EMA12 above EMA26 -> bullish
  - MACD histogram -0.46184 -> bearish
  - RSI(14) 50.9 (mid-range) -> neutral
  - Bollinger %B 0.23 -> bullish
  - Stochastic %D 27.2 -> bullish
  - Price +1.11% vs SMA50 -> bullish
  - ML model: P(next candle up) = 58.4% -> bullish

$ python3 -m xrp_predictor signal --symbol AAPL --interval 1d
Market predictor v1.3.0 — educational tool, NOT financial advice. All trading is simulated unless you explicitly go --live.

=== HOLD ===  AAPL $354.8800  (confidence 18%)
candle: 2026-11-16 14:30 UTC  interval: 1d  source: yahoo  score: +0.182
ML model: P(next candle up) = 56.0%
reasons:
  - EMA trend: EMA12 above EMA26 -> bullish
  - MACD histogram +0.83732 -> bullish
  - RSI(14) 59.1 (mid-range) -> bearish
  - Bollinger %B 0.84 -> bearish
  - Stochastic %D 85.7 -> bearish
  - Price +4.46% vs SMA50 -> bullish
  - ML model: P(next candle up) = 56.0% -> neutral

$ python3 -m xrp_predictor signal --symbol THYAO --interval 1h
Market predictor v1.3.0 — educational tool, NOT financial advice. All trading is simulated unless you explicitly go --live.

=== HOLD ===  THYAO ₺322.5000  (confidence 0%)
candle: 2026-11-16 14:30 UTC  interval: 1h  source: yahoo  score: +0.004
ML model: P(next candle up) = 49.9%
reasons:
  - EMA trend: EMA12 above EMA26 -> bullish
  - MACD histogram -0.22628 -> bearish
  - RSI(14) 59.0 (mid-range) -> bearish
  - Bollinger %B 0.77 -> bearish
  - Stochastic %D 72.2 -> bearish
  - Price +1.52% vs SMA50 -> bullish
  - ML model: P(next candle up) = 49.9% -> neutral

$ python3 -m xrp_predictor signal --symbol THYAO --interval 1d
Market predictor v1.3.0 — educational tool, NOT financial advice. All trading is simulated unless you explicitly go --live.

=== HOLD ===  THYAO ₺322.5000  (confidence 17%)
candle: 2026-11-16 06:30 UTC  interval: 1d  source: yahoo  score: +0.175
ML model: P(next candle up) = 55.8%
reasons:
  - EMA trend: EMA12 above EMA26 -> bullish
  - MACD histogram +6.50847 -> bullish
  - RSI(14) 72.2 (overbought) -> bearish
  - Bollinger %B 0.88 -> bearish
  - Stochastic %D 93.8 -> bearish
  - Price +12.58% vs SMA50 -> bullish
  - ML model: P(next candle up) = 55.8% -> neutral
```

Failures:

```
$ python3 -m xrp_predictor signal --symbol XRP --source binance
Market predictor v1.3.0 — educational tool, NOT financial advice. All trading is simulated unless you explicitly go --live.
Traceback (most recent call last):
  File "<frozen runpy>", line 203, in _run_module_as_main
  File "<frozen runpy>", line 88, in _run_code
  File "/home/user/blackover/xrp_predictor/__main__.py", line 6, in <module>
    sys.exit(main())
             ~~~~^^
  File "/home/user/blackover/xrp_predictor/cli.py", line 808, in main
    return args.func(args)
           ~~~~~~~~~^^^^^^
  File "/home/user/blackover/xrp_predictor/cli.py", line 123, in cmd_signal
    signal, _, _ = _live_signal(args)
                   ~~~~~~~~~~~~^^^^^^
  File "/home/user/blackover/xrp_predictor/cli.py", line 79, in _live_signal
    ind, source, asset = _load(args, symbol)
                         ~~~~~^^^^^^^^^^^^^^
  File "/home/user/blackover/xrp_predictor/cli.py", line 67, in _load
    df, source = fetch_ohlcv(interval=args.interval, limit=args.limit,
                 ~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
                             source=source_arg, coin=asset.symbol)
                             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/home/user/blackover/xrp_predictor/data.py", line 183, in fetch_ohlcv
    raise DataError(f"all data sources failed for {coin}:\n  " + "\n  ".join(errors))
xrp_predictor.data.DataError: all data sources failed for XRP:
  binance: 451 Client Error:  for url: https://api.binance.com/api/v3/klines?symbol=XRPUSDT&interval=1h&limit=720
  binance: 451 Client Error:  for url: https://api.binance.com/api/v3/klines?symbol=XRPUSDT&interval=1h&limit=720
  binance: 451 Client Error:  for url: https://api.binance.com/api/v3/klines?symbol=XRPUSDT&interval=1h&limit=720

$ python3 -m xrp_predictor signal --symbol AAPL --interval 1d --source stooq
Market predictor v1.3.0 — educational tool, NOT financial advice. All trading is simulated unless you explicitly go --live.
Traceback (most recent call last):
  File "<frozen runpy>", line 203, in _run_module_as_main
  File "<frozen runpy>", line 88, in _run_code
  File "/home/user/blackover/xrp_predictor/__main__.py", line 6, in <module>
    sys.exit(main())
             ~~~~^^
  File "/home/user/blackover/xrp_predictor/cli.py", line 808, in main
    return args.func(args)
           ~~~~~~~~~^^^^^^
  File "/home/user/blackover/xrp_predictor/cli.py", line 123, in cmd_signal
    signal, _, _ = _live_signal(args)
                   ~~~~~~~~~~~~^^^^^^
  File "/home/user/blackover/xrp_predictor/cli.py", line 79, in _live_signal
    ind, source, asset = _load(args, symbol)
                         ~~~~~^^^^^^^^^^^^^^
  File "/home/user/blackover/xrp_predictor/cli.py", line 73, in _load
    df, source = fetch_stock_ohlcv(asset, interval=args.interval,
                 ~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
                                   limit=args.limit, source=source_arg)
                                   ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/home/user/blackover/xrp_predictor/stocks.py", line 139, in fetch_stock_ohlcv
    raise DataError(f"all stock sources failed for {asset.symbol}:\n  "
                    + "\n  ".join(errors))
xrp_predictor.data.DataError: all stock sources failed for AAPL:
  stooq: ('Connection aborted.', ConnectionResetError(104, 'Connection reset by peer'))
  stooq: ('Connection aborted.', ConnectionResetError(104, 'Connection reset by peer'))
  stooq: ('Connection aborted.', ConnectionResetError(104, 'Connection reset by peer'))
```

News:

```
$ python3 -m xrp_predictor news --symbol AAPL
Market predictor v1.3.0 — educational tool, NOT financial advice. All trading is simulated unless you explicitly go --live.

News for AAPL — leaning positive (4 positive, 1 negative, 10 neutral)

  [ ] AAPL Stock Posted Best Week In 4 Months Amid Nvidia Disruption: Apple Reportedly Plans Smart Glasses To Rival Meta Next Year - Yahoo Finance  — Yahoo Finance
  [+] Is Apple Stock a Buy Right Now? - Yahoo Finance  — Yahoo Finance
  [-] Nvidia Stock's 'Icarus' Fall Isn’t Over Yet, But Microsoft, Apple Show Path to Recovery, Says Top Strateg - Benzinga  — Benzinga
  [ ] Apple targets June 2027 unveiling for AI smart glasses: report (AAPL:NASDAQ) - Seeking Alpha  — Seeking Alpha
  [+] Why Did AAPL, ZETA, FROG Stocks Jump To 52-Week Highs Last Week?c - Yahoo Finance  — Yahoo Finance
  [ ] What's Going On With Apple Stock Wednesday? - Benzinga  — Benzinga
  [ ] What Could Still Send Apple Stock Higher? - Yahoo Finance  — Yahoo Finance
  [ ] Apple's Q4 Earnings Loom: Buy or Sell AAPL Stock Ahead of Results? - Yahoo Finance  — Yahoo Finance
  [ ] Apple Shares (AAPL) Look Expensive Heading into 2027 - Yahoo Finance  — Yahoo Finance
  [ ] Where Will Apple Stock Be in 5 Years? - Yahoo Finance  — Yahoo Finance
  [+] Why Did AAPL, XERS, ZETA Stocks Jump To 52-Week Highs Today? - Yahoo Finance  — Yahoo Finance
  [+] AAPL, GOOG Stocks Gain Overnight As NVDA Slides: Are Retail Investors Rotating Into Traditional Tech Amid Leadership Turmoil? - Yahoo Finance  — Yahoo Finance
  [ ] Why Did AAPL, COST, JNJ Stocks Hit 52-Week Highs Today? - Yahoo Finance  — Yahoo Finance
  [ ] Apple users are about to lose something they've had for 28 years - Yahoo Finance  — Yahoo Finance
  [ ] ETFs Are Buying Apple (AAPL) on Wednesday - Yahoo Finance  — Yahoo Finance

(keyword-based mood scan, not deep analysis — read the articles before acting)

$ python3 -m xrp_predictor news --symbol THYAO
Market predictor v1.3.0 — educational tool, NOT financial advice. All trading is simulated unless you explicitly go --live.

News for THYAO — mixed/neutral (1 positive, 1 negative, 13 neutral)

  [ ] THYAO ve ASTOR öne çıktı: BIST’te en çok para giren ve çıkan hisseler açıklandı - Paratic Haber  — Paratic Haber
  [ ] THYAO Tahmini — 2027 için Fiyat Hedefi - TradingView  — TradingView
  [ ] THYAO Japonya Pazarı İçin Yeni Hamlesini Duyurdu - Finans Hattı  — Finans Hattı
  [ ] THYAO, MAVI ve ALARK dahil 14 hissede destek ve direnç seviyeleri - Paratic Haber  — Paratic Haber
  [ ] THYAO hisseleri için 10 hedef fiyat; Dev banka yüzde 111 prim potansiyeli açıkladı - Ekonomim  — Ekonomim
  [ ] THYAO & PGSUS ve TAVHL Hedef Fiyatı Güncellendi - Midas  — Midas
  [ ] Borsa İstanbul Devre Kesici Sistemi THYAO.E Hisselerinde İşlemleri Durdurdu - Investing.com Türkiye  — Investing.com Türkiye
  [ ] BIST:THYAO için burakkesmeci tarafından THYAO Detaylı Analiz: 290 Bir Sonraki Hikayenin Başlangıcı mı? - TradingView  — TradingView
  [+] THYAO Hissesinde BofA Ve Global Menkul'den Yüklü Alım - Finans Hattı  — Finans Hattı
  [ ] THYAO, SOKM ve ISCTR dahil 9 hisse için hedef fiyat açıklandı - Paratic Haber  — Paratic Haber
  [-] THYAO ve PGSUS sert düştü - Midas  — Midas
  [ ] THYAO Hisse Senetleri ve Fiyatları | TURK HAVA YOLLARI A.O. - Bloomberght  — Bloomberght
  [ ] BIST:THYAO için burakkesmeci tarafından THYAO Detaylı Analiz - 2026 - TradingView  — TradingView
  [ ] Borsada En Çok Yatırımcısı Olan 20 Hisse Senedi 2026 - Finans Hattı  — Finans Hattı
  [ ] Türk Hava Yolları (THYAO) Hisse: THY Hisse Fiyatı - Onedio  — Onedio

(keyword-based mood scan, not deep analysis — read the articles before acting)
```

### Step 3 commands

```
$ python3 -m xrp_predictor simulate --symbol XRP --interval 1h --trades-csv /tmp/x.csv
Market predictor v1.3.0 — educational tool, NOT financial advice. All trading is simulated unless you explicitly go --live.

Simulation — XRP (crypto), 1h candles, source kraken, starting capital $1,000.00, fee 0.10%/side

2026-10-23 22:00  BUY      775.2058 XRP @ $1.2887     value $999.00
2026-10-23 23:00  SELL     775.2058 XRP @ $1.2933     value $1,001.54  (+0.35%)
2026-10-24 13:00  BUY      780.4513 XRP @ $1.2820     value $1,000.54
2026-10-24 16:00  SELL     780.4513 XRP @ $1.2755     value $994.49  (-0.50%)
2026-10-25 05:00  BUY      764.9892 XRP @ $1.2987     value $993.50
2026-10-25 07:00  SELL     764.9892 XRP @ $1.3000     value $993.49  (+0.10%)
2026-10-25 17:00  BUY      761.1473 XRP @ $1.3039     value $992.50
2026-10-25 22:00  SELL     761.1473 XRP @ $1.2981     value $987.06  (-0.45%)
2026-10-26 13:00  BUY      768.5540 XRP @ $1.2830     value $986.07
2026-10-26 14:00  SELL     768.5540 XRP @ $1.3006     value $998.57  (+1.37%)
2026-10-28 00:00  BUY      751.6900 XRP @ $1.3271     value $997.57
2026-10-28 07:00  SELL     751.6900 XRP @ $1.3273     value $996.73  (+0.02%)
2026-10-28 20:00  BUY      748.4956 XRP @ $1.3303     value $995.73
2026-10-29 02:00  SELL     748.4956 XRP @ $1.2944     value $967.88  (-2.70%)
2026-10-29 20:00  BUY      781.5423 XRP @ $1.2372     value $966.91
2026-10-29 21:00  SELL     781.5423 XRP @ $1.2533     value $978.51  (+1.30%)
2026-10-30 07:00  BUY      782.5463 XRP @ $1.2492     value $977.53
2026-10-30 08:00  SELL     782.5463 XRP @ $1.2546     value $980.82  (+0.44%)
2026-11-01 18:00  BUY      785.8488 XRP @ $1.2469     value $979.84
2026-11-02 03:00  SELL     785.8488 XRP @ $1.2596     value $988.86  (+1.02%)
2026-11-02 17:00  BUY      796.1802 XRP @ $1.2408     value $987.87
2026-11-03 03:00  SELL     796.1802 XRP @ $1.2496     value $993.94  (+0.72%)
2026-11-03 14:00  BUY      778.0763 XRP @ $1.2762     value $992.95
2026-11-04 03:00  SELL     778.0763 XRP @ $1.2584     value $978.17  (-1.39%)
2026-11-04 16:00  BUY      743.6131 XRP @ $1.3141     value $977.19
2026-11-05 08:00  SELL     743.6131 XRP @ $1.3224     value $982.38  (+0.63%)
2026-11-05 14:00  BUY      767.0749 XRP @ $1.2794     value $981.40
2026-11-05 16:00  SELL     767.0749 XRP @ $1.2423     value $951.95  (-2.90%)
2026-11-06 12:00  BUY      800.6145 XRP @ $1.1878     value $950.99
2026-11-08 11:00  SELL     800.6145 XRP @ $1.1646     value $931.47  (-1.95%)
2026-11-08 15:00  BUY      805.9134 XRP @ $1.1546     value $930.54
2026-11-08 22:00  SELL     805.9134 XRP @ $1.1091     value $892.97  (-3.94%)

Period          : 2026-10-23 21:00 -> 2026-11-16 14:00 UTC
Bars ( 1h)      : 570
Strategy return : -10.70%   (fees 0.10%/side)
Buy & hold      : -25.66%
Max drawdown    : -11.13%
Sharpe (annual) : -6.30
Round trips     : 16   win rate: 43.8%

Final value of $1,000.00: $892.97 (-10.70%)  |  buy & hold would be $743.40 (-25.66%)
trades written to /tmp/x.csv
```

```
$ python3 -m xrp_predictor simulate --symbol AAPL --interval 1d --trades-csv /tmp/a.csv
Market predictor v1.3.0 — educational tool, NOT financial advice. All trading is simulated unless you explicitly go --live.

Simulation — AAPL (us_stock), 1d candles, source yahoo, starting capital $1,000.00, fee 0.10%/side

2024-08-16 13:30  BUY        4.4194 AAPL @ $226.0500   value $999.00
2025-03-21 13:30  SELL       4.4194 AAPL @ $218.2700   value $963.65  (-3.44%)
2025-04-07 13:30  BUY        5.3052 AAPL @ $181.4600   value $962.69
2025-05-05 13:30  SELL       5.3052 AAPL @ $198.8900   value $1,054.10  (+9.61%)
2025-05-15 13:30  BUY        4.9801 AAPL @ $211.4500   value $1,053.05
2025-06-20 13:30  SELL       4.9801 AAPL @ $201.0000   value $1,000.01  (-4.94%)
2025-07-02 13:30  BUY        4.7025 AAPL @ $212.4400   value $999.01
2026-01-30 14:30  SELL       4.7025 AAPL @ $259.4800   value $1,218.99  (+22.14%)
2026-02-10 14:30  BUY        4.4496 AAPL @ $273.6800   value $1,217.77
                  (position still open at end of data)

Period          : 2024-08-09 13:30 -> 2026-11-16 14:30 UTC
Bars ( 1d)      : 570
Strategy return : +57.87%   (fees 0.10%/side)
Buy & hold      : +64.07%
Max drawdown    : -20.09%
Sharpe (annual) : 0.91
Round trips     : 4   win rate: 50.0%

Final value of $1,000.00: $1,578.68 (+57.87%)  |  buy & hold would be $1,640.72 (+64.07%)
trades written to /tmp/a.csv
```

```
$ python3 -m xrp_predictor simulate --symbol THYAO --interval 1d --trades-csv /tmp/t.csv
Market predictor v1.3.0 — educational tool, NOT financial advice. All trading is simulated unless you explicitly go --live.

Simulation — THYAO (tr_stock), 1d candles, source yahoo, starting capital ₺1,000.00, fee 0.10%/side

2024-08-14 06:30  BUY        3.4242 THYAO @ ₺291.7500   value ₺999.00
2024-09-13 06:30  SELL       3.4242 THYAO @ ₺292.2500   value ₺999.71  (+0.17%)
2024-10-01 06:30  BUY        3.5957 THYAO @ ₺277.7500   value ₺998.71
2024-10-10 06:30  SELL       3.5957 THYAO @ ₺266.5000   value ₺957.30  (-4.05%)
2024-10-14 06:30  BUY        3.7104 THYAO @ ₺257.7500   value ₺956.34
2024-10-16 06:30  SELL       3.7104 THYAO @ ₺269.2500   value ₺998.01  (+4.46%)
2024-11-20 06:30  BUY        3.6790 THYAO @ ₺271.0000   value ₺997.02
2024-11-27 06:30  SELL       3.6790 THYAO @ ₺292.5000   value ₺1,075.04  (+7.93%)
2024-12-03 06:30  BUY        3.7420 THYAO @ ₺287.0000   value ₺1,073.96
2024-12-06 06:30  SELL       3.7420 THYAO @ ₺300.5000   value ₺1,123.36  (+4.70%)
2025-02-05 06:30  BUY        3.6946 THYAO @ ₺303.7500   value ₺1,122.23
2025-02-07 06:30  SELL       3.6946 THYAO @ ₺322.2500   value ₺1,189.39  (+6.09%)
2025-02-21 06:30  BUY        3.8422 THYAO @ ₺309.2500   value ₺1,188.20
2025-03-05 06:30  SELL       3.8422 THYAO @ ₺326.2500   value ₺1,252.27  (+5.50%)
2025-03-19 06:30  BUY        4.1527 THYAO @ ₺301.2500   value ₺1,251.02
2025-03-26 06:30  SELL       4.1527 THYAO @ ₺309.7500   value ₺1,285.03  (+2.82%)
2025-04-04 06:30  BUY        4.2124 THYAO @ ₺304.7500   value ₺1,283.74
2025-04-08 06:30  SELL       4.2124 THYAO @ ₺308.2500   value ₺1,297.19  (+1.15%)
2025-04-29 06:30  BUY        4.3523 THYAO @ ₺297.7500   value ₺1,295.89
2025-05-12 06:30  SELL       4.3523 THYAO @ ₺309.5000   value ₺1,345.68  (+3.95%)
2025-05-16 06:30  BUY        4.5882 THYAO @ ₺293.0000   value ₺1,344.34
2025-06-04 06:30  SELL       4.5882 THYAO @ ₺287.0000   value ₺1,315.49  (-2.05%)
2025-06-13 06:30  BUY        4.9827 THYAO @ ₺263.7500   value ₺1,314.18
2025-06-25 06:30  SELL       4.9827 THYAO @ ₺271.5000   value ₺1,351.44  (+2.94%)
2025-07-30 06:30  BUY        4.7000 THYAO @ ₺287.2500   value ₺1,350.09
2025-08-05 06:30  SELL       4.7000 THYAO @ ₺295.5000   value ₺1,387.47  (+2.87%)
2025-09-03 06:30  BUY        4.2098 THYAO @ ₺329.2500   value ₺1,386.09
2025-10-09 06:30  SELL       4.2098 THYAO @ ₺312.5000   value ₺1,314.26  (-5.09%)
2025-11-11 06:30  BUY        4.8093 THYAO @ ₺273.0000   value ₺1,312.94
2025-11-20 06:30  SELL       4.8093 THYAO @ ₺276.2500   value ₺1,327.24  (+1.19%)
2025-11-25 06:30  BUY        4.8524 THYAO @ ₺273.2500   value ₺1,325.92
2025-12-02 06:30  SELL       4.8524 THYAO @ ₺279.0000   value ₺1,352.46  (+2.10%)
2026-01-12 06:30  BUY        4.6350 THYAO @ ₺291.5000   value ₺1,351.11
2026-02-10 06:30  SELL       4.6350 THYAO @ ₺333.0000   value ₺1,541.92  (+14.24%)
2026-02-18 06:30  BUY        4.6927 THYAO @ ₺328.2500   value ₺1,540.38
2026-03-05 06:30  SELL       4.6927 THYAO @ ₺284.2500   value ₺1,332.57  (-13.40%)
2026-04-17 06:30  BUY        4.0463 THYAO @ ₺329.0000   value ₺1,331.23
2026-05-15 06:30  SELL       4.0463 THYAO @ ₺300.0000   value ₺1,212.68  (-8.81%)
2026-06-23 06:30  BUY        3.7276 THYAO @ ₺325.0000   value ₺1,211.46
2026-07-28 06:30  SELL       3.7276 THYAO @ ₺319.0000   value ₺1,187.91  (-1.85%)
2026-09-03 06:30  BUY        4.0711 THYAO @ ₺291.5000   value ₺1,186.72
2026-09-07 06:30  SELL       4.0711 THYAO @ ₺296.7500   value ₺1,206.89  (+1.80%)
2026-09-16 06:30  BUY        4.4164 THYAO @ ₺273.0000   value ₺1,205.68
2026-09-18 06:30  SELL       4.4164 THYAO @ ₺285.5000   value ₺1,259.62  (+4.58%)
2026-10-23 06:30  BUY        4.8774 THYAO @ ₺258.0000   value ₺1,258.37
2026-11-03 06:30  SELL       4.8774 THYAO @ ₺265.0000   value ₺1,291.21  (+2.71%)

Period          : 2024-08-09 06:30 -> 2026-11-16 06:30 UTC
Bars ( 1d)      : 570
Strategy return : +29.12%   (fees 0.10%/side)
Buy & hold      : +6.96%
Max drawdown    : -24.57%
Sharpe (annual) : 0.64
Round trips     : 23   win rate: 69.6%

Final value of ₺1,000.00: ₺1,291.21 (+29.12%)  |  buy & hold would be ₺1,069.59 (+6.96%)
trades written to /tmp/t.csv
```

```
$ python3 -m xrp_predictor advise --symbol AAPL --interval 1d --news
Market predictor v1.3.0 — educational tool, NOT financial advice. All trading is simulated unless you explicitly go --live.

=== RECOMMENDATION: HOLD ===  AAPL $354.7300
raw signal: HOLD (score +0.183, confidence 18%)   evidence trust: MEDIUM
ML model: P(next candle up) = 56.0%

signal reasons:
  - EMA trend: EMA12 above EMA26 -> bullish
  - MACD histogram +0.82774 -> bullish
  - RSI(14) 59.0 (mid-range) -> bearish
  - Bollinger %B 0.83 -> bearish
  - Stochastic %D 85.6 -> bearish
  - Price +4.41% vs SMA50 -> bullish
  - ML model: P(next candle up) = 56.0% -> neutral

recommendation notes:
  * Recent evidence (last 30 candles): strategy +6.32% vs just holding +6.32%, 0 trades, model direction correct 48% of the time.
  * Mixed recent evidence — treat the signal with caution and keep positions small.
  * News mood (15 recent headlines): 4 positive, 1 negative -> leaning positive. Keyword scan only — read the articles before trusting it.

top headlines:
  [ ] AAPL Stock Posted Best Week In 4 Months Amid Nvidia Disruption: Apple Reportedly Plans Smart Glasses To Rival Meta Next Year - Yahoo Finance
  [+] Is Apple Stock a Buy Right Now? - Yahoo Finance
  [-] Nvidia Stock's 'Icarus' Fall Isn’t Over Yet, But Microsoft, Apple Show Path to Recovery, Says Top Strateg - Benzinga
  [ ] Apple targets June 2027 unveiling for AI smart glasses: report (AAPL:NASDAQ) - Seeking Alpha
  [+] Why Did AAPL, ZETA, FROG Stocks Jump To 52-Week Highs Last Week?c - Yahoo Finance
```

```
$ python3 -m xrp_predictor scan --symbols XRP,BTC,AAPL,NVDA,THYAO,GARAN --interval 1d
Market predictor v1.3.0 — educational tool, NOT financial advice. All trading is simulated unless you explicitly go --live.

Opportunity scan — 1d candles, 6 symbols (sorted most bullish first)

SYMBOL  MARKET    ACTION           PRICE   SCORE  CONF  P(UP)
AAPL    US        HOLD    $     354.6150  +0.184   18%    56%
THYAO   BIST      HOLD    ₺     322.5000  +0.175   18%    56%
NVDA    US        HOLD    $     161.0500  +0.088    9%    60%
GARAN   BIST      HOLD    ₺     149.9000  +0.029    3%    44%
XRP     crypto    HOLD    $       0.9702  -0.105   10%    49%
BTC     crypto    HOLD    $   65186.6000  -0.135   14%    46%

No strong opportunity right now — everything is HOLD.
```

These simulations are backtests on real historical candles; they show the data
pipeline works end to end, not that the strategy is profitable.

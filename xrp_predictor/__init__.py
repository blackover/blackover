"""Short-term buy/sell signal predictor for crypto (XRP first), US stocks
and Borsa Istanbul stocks.

Educational tool: fetches market data, computes technical indicators,
trains a small machine-learning model on recent history and produces
BUY / SELL / HOLD signals on an hourly or daily schedule.  Can scan
several markets for opportunities, check news sentiment, and simulate
trading on real data.

This is NOT financial advice and does NOT place real orders unless live
trading is explicitly enabled by the user.
"""

__version__ = "1.3.0"

SYMBOL = "XRP"
QUOTE = "USD"

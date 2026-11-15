"""Short-term buy/sell signal predictor for XRP and other coins.

Educational tool: fetches market data, computes technical indicators,
trains a small machine-learning model on recent history and produces
BUY / SELL / HOLD signals on an hourly or daily schedule.  Can scan
several coins for opportunities and simulate trading on real data.

This is NOT financial advice and does NOT place real orders.
"""

__version__ = "1.2.0"

SYMBOL = "XRP"
QUOTE = "USD"

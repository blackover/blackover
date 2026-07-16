"""XRP short-term buy/sell signal predictor.

Educational tool: fetches XRP market data, computes technical indicators,
trains a small machine-learning model on recent history and produces
BUY / SELL / HOLD signals on an hourly or daily schedule.

This is NOT financial advice and does NOT place real orders.
"""

__version__ = "1.0.0"

SYMBOL = "XRP"
QUOTE = "USD"

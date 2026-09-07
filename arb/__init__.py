"""Simulated arbitrage scanner across Kalshi, Polymarket and sportsbook odds.

Nothing in this package places real orders. Every "trade" is a paper trade
recorded in a local SQLite database and settled against public resolution data.
"""

__version__ = "0.1.0"

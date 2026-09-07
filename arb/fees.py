"""Fee models.

Kalshi (fee schedule, July 2026): taker fee = 0.07 x C x P x (1-P), rounded up
to the next cent per order; maker fee (only on series flagged
`quadratic_with_maker_fees`) = 0.0175 x C x P x (1-P). Some series carry a
`fee_multiplier` (e.g. MLB game markets = 0.5).

Polymarket (docs.polymarket.com/polymarket-learn/trading/fees): taker fee =
C x rate x p x (1-p) with rate by category (sports 0.05, crypto 0.07,
finance/politics/tech/mentions 0.04, geopolitics 0, everything else 0.05).
Makers pay nothing. We always assume we are the taker on both legs, which is
the conservative choice for an arbitrage that must be executed immediately.
"""
from __future__ import annotations

import math

KALSHI_TAKER = 0.07
KALSHI_MAKER = 0.0175

POLY_RATES = {
    "sports": 0.05,
    "crypto": 0.07,
    "finance": 0.04,
    "politics": 0.04,
    "tech": 0.04,
    "mentions": 0.04,
    "geopolitics": 0.0,
    "economics": 0.05,
    "culture": 0.05,
    "weather": 0.05,
}
POLY_DEFAULT = 0.05


def quadratic_fee(rate: float, qty: float, price: float) -> float:
    """Unrounded fee = rate * qty * p * (1 - p)."""
    return rate * qty * price * (1.0 - price)


def kalshi_fee(qty: float, price: float, rate: float = KALSHI_TAKER, multiplier: float = 1.0) -> float:
    raw = quadratic_fee(rate * multiplier, qty, price)
    return math.ceil(raw * 100 - 1e-9) / 100.0


def polymarket_fee(qty: float, price: float, rate: float = POLY_DEFAULT) -> float:
    return round(quadratic_fee(rate, qty, price), 5)


def poly_rate_for_tags(tag_slugs: list[str], fees_enabled: bool = True, default: float = POLY_DEFAULT) -> float:
    if not fees_enabled:
        return 0.0
    slugs = {s.lower() for s in tag_slugs}
    if "geopolitics" in slugs:
        return 0.0
    for k in ("crypto", "sports", "finance", "politics", "tech", "mentions", "economics", "weather", "culture"):
        if k in slugs or any(s.startswith(k) for s in slugs):
            return POLY_RATES[k]
    return default


def fee_for(venue: str, qty: float, price: float, rate: float, round_cents: bool) -> float:
    """Generic per-leg fee used by the arbitrage engine."""
    if rate <= 0 or qty <= 0:
        return 0.0
    if round_cents:
        return math.ceil(quadratic_fee(rate, qty, price) * 100 - 1e-9) / 100.0
    return round(quadratic_fee(rate, qty, price), 5)

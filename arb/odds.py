"""Odds conversions and vig removal for sportsbook lines."""
from __future__ import annotations


def american_to_decimal(american: int | float) -> float:
    a = float(american)
    if a > 0:
        return 1.0 + a / 100.0
    if a < 0:
        return 1.0 + 100.0 / abs(a)
    raise ValueError("american odds cannot be 0")


def decimal_to_american(decimal: float) -> int:
    if decimal >= 2.0:
        return int(round((decimal - 1.0) * 100))
    return int(round(-100.0 / (decimal - 1.0)))


def implied_prob(american: int | float) -> float:
    return 1.0 / american_to_decimal(american)


def prob_to_american(p: float) -> int:
    if p <= 0 or p >= 1:
        raise ValueError("p must be in (0,1)")
    return decimal_to_american(1.0 / p)


def devig_multiplicative(implied: list[float]) -> list[float]:
    """Scale implied probabilities so they sum to 1 (the standard 'remove the
    juice proportionally' method)."""
    s = sum(implied)
    return [p / s for p in implied]


def devig_additive(implied: list[float]) -> list[float]:
    """Subtract the overround equally from each outcome."""
    over = sum(implied) - 1.0
    n = len(implied)
    return [max(1e-6, p - over / n) for p in implied]


def devig_power(implied: list[float], tol: float = 1e-10) -> list[float]:
    """Power method: find k such that sum(p_i ** k) == 1. Better matches the
    favourite-longshot bias than proportional scaling."""
    lo, hi = 0.5, 3.0
    for _ in range(200):
        k = (lo + hi) / 2
        s = sum(p ** k for p in implied)
        if abs(s - 1.0) < tol:
            break
        if s > 1.0:
            lo = k
        else:
            hi = k
    k = (lo + hi) / 2
    return [p ** k for p in implied]


def overround(implied: list[float]) -> float:
    return sum(implied) - 1.0

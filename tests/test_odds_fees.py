import math

import pytest

from arb.fees import kalshi_fee, poly_rate_for_tags, polymarket_fee, quadratic_fee
from arb.odds import (american_to_decimal, decimal_to_american, devig_multiplicative, devig_power, implied_prob,
                      overround)


def test_american_decimal_roundtrip():
    for a in (-180, +150, -110, +100, +2500, -5000):
        assert decimal_to_american(american_to_decimal(a)) == a


def test_implied_prob():
    assert implied_prob(-100) == pytest.approx(0.5)
    assert implied_prob(+300) == pytest.approx(0.25)
    assert implied_prob(-300) == pytest.approx(0.75)


def test_devig():
    imp = [implied_prob(-180), implied_prob(+150)]
    assert overround(imp) > 0
    fair = devig_multiplicative(imp)
    assert sum(fair) == pytest.approx(1.0)
    assert fair[0] > fair[1]
    fp = devig_power(imp)
    assert sum(fp) == pytest.approx(1.0, abs=1e-6)


def test_kalshi_fee_matches_schedule():
    # 100 contracts at 50c: 0.07 * 100 * 0.25 = $1.75
    assert kalshi_fee(100, 0.50) == pytest.approx(1.75)
    # rounds UP to the next cent
    assert kalshi_fee(1, 0.50) == pytest.approx(0.02)  # 0.0175 -> 0.02
    assert kalshi_fee(10, 0.10) == pytest.approx(0.07)  # 0.063 -> 0.07
    # MLB multiplier 0.5
    assert kalshi_fee(100, 0.50, multiplier=0.5) == pytest.approx(0.88)  # 0.875 rounds up


def test_polymarket_fee_matches_docs():
    # docs example: 100 shares @ 0.50 crypto (7%) = 1.75
    assert polymarket_fee(100, 0.5, 0.07) == pytest.approx(1.75)
    assert polymarket_fee(100, 0.5, 0.05) == pytest.approx(1.25)
    assert quadratic_fee(0.05, 100, 0.9) == pytest.approx(0.45)


def test_poly_rate_by_tag():
    assert poly_rate_for_tags(["sports", "nfl"]) == 0.05
    assert poly_rate_for_tags(["crypto"]) == 0.07
    assert poly_rate_for_tags(["politics"]) == 0.04
    assert poly_rate_for_tags(["geopolitics"]) == 0.0
    assert poly_rate_for_tags(["sports"], fees_enabled=False) == 0.0

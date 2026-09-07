from datetime import datetime, timezone

import pytest

from arb.arbitrage import Side, cross_pair, intra_set, walk
from arb.config import Settings
from arb.models import Book, Level, Outcome


def mk(venue, yes_asks, no_asks, rate=0.0, round_cents=False, title="X"):
    o = Outcome(venue=venue, id=f"{venue}-{title}", market_id="m", event_id="e", title=title, event_title="E",
                fee_rate=rate, fee_round_cents=round_cents)
    o.book = Book(yes_asks=[Level(*l) for l in yes_asks], no_asks=[Level(*l) for l in no_asks],
                  yes_bids=[], no_bids=[])
    return o


def settings(**kw):
    s = Settings(min_margin=0.0, max_per_trade=10_000)
    for k, v in kw.items():
        setattr(s, k, v)
    return s


def test_walk_stops_when_marginal_cost_exceeds_one():
    a = mk("kalshi", yes_asks=[(0.40, 100), (0.45, 100), (0.60, 100)], no_asks=[])
    b = mk("polymarket", yes_asks=[], no_asks=[(0.55, 50), (0.56, 100)])
    qty, fills = walk([Side(a, "yes"), Side(b, "no")], 1e9, 1e9, 0.0)
    # level pairs: (0.40,0.55)=0.95 x50, (0.40,0.56)=0.96 x50, (0.45,0.56)=1.01 stop
    assert qty == 100
    assert sum(q for _, q in fills[0]) == 100
    assert sum(q for _, q in fills[1]) == 100
    assert fills[1] == [(0.55, 50), (0.56, 50)]


def test_walk_respects_max_cost_and_whole_contracts():
    a = mk("kalshi", yes_asks=[(0.40, 1000)], no_asks=[])
    b = mk("polymarket", yes_asks=[], no_asks=[(0.50, 1000)])
    qty, fills = walk([Side(a, "yes"), Side(b, "no")], 1e9, 100.0, 0.0)
    assert qty == 111  # 100 / 0.90 = 111.1 -> 111 whole contracts
    assert sum(q for _, q in fills[0]) == pytest.approx(111)


def test_cross_pair_fees_reduce_profit_and_kalshi_rounds_up():
    a = mk("kalshi", yes_asks=[(0.40, 100)], no_asks=[(0.62, 100)], rate=0.07, round_cents=True)
    b = mk("polymarket", yes_asks=[(0.39, 100)], no_asks=[(0.55, 100)], rate=0.05)
    opps = cross_pair(a, b, "same", "k", "test", settings())
    assert len(opps) == 1
    o = opps[0]
    # YES@kalshi 0.40 + NO@poly 0.55 = 0.95; fees 0.07*100*.4*.6=1.68 ; 0.05*100*.55*.45=1.2375
    assert o.qty == 100
    assert o.cost == pytest.approx(95 + 1.68 + 1.2375, abs=0.011)
    assert o.profit == pytest.approx(100 - o.cost)
    assert o.margin > 0.02


def test_cross_pair_complement_relation():
    # kalshi 'Team A wins' vs polymarket 'Team B wins' -> YES + YES
    a = mk("kalshi", yes_asks=[(0.45, 10)], no_asks=[(0.60, 10)])
    b = mk("polymarket", yes_asks=[(0.50, 10)], no_asks=[(0.60, 10)])
    opps = cross_pair(a, b, "complement", "k", "test", settings())
    assert len(opps) == 1 and opps[0].qty == 10 and opps[0].profit == pytest.approx(0.5)


def test_no_arb_when_prices_sum_above_one():
    a = mk("kalshi", yes_asks=[(0.50, 100)], no_asks=[(0.52, 100)])
    b = mk("polymarket", yes_asks=[(0.50, 100)], no_asks=[(0.52, 100)])
    assert cross_pair(a, b, "same", "k", "test", settings()) == []


def test_min_margin_filter():
    a = mk("kalshi", yes_asks=[(0.40, 100)], no_asks=[])
    b = mk("polymarket", yes_asks=[], no_asks=[(0.58, 100)])  # 2% gross
    assert cross_pair(a, b, "same", "k", "t", settings(min_margin=0.05)) == []
    assert len(cross_pair(a, b, "same", "k", "t", settings(min_margin=0.01))) == 1


def test_intra_set_three_way():
    h = mk("kalshi", yes_asks=[(0.40, 20)], no_asks=[], title="H")
    d = mk("kalshi", yes_asks=[(0.28, 5), (0.30, 50)], no_asks=[], title="D")
    a = mk("kalshi", yes_asks=[(0.30, 20)], no_asks=[], title="A")
    o = intra_set([h, d, a], "k", "t", settings())
    assert o is not None
    # 0.40+0.28+0.30=0.98 for 5, then 0.40+0.30+0.30=1.00 -> stop
    assert o.qty == 5
    assert o.profit == pytest.approx(0.10)


def test_intra_set_none_when_no_edge():
    h = mk("kalshi", yes_asks=[(0.55, 20)], no_asks=[], title="H")
    a = mk("kalshi", yes_asks=[(0.50, 20)], no_asks=[], title="A")
    assert intra_set([h, a], "k", "t", settings()) is None

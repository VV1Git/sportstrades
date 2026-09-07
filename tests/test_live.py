from datetime import datetime, timedelta, timezone

import pytest

from arb.live import TICKS_SCHEMA, _gross_gap, analyze, episodes
from arb.models import KALSHI, POLYMARKET, Outcome
from arb.store import Store


def o(venue, bid, ask):
    return Outcome(venue=venue, id=venue, market_id="m", event_id="e", title="t", event_title="E", yes_bid=bid, yes_ask=ask)


def test_gross_gap_directions():
    # Kalshi ask 0.40, Poly bid 0.45 -> buy YES@K 0.40 + NO@P 0.55 = 0.95 -> +0.05
    assert _gross_gap(o(KALSHI, 0.38, 0.40), o(POLYMARKET, 0.45, 0.46)) == pytest.approx(0.05)
    # symmetric other way
    assert _gross_gap(o(KALSHI, 0.45, 0.46), o(POLYMARKET, 0.38, 0.40)) == pytest.approx(0.05)
    # aligned books -> negative gap (spread)
    assert _gross_gap(o(KALSHI, 0.40, 0.41), o(POLYMARKET, 0.40, 0.41)) == pytest.approx(-0.01)


def _tick(store, session, ts, phase, mid_diff, gap, net, key="mlb:d:1-2", team="1"):
    store.db.execute(
        "INSERT INTO ticks(session, ts, match_key, league, team, team_name, phase, status, k_bid, k_ask, k_bid_sz, k_ask_sz,"
        " p_bid, p_ask, p_bid_sz, p_ask_sz, mid_diff, gross_gap, net_margin, net_qty, net_profit)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (session, ts.isoformat(timespec="seconds"), key, "mlb", team, "Team", phase, "Top 3rd", 0.5, 0.51, 100, 100,
         0.5, 0.51, 100, 100, mid_diff, gap, net, 10 if net else None, 0.5 if net else None))


def test_episodes_and_analyze(tmp_path):
    store = Store(tmp_path / "t.db")
    store.db.executescript(TICKS_SCHEMA)
    store.db.execute("INSERT INTO live_sessions(id, started, leagues, interval_s) VALUES (1, 'x', 'mlb', 5)")
    t0 = datetime(2026, 9, 7, 2, 0, tzinfo=timezone.utc)
    # pre-game: tight
    for i in range(10):
        _tick(store, 1, t0 + timedelta(seconds=5 * i), "pre", 0.002, -0.01, None)
    # live: two decoupling episodes, one of 3 ticks (net arb), one single tick (gross only)
    live = [(0.005, -0.01, None), (0.03, 0.02, 0.01), (0.04, 0.03, 0.02), (0.03, 0.02, 0.01), (0.0, -0.01, None),
            (0.02, 0.005, None), (0.0, -0.02, None)]
    for i, (md, gap, net) in enumerate(live):
        _tick(store, 1, t0 + timedelta(minutes=30, seconds=5 * i), "live", md, gap, net)
    store.db.commit()
    a = analyze(store, 1)
    assert a["n_ticks"] == 17
    assert a["phases"]["pre"]["pct_gross_gap_positive"] == 0
    assert a["phases"]["live"]["pct_gross_gap_positive"] > 50
    assert a["phases"]["live"]["max_net_margin"] == 0.02
    eps = a["episodes"]
    assert eps["net_live"]["count"] == 1 and eps["net_live"]["max_seconds"] == 10 + 5  # 3 ticks x 5s
    assert eps["gross_live"]["count"] == 2 and eps["gross_live"]["single_tick"] == 1
    assert eps["gross_pre"]["count"] == 0
    assert a["games"][0]["live_net_arbs"] == 3
    assert a["top_moments"][0]["mid_diff"] == 0.04

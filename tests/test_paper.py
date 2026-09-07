from datetime import datetime, timezone

from arb.config import Settings
from arb.models import Leg, Opportunity
from arb.paper import PaperTrader
from arb.store import Store


def opp(key="k", qty=100, pa=0.40, pb=0.55, kind="cross"):
    legs = [Leg("kalshi", "A", "A", "yes", "YES A", qty, pa, qty * pa, 0.07 * qty * pa * (1 - pa),
                {"fee_rate": 0.07, "fee_round": True}),
            Leg("polymarket", "B", "B", "no", "NO B", qty, pb, qty * pb, 0.05 * qty * pb * (1 - pb),
                {"fee_rate": 0.05, "fee_round": False})]
    cost = sum(l.cost + l.fee for l in legs)
    return Opportunity(kind, key, "test", legs, qty, cost, qty, datetime.now(timezone.utc))


def test_paper_trader_caps_and_settles(tmp_path):
    store = Store(tmp_path / "t.db")
    s = Settings(max_per_trade=50.0, max_per_key=80.0, min_margin=0.0, bankroll=1000)
    o = opp()
    sid = store.start_scan("sports", ["nfl"])
    ids = store.add_opportunities(sid, [o])
    taken = PaperTrader(store, s).consider([o], ids, sid)
    assert len(taken) == 1
    t = taken[0]
    assert t["cost"] <= 50.0 + 1e-6
    assert t["qty"] == 51  # 50 / (0.95 + 0.0168 + 0.012375 fees per contract) = 51.06 -> 51
    # second identical opportunity is limited by the per-key cap (80 - ~50 = ~30 room)
    ids2 = store.add_opportunities(sid, [o])
    taken2 = PaperTrader(store, s).consider([o], ids2, sid)
    assert len(taken2) == 1 and taken2[0]["cost"] <= 30.0 + 1e-6
    # third is blocked entirely
    ids3 = store.add_opportunities(sid, [o])
    assert PaperTrader(store, s).consider([o], ids3, sid) == []
    assert store.summary()["trades_open"] == 2
    store.settle_trade(t["trade_id"], payout=t["qty"], pnl=t["qty"] - t["cost"])
    sm = store.summary()
    assert sm["trades_settled"] == 1 and sm["realized_pnl"] > 0

from datetime import datetime, timezone

from arb.config import Settings
from arb.models import Leg, Opportunity
from arb.paper import PaperTrader
from arb.store import Store


def opp(key="k", qty=100, pa=0.40, pb=0.55, kind="cross", a="A", b="B"):
    legs = [Leg("kalshi", a, a, "yes", f"YES {a}", qty, pa, qty * pa, 0.07 * qty * pa * (1 - pa),
                {"fee_rate": 0.07, "fee_round": True}),
            Leg("polymarket", b, b, "no", f"NO {b}", qty, pb, qty * pb, 0.05 * qty * pb * (1 - pb),
                {"fee_rate": 0.05, "fee_round": False})]
    cost = sum(l.cost + l.fee for l in legs)
    return Opportunity(kind, key, "test", legs, qty, cost, qty, datetime.now(timezone.utc))


def _offer(store, trader, o):
    sid = store.start_scan("sports", ["nfl"])
    return trader.consider([o], store.add_opportunities(sid, [o]), sid)


def test_paper_trader_respects_the_per_trade_cap(tmp_path):
    store = Store(tmp_path / "t.db")
    s = Settings(max_per_trade=50.0, max_per_key=80.0, min_margin=0.0, bankroll=1000)
    taken = _offer(store, PaperTrader(store, s), opp())
    assert len(taken) == 1
    t = taken[0]
    assert t["cost"] <= 50.0 + 1e-6
    assert t["qty"] == 51  # 50 / (0.95 + 0.0168 + 0.012375 fees per contract) = 51.06 -> 51


def test_the_same_hedge_is_never_bought_twice(tmp_path):
    """A resting mispricing still showing on the next scan is the same depth, not new
    depth. Without this the ledger re-buys it every pass (it did: 24 times)."""
    store = Store(tmp_path / "t.db")
    s = Settings(max_per_trade=50.0, max_per_key=10_000.0, min_margin=0.0, bankroll=10_000)
    trader = PaperTrader(store, s)
    assert len(_offer(store, trader, opp())) == 1
    for _ in range(5):                       # same legs, same sides, offered again and again
        assert _offer(store, trader, opp()) == []
    assert store.summary()["trades_open"] == 1

    # once it settles, the position is no longer held and the market may be entered again
    open_id = store.open_trades()[0]["id"]
    store.settle_trade(open_id, payout=51, pnl=1.0)
    assert len(_offer(store, trader, opp())) == 1
    assert store.summary()["trades_open"] == 1


def test_a_different_hedge_in_the_same_market_still_hits_the_per_key_cap(tmp_path):
    store = Store(tmp_path / "t.db")
    s = Settings(max_per_trade=50.0, max_per_key=80.0, min_margin=0.0, bankroll=1000)
    trader = PaperTrader(store, s)
    first = _offer(store, trader, opp(a="A", b="B"))          # ~$50 of the $80 key budget
    assert len(first) == 1
    second = _offer(store, trader, opp(a="C", b="D"))          # same match key, different contracts
    assert len(second) == 1 and second[0]["cost"] <= 30.0 + 1e-6
    assert _offer(store, trader, opp(a="E", b="F")) == []      # key budget now exhausted
    assert store.summary()["trades_open"] == 2


def test_settlement_updates_the_ledger(tmp_path):
    store = Store(tmp_path / "t.db")
    s = Settings(max_per_trade=50.0, max_per_key=80.0, min_margin=0.0, bankroll=1000)
    t = _offer(store, PaperTrader(store, s), opp())[0]
    store.settle_trade(t["trade_id"], payout=t["qty"], pnl=t["qty"] - t["cost"])
    sm = store.summary()
    assert sm["trades_settled"] == 1 and sm["realized_pnl"] > 0

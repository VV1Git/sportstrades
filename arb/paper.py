"""Paper-trading engine: turns detected opportunities into simulated fills,
respecting per-trade and per-market exposure caps and available cash."""
from __future__ import annotations

import math

from .config import Settings
from .fees import fee_for
from .models import Leg, Opportunity
from .store import Store


def scale_legs(legs: list[Leg], factor: float) -> list[Leg]:
    out = []
    for l in legs:
        qty = l.qty * factor
        cost = qty * l.avg_price
        fee = fee_for(l.venue, qty, l.avg_price, float(l.meta.get("fee_rate", 0.0)), bool(l.meta.get("fee_round", False)))
        out.append(Leg(venue=l.venue, outcome_id=l.outcome_id, market_id=l.market_id, side=l.side, label=l.label,
                       qty=qty, avg_price=l.avg_price, cost=cost, fee=fee, meta=dict(l.meta)))
    return out


class PaperTrader:
    def __init__(self, store: Store, settings: Settings):
        self.store = store
        self.s = settings

    def cash(self) -> float:
        return self.s.bankroll - self.store.deployed() + self.store.realized()

    def consider(self, opps: list[Opportunity], opp_ids: list[int], scan_id: int) -> list[dict]:
        taken: list[dict] = []
        cash = self.cash()
        seen_keys: dict[str, float] = {}
        for opp, opp_id in sorted(zip(opps, opp_ids), key=lambda x: -x[0].margin):
            if opp.margin < self.s.min_margin or opp.qty < 1:
                continue
            exposure = self.store.open_exposure(opp.match_key) + seen_keys.get(opp.match_key, 0.0)
            room = min(self.s.max_per_trade, self.s.max_per_key - exposure, cash)
            if room < 1.0:
                continue
            factor = min(1.0, room / opp.cost)
            qty = math.floor(opp.qty * factor + 1e-9)
            if qty < 1:
                continue
            factor = qty / opp.qty
            legs = scale_legs(opp.legs, factor)
            fees = sum(l.fee for l in legs)
            cost = sum(l.cost for l in legs) + fees
            payout = qty
            if payout - cost <= 0:
                continue
            tid = self.store.add_trade(opp_id, scan_id, opp.kind, opp.match_key, opp.description, qty, cost, fees, payout, legs)
            self.store.mark_taken(opp_id)
            cash -= cost
            seen_keys[opp.match_key] = seen_keys.get(opp.match_key, 0.0) + cost
            taken.append({"trade_id": tid, "kind": opp.kind, "key": opp.match_key, "qty": qty, "cost": cost,
                          "fees": fees, "profit": payout - cost, "margin": (payout - cost) / cost,
                          "description": opp.description})
        return taken

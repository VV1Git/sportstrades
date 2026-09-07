"""Arbitrage detection with order-book walking and taker fees.

A complete hedge buys $1-payout contracts on every outcome of a partition of
the event space. If the sum of the prices paid (plus fees) is below $1 the
position is risk-free. Three flavours:

* cross-venue: YES on venue A + NO on venue B for the *same* outcome
  (or YES + YES for *complementary* outcomes);
* intra-venue: YES on every outcome of a mutually-exclusive set at one venue;
* vegas (see vegas.py): a sportsbook moneyline bet + NO on that team at a
  prediction market.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone

from .config import Settings
from .fees import fee_for, quadratic_fee
from .models import Leg, Level, Opportunity, Outcome


@dataclass(slots=True)
class Side:
    outcome: Outcome
    side: str  # 'yes' | 'no'

    @property
    def asks(self) -> list[Level]:
        return self.outcome.asks(self.side)

    @property
    def rate(self) -> float:
        return self.outcome.fee_rate

    def label(self) -> str:
        return f"{self.side.upper()} '{self.outcome.title}' @{self.outcome.venue}"


def _fills_to_leg(s: Side, fills: list[tuple[float, float]]) -> Leg:
    qty = sum(q for _, q in fills)
    cost = sum(p * q for p, q in fills)
    raw_fee = sum(quadratic_fee(s.rate, q, p) for p, q in fills)
    fee = math.ceil(raw_fee * 100 - 1e-9) / 100 if s.outcome.fee_round_cents else round(raw_fee, 5)
    return Leg(
        venue=s.outcome.venue, outcome_id=s.outcome.id, market_id=s.outcome.market_id, side=s.side,
        label=s.label(), qty=qty, avg_price=cost / qty if qty else 0.0, cost=cost, fee=fee,
        meta={"fee_rate": s.rate, "fee_round": s.outcome.fee_round_cents, "event_id": s.outcome.event_id,
              "title": s.outcome.title, "event_title": s.outcome.event_title, "url": s.outcome.url,
              **{k: s.outcome.meta.get(k) for k in ("gamma_id", "outcome_index", "no_token", "team_code")
                 if k in s.outcome.meta}},
    )


def walk(sides: list[Side], max_qty: float, max_cost: float, min_margin: float,
         fixed_cost: float = 0.0) -> tuple[float, list[list[tuple[float, float]]]]:
    """Walk several ask ladders together. Returns (qty, fills per side).

    `fixed_cost` is an extra per-contract cost that is not on a ladder (used
    for the sportsbook leg, whose price is fixed at 1/decimal)."""
    ladders = [s.asks for s in sides]
    if any(not l for l in ladders):
        return 0.0, [[] for _ in sides]
    idx = [0] * len(sides)
    taken = [0.0] * len(sides)
    fills: list[list[tuple[float, float]]] = [[] for _ in sides]
    qty = 0.0
    cost = 0.0
    while qty < max_qty and cost < max_cost:
        if any(idx[i] >= len(ladders[i]) for i in range(len(sides))):
            break
        prices = [ladders[i][idx[i]].price for i in range(len(sides))]
        marginal = fixed_cost + sum(p + sides[i].rate * p * (1 - p) for i, p in enumerate(prices))
        if marginal >= 1.0 - min_margin:
            break
        avail = [ladders[i][idx[i]].size - taken[i] for i in range(len(sides))]
        q = min(min(avail), max_qty - qty, (max_cost - cost) / marginal)
        if q <= 1e-9:
            break
        for i, p in enumerate(prices):
            fills[i].append((p, q))
            taken[i] += q
            if taken[i] >= ladders[i][idx[i]].size - 1e-9:
                idx[i] += 1
                taken[i] = 0.0
        qty += q
        cost += q * marginal
    # whole contracts only: trim the fractional remainder off the end of every ladder
    whole = math.floor(qty + 1e-9)
    if whole < qty:
        for f in fills:
            trim = qty - whole
            while trim > 1e-9 and f:
                p, q = f[-1]
                if q <= trim + 1e-9:
                    f.pop()
                    trim -= q
                else:
                    f[-1] = (p, q - trim)
                    trim = 0.0
    return float(whole), fills


def _merge(fills: list[tuple[float, float]]) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for p, q in fills:
        if out and abs(out[-1][0] - p) < 1e-12:
            out[-1] = (p, out[-1][1] + q)
        else:
            out.append((p, q))
    return out


def opportunity(kind: str, key: str, description: str, sides: list[Side], s: Settings,
                max_qty: float | None = None, extra_legs: list[Leg] | None = None,
                fixed_cost: float = 0.0) -> Opportunity | None:
    qty, fills = walk(sides, max_qty or 1e9, s.max_per_trade, s.min_margin, fixed_cost)
    if qty < 1:
        return None
    legs = [_fills_to_leg(sd, _merge(f)) for sd, f in zip(sides, fills)]
    if extra_legs:
        legs = extra_legs + legs
    cost = sum(l.cost + l.fee for l in legs)
    payout = qty
    if payout - cost <= 0:
        return None
    return Opportunity(kind=kind, match_key=key, description=description, legs=legs, qty=qty, cost=cost,
                       payout=payout, ts=datetime.now(timezone.utc))


def cross_pair(a: Outcome, b: Outcome, relation: str, key: str, label: str, s: Settings) -> list[Opportunity]:
    """Same outcome on two venues: YES@A + NO@B and NO@A + YES@B.
    Complementary outcomes: YES@A + YES@B and NO@A + NO@B."""
    if relation == "same":
        combos = [("yes", "no"), ("no", "yes")]
    else:
        combos = [("yes", "yes"), ("no", "no")]
    out = []
    for sa, sb in combos:
        sides = [Side(a, sa), Side(b, sb)]
        desc = f"{label}: {sides[0].label()} + {sides[1].label()}"
        opp = opportunity("cross", key, desc, sides, s)
        if opp:
            out.append(opp)
    return out


def intra_set(outcomes: list[Outcome], key: str, label: str, s: Settings) -> Opportunity | None:
    """Buy YES on every outcome of a mutually exclusive, exhaustive set."""
    if len(outcomes) < 2:
        return None
    sides = [Side(o, "yes") for o in outcomes]
    desc = f"{label}: buy YES on all of " + ", ".join(f"'{o.title}'" for o in outcomes) + f" @{outcomes[0].venue}"
    return opportunity("intra", key, desc, sides, s)


def top_of_book_gap(a: Outcome, b: Outcome, relation: str = "same") -> float | None:
    """Quick pre-filter: best-case margin using top-of-book only (no fees)."""
    if relation == "same":
        c1 = (a.yes_ask or 9) + (b.no_ask or 9)
        c2 = (a.no_ask or 9) + (b.yes_ask or 9)
    else:
        c1 = (a.yes_ask or 9) + (b.yes_ask or 9)
        c2 = (a.no_ask or 9) + (b.no_ask or 9)
    c = min(c1, c2)
    return None if c > 5 else 1.0 - c

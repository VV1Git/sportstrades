"""Settle open paper trades against public resolution data."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from .config import LEAGUES
from .espn import Espn
from .kalshi import Kalshi
from .models import Leg
from .polymarket import Polymarket
from .store import Store, legs_from_json


class Settler:
    def __init__(self, store: Store, kalshi: Kalshi, poly: Polymarket, espn: Espn):
        self.store, self.kalshi, self.poly, self.espn = store, kalshi, poly, espn
        self._espn_cache: dict[tuple[str, str], list] = {}

    # each returns (payout or None if unresolved, note)
    def _kalshi(self, leg: Leg) -> tuple[float | None, str]:
        m = self.kalshi.market(leg.outcome_id)
        result = (m.get("result") or "").lower()
        status = (m.get("status") or "").lower()
        yes_value: float | None = None
        if result == "yes":
            yes_value = 1.0
        elif result == "no":
            yes_value = 0.0
        elif status in ("finalized", "settled", "determined") and m.get("settlement_value_dollars") not in (None, ""):
            yes_value = float(m["settlement_value_dollars"])
        if yes_value is None:
            return None, status
        v = yes_value if leg.side == "yes" else 1.0 - yes_value
        return leg.qty * v, f"kalshi result={result or yes_value}"

    def _poly(self, leg: Leg) -> tuple[float | None, str]:
        gid = leg.meta.get("gamma_id")
        m = self.poly.market_by_id(gid) if gid else self.poly.market_by_condition(leg.market_id)
        if not m:
            return None, "market not found"
        prices = m.get("outcomePrices")
        try:
            prices = [float(x) for x in (json.loads(prices) if isinstance(prices, str) else prices)]
        except (TypeError, ValueError):
            return None, "bad prices"
        resolved = bool(m.get("closed")) and all(abs(p - round(p * 2) / 2) < 1e-9 for p in prices) and abs(sum(prices) - 1) < 1e-6
        statuses = m.get("umaResolutionStatuses") or []
        if isinstance(statuses, str):
            try:
                statuses = json.loads(statuses)
            except ValueError:
                statuses = [statuses]
        if not resolved and not any("resolved" in str(s).lower() for s in statuses):
            return None, "unresolved"
        idx = int(leg.meta.get("outcome_index", 0))
        yes_value = prices[idx] if idx < len(prices) else None
        if yes_value is None:
            return None, "no price"
        v = yes_value if leg.side == "yes" else 1.0 - yes_value
        return leg.qty * v, f"poly prices={prices}"

    def _sportsbook(self, leg: Leg) -> tuple[float | None, str]:
        league = LEAGUES.get(leg.meta.get("league", ""))
        start = leg.meta.get("start")
        if not league or not start:
            return None, "missing league/start"
        day = datetime.fromisoformat(start).astimezone(timezone.utc)
        games = []
        for d in (day, day.replace(hour=0) - timedelta_days(1), day + timedelta_days(1)):
            key = (league.key, d.strftime("%Y%m%d"))
            if key not in self._espn_cache:
                try:
                    self._espn_cache[key] = self.espn.scoreboard(league, key[1])
                except Exception:
                    self._espn_cache[key] = []
            games.extend(self._espn_cache[key])
        team = leg.meta.get("espn_team") or leg.meta.get("team")
        home, away = leg.meta.get("home"), leg.meta.get("away")
        g = next((x for x in games if x.id == leg.meta.get("espn_event_id")), None)
        if g is None:
            g = next((x for x in games if {str(x.home["id"]), str(x.away["id"])} == {home, away}), None)
        if g is None:
            return None, "game not found"
        if not g.completed:
            return None, f"game state={g.state}"
        if g.home_score is not None and g.home_score == g.away_score:
            return leg.cost, "push (tie) - stake refunded"
        if g.winner == team:
            return leg.qty, f"won ({g.away_score}-{g.home_score})"
        return 0.0, f"lost ({g.away_score}-{g.home_score})"

    def run(self, verbose: bool = True) -> dict:
        settled = 0
        still_open = 0
        pnl_total = 0.0
        details = []
        for t in self.store.open_trades():
            legs = legs_from_json(t["legs"])
            payouts = []
            notes = []
            for leg in legs:
                try:
                    if leg.venue == "kalshi":
                        p, n = self._kalshi(leg)
                    elif leg.venue == "polymarket":
                        p, n = self._poly(leg)
                    else:
                        p, n = self._sportsbook(leg)
                except Exception as e:  # network / parse problems leave the trade open
                    p, n = None, f"error: {e}"
                payouts.append(p)
                notes.append(n)
            if any(p is None for p in payouts):
                still_open += 1
                continue
            payout = sum(payouts)
            pnl = payout - float(t["cost"])
            self.store.settle_trade(int(t["id"]), payout, pnl, "settled", "; ".join(notes))
            settled += 1
            pnl_total += pnl
            details.append({"trade_id": t["id"], "description": t["description"], "cost": t["cost"], "payout": payout, "pnl": pnl})
        return {"settled": settled, "still_open": still_open, "pnl": pnl_total, "details": details}


def timedelta_days(n: int):
    from datetime import timedelta
    return timedelta(days=n)

"""Sportsbook vs prediction-market comparison.

Two products:
1. Exact hedges: back a team at the sportsbook, buy NO on the same team at
   Kalshi/Polymarket. Cost per $1 of payout = 1/decimal + no_price + fee.
2. Pricing discrepancies: de-vig the sportsbook line and compare the fair
   probability to the prediction-market bid/ask. Not risk-free, but a large
   gap is where an edge (or a stale market) lives.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone

from .arbitrage import Side, opportunity
from .config import LEAGUES, Settings
from .models import BookOdds, Game, Leg, Opportunity, Outcome
from .odds import devig_multiplicative


@dataclass(slots=True)
class Discrepancy:
    match_key: str
    league: str
    game: str
    team: str
    venue: str
    bookmaker: str
    american: int
    implied: float
    fair: float
    pm_bid: float | None
    pm_ask: float | None
    edge_buy: float | None  # fair - ask   (>0: PM is cheap vs Vegas -> buy YES)
    edge_sell: float | None  # bid - fair  (>0: PM is rich vs Vegas -> sell / buy NO)

    def as_dict(self) -> dict:
        return asdict(self)


def best_quotes(game: Game) -> dict[str, BookOdds]:
    best: dict[str, BookOdds] = {}
    for team, quotes in game.sportsbook.items():
        for q in quotes:
            if team not in best or q.decimal > best[team].decimal:
                best[team] = q
    return best


def fair_probs(game: Game) -> tuple[dict[str, float], dict[str, BookOdds]]:
    """Average de-vigged probability per team across bookmakers that quote
    both sides. Returns ({team: fair}, {team: representative quote})."""
    by_book: dict[str, dict[str, BookOdds]] = {}
    for team, quotes in game.sportsbook.items():
        for q in quotes:
            by_book.setdefault(q.bookmaker, {})[team] = q
    teams = [game.home, game.away]
    acc: dict[str, list[float]] = {t: [] for t in teams}
    rep: dict[str, BookOdds] = {}
    for book, qs in by_book.items():
        if not all(t in qs for t in teams):
            continue
        implied = [qs[t].implied for t in teams]
        if sum(implied) <= 1.0 and not LEAGUES[game.league].three_way:
            continue  # incomplete/invalid two-way line
        if LEAGUES[game.league].three_way:
            # without draw odds we can only de-vig the two sides we have; skip
            continue
        fair = devig_multiplicative(implied)
        for t, f in zip(teams, fair):
            acc[t].append(f)
            rep.setdefault(t, qs[t])
    return {t: sum(v) / len(v) for t, v in acc.items() if v}, rep


def _pm_outcomes(game: Game, team: str) -> list[Outcome]:
    out = []
    if team in game.kalshi:
        out.append(game.kalshi[team])
    if team in game.polymarket:
        out.append(game.polymarket[team])
    return out


def vegas_opportunities(game: Game, s: Settings) -> list[Opportunity]:
    out: list[Opportunity] = []
    quotes = best_quotes(game)
    label = f"{LEAGUES[game.league].name} {game.away_name} @ {game.home_name}"
    for team, q in quotes.items():
        stake_per_payout = 1.0 / q.decimal
        max_payout = s.book_max_stake * q.decimal
        tname = game.team_name(team)
        other = game.away if team == game.home else game.home
        hedges: list[tuple[Outcome, str]] = [(o, "no") for o in _pm_outcomes(game, team)]
        if not LEAGUES[game.league].three_way:
            # YES on the opponent is a distinct hedge on Kalshi (separate market/book) but on a
            # two-outcome Polymarket market it is literally the same token as NO on this team.
            same_market = {o.market_id for o, _ in hedges}
            hedges += [(o, "yes") for o in _pm_outcomes(game, other) if o.market_id not in same_market]
        for o, side in hedges:
            sides = [Side(o, side)]
            desc = (f"{label}: bet {tname} {q.american:+d} @{q.bookmaker} + "
                    f"{side.upper()} '{o.title}' @{o.venue}")
            opp = opportunity("vegas", game.key, desc, sides, s, max_qty=max_payout, fixed_cost=stake_per_payout)
            if not opp:
                continue
            qty = opp.qty
            book_leg = Leg(
                venue="sportsbook", outcome_id=f"{q.bookmaker}:{game.key}:{team}", market_id=game.key,
                side="bet", label=f"BET {tname} {q.american:+d} @{q.bookmaker}", qty=qty,
                avg_price=stake_per_payout, cost=qty * stake_per_payout, fee=0.0,
                meta={"american": q.american, "decimal": q.decimal, "team": team, "team_name": tname,
                      "league": game.league, "espn_event_id": game.espn_event_id,
                      "start": game.start.isoformat() if game.start else None, "bookmaker": q.bookmaker,
                      "home": game.home, "away": game.away, "fee_rate": 0.0, "fee_round": False},
            )
            opp.legs.insert(0, book_leg)
            opp.cost += book_leg.cost
            if opp.profit > 0:
                out.append(opp)
    return out


def discrepancies(game: Game) -> list[Discrepancy]:
    fair, rep = fair_probs(game)
    if not fair:
        return []
    out = []
    label = f"{game.away_name} @ {game.home_name}"
    for team, f in fair.items():
        q = rep[team]
        for o in _pm_outcomes(game, team):
            eb = None if o.yes_ask is None else f - o.yes_ask
            es = None if o.yes_bid is None else o.yes_bid - f
            out.append(Discrepancy(game.key, game.league, label, game.team_name(team), o.venue, q.bookmaker,
                                   q.american, q.implied, f, o.yes_bid, o.yes_ask, eb, es))
    return out

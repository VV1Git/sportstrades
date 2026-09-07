"""Match the same real-world outcome across venues.

Sports games are matched structurally (league + teams + date). Everything
else goes through a conservative fuzzy matcher whose output is meant to be
reviewed (`arb pairs`) and confirmed in data/pairs.yaml before being traded.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml
from rapidfuzz import fuzz, process

from .config import DATA_DIR, LEAGUES
from .espn import EspnGame
from .models import KALSHI, POLYMARKET, BookOdds, Game, Outcome
from .oddsapi import OddsApiGame
from .odds import american_to_decimal
from .teams import TeamRegistry, norm

DRAW_WORDS = {"draw", "tie", "tied", "tie game"}


@dataclass
class GameCandidate:
    venue: str
    league: str
    event_id: str
    date: datetime | None
    legs: dict[str, Outcome] = field(default_factory=dict)  # team id | 'draw' -> outcome
    unresolved: list[str] = field(default_factory=list)
    order: list[str] = field(default_factory=list)  # team ids in venue's listing order


def _is_draw(text: str, code: str = "") -> bool:
    t = norm(text)
    return (t in DRAW_WORDS or code.upper() in ("TIE", "DRAW") or t.startswith("draw") or t.startswith("tie")
            or re.search(r"\b(draw|tie)\b", t) is not None)


def kalshi_candidates(outcomes: list[Outcome], league: str, reg: TeamRegistry) -> list[GameCandidate]:
    by_event: dict[str, list[Outcome]] = {}
    for o in outcomes:
        if o.venue == KALSHI and o.league == league:
            by_event.setdefault(o.event_id, []).append(o)
    out = []
    for eid, legs in by_event.items():
        c = GameCandidate(KALSHI, league, eid, legs[0].game_time)
        for o in sorted(legs, key=lambda x: x.id):
            text = o.meta.get("yes_sub_title") or o.title
            code = o.meta.get("team_code") or ""
            if _is_draw(text, code):
                o.team = "draw"
                c.legs["draw"] = o
                continue
            tid = reg.resolve(text, code)
            if tid and tid not in c.legs:
                o.team = tid
                c.legs[tid] = o
                c.order.append(tid)
            else:
                c.unresolved.append(f"{text} ({code})")
        if len([k for k in c.legs if k != "draw"]) == 2:
            out.append(c)
    return out


def poly_candidates(outcomes: list[Outcome], league: str, reg: TeamRegistry) -> list[GameCandidate]:
    by_event: dict[str, list[Outcome]] = {}
    for o in outcomes:
        if o.venue == POLYMARKET and o.league == league and o.meta.get("sports_type") == "moneyline" \
                and o.game_time is not None and o.meta.get("line") in (None, "", 0):
            by_event.setdefault(o.event_id, []).append(o)
    out = []
    for eid, legs in by_event.items():
        c = GameCandidate(POLYMARKET, league, eid, legs[0].game_time)
        for o in sorted(legs, key=lambda x: (-x.volume, x.meta.get("outcome_index", 0))):
            text = o.meta.get("team_text") or o.title
            if _is_draw(text):
                o.team = "draw"
                c.legs.setdefault("draw", o)
                continue
            tid = reg.resolve(text)
            if tid and tid not in c.legs:
                o.team = tid
                c.legs[tid] = o
                c.order.append(tid)
            elif not tid:
                c.unresolved.append(text)
        if len([k for k in c.legs if k != "draw"]) == 2:
            # keep listing order (outcome_index) for home/away inference
            c.order.sort(key=lambda t: c.legs[t].meta.get("outcome_index", 0))
            out.append(c)
    return out


def _cluster_key(team_ids) -> frozenset:
    return frozenset(t for t in team_ids if t != "draw")


def local_day(dt: datetime | None) -> str | None:
    """Calendar date of a game as the venues label it. Kalshi tickers carry the
    US-local date; exact UTC starts are shifted back 5h so a 7:10pm PT start
    (02:10Z next day) lands on the same date Kalshi uses."""
    if dt is None:
        return None
    return (dt.astimezone(timezone.utc) - timedelta(hours=5)).strftime("%Y-%m-%d")


def build_games(league: str, kalshi_c: list[GameCandidate], poly_c: list[GameCandidate],
                espn_games: list[EspnGame], oddsapi_games: list[OddsApiGame], reg: TeamRegistry,
                notes: list[str] | None = None) -> list[Game]:
    """Group candidates by (team pair, local game date). A pair that has two
    candidates from the same venue on the same date (double-header) is skipped
    as ambiguous rather than guessed."""
    items: list[tuple[str, object, datetime | None, frozenset, str | None]] = []
    for c in kalshi_c:
        # Kalshi's ticker date is already the local date (stored as noon UTC)
        day = c.date.strftime("%Y-%m-%d") if c.date else None
        items.append(("kalshi", c, c.date, _cluster_key(c.legs), day))
    for c in poly_c:
        items.append(("poly", c, c.date, _cluster_key(c.legs), local_day(c.date)))
    for g in espn_games:
        h, a = reg.espn_team_id(g.home), reg.espn_team_id(g.away)
        if h and a and h != a:
            items.append(("espn", g, g.start, frozenset({h, a}), local_day(g.start)))
    for g in oddsapi_games:
        h, a = reg.resolve(g.home_name), reg.resolve(g.away_name)
        if h and a:
            items.append(("oddsapi", g, g.start, frozenset({h, a}), local_day(g.start)))

    buckets: dict[tuple[frozenset, str | None], list] = {}
    for it in items:
        if len(it[3]) == 2:
            buckets.setdefault((it[3], it[4]), []).append(it)

    games: list[Game] = []
    for (pair, day), cl in buckets.items():
        kinds = [x[0] for x in cl]
        kset = set(kinds)
        if not ({"kalshi", "poly"} <= kset or (kset & {"espn", "oddsapi"}) and (kset & {"kalshi", "poly"})):
            continue
        if kinds.count("kalshi") > 1 or kinds.count("poly") > 1:
            if notes is not None:
                notes.append(f"{league} {day} {sorted(pair)}: several games for the same teams on one day; skipped")
            continue
        teams = sorted(pair)
        home = away = None
        start = None
        espn_id = None
        espn_home = espn_away = None
        for kind, obj, dt, _, _ in cl:
            if kind == "espn":
                home, away = reg.espn_team_id(obj.home), reg.espn_team_id(obj.away)
                start, espn_id = obj.start, obj.id
                espn_home, espn_away = str(obj.home["id"]), str(obj.away["id"])
                break
        if home is None:
            for kind, obj, dt, _, _ in cl:
                if kind == "poly" and len(obj.order) == 2:
                    # Polymarket lists US leagues away-first, soccer home-first
                    if LEAGUES[league].three_way:
                        home, away = obj.order[0], obj.order[1]
                    else:
                        away, home = obj.order[0], obj.order[1]
                    start = dt
                    break
        if start is None:
            start = next((dt for k, _, dt, _, _ in cl if k in ("poly", "espn", "oddsapi") and dt is not None), None)
        if home is None:
            for kind, obj, dt, _, _ in cl:
                if kind == "kalshi" and len(obj.order) == 2:
                    away, home = obj.order[0], obj.order[1]
                    break
        if home is None:
            away, home = teams[0], teams[1]
        exact_start = start is not None
        if start is None:
            start = next((dt for _, _, dt, _, _ in cl if dt is not None), None)
        g = Game(key=f"{league}:{day or 'nodate'}:{'-'.join(teams)}", league=league, start=start, home=home, away=away,
                 home_name=reg.short(home), away_name=reg.short(away), espn_event_id=espn_id,
                 espn_home=espn_home, espn_away=espn_away)
        g.exact_start = exact_start
        for kind, obj, dt, _, _ in cl:
            if kind == "kalshi":
                g.kalshi.update(obj.legs)
            elif kind == "poly":
                g.polymarket.update(obj.legs)
            elif kind == "espn":
                g.espn_state = obj.state
                espn_to_reg = {str(obj.home["id"]): home, str(obj.away["id"]): away}
                for q in obj.odds:
                    rid = espn_to_reg.get(q.team, q.team)
                    g.sportsbook.setdefault(rid, []).append(
                        BookOdds(q.bookmaker, rid, q.american, q.decimal, espn_team=q.team))
            elif kind == "oddsapi":
                for book, line in obj.lines.items():
                    for name, american in line.items():
                        tid = reg.resolve(name)
                        if tid in pair:
                            g.sportsbook.setdefault(tid, []).append(
                                BookOdds(book, tid, american, american_to_decimal(american)))
        games.append(g)
    return games


# ---------------------------------------------------------------------------
# General (non-game) fuzzy matching
# ---------------------------------------------------------------------------
_NUM = re.compile(r"\d+(?:\.\d+)?")
_STOP = re.compile(r"\b(will|the|be|a|an|of|in|on|at|to|by|for|before|after|than|or|and|is|does|do|market|resolves?)\b")


@dataclass
class PairCandidate:
    kalshi: Outcome
    polymarket: Outcome
    score: float
    relation: str = "same"  # or 'complement'
    confirmed: bool = False


def _ktext(o: Outcome) -> str:
    sub = o.meta.get("yes_sub_title") or ""
    t = o.event_title
    if sub and norm(sub) not in norm(t):
        t = f"{t} {sub}"
    return t


def _ptext(o: Outcome) -> str:
    q = o.meta.get("question") or o.title
    g = o.meta.get("group_title") or ""
    if g and norm(g) not in norm(q):
        q = f"{o.event_title} {g}" if o.event_title else f"{q} {g}"
    return q


def _clean(s: str) -> str:
    s = norm(s).replace("percent", " pct ").replace("%", " pct ")
    s = _STOP.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()


def numeric_signature(s: str) -> frozenset:
    return frozenset(x.rstrip("0").rstrip(".") if "." in x else x for x in _NUM.findall(s.replace(",", "")))


def load_confirmed_pairs(path: Path | None = None) -> list[dict]:
    path = path or DATA_DIR / "pairs.yaml"
    if not path.exists():
        return []
    d = yaml.safe_load(path.read_text()) or {}
    return d.get("pairs") or []


def _tokens(s: str) -> set[str]:
    return {t for t in s.split() if len(t) >= 3 and not t.isdigit()}


def general_candidates(kalshi_outs: list[Outcome], poly_outs: list[Outcome], min_score: float = 85.0,
                       window_days: float = 45.0, limit_per: int = 3, min_shared_tokens: int = 2,
                       max_block: int = 400) -> list[PairCandidate]:
    """Fuzzy-match non-sports Kalshi markets to Polymarket YES markets.

    Blocking: an inverted index on informative tokens limits each Kalshi text to
    Polymarket texts sharing >= `min_shared_tokens` tokens, so the quadratic
    fuzzy pass only touches a few hundred candidates per market."""
    k = [o for o in kalshi_outs if o.league is None and o.category != "Sports" and o.yes_ask is not None]
    p = [o for o in poly_outs if o.league is None and "sports" not in (o.category or "") and
         o.meta.get("outcome_name") == "Yes" and o.yes_ask is not None]
    if not k or not p:
        return []
    ptexts = [_clean(_ptext(o)) for o in p]
    ptoks = [_tokens(t) for t in ptexts]
    index: dict[str, list[int]] = {}
    for i, toks in enumerate(ptoks):
        for t in toks:
            index.setdefault(t, []).append(i)
    # very common tokens carry no information for blocking
    common = {t for t, ids in index.items() if len(ids) > max(50, len(p) // 20)}
    out: list[PairCandidate] = []
    for ko in k:
        kt = _clean(_ktext(ko))
        if len(kt) < 8:
            continue
        counts: dict[int, int] = {}
        for t in _tokens(kt) - common:
            for i in index.get(t, ()):
                counts[i] = counts.get(i, 0) + 1
        block = [i for i, c in counts.items() if c >= min_shared_tokens]
        if not block:
            continue
        if len(block) > max_block:
            block = sorted(block, key=lambda i: -counts[i])[:max_block]
        ksig = numeric_signature(kt)
        for txt, set_score, j in process.extract(kt, [ptexts[i] for i in block], scorer=fuzz.token_set_ratio,
                                                 limit=limit_per * 2, score_cutoff=min_score):
            po = p[block[j]]
            if ko.close_time and po.close_time and abs((ko.close_time - po.close_time).total_seconds()) > window_days * 86400:
                continue
            if ksig != numeric_signature(txt):
                continue
            # token_set_ratio alone scores "impeached and removed" == "impeached before term ends" at 100;
            # average it with token_sort_ratio, which penalises the words that differ.
            score = 0.5 * set_score + 0.5 * fuzz.token_sort_ratio(kt, txt)
            if score >= min_score:
                out.append(PairCandidate(ko, po, float(score)))
    out.sort(key=lambda c: -c.score)
    return out


def confirmed_pairs(kalshi_outs: list[Outcome], poly_outs: list[Outcome], pairs: list[dict]) -> list[PairCandidate]:
    kidx = {o.id: o for o in kalshi_outs}
    pidx: dict[str, Outcome] = {}
    for o in poly_outs:
        pidx[o.id] = o
        if o.meta.get("outcome_name") == "Yes":
            pidx.setdefault(o.market_id, o)
    out = []
    for pr in pairs:
        ko = kidx.get(str(pr.get("kalshi", "")))
        po = pidx.get(str(pr.get("polymarket", "")))
        if ko and po:
            out.append(PairCandidate(ko, po, 100.0, relation=pr.get("relation", "same"), confirmed=True))
    return out

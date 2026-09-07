"""ESPN public scoreboard: schedules, DraftKings moneylines, final scores.
No API key required. Used both as the keyless 'Vegas' source and to settle
paper sportsbook legs."""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from .config import CACHE_DIR, ESPN_BASE, League, Settings
from .http import PLAIN_UA, Http
from .models import BookOdds
from .odds import american_to_decimal

_AMERICAN = re.compile(r"^([+-]?\d+)$")


def parse_american(s) -> int | None:
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return int(s) if s != 0 else None
    t = str(s).strip().upper().replace("−", "-")
    if t in ("EVEN", "EV", "PK"):
        return 100
    m = _AMERICAN.match(t)
    return int(m.group(1)) if m else None


@dataclass(slots=True)
class EspnGame:
    id: str
    league: str
    name: str
    start: datetime
    home: dict
    away: dict
    state: str  # pre | in | post
    completed: bool
    home_score: int | None
    away_score: int | None
    winner: str | None  # team id
    odds: list[BookOdds] = field(default_factory=list)
    spread_home: float | None = None
    total: float | None = None
    provider: str = ""
    detail: str = ""  # e.g. "Top 7th", "Final", "9/7 - 7:10 PM EDT"


class Espn:
    def __init__(self, settings: Settings):
        self.http = Http(ESPN_BASE, rate=settings.espn_rate, user_agent=PLAIN_UA)

    def teams(self, league: League, ttl_hours: float = 24) -> list[dict]:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        f = CACHE_DIR / f"teams_{league.key}.json"
        if f.exists() and time.time() - f.stat().st_mtime < ttl_hours * 3600:
            return json.loads(f.read_text())
        d = self.http.get(f"{league.espn}/teams", {"limit": 1000})
        teams = []
        for grp in d.get("sports", []):
            for lg in grp.get("leagues", []):
                for t in lg.get("teams", []):
                    tm = t.get("team", {})
                    teams.append({k: tm.get(k) for k in (
                        "id", "abbreviation", "displayName", "shortDisplayName", "name", "location", "nickname")})
        f.write_text(json.dumps(teams))
        return teams

    def scoreboard(self, league: League, date: str | None = None) -> list[EspnGame]:
        params: dict = {"limit": 300}
        if date:
            params["dates"] = date
        if league.espn_extra:
            params.update(league.espn_extra)
        d = self.http.get(f"{league.espn}/scoreboard", params)
        games: list[EspnGame] = []
        for e in d.get("events", []):
            try:
                games.append(self._parse_event(e, league))
            except (KeyError, IndexError, TypeError, ValueError):
                continue
        return games

    def scoreboard_range(self, league: League, days_ahead: int = 2, days_back: int = 0) -> list[EspnGame]:
        seen: dict[str, EspnGame] = {}
        for g in self.scoreboard(league):  # default view (current week/day)
            seen[g.id] = g
        today = datetime.now(timezone.utc).date()
        for delta in range(-days_back, days_ahead + 1):
            day = (today + timedelta(days=delta)).strftime("%Y%m%d")
            try:
                for g in self.scoreboard(league, day):
                    seen[g.id] = g
            except Exception:
                continue
        return list(seen.values())

    def _parse_event(self, e: dict, league: League) -> EspnGame:
        c = e["competitions"][0]
        comps = c["competitors"]
        home = next(x for x in comps if x.get("homeAway") == "home")
        away = next(x for x in comps if x.get("homeAway") == "away")
        st = c.get("status", {}).get("type", {})
        completed = bool(st.get("completed"))

        def team(x):
            t = x.get("team", {})
            return {k: t.get(k) for k in ("id", "abbreviation", "displayName", "shortDisplayName", "name", "location")}

        def score(x):
            try:
                return int(float(x.get("score")))
            except (TypeError, ValueError):
                return None

        winner = None
        if completed:
            if home.get("winner"):
                winner = home["team"]["id"]
            elif away.get("winner"):
                winner = away["team"]["id"]
        odds: list[BookOdds] = []
        spread_home = total = None
        provider = ""
        for o in c.get("odds") or []:
            if not isinstance(o, dict):
                continue
            provider = (o.get("provider") or {}).get("name") or provider
            ml = o.get("moneyline") or {}
            h_ml = parse_american(((ml.get("home") or {}).get("close") or {}).get("odds"))
            a_ml = parse_american(((ml.get("away") or {}).get("close") or {}).get("odds"))
            if h_ml is None:
                h_ml = parse_american((o.get("homeTeamOdds") or {}).get("moneyLine"))
            if a_ml is None:
                a_ml = parse_american((o.get("awayTeamOdds") or {}).get("moneyLine"))
            if h_ml is not None:
                odds.append(BookOdds(provider or "espn", home["team"]["id"], h_ml, american_to_decimal(h_ml)))
            if a_ml is not None:
                odds.append(BookOdds(provider or "espn", away["team"]["id"], a_ml, american_to_decimal(a_ml)))
            try:
                spread_home = float(o.get("spread")) if o.get("spread") is not None else spread_home
            except (TypeError, ValueError):
                pass
            try:
                total = float(o.get("overUnder")) if o.get("overUnder") is not None else total
            except (TypeError, ValueError):
                pass
        start = datetime.fromisoformat(e["date"].replace("Z", "+00:00"))
        return EspnGame(
            id=str(e["id"]), league=league.key, name=e.get("name", ""), start=start,
            home=team(home), away=team(away), state=st.get("state", ""), completed=completed,
            home_score=score(home), away_score=score(away), winner=winner, odds=odds,
            spread_home=spread_home, total=total, provider=provider,
            detail=st.get("shortDetail") or st.get("detail") or "",
        )

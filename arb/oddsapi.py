"""Optional multi-bookmaker odds via The Odds API (needs ODDS_API_KEY)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .config import ODDS_API_BASE, League, Settings
from .http import Http
from .odds import american_to_decimal


@dataclass(slots=True)
class OddsApiGame:
    id: str
    league: str
    start: datetime
    home_name: str
    away_name: str
    # bookmaker -> {team_name: american}
    lines: dict[str, dict[str, int]] = field(default_factory=dict)


class OddsApi:
    def __init__(self, settings: Settings):
        self.key = settings.odds_api_key
        self.http = Http(ODDS_API_BASE, rate=2.0)
        self.remaining: str | None = None

    @property
    def enabled(self) -> bool:
        return bool(self.key)

    def games(self, league: League) -> list[OddsApiGame]:
        if not self.enabled or not league.odds_api:
            return []
        d = self.http.get(f"sports/{league.odds_api}/odds", {
            "apiKey": self.key, "regions": "us,us2", "markets": "h2h", "oddsFormat": "american"})
        out = []
        for g in d or []:
            og = OddsApiGame(id=g["id"], league=league.key,
                             start=datetime.fromisoformat(g["commence_time"].replace("Z", "+00:00")),
                             home_name=g["home_team"], away_name=g["away_team"])
            for b in g.get("bookmakers", []):
                for mk in b.get("markets", []):
                    if mk.get("key") != "h2h":
                        continue
                    og.lines[b["key"]] = {o["name"]: int(o["price"]) for o in mk.get("outcomes", []) if o.get("price")}
            out.append(og)
        return out


def decimal(american: int) -> float:
    return american_to_decimal(american)

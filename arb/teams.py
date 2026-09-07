"""Team-name resolution. ESPN's team list is the canonical registry for a
league; Kalshi ('New England', 'NYG', 'Los Angeles R') and Polymarket
('Patriots') strings are resolved onto ESPN team ids."""
from __future__ import annotations

import re
import unicodedata

from rapidfuzz import fuzz, process

# Kalshi / Polymarket abbreviation quirks -> ESPN abbreviation
ABBR_ALIASES = {
    "nfl": {"JAC": "JAX", "WAS": "WSH", "LVR": "LV", "OAK": "LV", "ARZ": "ARI", "BLT": "BAL", "CLV": "CLE",
            "HST": "HOU", "SD": "LAC", "STL": "LAR", "LA": "LAR", "GNB": "GB", "KAN": "KC", "NWE": "NE",
            "NOR": "NO", "SFO": "SF", "TAM": "TB"},
    "nba": {"PHO": "PHX", "GS": "GSW", "SA": "SAS", "NY": "NYK", "NO": "NOP", "UTAH": "UTA", "WSH": "WAS",
            "BRK": "BKN"},
    "mlb": {"WAS": "WSH", "KAN": "KC", "CWS": "CHW", "ARZ": "ARI", "AZ": "ARI", "ANA": "LAA"},
    "nhl": {"VEG": "VGK", "MON": "MTL", "WAS": "WSH"},
}

_DROP = re.compile(r"[^a-z0-9 ]+")
# tokens that are club-name furniture and must never carry a match on their own
_GENERIC = {"fc", "cf", "sc", "afc", "ac", "as", "us", "ss", "ssc", "sv", "fk", "gf", "bk", "if", "cd", "ud", "rc",
            "real", "sporting", "athletic", "atletico", "club", "united", "city", "town", "calcio", "de", "la", "le",
            "st", "san", "new", "york", "los", "angeles", "state", "university", "college"}
_SPACES = re.compile(r"\s+")
_SUFFIX = re.compile(r"\b(wins?|to win|fc|cf|sc|afc|club|the)\b")


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    s = s.lower().replace("&", " and ").replace("st.", "st").replace(".", "")
    s = _DROP.sub(" ", s)
    s = _SUFFIX.sub(" ", s)
    return _SPACES.sub(" ", s).strip()


class TeamRegistry:
    def __init__(self, league_key: str, teams: list[dict]):
        self.league = league_key
        self.teams = {str(t["id"]): t for t in teams if t.get("id")}
        self.by_abbr: dict[str, str] = {}
        self.by_full: dict[str, str] = {}
        self.by_loc: dict[str, list[str]] = {}
        self.by_nick: dict[str, list[str]] = {}
        self.choices: dict[str, str] = {}
        for tid, t in self.teams.items():
            if t.get("abbreviation"):
                self.by_abbr[t["abbreviation"].upper()] = tid
            for k in ("displayName", "shortDisplayName", "name", "nickname", "location"):
                if t.get(k):
                    self.choices.setdefault(norm(t[k]), tid)
            if t.get("displayName"):
                self.by_full[norm(t["displayName"])] = tid
            if t.get("location"):
                self.by_loc.setdefault(norm(t["location"]), []).append(tid)
            for k in ("name", "nickname", "shortDisplayName"):
                if t.get(k):
                    lst = self.by_nick.setdefault(norm(t[k]), [])
                    if tid not in lst:
                        lst.append(tid)
        self._choice_keys = list(self.choices.keys())

    def name(self, tid: str) -> str:
        t = self.teams.get(tid, {})
        return t.get("displayName") or t.get("name") or tid

    def short(self, tid: str) -> str:
        t = self.teams.get(tid, {})
        return t.get("shortDisplayName") or t.get("abbreviation") or tid

    def resolve_abbr(self, abbr: str | None) -> str | None:
        if not abbr:
            return None
        a = abbr.upper()
        a = ABBR_ALIASES.get(self.league, {}).get(a, a)
        return self.by_abbr.get(a)

    def _exact(self, t: str) -> str | None:
        if t in self.by_full:
            return self.by_full[t]
        if t in self.by_loc and len(self.by_loc[t]) == 1:
            return self.by_loc[t][0]
        if t in self.by_nick and len(self.by_nick[t]) == 1:
            return self.by_nick[t][0]
        return None

    def resolve(self, text: str | None, abbr: str | None = None, min_score: float = 88.0) -> str | None:
        t = norm(text or "")
        if t:
            tid = self._exact(t)
            if tid:
                return tid
        tid = self.resolve_abbr(abbr)
        if tid:
            return tid
        if not t:
            return None
        if t in self.by_loc and len(self.by_loc[t]) > 1:
            return None  # bare shared city name ("New York") is ambiguous
        # "new york g" -> shared location + nickname initial
        parts = t.split()
        if len(parts) >= 2 and len(parts[-1]) == 1:
            loc = " ".join(parts[:-1])
            for cand in self.by_loc.get(loc, []):
                nick = norm(self.teams[cand].get("name") or self.teams[cand].get("nickname") or "")
                if nick.startswith(parts[-1]):
                    return cand
        for nick, tids in self.by_nick.items():
            if len(tids) == 1 and nick and (t.endswith(" " + nick) or t.startswith(nick + " ")):
                return tids[0]
        # whole-word containment either way: "cagliari calcio" <-> "cagliari", "inter milan" <-> "inter"
        toks = set(parts)
        hits: set[str] = set()
        for key, tid in self.choices.items():
            ktoks = set(key.split())
            if not ktoks or ktoks & _GENERIC:
                continue
            if ktoks <= toks or (toks <= ktoks and len(toks) >= 1 and len(t) >= 5):
                hits.add(tid)
        if len(hits) == 1:
            return hits.pop()
        best = process.extractOne(t, self._choice_keys, scorer=fuzz.ratio, score_cutoff=min_score)
        if best:
            return self.choices[best[0]]
        return None

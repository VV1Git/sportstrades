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

    def espn_team_id(self, team: dict) -> str | None:
        return str(team["id"]) if team.get("id") is not None else None

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


# tokens that are club-name furniture for *clustering* venue names (smaller than _GENERIC on purpose:
# 'real', 'sporting', 'united' are furniture for containment checks but distinguish clubs here)
_FURNITURE = {"fc", "cf", "sc", "afc", "cfc", "ac", "as", "us", "ss", "ssc", "sv", "sl", "fk", "sk", "gf", "bk", "if",
              "cd", "ud", "rcd", "rc", "ca", "kv", "vv", "tsg", "vfb", "vfl", "fsv", "bsc", "sg", "psv", "nk", "hk",
              "club", "calcio", "de", "la", "le", "del", "do", "da", "esports", "esport", "gaming", "team", "hc"}


class DynamicRegistry:
    """Registry built from the participant names seen on the venues themselves,
    for leagues where ESPN has no team list (tennis players, esports rosters,
    KBO/NPB clubs). Names are clustered by similarity; each cluster is a
    canonical id. Same interface as TeamRegistry (resolve / name / short).

    Two names are the same participant when their informative tokens (club
    furniture and bare numbers removed) are identical, or when the surname /
    last word matches and the leading initials agree and no other cluster
    competes for that surname."""

    def __init__(self, league_key: str, names: list[str]):
        self.league = league_key
        self.clusters: list[list[str]] = []
        self.display: dict[str, str] = {}
        self._norm_to_id: dict[str, str] = {}
        for raw in sorted({n.strip() for n in names if n and n.strip()}, key=lambda x: -len(x)):
            n = norm(raw)
            if not n or n in self._norm_to_id:
                continue
            tid = self._find(n)
            if tid is None:
                tid = str(len(self.clusters))
                self.clusters.append([])
                self.display[tid] = raw
            self.clusters[int(tid)].append(n)
            self._norm_to_id[n] = tid
        self.teams = {tid: {"id": tid, "displayName": d} for tid, d in self.display.items()}

    @staticmethod
    def _informative(n: str) -> list[str]:
        """Drop bare numbers and *trailing* furniture ('Venezia FC', 'Cagliari Calcio',
        'Bounty Hunters Esports'). Leading furniture ('AC Milan' vs 'Inter Milan',
        'FC Porto') is kept: it is often the only thing telling two clubs apart."""
        toks = [t for t in n.split() if not t.isdigit() and len(t) > 1]
        while len(toks) > 1 and toks[-1] in _FURNITURE:
            toks.pop()
        return toks

    @staticmethod
    def _same_person(a: list[str], b: list[str]) -> bool:
        """'djokovic' vs 'novak djokovic', 'j sinner' vs 'jannik sinner', 'porto' vs 'fc porto'."""
        if not a or not b or a[-1] != b[-1] or len(a[-1]) < 4:
            return False
        fa, fb = a[:-1], b[:-1]
        if not fa or not fb:
            return True
        return fa[0][0] == fb[0][0] and (len(fa[0]) == 1 or len(fb[0]) == 1 or fa[0] == fb[0])

    def _find(self, n: str) -> str | None:
        ni = self._informative(n)
        if not ni:
            return None
        exact: list[str] = []
        surname: list[str] = []
        for tid, members in enumerate(self.clusters):
            for m in members:
                mi = self._informative(m)
                if mi == ni or (set(mi) == set(ni) and mi):
                    exact.append(str(tid))
                    break
                if self._same_person(ni, mi):
                    surname.append(str(tid))
                    break
        if exact:
            return exact[0]
        if len(set(surname)) == 1:
            return surname[0]
        return None

    def resolve(self, text: str | None, abbr: str | None = None, min_score: float = 86.0) -> str | None:
        n = norm(text or "")
        if not n:
            return None
        if n in self._norm_to_id:
            return self._norm_to_id[n]
        return self._find(n)

    def espn_team_id(self, team: dict) -> str | None:
        for k in ("displayName", "shortDisplayName", "location", "name"):
            tid = self.resolve(team.get(k)) if team.get(k) else None
            if tid:
                return tid
        return None

    def name(self, tid: str) -> str:
        return self.display.get(tid, tid)

    def short(self, tid: str) -> str:
        return self.display.get(tid, tid)

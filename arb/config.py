"""Settings and the league registry that ties the three data sources together."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
CACHE_DIR = DATA_DIR / "cache"
load_dotenv(ROOT / ".env")

KALSHI_BASE = "https://api.elections.kalshi.com/trade-api/v2"
GAMMA_BASE = "https://gamma-api.polymarket.com"
CLOB_BASE = "https://clob.polymarket.com"
ESPN_BASE = "https://site.api.espn.com/apis/site/v2/sports"
ODDS_API_BASE = "https://api.the-odds-api.com/v4"


@dataclass(frozen=True)
class League:
    key: str
    name: str
    kalshi_series: str  # Kalshi game-winner series ticker
    poly_sport: str  # Polymarket /sports key
    poly_tag: int  # Polymarket primary tag id
    espn: str | None  # ESPN path: <sport>/<league>; None -> no team registry / no sportsbook line
    odds_api: str | None = None  # The Odds API sport key
    three_way: bool = False  # soccer: draw is a real outcome
    espn_extra: dict | None = None  # extra scoreboard params (e.g. groups=80 for FBS)
    dynamic_registry: bool = False  # match venues by clustering their own names; ESPN (if any) only supplies lines/status


LEAGUES: dict[str, League] = {
    "nfl": League("nfl", "NFL", "KXNFLGAME", "nfl", 450, "football/nfl", "americanfootball_nfl"),
    "ncaaf": League("ncaaf", "College Football", "KXNCAAFGAME", "cfb", 100351, "football/college-football",
                    "americanfootball_ncaaf", espn_extra={"groups": "80"}),
    "nba": League("nba", "NBA", "KXNBAGAME", "nba", 745, "basketball/nba", "basketball_nba"),
    "wnba": League("wnba", "WNBA", "KXWNBAGAME", "wnba", 100254, "basketball/wnba", "basketball_wnba"),
    "ncaab": League("ncaab", "College Basketball", "KXNCAAMBGAME", "cbb", 101178,
                    "basketball/mens-college-basketball", "basketball_ncaab", espn_extra={"groups": "50"}),
    "mlb": League("mlb", "MLB", "KXMLBGAME", "mlb", 100381, "baseball/mlb", "baseball_mlb"),
    "nhl": League("nhl", "NHL", "KXNHLGAME", "nhl", 899, "hockey/nhl", "icehockey_nhl"),
    "epl": League("epl", "Premier League", "KXEPLGAME", "epl", 306, "soccer/eng.1", "soccer_epl", True),
    "laliga": League("laliga", "La Liga", "KXLALIGAGAME", "lal", 780, "soccer/esp.1", "soccer_spain_la_liga", True),
    "bundesliga": League("bundesliga", "Bundesliga", "KXBUNDESLIGAGAME", "bun", 1494, "soccer/ger.1",
                         "soccer_germany_bundesliga", True),
    "seriea": League("seriea", "Serie A", "KXSERIEAGAME", "sea", 100618, "soccer/ita.1", "soccer_italy_serie_a", True),
    "ligue1": League("ligue1", "Ligue 1", "KXLIGUE1GAME", "fl1", 102070, "soccer/fra.1", "soccer_france_ligue_one", True),
    "mls": League("mls", "MLS", "KXMLSGAME", "mls", 100100, "soccer/usa.1", "soccer_usa_mls", True),
    "ucl": League("ucl", "Champions League", "KXUCLGAME", "ucl", 1234, "soccer/uefa.champions",
                  "soccer_uefa_champs_league", True),
}

# Niche leagues both venues list game markets for. No ESPN team registry: participants are
# clustered from the venues' own names (see teams.DynamicRegistry), and no sportsbook line.
NICHE: dict[str, League] = {
    "itf_m": League("itf_m", "ITF Men's tennis", "KXITFMATCH", "itf", 104923, None),
    "itf_w": League("itf_w", "ITF Women's tennis", "KXITFWMATCH", "itf", 104923, None),
    "atp": League("atp", "ATP tennis", "KXATPMATCH", "atp", 101232, None, "tennis_atp"),
    "wta": League("wta", "WTA tennis", "KXWTAMATCH", "wta", 102123, None, "tennis_wta"),
    "cs2": League("cs2", "Counter-Strike 2", "KXCS2GAME", "cs2", 100780, None),
    "dota2": League("dota2", "Dota 2", "KXDOTA2GAME", "dota2", 102366, None),
    "lol": League("lol", "League of Legends", "KXLOLGAME", "lol", 65, None),
    "valorant": League("valorant", "Valorant", "KXVALORANTGAME", "val", 101672, None),
    "r6": League("r6", "Rainbow Six", "KXR6GAME", "r6siege", 102755, None),
    "khl": League("khl", "KHL hockey", "KXKHLGAME", "khl", 102908, None),
    "npb": League("npb", "NPB baseball", "KXNPBGAME", "npb", 105452, None),
    "kbo": League("kbo", "KBO baseball", "KXKBOGAME", "kbo", 102668, None),
    "championship": League("championship", "EFL Championship", "KXEFLCHAMPIONSHIPGAME", "elc", 102643, "soccer/eng.2", "soccer_efl_champ", True, dynamic_registry=True),
    "league_one": League("league_one", "EFL League One", "KXEFLL1GAME", "el1", 104319, None, three_way=True),
    "ligamx": League("ligamx", "Liga MX", "KXLIGAMXGAME", "mex", 102448, "soccer/mex.1", "soccer_mexico_ligamx", True, dynamic_registry=True),
    "brasileirao": League("brasileirao", "Brasileirão Série A", "KXBRASILEIROGAME", "bra", 102648, "soccer/bra.1", "soccer_brazil_campeonato", True, dynamic_registry=True),
    "brasileirao_b": League("brasileirao_b", "Brasileirão Série B", "KXBRASILEIROBGAME", "bra2", 105921, "soccer/bra.2", "soccer_brazil_serie_b", True, dynamic_registry=True),
    "saudi": League("saudi", "Saudi Pro League", "KXSAUDIPLGAME", "spl", 102650, None, three_way=True),
    "uel": League("uel", "Europa League", "KXUELGAME", "uel", 101787, "soccer/uefa.europa", "soccer_uefa_europa_league", True, dynamic_registry=True),
    "eredivisie": League("eredivisie", "Eredivisie", "KXEREDIVISIEGAME", "ere", 101735, "soccer/ned.1", "soccer_netherlands_eredivisie", True, dynamic_registry=True),
    "kleague": League("kleague", "K League 1", "KXKLEAGUEGAME", "kor", 102771, None, three_way=True),
    "jleague": League("jleague", "J1 League", "KXJLEAGUEGAME", "jap", 102649, "soccer/jpn.1", "soccer_japan_j_league", True, dynamic_registry=True),
    "usl": League("usl", "USL Championship", "KXUSLGAME", "uslc", 105703, "soccer/usa.usl.1", None, True, dynamic_registry=True),
    "ligue2": League("ligue2", "Ligue 2", "KXLIGUE2GAME", "fr2", 102871, "soccer/fra.2", "soccer_france_ligue_two", True, dynamic_registry=True),
    "liga_portugal": League("liga_portugal", "Liga Portugal", "KXLIGAPORTUGALGAME", "por", 101772, "soccer/por.1", "soccer_portugal_primeira_liga", True, dynamic_registry=True),
    "egypt": League("egypt", "Egyptian Premier League", "KXEGYPLGAME", "egy1", 105919, None, three_way=True),
    "serie_b": League("serie_b", "Serie B", "KXSERIEBGAME", "itsb", 102870, "soccer/ita.2", "soccer_italy_serie_b", True, dynamic_registry=True),
    "eerste": League("eerste", "Eerste Divisie", "KXEERSTEDIVGAME", "ned2", 105727, "soccer/ned.2", None, True, dynamic_registry=True),
}
LEAGUES.update(NICHE)

DEFAULT_LEAGUES = ["nfl", "ncaaf", "nba", "wnba", "mlb", "nhl", "epl", "laliga", "bundesliga", "seriea", "ligue1", "mls", "ucl"]
NICHE_LEAGUES = list(NICHE)

KALSHI_SERIES_TO_LEAGUE = {lg.kalshi_series: lg.key for lg in LEAGUES.values()}
POLY_TAG_TO_LEAGUE = {lg.poly_tag: lg.key for lg in LEAGUES.values()}


def _f(name: str, default: float) -> float:
    v = os.getenv(name)
    return float(v) if v not in (None, "") else default


@dataclass
class Settings:
    odds_api_key: str | None = None
    bankroll: float = 10_000.0
    max_per_trade: float = 500.0  # max outlay per paper trade
    max_per_key: float = 1_000.0  # max open outlay per matched market/game
    min_margin: float = 0.005  # 0.5% net-of-fees minimum to count as an arb
    kalshi_taker_rate: float = 0.07
    poly_fee_default: float = 0.05
    book_max_stake: float = 500.0  # assumed sportsbook liquidity per bet
    book_depth: int = 20
    db_path: Path = DATA_DIR / "paper.db"
    kalshi_rate: float = 8.0
    gamma_rate: float = 10.0
    clob_rate: float = 10.0
    espn_rate: float = 5.0
    max_workers: int = 8

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            odds_api_key=os.getenv("ODDS_API_KEY") or None,
            bankroll=_f("PAPER_BANKROLL", 10_000.0),
            max_per_trade=_f("PAPER_MAX_PER_TRADE", 500.0),
            max_per_key=_f("PAPER_MAX_PER_KEY", 1_000.0),
            min_margin=_f("PAPER_MIN_MARGIN", 0.005),
            kalshi_taker_rate=_f("KALSHI_TAKER_RATE", 0.07),
            poly_fee_default=_f("POLY_FEE_RATE_DEFAULT", 0.05),
            book_max_stake=_f("BOOK_MAX_STAKE", 500.0),
            db_path=Path(os.getenv("PAPER_DB") or DATA_DIR / "paper.db"),
            kalshi_rate=_f("KALSHI_RATE", 8.0),
            gamma_rate=_f("GAMMA_RATE", 10.0),
            clob_rate=_f("CLOB_RATE", 10.0),
        )

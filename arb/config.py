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
    espn: str  # ESPN path: <sport>/<league>
    odds_api: str | None = None  # The Odds API sport key
    three_way: bool = False  # soccer: draw is a real outcome
    espn_extra: dict | None = None  # extra scoreboard params (e.g. groups=80 for FBS)


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

DEFAULT_LEAGUES = ["nfl", "ncaaf", "nba", "wnba", "mlb", "nhl", "epl", "laliga", "bundesliga", "seriea", "ligue1", "mls", "ucl"]

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
        )

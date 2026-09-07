from datetime import datetime, timezone

from arb.config import LEAGUES
from arb.espn import EspnGame, parse_american
from arb.kalshi import event_date_from_ticker
from arb.matching import build_games, general_candidates, kalshi_candidates, numeric_signature, poly_candidates
from arb.models import KALSHI, POLYMARKET, BookOdds, Outcome
from arb.teams import TeamRegistry

NFL = [
    {"id": "17", "abbreviation": "NE", "displayName": "New England Patriots", "shortDisplayName": "Patriots",
     "name": "Patriots", "location": "New England", "nickname": "Patriots"},
    {"id": "26", "abbreviation": "SEA", "displayName": "Seattle Seahawks", "shortDisplayName": "Seahawks",
     "name": "Seahawks", "location": "Seattle", "nickname": "Seahawks"},
    {"id": "19", "abbreviation": "NYG", "displayName": "New York Giants", "shortDisplayName": "Giants",
     "name": "Giants", "location": "New York", "nickname": "Giants"},
    {"id": "20", "abbreviation": "NYJ", "displayName": "New York Jets", "shortDisplayName": "Jets",
     "name": "Jets", "location": "New York", "nickname": "Jets"},
    {"id": "14", "abbreviation": "LAR", "displayName": "Los Angeles Rams", "shortDisplayName": "Rams",
     "name": "Rams", "location": "Los Angeles", "nickname": "Rams"},
    {"id": "24", "abbreviation": "LAC", "displayName": "Los Angeles Chargers", "shortDisplayName": "Chargers",
     "name": "Chargers", "location": "Los Angeles", "nickname": "Chargers"},
    {"id": "30", "abbreviation": "JAX", "displayName": "Jacksonville Jaguars", "shortDisplayName": "Jaguars",
     "name": "Jaguars", "location": "Jacksonville", "nickname": "Jaguars"},
    {"id": "28", "abbreviation": "WSH", "displayName": "Washington Commanders", "shortDisplayName": "Commanders",
     "name": "Commanders", "location": "Washington", "nickname": "Commanders"},
]


def reg():
    return TeamRegistry("nfl", NFL)


def test_resolve_kalshi_styles():
    r = reg()
    assert r.resolve("New England", "NE") == "17"
    assert r.resolve("New York G", "NYG") == "19"
    assert r.resolve("New York G") == "19"  # no abbreviation, initial heuristic
    assert r.resolve("New York J") == "20"
    assert r.resolve("Los Angeles R") == "14"
    assert r.resolve("Los Angeles C") == "24"
    assert r.resolve("Jacksonville", "JAC") == "30"  # alias JAC -> JAX
    assert r.resolve("Washington", "WAS") == "28"
    assert r.resolve("New York") is None  # ambiguous


def test_resolve_polymarket_and_full_names():
    r = reg()
    assert r.resolve("Patriots") == "17"
    assert r.resolve("Seahawks") == "26"
    assert r.resolve("New England Patriots") == "17"
    assert r.resolve("Giants") == "19"


def test_ticker_date():
    d = event_date_from_ticker("KXNFLGAME-26SEP09NESEA")
    assert (d.year, d.month, d.day) == (2026, 9, 9)
    assert event_date_from_ticker("KXFED-26SEP") is None


def test_parse_american():
    assert parse_american("-180") == -180
    assert parse_american("+150") == 150
    assert parse_american("EVEN") == 100
    assert parse_american(None) is None


def k_out(ticker, sub, code, event="KXNFLGAME-26SEP09NESEA", ask=0.4, bid=0.38):
    return Outcome(venue=KALSHI, id=ticker, market_id=ticker, event_id=event, title=f"{sub} wins", event_title="NE vs SEA",
                   category="Sports", league="nfl", game_time=event_date_from_ticker(event), yes_bid=bid, yes_ask=ask,
                   yes_bid_size=100, yes_ask_size=100, fee_rate=0.07, fee_round_cents=True,
                   meta={"yes_sub_title": sub, "team_code": code})


def p_out(tok, name, idx, event="559", ask=0.4, bid=0.38, gt=datetime(2026, 9, 10, 0, 20, tzinfo=timezone.utc)):
    return Outcome(venue=POLYMARKET, id=tok, market_id="0xc", event_id=event, title=name, event_title="Patriots vs. Seahawks",
                   category="sports,nfl", league="nfl", game_time=gt, yes_bid=bid, yes_ask=ask, fee_rate=0.05,
                   meta={"team_text": name, "outcome_index": idx, "sports_type": "moneyline", "line": None, "no_token": "x"})


def test_build_games_across_three_sources():
    r = reg()
    k = [k_out("KXNFLGAME-26SEP09NESEA-NE", "New England", "NE", ask=0.37, bid=0.35),
         k_out("KXNFLGAME-26SEP09NESEA-SEA", "Seattle", "SEA", ask=0.66, bid=0.63)]
    p = [p_out("t0", "Patriots", 0, ask=0.38, bid=0.37), p_out("t1", "Seahawks", 1, ask=0.63, bid=0.62)]
    espn = [EspnGame(id="401", league="nfl", name="NE at SEA", start=datetime(2026, 9, 10, 0, 20, tzinfo=timezone.utc),
                     home={"id": "26", "abbreviation": "SEA", "displayName": "Seattle Seahawks"},
                     away={"id": "17", "abbreviation": "NE", "displayName": "New England Patriots"},
                     state="pre", completed=False, home_score=None, away_score=None, winner=None,
                     odds=[BookOdds("Draft Kings", "26", -180, 1 + 100 / 180), BookOdds("Draft Kings", "17", 150, 2.5)])]
    kc = kalshi_candidates(k, "nfl", r)
    pc = poly_candidates(p, "nfl", r)
    assert len(kc) == 1 and set(kc[0].legs) == {"17", "26"}
    assert len(pc) == 1 and set(pc[0].legs) == {"17", "26"}
    games = build_games("nfl", kc, pc, espn, [], r)
    assert len(games) == 1
    g = games[0]
    assert g.home == "26" and g.away == "17"
    assert g.key == "nfl:2026-09-09:17-26"  # local (US) game date, as Kalshi labels it
    assert g.kalshi["17"].id.endswith("-NE") and g.polymarket["17"].id == "t0"
    assert g.sportsbook["26"][0].american == -180
    assert g.espn_event_id == "401"


def test_build_games_separates_same_matchup_different_dates():
    r = reg()
    k1 = [k_out("KXNFLGAME-26SEP09NESEA-NE", "New England", "NE"), k_out("KXNFLGAME-26SEP09NESEA-SEA", "Seattle", "SEA")]
    k2 = [k_out("KXNFLGAME-26DEC20NESEA-NE", "New England", "NE", event="KXNFLGAME-26DEC20NESEA"),
          k_out("KXNFLGAME-26DEC20NESEA-SEA", "Seattle", "SEA", event="KXNFLGAME-26DEC20NESEA")]
    p = [p_out("t0", "Patriots", 0), p_out("t1", "Seahawks", 1)]
    games = build_games("nfl", kalshi_candidates(k1 + k2, "nfl", r), poly_candidates(p, "nfl", r), [], [], r)
    assert len(games) == 1  # December Kalshi event has no counterpart -> dropped
    assert games[0].key.startswith("nfl:2026-09-")


def test_numeric_signature_guard():
    assert numeric_signature("bitcoin above 100,000 on sep 30") == frozenset({"100000", "30"})
    assert numeric_signature("fed cuts 25 bps") != numeric_signature("fed cuts 50 bps")


def test_general_candidates_requires_numbers_to_agree():
    k = Outcome(venue=KALSHI, id="KXBTC-100K", market_id="m", event_id="e", title="Above $100,000",
                event_title="Bitcoin price on Sep 30?", category="Crypto", yes_ask=0.4, yes_bid=0.38,
                meta={"yes_sub_title": "Above $100,000"})
    p_good = Outcome(venue=POLYMARKET, id="p1", market_id="c1", event_id="e1", title="q", event_title="",
                     category="crypto", yes_ask=0.41, yes_bid=0.4,
                     meta={"question": "Will Bitcoin be above $100,000 on Sep 30?", "outcome_name": "Yes"})
    p_bad = Outcome(venue=POLYMARKET, id="p2", market_id="c2", event_id="e2", title="q", event_title="",
                    category="crypto", yes_ask=0.2, yes_bid=0.19,
                    meta={"question": "Will Bitcoin be above $110,000 on Sep 30?", "outcome_name": "Yes"})
    c = general_candidates([k], [p_good, p_bad], min_score=70)
    assert [x.polymarket.id for x in c] == ["p1"]

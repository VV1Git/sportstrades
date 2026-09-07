"""One scan = fetch -> match -> detect -> record -> (paper) trade."""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .arbitrage import cross_pair, intra_set, top_of_book_gap
from .config import LEAGUES, Settings
from .espn import Espn
from .kalshi import Kalshi
from .matching import (PairCandidate, build_games, confirmed_pairs, general_candidates, kalshi_candidates,
                       load_confirmed_pairs, poly_candidates)
from .models import KALSHI, POLYMARKET, Game, Opportunity, Outcome
from .oddsapi import OddsApi
from .paper import PaperTrader
from .polymarket import Polymarket
from .report import console
from .store import Store
from .teams import DynamicRegistry, TeamRegistry
from .vegas import Discrepancy, discrepancies, vegas_opportunities


@dataclass
class ScanResult:
    scan_id: int
    games: list[Game] = field(default_factory=list)
    pairs: list[PairCandidate] = field(default_factory=list)
    opportunities: list[Opportunity] = field(default_factory=list)
    discrepancies: list[Discrepancy] = field(default_factory=list)
    taken: list[dict] = field(default_factory=list)
    n_kalshi: int = 0
    n_poly: int = 0
    duration: float = 0.0
    unmatched_notes: list[str] = field(default_factory=list)


class Scanner:
    def __init__(self, settings: Settings, leagues: list[str], scope: str = "sports", paper: bool = True,
                 store: Store | None = None, quiet: bool = False, general_min_score: float | None = None,
                 include_live: bool = False, record_quotes: bool = True):
        self.s = settings
        self.include_live = include_live
        self.record_quotes = record_quotes
        self.leagues = [l for l in leagues if l in LEAGUES]
        self.scope = scope
        self.paper = paper
        self.quiet = quiet
        self.general_min_score = general_min_score
        self.kalshi = Kalshi(settings)
        self.poly = Polymarket(settings)
        self.espn = Espn(settings)
        self.oddsapi = OddsApi(settings)
        self.store = store or Store(settings.db_path)
        self.registries: dict[str, TeamRegistry] = {}

    def log(self, msg: str) -> None:
        if not self.quiet:
            console.log(msg)

    def registry(self, league: str, kalshi_outs: list[Outcome] | None = None,
                 poly_outs: list[Outcome] | None = None):
        if league not in self.registries:
            if LEAGUES[league].espn and not LEAGUES[league].dynamic_registry:
                self.registries[league] = TeamRegistry(league, self.espn.teams(LEAGUES[league]))
            else:
                names = [o.meta.get("yes_sub_title") or o.title for o in (kalshi_outs or []) if o.league == league]
                names += [o.meta.get("team_text") or o.title for o in (poly_outs or [])
                          if o.league == league and o.meta.get("sports_type") == "moneyline"]
                self.registries[league] = DynamicRegistry(league, names)
        return self.registries[league]

    # ------------------------------------------------------------------
    def fetch_kalshi(self) -> list[Outcome]:
        if self.scope == "all":
            outs = self.kalshi.outcomes_from_events(self.kalshi.iter_events(status="open", with_nested_markets=True))
            return outs
        with ThreadPoolExecutor(max_workers=min(self.s.max_workers, 6)) as ex:
            lists = list(ex.map(lambda lg: self.kalshi.league_outcomes(LEAGUES[lg].kalshi_series), self.leagues))
        return [o for lst in lists for o in lst]

    def fetch_poly(self) -> list[Outcome]:
        if self.scope == "all":
            return self.poly.outcomes_from_events(self.poly.iter_all_active_events())
        with ThreadPoolExecutor(max_workers=min(self.s.max_workers, 6)) as ex:
            lists = list(ex.map(lambda lg: self.poly.events_for_league(LEAGUES[lg].poly_tag, LEAGUES[lg].poly_sport), self.leagues))
        outs = []
        for lg, lst in zip(self.leagues, lists):
            # events fetched for a league are stamped with it (their tags do not always carry the league)
            outs.extend(self.poly.outcomes_from_events(lst, league=lg))
        return outs

    @staticmethod
    def _worth_a_book(g: Game, slack: float = 0.03) -> bool:
        """Top-of-book pre-filter: only pull full Kalshi ladders when the best
        quotes are within `slack` of an arbitrage (or a sportsbook line exists)."""
        if g.sportsbook:
            return True
        for team in set(g.kalshi) & set(g.polymarket):
            gap = top_of_book_gap(g.kalshi[team], g.polymarket[team], "same")
            if gap is not None and gap > -slack:
                return True
        for legs in (g.kalshi, g.polymarket):
            asks = [o.yes_ask for o in legs.values() if o.yes_ask is not None]
            if len(asks) == len(legs) >= 2 and sum(asks) < 1.0 + slack:
                return True
        return False

    def fetch_books(self, games: list[Game], pairs: list[PairCandidate]) -> None:
        k_outs: dict[str, Outcome] = {}
        p_outs: dict[str, Outcome] = {}
        for g in games:
            if self._worth_a_book(g):
                for o in g.kalshi.values():
                    k_outs[o.id] = o
            for o in g.polymarket.values():
                p_outs[o.id] = o
        for pr in pairs:
            k_outs[pr.kalshi.id] = pr.kalshi
            p_outs[pr.polymarket.id] = pr.polymarket
        if p_outs:
            self.poly.attach_books(list(p_outs.values()))

        def _kb(o: Outcome) -> None:
            try:
                o.book = self.kalshi.orderbook(o.id, self.s.book_depth)
                b = o.book.best("yes")
                if b:
                    o.yes_ask, o.yes_ask_size = b.price, b.size
                if o.book.yes_bids:
                    o.yes_bid, o.yes_bid_size = o.book.yes_bids[0].price, o.book.yes_bids[0].size
            except Exception as e:  # keep top-of-book from the list endpoint
                self.log(f"[yellow]orderbook failed for {o.id}: {e}")

        with ThreadPoolExecutor(max_workers=self.s.max_workers) as ex:
            list(ex.map(_kb, k_outs.values()))

    # ------------------------------------------------------------------
    def detect(self, games: list[Game], pairs: list[PairCandidate]) -> tuple[list[Opportunity], list[Discrepancy]]:
        opps: list[Opportunity] = []
        discs: list[Discrepancy] = []
        now = datetime.now(timezone.utc)
        for g in games:
            if not self.include_live and g.is_live_or_done(now):
                continue  # in-play prices move too fast for a two-leg hedge to be realistic
            label = f"{LEAGUES[g.league].name} {g.away_name} @ {g.home_name} {g.start.strftime('%m-%d') if g.start else ''}"
            for team in set(g.kalshi) & set(g.polymarket):
                opps += cross_pair(g.kalshi[team], g.polymarket[team], "same", g.key, label, self.s)
            if len(g.kalshi) >= 2 and g.kalshi and all(o.book or o.yes_ask for o in g.kalshi.values()):
                if not LEAGUES[g.league].three_way or "draw" in g.kalshi:
                    o = intra_set(list(g.kalshi.values()), g.key, label, self.s)
                    if o:
                        opps.append(o)
            if len(g.polymarket) >= 2:
                if not LEAGUES[g.league].three_way or "draw" in g.polymarket:
                    o = intra_set(list(g.polymarket.values()), g.key, label, self.s)
                    if o:
                        opps.append(o)
            if g.sportsbook:
                opps += vegas_opportunities(g, self.s)
                discs += discrepancies(g)
        for pr in pairs:
            label = f"[{'confirmed' if pr.confirmed else f'fuzzy {pr.score:.0f}'}] {pr.kalshi.event_title} / {pr.polymarket.meta.get('question', pr.polymarket.title)}"
            key = f"pair:{pr.kalshi.id}:{pr.polymarket.market_id}"
            opps += cross_pair(pr.kalshi, pr.polymarket, pr.relation, key, label, self.s)
        return opps, discs

    # ------------------------------------------------------------------
    def run(self) -> ScanResult:
        t0 = time.time()
        scan_id = self.store.start_scan(self.scope, self.leagues)
        try:
            return self._run(scan_id, t0)
        except BaseException as e:
            self.store.finish_scan(scan_id, duration_s=time.time() - t0, notes=f"failed: {e!r}"[:500])
            raise

    def _run(self, scan_id: int, t0: float) -> ScanResult:
        res = ScanResult(scan_id=scan_id)

        with ThreadPoolExecutor(max_workers=3) as ex:
            fk = ex.submit(self.fetch_kalshi)
            fp = ex.submit(self.fetch_poly)
            fe = ex.submit(lambda: {lg: self.espn.scoreboard_range(LEAGUES[lg], days_ahead=2)
                                    for lg in self.leagues if LEAGUES[lg].espn})
            kalshi_outs, poly_outs, espn_games = fk.result(), fp.result(), fe.result()
        res.n_kalshi, res.n_poly = len(kalshi_outs), len(poly_outs)
        self.log(f"fetched {len(kalshi_outs)} Kalshi outcomes, {len(poly_outs)} Polymarket outcomes, "
                 f"{sum(len(v) for v in espn_games.values())} ESPN games")

        oddsapi_games = {}
        if self.oddsapi.enabled:
            for lg in self.leagues:
                try:
                    oddsapi_games[lg] = self.oddsapi.games(LEAGUES[lg])
                except Exception as e:
                    self.log(f"[yellow]Odds API failed for {lg}: {e}")

        games: list[Game] = []
        for lg in self.leagues:
            reg = self.registry(lg, kalshi_outs, poly_outs)
            kc = kalshi_candidates(kalshi_outs, lg, reg)
            pc = poly_candidates(poly_outs, lg, reg)
            for c in kc + pc:
                for u in c.unresolved:
                    res.unmatched_notes.append(f"{lg} {c.venue} {c.event_id}: could not resolve team '{u}'")
            games += build_games(lg, kc, pc, espn_games.get(lg, []), oddsapi_games.get(lg, []), reg, res.unmatched_notes)
        res.games = games
        self.log(f"matched {len(games)} games "
                 f"({sum(1 for g in games if g.kalshi and g.polymarket)} on both prediction markets, "
                 f"{sum(1 for g in games if g.sportsbook and (g.kalshi or g.polymarket))} with a sportsbook line)")

        pairs: list[PairCandidate] = confirmed_pairs(kalshi_outs, poly_outs, load_confirmed_pairs())
        if self.scope == "all":
            cands = general_candidates(kalshi_outs, poly_outs, min_score=85.0)
            if self.general_min_score is not None:
                # opt-in: also trade unconfirmed fuzzy pairs above the given score
                confirmed_ids = {(p.kalshi.id, p.polymarket.market_id) for p in pairs}
                auto = [c for c in cands if c.score >= self.general_min_score
                        and (c.kalshi.id, c.polymarket.market_id) not in confirmed_ids]
                # only fetch books for pairs whose top-of-book could possibly be an arb
                auto = [c for c in auto if (top_of_book_gap(c.kalshi, c.polymarket, c.relation) or -1) > -0.05]
                pairs += auto
            res.pairs = cands
        else:
            res.pairs = pairs

        self.fetch_books(games, pairs)
        opps, discs = self.detect(games, pairs)
        res.opportunities, res.discrepancies = opps, discs

        quotes = []
        for g in games:
            for venue, legs in ((KALSHI, g.kalshi), (POLYMARKET, g.polymarket)):
                for team, o in legs.items():
                    quotes.append((g.key, g.league, team, venue, o.id, o.yes_bid, o.yes_ask, o.yes_bid_size, o.yes_ask_size))
        if quotes and self.record_quotes:
            self.store.add_quotes(scan_id, quotes)
        if discs:
            self.store.add_discrepancies(scan_id, [d.as_dict() for d in discs])
        opp_ids = self.store.add_opportunities(scan_id, opps)
        if self.paper and opps:
            res.taken = PaperTrader(self.store, self.s).consider(opps, opp_ids, scan_id)
        res.duration = time.time() - t0
        self.store.finish_scan(scan_id, n_kalshi=res.n_kalshi, n_poly=res.n_poly, n_games=len(games),
                               n_pairs=len(pairs), n_opps=len(opps), duration_s=res.duration)
        return res

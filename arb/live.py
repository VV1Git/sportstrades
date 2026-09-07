"""Live-game sampler and decoupling analysis.

Hypothesis under test: Kalshi and Polymarket converge over long horizons but
decouple during the high-volatility minutes of a game itself, when each
venue's order book is re-priced by different participants at different speeds.

`LiveSampler` records both venues' top-of-book (and full ladders) for every
matched game that is in progress, at a few-second cadence, alongside soon-to-
start games as a control group. `analyze()` then measures, by phase:

* how far apart the two venues' mid prices sit,
* how often the best prices cross (gross gap > 0) and how often that survives
  taker fees and book depth (a real arb),
* how long such windows last (episode length), which decides executability.
"""
from __future__ import annotations

import json
import math
import statistics
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from .arbitrage import cross_pair
from .config import LEAGUES, Settings
from .matching import build_games, kalshi_candidates, poly_candidates
from .models import KALSHI, POLYMARKET, Game, Outcome
from .scan import Scanner
from .store import Store

TICKS_SCHEMA = """
CREATE TABLE IF NOT EXISTS live_sessions(
  id INTEGER PRIMARY KEY, started TEXT, ended TEXT, leagues TEXT, interval_s REAL, n_ticks INT, notes TEXT);
CREATE TABLE IF NOT EXISTS ticks(
  id INTEGER PRIMARY KEY, session INT, ts TEXT, match_key TEXT, league TEXT, team TEXT, team_name TEXT, phase TEXT,
  status TEXT, k_bid REAL, k_ask REAL, k_bid_sz REAL, k_ask_sz REAL, p_bid REAL, p_ask REAL, p_bid_sz REAL, p_ask_sz REAL,
  mid_diff REAL, gross_gap REAL, net_margin REAL, net_qty REAL, net_profit REAL, k_list_bid REAL, k_list_ask REAL);
CREATE INDEX IF NOT EXISTS ix_ticks_key ON ticks(match_key, team, ts);
CREATE INDEX IF NOT EXISTS ix_ticks_session ON ticks(session);
"""


def _gross_gap(k: Outcome, p: Outcome) -> float | None:
    """Best top-of-book crossing, before fees: buy cheap YES on one venue and
    NO on the other. Positive means the best prices already cross."""
    gaps = []
    if k.yes_ask is not None and p.yes_bid is not None:
        gaps.append(p.yes_bid - k.yes_ask)  # YES@Kalshi + NO@Poly (NO ask = 1 - YES bid)
    if p.yes_ask is not None and k.yes_bid is not None:
        gaps.append(k.yes_bid - p.yes_ask)  # YES@Poly + NO@Kalshi
    return max(gaps) if gaps else None


class LiveSampler:
    def __init__(self, settings: Settings, leagues: list[str], interval: float = 5.0, pregame_hours: float = 6.0,
                 rematch_every: float = 600.0, store: Store | None = None, quiet: bool = False, max_games: int = 24):
        self.s = settings
        self.max_games = max_games
        self.leagues = [l for l in leagues if l in LEAGUES]
        self.interval = interval
        self.pregame_hours = pregame_hours
        self.rematch_every = rematch_every
        self.quiet = quiet
        self.scanner = Scanner(settings, self.leagues, scope="sports", paper=False, store=store, quiet=True, include_live=True)
        self.store = self.scanner.store
        self.store.db.executescript(TICKS_SCHEMA)
        for col in ("k_list_bid", "k_list_ask"):
            try:
                self.store.db.execute(f"ALTER TABLE ticks ADD COLUMN {col} REAL")
            except Exception:
                pass
        self.games: list[Game] = []
        self.espn_state: dict[str, tuple[str, str]] = {}  # espn event id -> (state, status text)
        self._last_match = 0.0
        self._last_espn = 0.0
        self.session_id: int | None = None
        self.n_ticks = 0

    def log(self, msg: str) -> None:
        if not self.quiet:
            from .report import console
            console.log(msg)

    # ------------------------------------------------------------------
    def refresh_espn(self) -> None:
        for lg in self.leagues:
            if not LEAGUES[lg].espn:
                continue
            try:
                for g in self.scanner.espn.scoreboard(LEAGUES[lg]):
                    score = f"{g.away_score}-{g.home_score}" if g.home_score is not None else ""
                    self.espn_state[g.id] = (g.state, f"{g.detail} {score}".strip())
            except Exception as e:
                self.log(f"[yellow]espn {lg}: {e}")
        self._last_espn = time.time()

    def refresh_games(self) -> None:
        sc = self.scanner
        with ThreadPoolExecutor(max_workers=3) as ex:
            fk = ex.submit(sc.fetch_kalshi)
            fp = ex.submit(sc.fetch_poly)
            fe = ex.submit(lambda: {lg: sc.espn.scoreboard_range(LEAGUES[lg], days_ahead=1)
                                    for lg in self.leagues if LEAGUES[lg].espn})
            kalshi_outs, poly_outs, espn_games = fk.result(), fp.result(), fe.result()
        now = datetime.now(timezone.utc)
        games: list[Game] = []
        for lg in self.leagues:
            reg = sc.registry(lg, kalshi_outs, poly_outs)
            games += build_games(lg, kalshi_candidates(kalshi_outs, lg, reg), poly_candidates(poly_outs, lg, reg),
                                 espn_games.get(lg, []), [], reg)
        keep = []
        for g in games:
            if not (g.kalshi and g.polymarket):
                continue  # need both prediction markets to measure a gap
            if g.espn_state == "post":
                continue
            if g.start is not None and g.exact_start and g.start > now + timedelta(hours=self.pregame_hours):
                continue
            if g.start is not None and g.exact_start and g.start < now - timedelta(hours=5):
                continue
            keep.append(g)
        self.refresh_espn()
        # cap so a tick stays a few seconds; major leagues first, then keep at least a third of the
        # slots for the pre-game control group
        from .config import NICHE_LEAGUES
        rank = lambda g: (1 if g.league in NICHE_LEAGUES else 0, g.start or now)
        live = sorted((g for g in keep if self.phase(g, now) == "live"), key=rank)
        pre = sorted((g for g in keep if self.phase(g, now) != "live"), key=rank)
        n_pre = max(min(len(pre), self.max_games // 3), self.max_games - len(live))
        self.games = live[: self.max_games - n_pre] + pre[:n_pre]
        self._last_match = time.time()
        live = sum(1 for g in keep if self.phase(g, now) == "live")
        self.log(f"tracking {len(keep)} games on both venues ({live} live, {len(keep) - live} pre-game)")

    def phase(self, g: Game, now: datetime) -> str:
        if g.espn_event_id and g.espn_event_id in self.espn_state:
            st = self.espn_state[g.espn_event_id][0]
            if st == "in":
                return "live"
            if st == "post":
                return "post"
            if st == "pre":
                return "pre"
        if g.start is None:
            return "unknown"
        if g.start > now:
            return "pre"
        return "live" if now < g.start + timedelta(hours=4) else "post"

    def status_text(self, g: Game) -> str:
        if g.espn_event_id and g.espn_event_id in self.espn_state:
            return self.espn_state[g.espn_event_id][1]
        return ""

    # ------------------------------------------------------------------
    def tick(self) -> int:
        now = datetime.now(timezone.utc)
        if time.time() - self._last_espn > 60:
            self.refresh_espn()
        # refresh Kalshi top-of-book for the leagues we track (one list call per league)
        k_by_ticker: dict[str, dict] = {}
        for lg in {g.league for g in self.games}:
            try:
                for m in self.scanner.kalshi.markets(series_ticker=LEAGUES[lg].kalshi_series, status="open"):
                    k_by_ticker[m["ticker"]] = m
            except Exception as e:
                self.log(f"[yellow]kalshi list {lg}: {e}")
        k_outs: list[Outcome] = []
        p_outs: list[Outcome] = []
        list_quotes: dict[str, tuple[float | None, float | None]] = {}
        for g in self.games:
            for o in g.kalshi.values():
                m = k_by_ticker.get(o.id)
                if m:
                    fresh = self.scanner.kalshi.outcome_from_market(m, {"series_ticker": LEAGUES[g.league].kalshi_series})
                    if fresh:
                        list_quotes[o.id] = (fresh.yes_bid, fresh.yes_ask)
                        o.yes_bid, o.yes_ask = fresh.yes_bid, fresh.yes_ask
                        o.yes_bid_size, o.yes_ask_size = fresh.yes_bid_size, fresh.yes_ask_size
                        o.book = None
                k_outs.append(o)
            p_outs.extend(g.polymarket.values())
        if p_outs:
            try:
                self.scanner.poly.attach_books(p_outs)
            except Exception as e:
                self.log(f"[yellow]poly books: {e}")

        # executable Kalshi ladders for every tracked game (the list endpoint can lag the book)
        def _kb(o: Outcome) -> None:
            try:
                o.book = self.scanner.kalshi.orderbook(o.id, self.s.book_depth)
                b = o.book.best("yes")
                if b:
                    o.yes_ask, o.yes_ask_size = b.price, b.size
                if o.book.yes_bids:
                    o.yes_bid, o.yes_bid_size = o.book.yes_bids[0].price, o.book.yes_bids[0].size
            except Exception:
                o.book = None

        if k_outs:
            with ThreadPoolExecutor(max_workers=self.s.max_workers) as ex:
                list(ex.map(_kb, k_outs))

        rows = []
        ts = now.isoformat(timespec="seconds")
        for g in self.games:
            ph = self.phase(g, now)
            status = self.status_text(g)
            for team in set(g.kalshi) & set(g.polymarket):
                k, p = g.kalshi[team], g.polymarket[team]
                mid_diff = None
                if k.mid is not None and p.mid is not None:
                    mid_diff = k.mid - p.mid
                gap = _gross_gap(k, p)
                net_margin = net_qty = net_profit = None
                if gap is not None and gap > -0.02:
                    opps = cross_pair(k, p, "same", g.key, "live", self.s)
                    if opps:
                        best = max(opps, key=lambda o: o.margin)
                        net_margin, net_qty, net_profit = best.margin, best.qty, best.profit
                lq = list_quotes.get(k.id, (None, None))
                rows.append((self.session_id, ts, g.key, g.league, team, g.team_name(team), ph, status,
                             k.yes_bid, k.yes_ask, k.yes_bid_size, k.yes_ask_size,
                             p.yes_bid, p.yes_ask, p.yes_bid_size, p.yes_ask_size,
                             mid_diff, gap, net_margin, net_qty, net_profit, lq[0], lq[1]))
        if rows:
            self.store.db.executemany(
                "INSERT INTO ticks(session, ts, match_key, league, team, team_name, phase, status, k_bid, k_ask, k_bid_sz, k_ask_sz,"
                " p_bid, p_ask, p_bid_sz, p_ask_sz, mid_diff, gross_gap, net_margin, net_qty, net_profit, k_list_bid, k_list_ask)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
            self.store.db.commit()
        self.n_ticks += 1
        return len(rows)

    def run(self, duration: float = 0.0) -> int:
        cur = self.store.db.execute("INSERT INTO live_sessions(started, leagues, interval_s) VALUES (?,?,?)",
                                    (datetime.now(timezone.utc).isoformat(timespec="seconds"), ",".join(self.leagues), self.interval))
        self.store.db.commit()
        self.session_id = int(cur.lastrowid)
        t_end = time.time() + duration if duration > 0 else float("inf")
        try:
            while time.time() < t_end:
                t0 = time.time()
                if t0 - self._last_match > self.rematch_every or not self.games:
                    try:
                        self.refresh_games()
                    except Exception as e:
                        self.log(f"[red]refresh failed: {e!r}")
                try:
                    n = self.tick()
                except Exception as e:
                    self.log(f"[red]tick failed: {e!r}")
                    n = 0
                if self.n_ticks % 12 == 1:
                    now = datetime.now(timezone.utc)
                    live = [g for g in self.games if self.phase(g, now) == "live"]
                    self.log(f"tick {self.n_ticks}: {n} rows, {len(live)} live games "
                             + ", ".join(f"{g.away_name}@{g.home_name} [{self.status_text(g)}]" for g in live[:6]))
                time.sleep(max(0.0, self.interval - (time.time() - t0)))
        except KeyboardInterrupt:
            pass
        finally:
            self.store.db.execute("UPDATE live_sessions SET ended=?, n_ticks=? WHERE id=?",
                                  (datetime.now(timezone.utc).isoformat(timespec="seconds"), self.n_ticks, self.session_id))
            self.store.db.commit()
        return self.n_ticks


# ----------------------------------------------------------------------
# Analysis
# ----------------------------------------------------------------------
def _pct(xs: list[float], q: float) -> float | None:
    if not xs:
        return None
    xs = sorted(xs)
    k = min(len(xs) - 1, max(0, int(round(q * (len(xs) - 1)))))
    return xs[k]


def episodes(rows: list[dict], key: str, positive=lambda v: v is not None and v > 0) -> list[dict]:
    """Runs of consecutive ticks (per game/team) where `key` is positive."""
    out = []
    by: dict[tuple, list[dict]] = {}
    for r in rows:
        by.setdefault((r["match_key"], r["team"]), []).append(r)
    for (mk, team), rs in by.items():
        rs.sort(key=lambda r: r["ts"])
        run: list[dict] = []
        for r in rs + [None]:
            if r is not None and positive(r[key]):
                run.append(r)
                continue
            if run:
                t0 = datetime.fromisoformat(run[0]["ts"])
                t1 = datetime.fromisoformat(run[-1]["ts"])
                out.append({"match_key": mk, "team": team, "team_name": run[0]["team_name"], "phase": run[0]["phase"],
                            "start": run[0]["ts"], "ticks": len(run), "seconds": (t1 - t0).total_seconds(),
                            "peak": max(x[key] for x in run), "status": run[0]["status"]})
                run = []
    return out


def analyze(store: Store, session: int | None = None, sessions: list[int] | None = None) -> dict:
    """Analyse recorded ticks: one session, a list of sessions, or all."""
    store.db.executescript(TICKS_SCHEMA)
    if session is not None:
        sessions = [session]
    if sessions:
        marks = ",".join("?" * len(sessions))
        rows = [dict(r) for r in store.db.execute(f"SELECT * FROM ticks WHERE session IN ({marks}) ORDER BY ts", sessions).fetchall()]
    else:
        rows = [dict(r) for r in store.db.execute("SELECT * FROM ticks ORDER BY ts").fetchall()]
    sess = [dict(r) for r in store.db.execute("SELECT * FROM live_sessions ORDER BY id").fetchall()]
    interval = None
    if sessions:
        s_row = next((s for s in sess if s["id"] == sessions[-1]), None)
        interval = s_row["interval_s"] if s_row else None
    from .config import NICHE_LEAGUES
    out: dict = {"sessions": sess, "n_ticks": len(rows), "phases": {}, "phases_by_group": {}, "games": [], "episodes": {},
                 "top_moments": []}

    def _group(r: dict) -> str:
        return "niche" if r["league"] in NICHE_LEAGUES else "major"

    def _phase_stats(rs: list[dict]) -> dict:
        absd = [abs(r["mid_diff"]) for r in rs]
        gaps = [r["gross_gap"] for r in rs if r["gross_gap"] is not None]
        nets = [r["net_margin"] for r in rs if r["net_margin"] is not None and r["net_margin"] > 0]
        d = {
            "samples": len(rs), "games": len({r["match_key"] for r in rs}),
            "mean_abs_mid_diff": statistics.fmean(absd), "p50_abs_mid_diff": _pct(absd, 0.5),
            "p90_abs_mid_diff": _pct(absd, 0.9), "p99_abs_mid_diff": _pct(absd, 0.99), "max_abs_mid_diff": max(absd),
            "pct_gross_gap_positive": 100.0 * sum(1 for g in gaps if g > 0) / len(gaps) if gaps else None,
            "pct_gross_gap_ge_2c": 100.0 * sum(1 for g in gaps if g >= 0.02) / len(gaps) if gaps else None,
            "pct_net_arb": 100.0 * len(nets) / len(rs),
            "mean_net_margin_when_arb": statistics.fmean(nets) if nets else None,
            "max_net_margin": max(nets) if nets else None,
            "mean_kalshi_spread": statistics.fmean([r["k_ask"] - r["k_bid"] for r in rs if r["k_ask"] and r["k_bid"]]) or None,
            "mean_poly_spread": statistics.fmean([r["p_ask"] - r["p_bid"] for r in rs if r["p_ask"] and r["p_bid"]]) or None,
        }
        lst = [r for r in rs if r.get("k_list_ask") is not None and r.get("k_list_bid") is not None and r["p_bid"] is not None
               and r["p_ask"] is not None and r["k_ask"] is not None and r["k_bid"] is not None]
        if lst:
            phantom = sum(1 for r in lst if max(r["p_bid"] - r["k_list_ask"], r["k_list_bid"] - r["p_ask"]) > 0
                          and (r["gross_gap"] is None or r["gross_gap"] <= 0))
            d["list_vs_book_samples"] = len(lst)
            d["phantom_gap_pct"] = 100.0 * phantom / len(lst)
            d["mean_list_book_diff"] = statistics.fmean(abs(r["k_list_ask"] - r["k_ask"]) + abs(r["k_list_bid"] - r["k_bid"]) for r in lst)
        return d

    for grp in ("major", "niche"):
        for ph in ("pre", "live"):
            rs = [r for r in rows if r["phase"] == ph and r["mid_diff"] is not None and _group(r) == grp]
            if rs:
                out["phases_by_group"][f"{grp}/{ph}"] = _phase_stats(rs)
    for ph in ("pre", "live", "post", "unknown"):
        rs = [r for r in rows if r["phase"] == ph and r["mid_diff"] is not None]
        if not rs:
            continue
        absd = [abs(r["mid_diff"]) for r in rs]
        gaps = [r["gross_gap"] for r in rs if r["gross_gap"] is not None]
        nets = [r["net_margin"] for r in rs if r["net_margin"] is not None and r["net_margin"] > 0]
        out["phases"][ph] = {
            "samples": len(rs),
            "games": len({r["match_key"] for r in rs}),
            "mean_abs_mid_diff": statistics.fmean(absd),
            "p50_abs_mid_diff": _pct(absd, 0.5), "p90_abs_mid_diff": _pct(absd, 0.9), "p99_abs_mid_diff": _pct(absd, 0.99),
            "max_abs_mid_diff": max(absd),
            "pct_gross_gap_positive": 100.0 * sum(1 for g in gaps if g > 0) / len(gaps) if gaps else None,
            "pct_gross_gap_ge_2c": 100.0 * sum(1 for g in gaps if g >= 0.02) / len(gaps) if gaps else None,
            "pct_net_arb": 100.0 * len(nets) / len(rs),
            "mean_net_margin_when_arb": statistics.fmean(nets) if nets else None,
            "max_net_margin": max(nets) if nets else None,
            "mean_net_profit_when_arb": statistics.fmean([r["net_profit"] for r in rs if r["net_profit"]]) if nets else None,
            "mean_kalshi_spread": statistics.fmean([r["k_ask"] - r["k_bid"] for r in rs if r["k_ask"] and r["k_bid"]]) if rs else None,
            "mean_poly_spread": statistics.fmean([r["p_ask"] - r["p_bid"] for r in rs if r["p_ask"] and r["p_bid"]]) if rs else None,
        }
        # Kalshi list endpoint vs executable book: how often does the *list* show a crossing the book does not?
        lst = [r for r in rs if r.get("k_list_ask") is not None and r.get("k_list_bid") is not None
               and r["p_bid"] is not None and r["p_ask"] is not None and r["k_ask"] is not None and r["k_bid"] is not None]
        if lst:
            phantom = 0
            lag = []
            for r in lst:
                list_gap = max(r["p_bid"] - r["k_list_ask"], r["k_list_bid"] - r["p_ask"])
                if list_gap > 0 and (r["gross_gap"] is None or r["gross_gap"] <= 0):
                    phantom += 1
                lag.append(abs(r["k_list_ask"] - r["k_ask"]) + abs(r["k_list_bid"] - r["k_bid"]))
            out["phases"][ph]["list_vs_book_samples"] = len(lst)
            out["phases"][ph]["phantom_gap_pct"] = 100.0 * phantom / len(lst)
            out["phases"][ph]["mean_list_book_diff"] = statistics.fmean(lag)
    for kind, key in (("gross", "gross_gap"), ("net", "net_margin")):
        eps = episodes(rows, key)
        for ph in ("pre", "live"):
            e = [x for x in eps if x["phase"] == ph]
            secs = [x["seconds"] + (interval or 0) for x in e]
            out["episodes"][f"{kind}_{ph}"] = {
                "count": len(e), "median_seconds": statistics.median(secs) if secs else None,
                "max_seconds": max(secs) if secs else None, "single_tick": sum(1 for x in e if x["ticks"] == 1),
                "peak": max((x["peak"] for x in e), default=None),
            }
        out["episodes"][f"{kind}_list"] = sorted(eps, key=lambda x: -x["peak"])[:15]
    by_game: dict[str, list[dict]] = {}
    for r in rows:
        by_game.setdefault(r["match_key"], []).append(r)
    for mk, rs in by_game.items():
        live = [r for r in rs if r["phase"] == "live" and r["mid_diff"] is not None]
        pre = [r for r in rs if r["phase"] == "pre" and r["mid_diff"] is not None]
        out["games"].append({
            "match_key": mk, "league": rs[0]["league"], "label": " / ".join(sorted({r["team_name"] for r in rs})),
            "live_samples": len(live), "pre_samples": len(pre),
            "live_mean_abs_mid_diff": statistics.fmean([abs(r["mid_diff"]) for r in live]) if live else None,
            "pre_mean_abs_mid_diff": statistics.fmean([abs(r["mid_diff"]) for r in pre]) if pre else None,
            "live_pct_gross_positive": 100.0 * sum(1 for r in live if (r["gross_gap"] or -1) > 0) / len(live) if live else None,
            "live_net_arbs": sum(1 for r in live if (r["net_margin"] or 0) > 0),
            "best_net_margin": max((r["net_margin"] or 0) for r in rs),
            "max_abs_mid_diff": max(abs(r["mid_diff"]) for r in rs if r["mid_diff"] is not None) if any(r["mid_diff"] is not None for r in rs) else None,
        })
    out["games"].sort(key=lambda g: -(g["live_samples"] or 0))
    out["top_moments"] = sorted([r for r in rows if r["mid_diff"] is not None], key=lambda r: -abs(r["mid_diff"]))[:12]
    return out

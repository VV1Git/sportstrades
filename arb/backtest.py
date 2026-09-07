"""Historical replay: would cross-venue arbitrage have paid on past games?

For every settled game matched across Kalshi and Polymarket in the last N days
we pull Kalshi's one-minute candlesticks (closing YES bid and ask each minute)
and Polymarket's per-minute price history for the same team, align them by
minute, and ask at each minute whether buying YES on one venue and NO on the
other would have cost less than $1 after taker fees.

Polymarket's history is a mid price, not a quote, and an empty book reports a
mid of 0.50, so a minute only counts when Polymarket actually *traded* in that
minute or the one before; the last trade price stands in for the executable
price, with a spread assumed around it (`--poly-spread`, default 2 cents).
Fills are assumed at a fixed size (`--size`) whenever a signal appears, one
trade per episode of consecutive signal minutes. The result is an upper bound
on what a latency-free taker could have made, split into pre-game and in-game
minutes; signals that persist a second minute are counted separately.
"""
from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import CACHE_DIR, LEAGUES, NICHE_LEAGUES, Settings
from .fees import quadratic_fee
from .kalshi import Kalshi
from .matching import build_games, kalshi_candidates, poly_candidates
from .models import Game, Outcome
from .polymarket import Polymarket
from .espn import Espn
from .teams import DynamicRegistry, TeamRegistry


@dataclass
class LegResult:
    league: str
    game_key: str
    team_name: str
    kalshi_ticker: str
    poly_token: str
    start_ts: int
    minutes: int = 0
    live_minutes: int = 0
    candle_minutes: int = 0  # Kalshi minutes seen, before requiring a Polymarket trade
    gross_pre: int = 0
    gross_live: int = 0
    net_pre: int = 0
    net_live: int = 0
    episodes_pre: int = 0
    episodes_live: int = 0
    profit_pre: float = 0.0
    profit_live: float = 0.0
    persist_pre: int = 0
    persist_live: int = 0
    persist_profit_pre: float = 0.0
    persist_profit_live: float = 0.0
    best_margin: float = 0.0
    best_minute: int | None = None
    mid_gap_sum: float = 0.0
    mid_gap_live_sum: float = 0.0
    signals: list[dict] = field(default_factory=list)


class Backtester:
    def __init__(self, settings: Settings, leagues: list[str], days: int = 14, poly_spread: float = 0.02,
                 size: float = 50.0, pre_hours: float = 6.0, post_hours: float = 5.0, quiet: bool = False,
                 max_games: int | None = None):
        self.s = settings
        self.leagues = [l for l in leagues if l in LEAGUES]
        self.days = days
        self.half = poly_spread / 2
        self.size = size
        self.pre_hours, self.post_hours = pre_hours, post_hours
        self.quiet = quiet
        self.max_games = max_games
        self.kalshi = Kalshi(settings)
        self.poly = Polymarket(settings)
        self.espn = Espn(settings)
        self.cache = CACHE_DIR / "hist"
        self.cache.mkdir(parents=True, exist_ok=True)

    def log(self, msg: str) -> None:
        if not self.quiet:
            from .report import console
            console.log(msg)

    # ------------------------------------------------------------------ data
    def _cached(self, key: str, fn):
        f = self.cache / (key + ".json")
        if f.exists():
            return json.loads(f.read_text())
        v = fn()
        f.write_text(json.dumps(v))
        return v

    def matched_games(self, league: str) -> list[Game]:
        L = LEAGUES[league]
        since = datetime.now(timezone.utc) - timedelta(days=self.days)
        kms = self.kalshi.markets(series_ticker=L.kalshi_series, status="settled", min_close_ts=int(since.timestamp()))
        k_outs = []
        for m in kms:
            o = self.kalshi.outcome_from_market(m, {"event_ticker": m.get("event_ticker"), "series_ticker": L.kalshi_series,
                                                    "category": "Sports", "mutually_exclusive": True})
            if o:
                o.meta["result"] = m.get("result")
                k_outs.append(o)
        p_events = self.poly.closed_events_for_sport(L.poly_sport, since)
        p_outs = [o for o in self.poly.outcomes_from_events(p_events, league=league, include_closed=True)
                  if o.game_time and o.game_time >= since]
        if L.espn and not L.dynamic_registry:
            reg = TeamRegistry(league, self.espn.teams(L))
        else:
            names = [o.meta.get("yes_sub_title") or o.title for o in k_outs]
            names += [o.meta.get("team_text") or o.title for o in p_outs if o.meta.get("sports_type") == "moneyline"]
            reg = DynamicRegistry(league, names)
        games = build_games(league, kalshi_candidates(k_outs, league, reg), poly_candidates(p_outs, league, reg), [], [], reg)
        games = [g for g in games if g.kalshi and g.polymarket and g.start is not None and g.exact_start]
        games.sort(key=lambda g: g.start)
        self.log(f"{league}: {len(kms)} settled Kalshi markets, {len(p_events)} closed Polymarket events -> {len(games)} matched games")
        return games[-self.max_games:] if self.max_games else games

    def leg_series(self, league: str, g: Game, team: str) -> tuple[list[dict], list[tuple[int, float]], list[tuple[int, float, float]]]:
        """Kalshi minute candles, Polymarket history points and Polymarket trades (ts, price, size) for one leg."""
        k, p = g.kalshi[team], g.polymarket[team]
        start = int(g.start.timestamp())
        t0, t1 = start - int(self.pre_hours * 3600), start + int(self.post_hours * 3600)
        kc = self._cached(f"k_{k.id}_{t0}_{t1}", lambda: self.kalshi.candles(LEAGUES[league].kalshi_series, k.id, t0, t1, 1))
        ph = self._cached(f"p_{p.id[:24]}_{t0}_{t1}", lambda: self.poly.price_history(p.id, t0, t1, 1))
        tr = self._cached(f"t_{p.market_id[:24]}_{t0}_{t1}", lambda: self.poly.trades(p.market_id, t0, t1))
        mine = [(int(ts), float(px), float(sz)) for ts, tok, px, sz in tr if str(tok) == p.id]
        return kc, [(int(t), float(v)) for t, v in ph], mine

    # ------------------------------------------------------------------ replay
    def replay_leg(self, league: str, g: Game, team: str) -> LegResult | None:
        k, p = g.kalshi[team], g.polymarket[team]
        try:
            kc, ph, trades = self.leg_series(league, g, team)
        except Exception as e:
            self.log(f"[yellow]{g.key} {team}: {e}")
            return None
        if len(kc) < 10 or len(ph) < 10:
            return None
        start = int(g.start.timestamp())
        res = LegResult(league, g.key, g.team_name(team), k.id, p.id, start)
        # Polymarket history point per minute (stamped at the start of the minute)
        pmin: dict[int, float] = {}
        for t, v in ph:
            pmin[t // 60] = v
        # last trade price and traded size per minute
        tmin: dict[int, tuple[float, float]] = {}
        for ts, px, sz in sorted(trades):
            mnt = ts // 60
            prev = tmin.get(mnt)
            tmin[mnt] = (px, (prev[1] if prev else 0.0) + sz)
        rate_k, rate_p = k.fee_rate, p.fee_rate
        run_pre = run_live = False
        len_pre = len_live = 0
        for c in kc:
            m = c["ts"] // 60 - 1  # candle end -> the minute it covers
            if c["bid"] is None or c["ask"] is None:
                run_pre = run_live = False
                continue
            # Polymarket's history point stamped at minute m holds the price at the *start* of m;
            # the value as of the end of candle minute m is therefore the point stamped m+1.
            # (Verified against trade prints: Kalshi's 20:03 close matched Polymarket's 20:04 point.)
            res.candle_minutes += 1
            # Price: Polymarket's history point stamped m+1 = the price at the end of candle minute m
            # (verified against trade prints; the point stamped m holds the start-of-minute price).
            pm = pmin.get(m + 1)
            if pm is None:
                pm = pmin.get(m)
            # Validity: the market must actually be trading. An empty Polymarket book reports a mid of
            # 0.50 and a one-sided book reports nonsense, so require a trade within the last 3 minutes
            # whose price is within a dime of the history point.
            tr = tmin.get(m) or tmin.get(m - 1) or tmin.get(m - 2)
            if pm is None or tr is None or abs(tr[0] - pm) > 0.10:
                run_pre = run_live = False
                len_pre = len_live = 0
                continue
            if pm <= 0.005 or pm >= 0.995:
                run_pre = run_live = False
                len_pre = len_live = 0
                continue
            live = c["ts"] - 60 >= start  # the whole candle minute falls after the scheduled start
            res.minutes += 1
            if live:
                res.live_minutes += 1
            gap_mid = abs((c["bid"] + c["ask"]) / 2 - pm)
            res.mid_gap_sum += gap_mid
            if live:
                res.mid_gap_live_sum += gap_mid
            p_bid, p_ask = pm - self.half, pm + self.half
            # A: YES@Kalshi ask + NO@Polymarket (= 1 - poly bid)   B: YES@Polymarket ask + NO@Kalshi (= 1 - kalshi bid)
            cost_a = c["ask"] + (1 - p_bid)
            cost_b = p_ask + (1 - c["bid"])
            fee_a = quadratic_fee(rate_k, 1, c["ask"]) + quadratic_fee(rate_p, 1, 1 - p_bid)
            fee_b = quadratic_fee(rate_p, 1, p_ask) + quadratic_fee(rate_k, 1, 1 - c["bid"])
            gross = 1 - min(cost_a, cost_b)
            net_a, net_b = 1 - cost_a - fee_a, 1 - cost_b - fee_b
            net = max(net_a, net_b)
            if gross > 0:
                if live:
                    res.gross_live += 1
                else:
                    res.gross_pre += 1
            if net > 0:
                if live:
                    res.net_live += 1
                else:
                    res.net_pre += 1
                running = run_live if live else run_pre
                if not running:  # first minute of an episode: one trade at the configured size
                    profit = net * self.size
                    if live:
                        res.episodes_live += 1
                        res.profit_live += profit
                    else:
                        res.episodes_pre += 1
                        res.profit_pre += profit
                    res.signals.append({"ts": c["ts"], "live": live, "net": net, "gross": gross, "k_bid": c["bid"], "k_ask": c["ask"], "p_mid": pm,
                                        "p_traded": tr[1], "side": "YES@K+NO@P" if net_a >= net_b else "YES@P+NO@K"})
                if net > res.best_margin:
                    res.best_margin, res.best_minute = net, c["ts"]
                if live:
                    run_live = True
                    len_live += 1
                    if len_live == 2:  # the signal has survived a full minute: a human-speed trader could act on it
                        res.persist_live += 1
                        res.persist_profit_live += net * self.size
                else:
                    run_pre = True
                    len_pre += 1
                    if len_pre == 2:
                        res.persist_pre += 1
                        res.persist_profit_pre += net * self.size
            else:
                if live:
                    run_live = False
                    len_live = 0
                else:
                    run_pre = False
                    len_pre = 0
        return res

    def run(self) -> dict:
        t0 = time.time()
        results: list[LegResult] = []
        n_games = 0
        for lg in self.leagues:
            try:
                games = self.matched_games(lg)
            except Exception as e:
                self.log(f"[red]{lg}: {e!r}")
                continue
            n_games += len(games)
            jobs = [(lg, g, team) for g in games for team in set(g.kalshi) & set(g.polymarket)]
            with ThreadPoolExecutor(max_workers=self.s.max_workers) as ex:
                for r in ex.map(lambda j: self.replay_leg(*j), jobs):
                    if r:
                        results.append(r)
            self.log(f"{lg}: replayed {sum(1 for r in results if r.league == lg)} legs")
        return self.summarise(results, n_games, time.time() - t0)

    def summarise(self, results: list[LegResult], n_games: int, secs: float) -> dict:
        def agg(rs: list[LegResult]) -> dict:
            mins = sum(r.minutes for r in rs)
            live = sum(r.live_minutes for r in rs)
            pre = mins - live
            return {
                "legs": len(rs), "games": len({r.game_key for r in rs}), "minutes": mins, "pre_minutes": pre, "live_minutes": live,
                "candle_minutes": sum(r.candle_minutes for r in rs),
                "mean_mid_gap": (sum(r.mid_gap_sum for r in rs) / mins) if mins else None,
                "mean_mid_gap_live": (sum(r.mid_gap_live_sum for r in rs) / live) if live else None,
                "mean_mid_gap_pre": ((sum(r.mid_gap_sum for r in rs) - sum(r.mid_gap_live_sum for r in rs)) / pre) if pre else None,
                "gross_pct_pre": 100 * sum(r.gross_pre for r in rs) / pre if pre else None,
                "gross_pct_live": 100 * sum(r.gross_live for r in rs) / live if live else None,
                "net_pct_pre": 100 * sum(r.net_pre for r in rs) / pre if pre else None,
                "net_pct_live": 100 * sum(r.net_live for r in rs) / live if live else None,
                "episodes_pre": sum(r.episodes_pre for r in rs), "episodes_live": sum(r.episodes_live for r in rs),
                "profit_pre": sum(r.profit_pre for r in rs), "profit_live": sum(r.profit_live for r in rs),
                "persist_pre": sum(r.persist_pre for r in rs), "persist_live": sum(r.persist_live for r in rs),
                "persist_profit_pre": sum(r.persist_profit_pre for r in rs), "persist_profit_live": sum(r.persist_profit_live for r in rs),
                "best_margin": max((r.best_margin for r in rs), default=0.0),
            }
        by_league = {lg: agg([r for r in results if r.league == lg]) for lg in sorted({r.league for r in results})}
        total = agg(results)
        top = sorted((s | {"league": r.league, "game": r.game_key, "team": r.team_name} for r in results for s in r.signals),
                     key=lambda x: -x["net"])[:15]
        # per-game profit distribution
        per_game: dict[str, float] = {}
        for r in results:
            per_game[r.game_key] = per_game.get(r.game_key, 0.0) + r.profit_pre + r.profit_live
        return {"days": self.days, "size": self.size, "poly_spread": 2 * self.half, "games_matched": n_games, "seconds": secs,
                "by_league": by_league, "total": total, "top_signals": top,
                "games_with_any_profit": sum(1 for v in per_game.values() if v > 0), "games_total": len(per_game)}

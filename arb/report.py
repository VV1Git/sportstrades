"""Rich console reports."""
from __future__ import annotations

import shutil
import sys

from rich.console import Console
from rich.table import Table

from .models import Game, Opportunity
from .store import Store
from .vegas import Discrepancy

# When piped (not a TTY) rich assumes 80 columns and wraps every cell; use a wide layout instead.
console = Console(width=None if sys.stdout.isatty() else max(shutil.get_terminal_size().columns, 170))


def money(x: float | None) -> str:
    return "-" if x is None else f"${x:,.2f}"


def pct(x: float | None) -> str:
    return "-" if x is None else f"{x * 100:+.2f}%"


def price(x: float | None) -> str:
    return "-" if x is None else f"{x:.3f}"


def games_table(games: list[Game], title: str = "Matched games") -> Table:
    t = Table(title=title, expand=False)
    for c in ("League", "Game", "Start (UTC)", "Team", "Kalshi bid/ask", "Poly bid/ask", "Book (best)"):
        t.add_column(c)
    for g in sorted(games, key=lambda g: (g.league, g.start or "")):
        for team in (g.away, g.home):
            k = g.kalshi.get(team)
            p = g.polymarket.get(team)
            qs = g.sportsbook.get(team, [])
            best = max(qs, key=lambda q: q.decimal) if qs else None
            t.add_row(
                g.league.upper(), f"{g.away_name} @ {g.home_name}", g.start.strftime("%m-%d %H:%M") if g.start else "-",
                g.team_name(team),
                f"{price(k.yes_bid)}/{price(k.yes_ask)}" if k else "-",
                f"{price(p.yes_bid)}/{price(p.yes_ask)}" if p else "-",
                f"{best.american:+d} ({best.implied:.3f}) {best.bookmaker}" if best else "-",
            )
    return t


def opps_table(opps: list[Opportunity], title: str = "Arbitrage opportunities (net of taker fees)") -> Table:
    t = Table(title=title, expand=False)
    for c in ("Kind", "Qty", "Cost", "Payout", "Fees", "Profit", "Margin", "Description"):
        t.add_column(c)
    for o in sorted(opps, key=lambda o: -o.margin):
        t.add_row(o.kind, f"{o.qty:.0f}", money(o.cost), money(o.payout), money(o.fees), money(o.profit), pct(o.margin),
                  o.description[:140])
    return t


def discrepancy_table(rows: list[Discrepancy], limit: int = 25, min_abs_edge: float = 0.0) -> Table:
    t = Table(title="Vegas (de-vigged) vs prediction market   [fair = proportional de-vig, fair^ = power de-vig]", expand=False)
    for c in ("League", "Game", "Team", "Book", "ML", "Fair p", "Fair^ p", "Venue", "PM bid", "PM ask", "Edge buy", "Edge sell"):
        t.add_column(c)
    rows = [r for r in rows if max(abs(r.edge_buy or 0), abs(r.edge_sell or 0)) >= min_abs_edge]
    rows.sort(key=lambda r: -max(r.edge_buy or -9, r.edge_sell or -9))
    for r in rows[:limit]:
        t.add_row(r.league.upper(), r.game, r.team, r.bookmaker, f"{r.american:+d}", f"{r.fair:.3f}",
                  "-" if r.fair_power is None else f"{r.fair_power:.3f}", r.venue,
                  price(r.pm_bid), price(r.pm_ask), pct(r.edge_buy), pct(r.edge_sell))
    return t


def league_gap_table(games: list[Game], title: str = "Cross-venue tightness by league (top of book)") -> Table:
    """How far apart Kalshi and Polymarket sit per league, and whether the best
    prices ever cross."""
    import statistics
    t = Table(title=title, expand=False)
    for c in ("League", "Games both venues", "Legs", "|mid diff| mean", "|mid diff| max", "K spread", "P spread",
              "gross gap>0", "best gross gap", "K ask depth", "P ask depth"):
        t.add_column(c)
    by: dict[str, list] = {}
    for g in games:
        for team in set(g.kalshi) & set(g.polymarket):
            by.setdefault(g.league, []).append((g, g.kalshi[team], g.polymarket[team]))
    for lg, legs in sorted(by.items(), key=lambda x: -len(x[1])):
        md = [abs(k.mid - p.mid) for _, k, p in legs if k.mid is not None and p.mid is not None]
        ks = [k.yes_ask - k.yes_bid for _, k, p in legs if k.yes_ask is not None and k.yes_bid is not None]
        ps = [p.yes_ask - p.yes_bid for _, k, p in legs if p.yes_ask is not None and p.yes_bid is not None]
        gaps = []
        for _, k, p in legs:
            g1 = p.yes_bid - k.yes_ask if p.yes_bid is not None and k.yes_ask is not None else None
            g2 = k.yes_bid - p.yes_ask if k.yes_bid is not None and p.yes_ask is not None else None
            gg = [x for x in (g1, g2) if x is not None]
            if gg:
                gaps.append(max(gg))
        kd = [k.yes_ask_size for _, k, p in legs if k.yes_ask_size]
        pd = [p.yes_ask_size for _, k, p in legs if p.yes_ask_size]
        t.add_row(lg.upper(), str(len({g.key for g, _, _ in legs})), str(len(legs)),
                  f"{statistics.fmean(md):.4f}" if md else "-", f"{max(md):.3f}" if md else "-",
                  f"{statistics.fmean(ks):.3f}" if ks else "-", f"{statistics.fmean(ps):.3f}" if ps else "-",
                  f"{sum(1 for x in gaps if x > 0)}/{len(gaps)}" if gaps else "-",
                  f"{max(gaps):+.3f}" if gaps else "-",
                  f"{statistics.median(kd):.0f}" if kd else "-", f"{statistics.median(pd):.0f}" if pd else "-")
    return t


def summary_table(store: Store) -> Table:
    s = store.summary()
    t = Table(title="Paper-trading summary", show_header=False)
    t.add_column("k")
    t.add_column("v")
    t.add_row("Scans", str(s["scans"]))
    t.add_row("Opportunities logged", str(s["opportunities"]))
    for k, v in s["by_kind"].items():
        t.add_row(f"  {k}", f"{v['count']} opps, avg margin {pct(v['avg_margin'])}, sum profit {money(v['total_profit'])}")
    t.add_row("Open paper trades", f"{s['trades_open']}  (deployed {money(s['deployed'])}, locked-in profit {money(s['expected_open_profit'])})")
    t.add_row("Settled paper trades", f"{s['trades_settled']}  ({s['settled_wins']} profitable)")
    t.add_row("Realized P&L", money(s["realized_pnl"]) + (f"  ({pct(s['realized_pnl'] / s['cost_settled'])} on capital)" if s["cost_settled"] else ""))
    return t


def trades_table(rows, title: str = "Paper trades") -> Table:
    t = Table(title=title, expand=False)
    for c in ("ID", "Time", "Kind", "Status", "Qty", "Cost", "Payout", "P&L", "Description"):
        t.add_column(c)
    for r in rows:
        t.add_row(str(r["id"]), r["ts"][5:16], r["kind"], r["status"], f"{r['qty']:.0f}", money(r["cost"]),
                  money(r["payout"] if r["payout"] is not None else r["payout_if_complete"]),
                  money(r["pnl"]) if r["pnl"] is not None else f"(exp {money(r['payout_if_complete'] - r['cost'])})",
                  (r["description"] or "")[:110])
    return t

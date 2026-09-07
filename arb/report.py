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
    t = Table(title="Vegas (de-vigged) vs prediction market", expand=False)
    for c in ("League", "Game", "Team", "Book", "ML", "Fair p", "Venue", "PM bid", "PM ask", "Edge buy", "Edge sell"):
        t.add_column(c)
    rows = [r for r in rows if max(abs(r.edge_buy or 0), abs(r.edge_sell or 0)) >= min_abs_edge]
    rows.sort(key=lambda r: -max(r.edge_buy or -9, r.edge_sell or -9))
    for r in rows[:limit]:
        t.add_row(r.league.upper(), r.game, r.team, r.bookmaker, f"{r.american:+d}", f"{r.fair:.3f}", r.venue,
                  price(r.pm_bid), price(r.pm_ask), pct(r.edge_buy), pct(r.edge_sell))
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

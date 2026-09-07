"""Command-line entry point: `arb <command>`."""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path

from rich.table import Table

from . import report
from .config import DEFAULT_LEAGUES, LEAGUES, Settings
from .espn import Espn
from .kalshi import Kalshi
from .polymarket import Polymarket
from .report import console
from .scan import Scanner
from .settle import Settler
from .store import Store


def _leagues(arg: str | None) -> list[str]:
    from .config import NICHE_LEAGUES
    if not arg or arg == "all":
        return list(DEFAULT_LEAGUES)
    if arg == "niche":
        return list(NICHE_LEAGUES)
    if arg == "everything":
        return list(DEFAULT_LEAGUES) + list(NICHE_LEAGUES)
    out = [x.strip().lower() for x in arg.split(",") if x.strip()]
    bad = [x for x in out if x not in LEAGUES]
    if bad:
        sys.exit(f"unknown league(s): {bad}. Known: {', '.join(LEAGUES)}")
    return out


def _settings(args) -> Settings:
    s = Settings.from_env()
    if getattr(args, "min_margin", None) is not None:
        s.min_margin = args.min_margin
    if getattr(args, "max_per_trade", None) is not None:
        s.max_per_trade = args.max_per_trade
    return s


def _print_scan(res, args, show_games: bool = True, show_vegas: bool = True, show_opps: bool = True) -> None:
    if show_games and res.games:
        console.print(report.games_table(res.games))
    if res.games:
        console.print(report.league_gap_table(res.games))
    if res.unmatched_notes and args.verbose:
        for n in res.unmatched_notes[:40]:
            console.print(f"[dim]{n}")
    if show_opps:
        if res.opportunities:
            console.print(report.opps_table(res.opportunities))
        else:
            console.print("[bold]No risk-free arbitrage found net of taker fees in this scan.[/bold]")
        if res.taken:
            console.print(f"[green]Paper-traded {len(res.taken)} opportunit{'y' if len(res.taken) == 1 else 'ies'} "
                          f"for {sum(t['cost'] for t in res.taken):,.2f} outlay, locked-in profit "
                          f"{sum(t['profit'] for t in res.taken):,.2f}")
    if show_vegas and res.discrepancies:
        console.print(report.discrepancy_table(res.discrepancies, limit=args.top, min_abs_edge=args.min_edge))
    console.print(f"[dim]scan #{res.scan_id}: {res.n_kalshi} Kalshi / {res.n_poly} Polymarket outcomes, "
                  f"{len(res.games)} games, {len(res.pairs)} non-game pairs, {len(res.opportunities)} opps, "
                  f"{res.duration:.1f}s")


def cmd_scan(args) -> None:
    s = _settings(args)
    sc = Scanner(s, _leagues(args.leagues), scope=args.scope, paper=not args.no_paper, quiet=args.quiet,
                 general_min_score=args.general_threshold, include_live=args.include_live)
    res = sc.run()
    if args.json:
        print(json.dumps({
            "scan_id": res.scan_id, "games": len(res.games), "opportunities": [
                {"kind": o.kind, "key": o.match_key, "qty": o.qty, "cost": o.cost, "payout": o.payout,
                 "profit": o.profit, "margin": o.margin, "description": o.description,
                 "legs": [asdict(l) for l in o.legs]} for o in res.opportunities],
            "discrepancies": [d.as_dict() for d in res.discrepancies], "taken": res.taken,
        }, indent=1, default=str))
        return
    _print_scan(res, args, show_games=args.verbose or args.games)


def _write_status(store: Store, path: str, res, taken: list[dict], settled: dict | None, n: int) -> None:
    """Machine-readable snapshot for anything watching the always-on trader."""
    from datetime import datetime, timezone
    summ = store.summary()
    snap = {
        "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "scans_this_process": n,
        "bankroll": store_settings_bankroll,
        "cash_available": store_settings_bankroll - summ["deployed"] + summ["realized_pnl"],
        "deployed": summ["deployed"],
        "realized_pnl": summ["realized_pnl"],
        "locked_in_profit_open": summ["expected_open_profit"],
        "simulated_total_profit": summ["realized_pnl"] + summ["expected_open_profit"],
        "trades_open": summ["trades_open"], "trades_settled": summ["trades_settled"],
        "settled_wins": summ["settled_wins"],
        "opportunities_logged": summ["opportunities"],
        "last_scan": {"id": res.scan_id, "games": len(res.games), "kalshi_outcomes": res.n_kalshi, "poly_outcomes": res.n_poly,
                      "opportunities": len(res.opportunities), "taken": taken, "seconds": round(res.duration, 1)},
        "last_settlement": settled,
    }
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(path).with_suffix(".tmp")
    tmp.write_text(json.dumps(snap, indent=1, default=str))
    tmp.replace(path)
    txt = (f"{snap['updated']}  scan #{res.scan_id}: {len(res.games)} games, {len(res.opportunities)} opps, {len(taken)} taken | "
           f"open {summ['trades_open']} (${summ['deployed']:,.0f} deployed, ${summ['expected_open_profit']:+,.2f} locked in) | "
           f"settled {summ['trades_settled']} realized ${summ['realized_pnl']:+,.2f} | simulated total ${snap['simulated_total_profit']:+,.2f}")
    Path(path).with_suffix(".txt").write_text(txt + "\n")


store_settings_bankroll = 10_000.0


def cmd_run(args) -> None:
    global store_settings_bankroll
    s = _settings(args)
    store_settings_bankroll = s.bankroll
    leagues = _leagues(args.leagues)
    console.print(f"[bold]Paper-trading loop[/bold] every {args.interval}s on {len(leagues)} leagues (scope={args.scope}). Ctrl-C to stop.")
    n = 0
    while True:
        n += 1
        settled = None
        try:
            sc = Scanner(s, leagues, scope=args.scope, paper=not args.no_paper, quiet=True,
                         general_min_score=args.general_threshold, include_live=args.include_live)
            res = sc.run()
            console.rule(f"scan {n} (#{res.scan_id}) {time.strftime('%H:%M:%S')}")
            _print_scan(res, args, show_games=False, show_vegas=args.verbose)
            if n % max(1, args.settle_every) == 0:
                settled = Settler(sc.store, sc.kalshi, sc.poly, sc.espn).run(verbose=False)
                if settled["settled"]:
                    console.print(f"[cyan]settled {settled['settled']} trades, P&L {settled['pnl']:+,.2f}")
            if args.status_file:
                _write_status(sc.store, args.status_file, res, res.taken, settled, n)
        except KeyboardInterrupt:
            raise
        except Exception as e:
            console.print(f"[red]scan failed: {e!r}")
        if args.max_scans and n >= args.max_scans:
            break
        try:
            time.sleep(args.interval)
        except KeyboardInterrupt:
            break
    console.print(report.summary_table(Store(s.db_path)))


def cmd_vegas(args) -> None:
    s = _settings(args)
    sc = Scanner(s, _leagues(args.leagues), scope="sports", paper=False, quiet=args.quiet, include_live=args.include_live)
    res = sc.run()
    if args.json:
        print(json.dumps([d.as_dict() for d in res.discrepancies], indent=1, default=str))
        return
    games = [g for g in res.games if g.sportsbook]
    if games:
        console.print(report.games_table(games, title="Games with a sportsbook line"))
    vegas_opps = [o for o in res.opportunities if o.kind == "vegas"]
    if vegas_opps:
        console.print(report.opps_table(vegas_opps, title="Sportsbook + prediction-market hedges (risk-free)"))
    else:
        console.print("[bold]No risk-free sportsbook/prediction-market hedge found.[/bold]")
    if res.discrepancies:
        console.print(report.discrepancy_table(res.discrepancies, limit=args.top, min_abs_edge=args.min_edge))
    else:
        console.print("No games with both a sportsbook line and a prediction-market quote right now.")


def cmd_match(args) -> None:
    s = _settings(args)
    sc = Scanner(s, _leagues(args.leagues), scope=args.scope, paper=False, quiet=args.quiet)
    res = sc.run()
    console.print(report.games_table(res.games))
    console.print(report.league_gap_table(res.games))
    for n in res.unmatched_notes[:60]:
        console.print(f"[dim]{n}")
    if args.scope == "all":
        t = Table(title="Non-game fuzzy pair candidates (review, then add to data/pairs.yaml)")
        for c in ("Score", "Kalshi ticker", "Kalshi", "Polymarket", "K ask", "P ask", "Gap"):
            t.add_column(c)
        for c in res.pairs[:args.top]:
            from .arbitrage import top_of_book_gap
            gap = top_of_book_gap(c.kalshi, c.polymarket, c.relation)
            t.add_row(f"{c.score:.0f}", c.kalshi.id, f"{c.kalshi.event_title} / {c.kalshi.title}"[:70],
                      c.polymarket.meta.get("question", c.polymarket.title)[:70],
                      report.price(c.kalshi.yes_ask), report.price(c.polymarket.yes_ask), report.pct(gap))
        console.print(t)


def cmd_live(args) -> None:
    from .live import LiveSampler
    s = _settings(args)
    ls = LiveSampler(s, _leagues(args.leagues), interval=args.interval, pregame_hours=args.pregame_hours,
                     rematch_every=args.rematch_every, quiet=args.quiet, max_games=args.max_games)
    console.print(f"[bold]Live sampler[/bold] every {args.interval}s on {', '.join(ls.leagues)}"
                  f"{' for ' + str(args.duration) + 's' if args.duration else ' until Ctrl-C'}; games within {args.pregame_hours}h are the pre-game control.")
    n = ls.run(duration=args.duration)
    console.print(f"recorded {n} ticks in session #{ls.session_id}")
    from .live import analyze
    from .live_report import print_live_analysis
    print_live_analysis(analyze(ls.store, ls.session_id))


def cmd_live_report(args) -> None:
    from .live import analyze
    from .live_report import print_live_analysis
    s = _settings(args)
    sessions = [int(x) for x in args.sessions.split(",")] if args.sessions else None
    a = analyze(Store(s.db_path), args.session, sessions)
    if args.json:
        print(json.dumps(a, indent=1, default=str))
        return
    print_live_analysis(a)


def cmd_backtest(args) -> None:
    from .backtest import Backtester
    s = _settings(args)
    bt = Backtester(s, _leagues(args.leagues), days=args.days, poly_spread=args.poly_spread, size=args.size,
                    quiet=args.quiet, max_games=args.max_games)
    res = bt.run()
    if args.json:
        print(json.dumps(res, indent=1, default=str))
        return
    t = Table(title=f"Historical replay, last {res['days']} days: {res['games_matched']} matched games, "
                    f"size {res['size']:.0f} contracts per signal, Polymarket spread assumed {res['poly_spread'] * 100:.0f}¢")
    for c in ("League", "Games", "Legs", "Minutes pre / live", "|mid gap| pre / live", "gross cross % pre / live",
              "net % pre / live", "1-min signals pre / live", "Profit (1-min) pre / live", "Persistent (≥2 min) pre / live",
              "Profit (persistent) pre / live", "Best margin"):
        t.add_column(c)

    def row(name, a):
        f = lambda x, fmt="{:.1f}": "-" if x is None else fmt.format(x)
        t.add_row(name, str(a["games"]), str(a["legs"]), f"{a['pre_minutes']:,} / {a['live_minutes']:,}",
                  f"{f(a['mean_mid_gap_pre'], '{:.3f}')} / {f(a['mean_mid_gap_live'], '{:.3f}')}",
                  f"{f(a['gross_pct_pre'])}% / {f(a['gross_pct_live'])}%", f"{f(a['net_pct_pre'], '{:.2f}')}% / {f(a['net_pct_live'], '{:.2f}')}%",
                  f"{a['episodes_pre']} / {a['episodes_live']}", f"${a['profit_pre']:,.0f} / ${a['profit_live']:,.0f}",
                  f"{a['persist_pre']} / {a['persist_live']}", f"${a['persist_profit_pre']:,.0f} / ${a['persist_profit_live']:,.0f}",
                  f"{a['best_margin'] * 100:.2f}%")
    for lg, a in res["by_league"].items():
        row(LEAGUES[lg].name if lg in LEAGUES else lg, a)
    row("All", res["total"])
    console.print(t)
    tot = res["total"]
    console.print(f"[bold]Simulated profit, zero-latency taker: ${tot['profit_pre'] + tot['profit_live']:,.2f}[/bold] over {res['days']} days "
                  f"({res['games_with_any_profit']} of {res['games_total']} games produced any signal), every one-minute signal filled for "
                  f"up to {res['size']:.0f} contracts, capped by what both venues traded in the previous three minutes "
                  f"({tot.get('unbacked', 0):,} signal-minutes dropped for no volume on one side).  "
                  f"[bold]Signals that persisted a second minute: ${tot['persist_profit_pre'] + tot['persist_profit_live']:,.2f}[/bold]. "
                  f"Replay took {res['seconds']:.0f}s.")
    if res["top_signals"]:
        t = Table(title="Largest signals")
        for c in ("League", "Game", "Team", "When (UTC)", "Phase", "Kalshi bid/ask", "Poly price", "Traded 3 min K / P", "Side", "Gross", "Net margin", "Size", "Profit"):
            t.add_column(c)
        for x in res["top_signals"][:12]:
            when = time.strftime("%m-%d %H:%M", time.gmtime(x["ts"]))
            t.add_row(x["league"].upper(), x["game"].split(":")[1], x["team"], when, "live" if x["live"] else "pre",
                      f"{x['k_bid']:.2f}/{x['k_ask']:.2f}", f"{x['p_mid']:.3f}", f"{x.get('k_traded', 0):.0f} / {x.get('p_traded', 0):.0f}", x["side"],
                      f"{x['gross'] * 100:+.1f}¢", f"{x['net'] * 100:+.2f}%", f"{x.get('size', 0):.0f}", f"${x.get('profit', 0):,.2f}")
        console.print(t)


def cmd_settle(args) -> None:
    s = _settings(args)
    store = Store(s.db_path)
    st = Settler(store, Kalshi(s), Polymarket(s), Espn(s)).run()
    console.print(f"settled {st['settled']} trades, {st['still_open']} still open, P&L from this pass {st['pnl']:+,.2f}")
    for d in st["details"]:
        console.print(f"  #{d['trade_id']} {d['pnl']:+.2f}  {d['description'][:100]}")
    console.print(report.summary_table(store))


def cmd_report(args) -> None:
    s = _settings(args)
    store = Store(s.db_path)
    if args.json:
        print(json.dumps(store.summary(), indent=1, default=str))
        return
    console.print(report.summary_table(store))
    console.print(report.trades_table(store.trades(limit=args.top)))
    last = store.last_scan()
    if last:
        discs = store.discrepancies_for_scan(int(last["id"]))
        if discs:
            from .vegas import Discrepancy
            rows = [Discrepancy(r["match_key"], r["league"], r["game"], r["team"], r["venue"], r["bookmaker"], r["american"],
                                r["implied"], r["fair"], r["fair_power"], r["pm_bid"], r["pm_ask"], r["edge_buy"], r["edge_sell"]) for r in discs]
            console.print(report.discrepancy_table(rows, limit=args.top, min_abs_edge=args.min_edge))


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="arb", description="Simulated Kalshi / Polymarket / sportsbook arbitrage scanner (paper trading only).")
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp, scope=True):
        sp.add_argument("--leagues", default=None, help="comma list, e.g. nfl,mlb; or 'all' (major leagues, default), 'niche' (tennis, esports, KBO/NPB/KHL, 2nd-tier soccer), 'everything'")
        if scope:
            sp.add_argument("--scope", choices=["sports", "all"], default="sports",
                            help="sports: game markets only (fast). all: also crawl every open market on both venues and fuzzy-match non-sports events")
        sp.add_argument("--min-margin", type=float, default=None, help="min net margin to count as an arb (default 0.005)")
        sp.add_argument("--max-per-trade", type=float, default=None, help="max $ outlay per paper trade (default 500)")
        sp.add_argument("--min-edge", type=float, default=0.0, help="hide vegas discrepancies smaller than this")
        sp.add_argument("--top", type=int, default=25)
        sp.add_argument("--general-threshold", type=float, default=None, help="(scope=all) also trade unconfirmed fuzzy pairs scoring at least this (e.g. 97); default: confirmed pairs only")
        sp.add_argument("--include-live", action="store_true", help="also evaluate games that have already started")
        sp.add_argument("--quiet", action="store_true")
        sp.add_argument("--verbose", "-v", action="store_true")
        sp.add_argument("--json", action="store_true")

    sp = sub.add_parser("scan", help="one pass: fetch, match, detect, paper-trade")
    common(sp)
    sp.add_argument("--no-paper", action="store_true", help="detect only, do not record paper trades")
    sp.add_argument("--games", action="store_true", help="print the matched-games table")
    sp.set_defaults(fn=cmd_scan)

    sp = sub.add_parser("run", help="loop scan every N seconds")
    common(sp)
    sp.add_argument("--interval", type=int, default=120)
    sp.add_argument("--max-scans", type=int, default=0)
    sp.add_argument("--settle-every", type=int, default=10, help="run settlement every N scans")
    sp.add_argument("--no-paper", action="store_true")
    sp.add_argument("--status-file", default="data/status.json", help="JSON snapshot rewritten after every scan ('' to disable)")
    sp.set_defaults(fn=cmd_run)

    sp = sub.add_parser("vegas", help="sportsbook vs Kalshi/Polymarket comparison")
    common(sp, scope=False)
    sp.set_defaults(fn=cmd_vegas)

    sp = sub.add_parser("match", help="show how markets were matched across venues")
    common(sp)
    sp.set_defaults(fn=cmd_match)

    sp = sub.add_parser("live", help="sample Kalshi vs Polymarket every few seconds for games in progress")
    common(sp, scope=False)
    sp.add_argument("--interval", type=float, default=5.0)
    sp.add_argument("--duration", type=float, default=0.0, help="seconds to run (0 = until Ctrl-C)")
    sp.add_argument("--pregame-hours", type=float, default=6.0, help="also sample games starting within N hours as a control")
    sp.add_argument("--rematch-every", type=float, default=600.0, help="seconds between re-matching games across venues")
    sp.add_argument("--max-games", type=int, default=24, help="cap on tracked games (live first, then soonest)")
    sp.set_defaults(fn=cmd_live)

    sp = sub.add_parser("live-report", help="analyse recorded live samples: pre-game vs in-game decoupling")
    common(sp, scope=False)
    sp.add_argument("--session", type=int, default=None)
    sp.add_argument("--sessions", default=None, help="comma list of session ids to combine")
    sp.set_defaults(fn=cmd_live_report)

    sp = sub.add_parser("backtest", help="replay past games minute by minute from Kalshi candles and Polymarket price history")
    common(sp, scope=False)
    sp.add_argument("--days", type=int, default=14)
    sp.add_argument("--poly-spread", type=float, default=0.02, help="assumed Polymarket bid-ask spread (history is a mid price)")
    sp.add_argument("--size", type=float, default=50.0, help="contracts filled per signal on each venue")
    sp.add_argument("--max-games", type=int, default=None, help="cap games per league (most recent)")
    sp.set_defaults(fn=cmd_backtest)

    sp = sub.add_parser("settle", help="settle open paper trades against resolutions")
    common(sp, scope=False)
    sp.set_defaults(fn=cmd_settle)

    sp = sub.add_parser("report", help="paper-trading P&L report")
    common(sp, scope=False)
    sp.set_defaults(fn=cmd_report)

    args = p.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()

"""Build docs/report.html (the shareable write-up) from the ledger database.

Every number in the page is computed here from data/paper.db so the report can
be regenerated after more scans or live sessions:

    .venv/bin/python docs/build_report.py [--live-sessions 3,4] [--out docs/report.html]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path
from string import Template

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from arb.config import DEFAULT_LEAGUES, LEAGUES, NICHE_LEAGUES, Settings  # noqa: E402
from arb.live import analyze  # noqa: E402
from arb.odds import american_to_decimal, devig_multiplicative, devig_power  # noqa: E402
from arb.store import Store  # noqa: E402

TEMPLATE = Path(__file__).with_name("report_template.html")


# ----------------------------------------------------------------------------- data
def latest_scan_for(db, leagues: list[str]) -> int | None:
    key = ",".join(leagues)
    r = db.execute("SELECT id FROM scans WHERE leagues=? AND n_games IS NOT NULL ORDER BY id DESC LIMIT 1", (key,)).fetchone()
    return int(r[0]) if r else None


def tightness(db, scan_id: int) -> tuple[list[dict], dict]:
    rows = db.execute("SELECT * FROM quotes WHERE scan_id=?", (scan_id,)).fetchall()
    by: dict[tuple, dict] = {}
    for r in rows:
        by.setdefault((r["match_key"], r["team"]), {})[r["venue"]] = r
    per: dict[str, dict] = {}
    for (mk, team), v in by.items():
        if "kalshi" not in v or "polymarket" not in v:
            continue
        k, p = v["kalshi"], v["polymarket"]
        d = per.setdefault(k["league"], {"league": k["league"], "legs": 0, "games": set(), "md": [], "ks": [], "ps": [], "gaps": [], "kd": [], "pd": []})
        d["legs"] += 1
        d["games"].add(mk)
        if None not in (k["yes_bid"], k["yes_ask"], p["yes_bid"], p["yes_ask"]):
            d["md"].append(abs((k["yes_bid"] + k["yes_ask"]) / 2 - (p["yes_bid"] + p["yes_ask"]) / 2))
            d["ks"].append(k["yes_ask"] - k["yes_bid"])
            d["ps"].append(p["yes_ask"] - p["yes_bid"])
            d["gaps"].append(max(p["yes_bid"] - k["yes_ask"], k["yes_bid"] - p["yes_ask"]))
        if k["ask_size"]:
            d["kd"].append(k["ask_size"])
        if p["ask_size"]:
            d["pd"].append(p["ask_size"])
    out = []
    tot = {"legs": 0, "games": set(), "md": [], "ks": [], "ps": [], "gaps": [], "kd": [], "pd": []}
    for lg, d in per.items():
        for kk in ("md", "ks", "ps", "gaps", "kd", "pd"):
            tot[kk] += d[kk]
        tot["legs"] += d["legs"]
        tot["games"] |= d["games"]
        if not d["md"]:
            continue
        out.append({
            "league": lg, "name": LEAGUES[lg].name if lg in LEAGUES else lg, "legs": d["legs"], "games": len(d["games"]),
            "mean_md": statistics.fmean(d["md"]), "max_md": max(d["md"]), "k_spread": statistics.fmean(d["ks"]),
            "p_spread": statistics.fmean(d["ps"]), "cross": sum(1 for g in d["gaps"] if g > 0), "n_gaps": len(d["gaps"]),
            "best": max(d["gaps"]), "k_depth": statistics.median(d["kd"]) if d["kd"] else 0,
            "p_depth": statistics.median(d["pd"]) if d["pd"] else 0,
        })
    out.sort(key=lambda x: -x["legs"])
    total = {
        "legs": tot["legs"], "games": len(tot["games"]), "mean_md": statistics.fmean(tot["md"]) if tot["md"] else 0,
        "max_md": max(tot["md"]) if tot["md"] else 0, "k_spread": statistics.fmean(tot["ks"]) if tot["ks"] else 0,
        "p_spread": statistics.fmean(tot["ps"]) if tot["ps"] else 0, "cross": sum(1 for g in tot["gaps"] if g > 0),
        "n_gaps": len(tot["gaps"]), "best": max(tot["gaps"]) if tot["gaps"] else 0,
        "k_depth": statistics.median(tot["kd"]) if tot["kd"] else 0, "p_depth": statistics.median(tot["pd"]) if tot["pd"] else 0,
        "within_1c": 100 * sum(1 for x in tot["md"] if x <= 0.01 + 1e-9) / len(tot["md"]) if tot["md"] else 0,
        "within_2c": 100 * sum(1 for x in tot["md"] if x <= 0.02 + 1e-9) / len(tot["md"]) if tot["md"] else 0,
    }
    return out, total


def vegas(db) -> dict:
    r = db.execute("SELECT scan_id FROM discrepancies WHERE fair_power IS NOT NULL ORDER BY id DESC LIMIT 1").fetchone()
    if not r:
        return {"rows": [], "n": 0}
    sid = int(r[0])
    rows = [dict(x) for x in db.execute("SELECT * FROM discrepancies WHERE scan_id=?", (sid,))]
    per: dict[tuple, list] = {}
    games: dict[str, dict] = {}
    for x in rows:
        games.setdefault(x["match_key"], {})[x["team"]] = x
        if x["pm_bid"] is None or x["pm_ask"] is None or x["fair_power"] is None:
            continue
        mid = (x["pm_bid"] + x["pm_ask"]) / 2
        per.setdefault((x["league"], x["venue"]), []).append((mid - x["fair"], mid - x["fair_power"]))
    table = []
    allm, allp = [], []
    for (lg, v), xs in sorted(per.items()):
        m = [a for a, _ in xs]
        pw = [b for _, b in xs]
        allm += m
        allp += pw
        table.append({"league": LEAGUES[lg].name if lg in LEAGUES else lg, "venue": v, "n": len(xs),
                      "mult_mean": statistics.fmean(m), "mult_abs": statistics.fmean(map(abs, m)),
                      "pow_mean": statistics.fmean(pw), "pow_abs": statistics.fmean(map(abs, pw))})
    inside = total = 0
    for mk, teams in games.items():
        if len(teams) != 2:
            continue
        (t1, r1), (t2, r2) = list(teams.items())
        for x, other in ((r1, r2), (r2, r1)):
            if x["pm_bid"] is None or x["pm_ask"] is None:
                continue
            mid = (x["pm_bid"] + x["pm_ask"]) / 2
            total += 1
            inside += (1 - other["implied"]) <= mid <= x["implied"]
    # favourites only, both methods
    fav = []
    for mk, teams in games.items():
        if len(teams) != 2:
            continue
        (t1, r1), (t2, r2) = list(teams.items())
        f = r1 if r1["implied"] > r2["implied"] else r2
        if f["pm_bid"] is None or f["pm_ask"] is None or f["fair_power"] is None:
            continue
        mid = (f["pm_bid"] + f["pm_ask"]) / 2
        fav.append((mid - f["fair"], mid - f["fair_power"]))
    return {
        "scan_id": sid, "rows": table, "n": len(allm), "games": len(games),
        "all_mult": statistics.fmean(allm) if allm else 0, "all_mult_abs": statistics.fmean(map(abs, allm)) if allm else 0,
        "all_pow": statistics.fmean(allp) if allp else 0, "all_pow_abs": statistics.fmean(map(abs, allp)) if allp else 0,
        "inside": inside, "inside_total": total,
        "fav_n": len(fav), "fav_mult_above": sum(1 for a, _ in fav if a > 0), "fav_pow_above": sum(1 for _, b in fav if b > 0),
        "fav_mult_mean": statistics.fmean(a for a, _ in fav) if fav else 0, "fav_pow_mean": statistics.fmean(b for _, b in fav) if fav else 0,
        "worst": sorted([x for x in rows if x["pm_bid"] is not None and x["pm_ask"] is not None and x["fair_power"] is not None],
                        key=lambda x: -abs((x["pm_bid"] + x["pm_ask"]) / 2 - x["fair_power"]))[:6],
    }


def series(db, sessions: list[int], team_name: str) -> list[dict]:
    marks = ",".join("?" * len(sessions))
    rows = db.execute(f"SELECT ts, phase, status, k_bid, k_ask, k_list_bid, k_list_ask, p_bid, p_ask, gross_gap, net_margin, k_ask_sz, p_ask_sz"
                      f" FROM ticks WHERE session IN ({marks}) AND team_name=? ORDER BY ts", (*sessions, team_name)).fetchall()
    out = []
    for r in rows:
        if None in (r["k_bid"], r["k_ask"], r["p_bid"], r["p_ask"]):
            continue
        lg = None
        if r["k_list_bid"] is not None and r["k_list_ask"] is not None:
            lg = max(r["p_bid"] - r["k_list_ask"], r["k_list_bid"] - r["p_ask"])
        out.append({"t": r["ts"], "ph": r["phase"], "st": r["status"] or "", "kb": r["k_bid"], "ka": r["k_ask"],
                    "lb": r["k_list_bid"], "la": r["k_list_ask"], "pb": r["p_bid"], "pa": r["p_ask"],
                    "gap": r["gross_gap"], "lgap": lg, "net": r["net_margin"], "ks": r["k_ask_sz"], "ps": r["p_ask_sz"]})
    return out


# ----------------------------------------------------------------------------- formatting
def c(x, nd=1) -> str:
    """cents"""
    return "-" if x is None else f"{x * 100:.{nd}f}¢"


def pct(x, nd=1) -> str:
    return "-" if x is None else f"{x:.{nd}f}%"


def num(x) -> str:
    return f"{x:,.0f}"


def tight_table(rows: list[dict], total: dict, cap: int | None = None) -> str:
    rs = rows[:cap] if cap else rows
    body = "".join(
        f"<tr><td>{r['name']}</td><td class=n>{r['legs']}</td><td class=n>{c(r['mean_md'])}</td><td class=n>{c(r['max_md'], 0)}</td>"
        f"<td class=n>{c(r['k_spread'])}</td><td class=n>{c(r['p_spread'])}</td><td class=n>{r['cross']}/{r['n_gaps']}</td>"
        f"<td class=n>{'+' if r['best'] > 0 else ''}{c(r['best'], 0)}</td><td class=n>{num(r['k_depth'])} / {num(r['p_depth'])}</td></tr>"
        for r in rs)
    foot = (f"<tr class=total><td>All ({total['games']} games)</td><td class=n>{total['legs']}</td><td class=n>{c(total['mean_md'])}</td>"
            f"<td class=n>{c(total['max_md'], 0)}</td><td class=n>{c(total['k_spread'])}</td><td class=n>{c(total['p_spread'])}</td>"
            f"<td class=n>{total['cross']}/{total['n_gaps']}</td><td class=n>{'+' if total['best'] > 0 else ''}{c(total['best'], 0)}</td>"
            f"<td class=n>{num(total['k_depth'])} / {num(total['p_depth'])}</td></tr>")
    return ("<table><thead><tr><th>League</th><th class=n>Legs</th><th class=n>Mean |mid gap|</th><th class=n>Max</th>"
            "<th class=n>Kalshi spread</th><th class=n>Poly spread</th><th class=n>Best prices cross</th><th class=n>Best crossing</th>"
            "<th class=n>Size at best (K / P)</th></tr></thead><tbody>" + body + foot + "</tbody></table>")


def vegas_table(v: dict) -> str:
    body = "".join(
        f"<tr><td>{r['league']}</td><td>{r['venue'].title()}</td><td class=n>{r['n']}</td>"
        f"<td class=n>{r['mult_mean'] * 100:+.2f} / {r['mult_abs'] * 100:.2f}</td><td class=n>{r['pow_mean'] * 100:+.2f} / {r['pow_abs'] * 100:.2f}</td></tr>"
        for r in v["rows"])
    foot = (f"<tr class=total><td>All</td><td></td><td class=n>{v['n']}</td><td class=n>{v['all_mult'] * 100:+.2f} / {v['all_mult_abs'] * 100:.2f}</td>"
            f"<td class=n>{v['all_pow'] * 100:+.2f} / {v['all_pow_abs'] * 100:.2f}</td></tr>")
    return ("<table><thead><tr><th>League</th><th>Venue</th><th class=n>n</th><th class=n>PM mid − fair, proportional<br><span class=sub>mean / mean |·| (points)</span></th>"
            "<th class=n>PM mid − fair, power<br><span class=sub>mean / mean |·| (points)</span></th></tr></thead><tbody>" + body + foot + "</tbody></table>")


def phase_table(a: dict) -> str:
    order = ["major/pre", "major/live", "niche/pre", "niche/live"]
    rows = []
    for k in order:
        v = a["phases_by_group"].get(k)
        if not v:
            continue
        grp, ph = k.split("/")
        rows.append(f"<tr><td>{grp.title()} leagues</td><td>{'in game' if ph == 'live' else 'before the game'}</td><td class=n>{v['samples']:,}</td><td class=n>{v['games']}</td>"
                    f"<td class=n>{c(v['mean_abs_mid_diff'])}</td><td class=n>{c(v['p90_abs_mid_diff'])}</td><td class=n>{c(v['max_abs_mid_diff'], 0)}</td>"
                    f"<td class=n>{pct(v['pct_gross_gap_positive'])}</td><td class=n>{pct(v.get('phantom_gap_pct'))}</td>"
                    f"<td class=n>{pct(v['pct_net_arb'], 2)}</td><td class=n>{c(v['mean_kalshi_spread'])} / {c(v['mean_poly_spread'])}</td></tr>")
    return ("<table><thead><tr><th>Group</th><th>Phase</th><th class=n>Samples</th><th class=n>Games</th><th class=n>Mean |mid gap|</th><th class=n>p90</th><th class=n>Max</th>"
            "<th class=n>Books cross</th><th class=n>Phantom (list only)</th><th class=n>Net arb after fees</th><th class=n>Spreads K / P</th></tr></thead><tbody>"
            + "".join(rows) + "</tbody></table>")


def episodes_table(a: dict) -> str:
    e = a["episodes"]
    rows = []
    for kind, label in (("gross", "Best prices cross (before fees)"), ("net", "Profitable after fees and depth")):
        for ph in ("pre", "live"):
            v = e.get(f"{kind}_{ph}")
            if not v:
                continue
            rows.append(f"<tr><td>{label}</td><td>{'in game' if ph == 'live' else 'before the game'}</td><td class=n>{v['count']}</td><td class=n>{v['single_tick']}</td>"
                        f"<td class=n>{'-' if v['median_seconds'] is None else f'{v[chr(109)+chr(101)+chr(100)+chr(105)+chr(97)+chr(110)+chr(95)+chr(115)+chr(101)+chr(99)+chr(111)+chr(110)+chr(100)+chr(115)]:.0f}'}</td>"
                        f"<td class=n>{'-' if v['max_seconds'] is None else f'{v[chr(109)+chr(97)+chr(120)+chr(95)+chr(115)+chr(101)+chr(99)+chr(111)+chr(110)+chr(100)+chr(115)]:.0f}'}</td>"
                        f"<td class=n>{'-' if v['peak'] is None else (c(v['peak'], 1) if kind == 'gross' else pct(v['peak'] * 100, 2))}</td></tr>")
    return ("<table><thead><tr><th>Window</th><th>Phase</th><th class=n>Episodes</th><th class=n>One tick only</th><th class=n>Median length (s)</th>"
            "<th class=n>Longest (s)</th><th class=n>Peak</th></tr></thead><tbody>" + "".join(rows) + "</tbody></table>")


def net_list(a: dict) -> str:
    xs = a["episodes"].get("net_list") or []
    if not xs:
        return "<p class=muted>No fee-positive window was recorded.</p>"
    rows = "".join(f"<tr><td>{x['team_name']}</td><td>{x['phase']}</td><td class=mono>{x['start'][11:19]}Z</td><td class=n>{x['ticks']}</td>"
                   f"<td class=n>{x['seconds']:.0f}</td><td class=n>{x['peak'] * 100:+.2f}%</td><td>{x['status'] or ''}</td></tr>" for x in xs[:8])
    return ("<table><thead><tr><th>Outcome</th><th>Phase</th><th>Start (UTC)</th><th class=n>Ticks</th><th class=n>Seconds</th><th class=n>Peak margin</th><th>Game state</th></tr></thead><tbody>"
            + rows + "</tbody></table>")


# ----------------------------------------------------------------------------- main
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live-sessions", default="3,4")
    ap.add_argument("--series-team", default="Dodgers")
    ap.add_argument("--out", default=str(Path(__file__).with_name("report.html")))
    args = ap.parse_args()
    s = Settings.from_env()
    store = Store(s.db_path)
    db = store.db
    sessions = [int(x) for x in args.live_sessions.split(",") if x]

    maj_id = latest_scan_for(db, DEFAULT_LEAGUES)
    nic_id = latest_scan_for(db, NICHE_LEAGUES)
    maj_rows, maj_tot = tightness(db, maj_id) if maj_id else ([], {})
    nic_rows, nic_tot = tightness(db, nic_id) if nic_id else ([], {})
    v = vegas(db)
    a = analyze(store, sessions=sessions)
    ser = series(db, sessions, args.series_team)
    summ = store.summary()

    live = a["phases_by_group"].get("major/live", {})
    live_n = a["phases_by_group"].get("niche/live", {})
    pre_n = a["phases_by_group"].get("niche/pre", {})
    phantom_live = a["phases"].get("live", {}).get("phantom_gap_pct")
    real_live = a["phases"].get("live", {}).get("pct_gross_gap_positive")
    net_live = a["phases"].get("live", {}).get("pct_net_arb")
    ep = a["episodes"]
    sess_rows = [x for x in a["sessions"] if x["id"] in sessions]
    span = ""
    if sess_rows:
        t0 = min(x["started"] for x in sess_rows)
        t1 = max((x["ended"] or datetime.now(timezone.utc).isoformat(timespec="seconds")) for x in sess_rows)
        span = f"{t0[11:16]}–{t1[11:16]} UTC"

    bars = [{"g": "major", "name": r["name"], "v": r["mean_md"], "legs": r["legs"], "ks": r["k_spread"], "ps": r["p_spread"], "x": f"{r['cross']}/{r['n_gaps']}"} for r in maj_rows]
    bars += [{"g": "niche", "name": r["name"], "v": r["mean_md"], "legs": r["legs"], "ks": r["k_spread"], "ps": r["p_spread"], "x": f"{r['cross']}/{r['n_gaps']}"} for r in nic_rows]

    worst_rows = "".join(
        f"<tr><td>{x['league'].upper()}</td><td>{x['game']}</td><td>{x['team']}</td><td>{x['venue'].title()}</td><td class=n>{x['american']:+d}</td>"
        f"<td class=n>{x['fair']:.3f}</td><td class=n>{x['fair_power']:.3f}</td><td class=n>{x['pm_bid']:.2f} / {x['pm_ask']:.2f}</td>"
        f"<td class=n>{((x['pm_bid'] + x['pm_ask']) / 2 - x['fair_power']) * 100:+.1f}</td></tr>" for x in v.get("worst", []))

    data = {"series": ser, "bars": bars, "team": args.series_team}
    ctx = {
        "GEN_TS": datetime.now(timezone.utc).strftime("%-d %b %Y, %H:%M UTC"),
        "STAT_GAP": c(maj_tot.get("mean_md")), "STAT_WITHIN1": pct(maj_tot.get("within_1c"), 0),
        "STAT_LEGS": str(maj_tot.get("legs", 0)), "STAT_GAMES": str(maj_tot.get("games", 0)),
        "STAT_CROSS": f"{maj_tot.get('cross', 0)}/{maj_tot.get('n_gaps', 0)}", "STAT_BEST": c(maj_tot.get("best"), 0),
        "STAT_TRADES": str(summ["trades_open"] + summ["trades_settled"]),
        "STAT_LOCKED": f"${summ['expected_open_profit'] + summ['realized_pnl']:,.2f}",
        "STAT_DEPLOYED": f"${summ['deployed'] + summ['cost_settled']:,.0f}",
        "STAT_INSIDE": f"{v.get('inside', 0)} of {v.get('inside_total', 0)}",
        "STAT_PHANTOM": pct(phantom_live), "STAT_REAL": pct(real_live), "STAT_NET": pct(net_live, 2),
        "VEGAS_GAMES": str(v.get("games", 0)), "VEGAS_N": str(v.get("n", 0)),
        "VEGAS_ALL_MULT_ABS": f"{v.get('all_mult_abs', 0) * 100:.1f}", "VEGAS_ALL_POW_ABS": f"{v.get('all_pow_abs', 0) * 100:.1f}",
        "VEGAS_FAV_N": str(v.get("fav_n", 0)), "VEGAS_FAV_MULT_ABOVE": str(v.get("fav_mult_above", 0)), "VEGAS_FAV_POW_ABOVE": str(v.get("fav_pow_above", 0)),
        "VEGAS_FAV_MULT_MEAN": f"{v.get('fav_mult_mean', 0) * 100:+.1f}", "VEGAS_FAV_POW_MEAN": f"{v.get('fav_pow_mean', 0) * 100:+.1f}",
        "VEGAS_TABLE": vegas_table(v), "VEGAS_WORST": worst_rows,
        "MAJ_TABLE": tight_table(maj_rows, maj_tot), "NIC_TABLE": tight_table(nic_rows, nic_tot),
        "MAJ_WITHIN1": pct(maj_tot.get("within_1c"), 0), "MAJ_WITHIN2": pct(maj_tot.get("within_2c"), 0),
        "NIC_WITHIN1": pct(nic_tot.get("within_1c"), 0), "NIC_GAP": c(nic_tot.get("mean_md")), "NIC_LEGS": str(nic_tot.get("legs", 0)),
        "NIC_GAMES": str(nic_tot.get("games", 0)), "NIC_KSPREAD": c(nic_tot.get("k_spread")), "NIC_PSPREAD": c(nic_tot.get("p_spread")),
        "NIC_DEPTH": f"{num(nic_tot.get('k_depth', 0))} / {num(nic_tot.get('p_depth', 0))}", "NIC_CROSS": f"{nic_tot.get('cross', 0)}/{nic_tot.get('n_gaps', 0)}",
        "MAJ_KSPREAD": c(maj_tot.get("k_spread")), "MAJ_PSPREAD": c(maj_tot.get("p_spread")), "MAJ_DEPTH": f"{num(maj_tot.get('k_depth', 0))} / {num(maj_tot.get('p_depth', 0))}",
        "LIVE_SPAN": span, "LIVE_TICKS": f"{a['n_ticks']:,}", "LIVE_GAMES": str(len(a["games"])),
        "LIVE_MAJ_GAMES": str(live.get("games", 0)), "LIVE_MAJ_SAMPLES": f"{live.get('samples', 0):,}",
        "LIVE_MAJ_GAP": c(live.get("mean_abs_mid_diff")), "LIVE_MAJ_P90": c(live.get("p90_abs_mid_diff")),
        "LIVE_MAJ_CROSS": pct(live.get("pct_gross_gap_positive")), "LIVE_MAJ_PHANTOM": pct(live.get("phantom_gap_pct")),
        "LIVE_MAJ_NET": pct(live.get("pct_net_arb"), 2), "LIVE_MAJ_LISTDIFF": c(live.get("mean_list_book_diff")),
        "LIVE_NIC_GAP": c(live_n.get("mean_abs_mid_diff")), "LIVE_NIC_PRE_GAP": c(pre_n.get("mean_abs_mid_diff")),
        "LIVE_NIC_CROSS": pct(live_n.get("pct_gross_gap_positive")), "LIVE_NIC_PRE_CROSS": pct(pre_n.get("pct_gross_gap_positive")),
        "LIVE_NIC_NET": pct(live_n.get("pct_net_arb"), 2),
        "LIVE_NET_EP": str(ep.get("net_live", {}).get("count", 0)), "LIVE_NET_MED": "-" if not ep.get("net_live", {}).get("median_seconds") else f"{ep['net_live']['median_seconds']:.0f}",
        "LIVE_NET_MAX": "-" if not ep.get("net_live", {}).get("max_seconds") else f"{ep['net_live']['max_seconds']:.0f}",
        "LIVE_GROSS_EP": str(ep.get("gross_live", {}).get("count", 0)), "LIVE_GROSS_MED": "-" if not ep.get("gross_live", {}).get("median_seconds") else f"{ep['gross_live']['median_seconds']:.0f}",
        "PHASE_TABLE": phase_table(a), "EPISODE_TABLE": episodes_table(a), "NET_LIST": net_list(a),
        "SERIES_TEAM": args.series_team, "SERIES_N": str(len(ser)),
        "DATA_JSON": json.dumps(data, separators=(",", ":")).replace("</", "<\\/"),
    }
    html = Template(TEMPLATE.read_text()).safe_substitute(ctx)
    Path(args.out).write_text(html)
    print(f"wrote {args.out} ({len(html) / 1024:.0f} KB); majors scan {maj_id}, niche scan {nic_id}, vegas scan {v.get('scan_id')}, live sessions {sessions}, series points {len(ser)}")


if __name__ == "__main__":
    main()

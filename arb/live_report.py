"""Console rendering of the live-sampling analysis."""
from __future__ import annotations

from rich.table import Table

from .report import console, pct, price


def _f(x, fmt="{:.4f}"):
    return "-" if x is None else fmt.format(x)


def print_live_analysis(a: dict) -> None:
    if not a["n_ticks"]:
        console.print("No live ticks recorded yet. Run `arb live` while games are in progress.")
        return
    sess = a["sessions"]
    console.print(f"[bold]{a['n_ticks']} venue-pair samples across {len(sess)} session(s)[/bold] "
                  f"(latest: {sess[-1]['started']} .. {sess[-1]['ended'] or 'running'}, every {sess[-1]['interval_s']}s)")
    t = Table(title="Kalshi vs Polymarket by game phase (same team, same game)")
    for c in ("Phase", "Samples", "Games", "|mid diff| mean", "p50", "p90", "p99", "max", "gross gap>0", "gross gap≥2c",
              "net arb (after fees)", "net margin when arb", "K spread", "P spread"):
        t.add_column(c)
    for ph, v in a["phases"].items():
        t.add_row(ph, str(v["samples"]), str(v["games"]), _f(v["mean_abs_mid_diff"]), _f(v["p50_abs_mid_diff"]),
                  _f(v["p90_abs_mid_diff"]), _f(v["p99_abs_mid_diff"]), _f(v["max_abs_mid_diff"]),
                  _f(v["pct_gross_gap_positive"], "{:.1f}%"), _f(v["pct_gross_gap_ge_2c"], "{:.1f}%"),
                  _f(v["pct_net_arb"], "{:.2f}%"), pct(v["mean_net_margin_when_arb"]) if v["mean_net_margin_when_arb"] else "-",
                  _f(v["mean_kalshi_spread"]), _f(v["mean_poly_spread"]))
    console.print(t)
    if a.get("phases_by_group"):
        t = Table(title="Same, split by league group (major = ESPN-registry leagues, niche = tennis/esports/2nd-tier)")
        for c in ("Group / phase", "Samples", "Games", "|mid diff| mean", "p90", "gross gap>0", "net arb", "K spread", "P spread", "phantom (list only)"):
            t.add_column(c)
        for k, v in a["phases_by_group"].items():
            t.add_row(k, str(v["samples"]), str(v["games"]), _f(v["mean_abs_mid_diff"]), _f(v["p90_abs_mid_diff"]),
                      _f(v["pct_gross_gap_positive"], "{:.1f}%"), _f(v["pct_net_arb"], "{:.2f}%"), _f(v["mean_kalshi_spread"]),
                      _f(v["mean_poly_spread"]), _f(v.get("phantom_gap_pct"), "{:.1f}%"))
        console.print(t)
    if any("phantom_gap_pct" in v for v in a["phases"].values()):
        t = Table(title="Kalshi list endpoint vs executable order book")
        for c in ("Phase", "Samples", "mean |list − book| (bid+ask)", "phantom crossings (list only)"):
            t.add_column(c)
        for ph, v in a["phases"].items():
            if "phantom_gap_pct" in v:
                t.add_row(ph, str(v["list_vs_book_samples"]), _f(v["mean_list_book_diff"]), _f(v["phantom_gap_pct"], "{:.1f}%"))
        console.print(t)
    e = a["episodes"]
    t = Table(title="Decoupling episodes (consecutive ticks where the venues cross)")
    for c in ("Kind", "Phase", "Episodes", "Single-tick", "Median length (s)", "Max length (s)", "Peak"):
        t.add_column(c)
    for kind in ("gross", "net"):
        for ph in ("pre", "live"):
            v = e.get(f"{kind}_{ph}")
            if v:
                t.add_row(kind, ph, str(v["count"]), str(v["single_tick"]), _f(v["median_seconds"], "{:.0f}"),
                          _f(v["max_seconds"], "{:.0f}"), _f(v["peak"], "{:.3f}"))
    console.print(t)
    if e.get("net_list"):
        t = Table(title="Largest net-arb episodes")
        for c in ("Phase", "Team", "Start (UTC)", "Ticks", "Seconds", "Peak margin", "Game state"):
            t.add_column(c)
        for x in e["net_list"][:10]:
            t.add_row(x["phase"], x["team_name"], x["start"][11:19], str(x["ticks"]), f"{x['seconds']:.0f}", pct(x["peak"]), x["status"] or "")
        console.print(t)
    t = Table(title="Per game")
    for c in ("League", "Game", "Live samples", "Pre samples", "|mid diff| live", "|mid diff| pre", "gross>0 live", "net arbs live", "best net margin", "max |mid diff|"):
        t.add_column(c)
    for g in a["games"][:30]:
        t.add_row(g["league"].upper(), g["label"][:34], str(g["live_samples"]), str(g["pre_samples"]), _f(g["live_mean_abs_mid_diff"]),
                  _f(g["pre_mean_abs_mid_diff"]), _f(g["live_pct_gross_positive"], "{:.1f}%"), str(g["live_net_arbs"]),
                  pct(g["best_net_margin"]) if g["best_net_margin"] else "-", _f(g["max_abs_mid_diff"]))
    console.print(t)
    if a["top_moments"]:
        t = Table(title="Widest moments (mid-price disagreement)")
        for c in ("Time (UTC)", "Phase", "Team", "Kalshi bid/ask", "Poly bid/ask", "mid diff", "gross gap", "net margin", "Game state"):
            t.add_column(c)
        for r in a["top_moments"]:
            t.add_row(r["ts"][11:19], r["phase"], r["team_name"], f"{price(r['k_bid'])}/{price(r['k_ask'])}",
                      f"{price(r['p_bid'])}/{price(r['p_ask'])}", f"{r['mid_diff']:+.3f}", _f(r["gross_gap"], "{:+.3f}"),
                      pct(r["net_margin"]) if r["net_margin"] else "-", r["status"] or "")
        console.print(t)

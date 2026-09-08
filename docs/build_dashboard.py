"""Render the paper-trading ledger as a self-contained dashboard page.

    python docs/build_dashboard.py --db data/cloud.db --out site/index.html

Reads only the ledger tables, so it works against the small cloud database as
well as the full local one. Every number on the page is computed here; the page
itself ships no data dependencies and reloads itself every five minutes.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def epoch_ms(ts: str | None) -> int | None:
    """Ledger timestamps are ISO-8601, sometimes with an offset and sometimes bare."""
    if not ts:
        return None
    t = ts.strip().replace("Z", "+00:00")
    try:
        d = datetime.fromisoformat(t)
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return int(d.timestamp() * 1000)


def money(x: float, nd: int = 2) -> str:
    return f"{'-' if x < 0 else ''}${abs(x):,.{nd}f}"


def load_replays(paths: list[str]) -> list[dict]:
    """Historical replays produced by `arb backtest --json`. These are simulated on
    each venue's own recorded history, not live paper trades, so they are shown
    separately from the ledger rather than merged into it."""
    out = []
    for pth in paths:
        f = Path(pth)
        if not f.exists():
            continue
        try:
            d = json.loads(f.read_text())
        except ValueError:
            continue
        if not d.get("curve"):
            continue
        t = d["total"]
        out.append({
            "name": f.stem.replace("backtest_", "").replace("_", " ").replace("30d", "").replace("14d", "").strip().title() or f.stem,
            "days": d["days"], "size": d["size"], "games": t["games"], "curve": d["curve"],
            "total": t["profit_pre"] + t["profit_live"],
            "persist": t.get("persist_profit_pre", 0) + t.get("persist_profit_live", 0),
            "signals": t["episodes_pre"] + t["episodes_live"],
        })
    return out


def load(db_path: Path, bankroll: float) -> dict:
    db = sqlite3.connect(str(db_path))
    db.row_factory = sqlite3.Row
    q = lambda s, *a: db.execute(s, a).fetchall()
    one = lambda s, *a: db.execute(s, a).fetchone()

    trades = [dict(r) for r in q("SELECT * FROM trades ORDER BY id")]
    settled = [t for t in trades if t["status"] == "settled"]
    open_t = [t for t in trades if t["status"] == "open"]
    realized = sum(t["pnl"] or 0 for t in settled)
    locked = sum((t["payout_if_complete"] or 0) - t["cost"] for t in open_t)
    deployed = sum(t["cost"] for t in open_t)
    cost_settled = sum(t["cost"] for t in settled)

    # cumulative realized profit, stepped by settlement time, for a chosen set of leagues
    def build_curve(keep=None) -> list[dict]:
        out, run = [], 0.0
        for t in sorted((t for t in settled if t["settled_ts"]), key=lambda t: t["settled_ts"]):
            if keep is not None and (t["match_key"] or ":").split(":")[0] not in keep:
                continue
            run += t["pnl"] or 0
            out.append({"ms": epoch_ms(t["settled_ts"]), "t": (t["settled_ts"] or "")[:16].replace("T", " "),
                        "v": round(run, 4), "id": t["id"],
                        "d": (t["description"] or "")[:80], "p": round(t["pnl"] or 0, 2)})
        return [c for c in out if c["ms"] is not None]

    from arb.config import DEFAULT_LEAGUES
    curve = build_curve()
    curve_majors = build_curve(set(DEFAULT_LEAGUES))

    curve = [c for c in curve if c["ms"] is not None]
    scans = [dict(r) for r in q("SELECT id, ts, n_games, n_opps, duration_s FROM scans WHERE n_games IS NOT NULL ORDER BY id")]
    activity = [{"ms": epoch_ms(s["ts"]), "t": (s["ts"] or "")[:16].replace("T", " "),
                 "g": s["n_games"] or 0, "o": s["n_opps"] or 0, "id": s["id"]} for s in scans]

    activity = [a for a in activity if a["ms"] is not None]
    by_kind = [dict(r) for r in q(
        "SELECT kind, COUNT(*) n, AVG(margin) m, SUM(profit) p FROM opportunities GROUP BY kind ORDER BY n DESC")]
    by_league = {}
    for t in trades:
        key = (t["match_key"] or ":").split(":")[0] or "other"
        d = by_league.setdefault(key, {"league": key, "n": 0, "cost": 0.0, "realized": 0.0, "locked": 0.0})
        d["n"] += 1
        d["cost"] += t["cost"]
        if t["status"] == "settled":
            d["realized"] += t["pnl"] or 0
        else:
            d["locked"] += (t["payout_if_complete"] or 0) - t["cost"]
    leagues = sorted(by_league.values(), key=lambda d: -(d["realized"] + d["locked"]))

    last = one("SELECT * FROM scans ORDER BY id DESC LIMIT 1")
    return {
        "generated": datetime.now(timezone.utc),
        "realized": realized, "locked": locked, "total": realized + locked,
        "deployed": deployed, "cash": bankroll - deployed + realized, "bankroll": bankroll,
        "cost_settled": cost_settled,
        "n_open": len(open_t), "n_settled": len(settled),
        "n_wins": sum(1 for t in settled if (t["pnl"] or 0) > 0),
        "n_scans": len(scans), "n_opps": one("SELECT COUNT(*) c FROM opportunities")["c"],
        "curve": curve, "curve_majors": curve_majors, "activity": activity[-160:], "by_kind": by_kind, "leagues": leagues,
        "settled_rows": sorted(settled, key=lambda t: t["settled_ts"] or "", reverse=True)[:20],
        "open_rows": sorted(open_t, key=lambda t: -((t["payout_if_complete"] or 0) - t["cost"]))[:20],
        "last_scan": dict(last) if last else None,
        "first_ts": scans[0]["ts"] if scans else None,
    }


def rows_html(trades: list[dict], settled: bool) -> str:
    out = []
    for t in trades:
        if settled:
            val = f"<td class='n {'up' if (t['pnl'] or 0) > 0 else 'down' if (t['pnl'] or 0) < 0 else ''}'>{money(t['pnl'] or 0)}</td>"
            when = (t["settled_ts"] or "")[:16].replace("T", " ")
        else:
            val = f"<td class='n'>{money((t['payout_if_complete'] or 0) - t['cost'])}</td>"
            when = (t["ts"] or "")[:16].replace("T", " ")
        desc = (t["description"] or "").replace("<", "&lt;")
        out.append(f"<tr><td class=mono>{t['id']}</td><td class=mono>{when}</td><td>{t['kind']}</td>"
                   f"<td class=n>{t['qty']:.0f}</td><td class=n>{money(t['cost'])}</td>{val}<td class=desc>{desc}</td></tr>")
    return "".join(out) or "<tr><td colspan=7 class=muted>Nothing yet.</td></tr>"


TEMPLATE = """<!doctype html>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="600">
<title>sportstrades · paper-trading dashboard</title>
<style>
:root{color-scheme:light;
 --paper:#f4f6f5;--surface:#fff;--ink:#141a18;--ink-2:#55605c;--ink-3:#7c8783;--rule:#d9dfdb;--grid:#e8ece9;
 --acc:#0b7f5a;--acc2:#3352c7;--bar:#4f66a8;--s1:#2151af;--s2:#5381d6;--s3:#ac3273;--up:#0b7f5a;--down:#b3261e;--tip:#fff;--shadow:0 6px 24px rgba(20,26,24,.10);
 --sans:ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif;--mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){color-scheme:dark;
 --paper:#0f1312;--surface:#171c1a;--ink:#e9edeb;--ink-2:#a3aca8;--ink-3:#7c8783;--rule:#2a322e;--grid:#232a27;
 --acc:#22a878;--acc2:#6b7ae3;--bar:#7a8fd0;--s1:#3664be;--s2:#6591e1;--s3:#c1558a;--up:#22a878;--down:#e07a72;--tip:#1d2320;--shadow:0 6px 24px rgba(0,0,0,.45)}}
*{box-sizing:border-box}
body{margin:0;background:var(--paper);color:var(--ink);font:15px/1.5 var(--sans);-webkit-font-smoothing:antialiased}
.wrap{max-width:1120px;margin:0 auto;padding:clamp(18px,3vw,36px) clamp(14px,3vw,28px) 64px}
header{display:flex;flex-wrap:wrap;gap:12px;align-items:baseline;justify-content:space-between;margin-bottom:22px}
h1{font:700 22px/1.2 var(--sans);margin:0;letter-spacing:-.01em}
h2{font:600 16px/1.2 var(--sans);margin:0 0 2px;letter-spacing:-.005em}
.sub{color:var(--ink-2);font-size:13px}
.live{display:inline-flex;align-items:center;gap:6px;font:500 12px/1 var(--mono);color:var(--ink-2)}
.live i{width:7px;height:7px;border-radius:50%;background:var(--up);display:inline-block}
.hero{background:var(--surface);border:1px solid var(--rule);border-radius:10px;padding:22px 24px;margin-bottom:14px}
.hero .lab{font:500 12px/1 var(--mono);letter-spacing:.06em;text-transform:uppercase;color:var(--ink-2)}
.hero .big{font:600 clamp(40px,7vw,60px)/1 var(--sans);letter-spacing:-.02em;margin:8px 0 6px}
.hero .note{color:var(--ink-2);font-size:13.5px;max-width:62ch}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(158px,1fr));gap:12px;margin-bottom:20px}
.tile{background:var(--surface);border:1px solid var(--rule);border-radius:8px;padding:13px 15px}
.tile .v{font:600 25px/1.1 var(--sans);letter-spacing:-.015em}
.tile .l{font-size:12.5px;color:var(--ink-2);margin-top:3px;line-height:1.3}
.card{background:var(--surface);border:1px solid var(--rule);border-radius:10px;padding:16px 18px 12px;margin-bottom:16px;position:relative}
.cap{font-size:12.5px;color:var(--ink-2);margin:2px 0 10px;max-width:82ch}
.legend{display:flex;flex-wrap:wrap;gap:5px 16px;font:500 12px/1.3 var(--sans);color:var(--ink-2);margin:2px 0 6px}
.legend span{display:inline-flex;align-items:center;gap:6px}
.legend i{width:20px;height:0;border-top:2px solid;border-radius:2px}
.legend i.sw{width:12px;height:11px;border:0;border-radius:2px}
svg{display:block;width:100%;height:auto;overflow:visible}
svg text{font-family:var(--mono);font-size:11.5px;fill:var(--ink-3);font-variant-numeric:tabular-nums}
svg .grid line{stroke:var(--grid);stroke-width:1}
svg .axis line{stroke:var(--rule);stroke-width:1}
svg .ln{fill:none;stroke:var(--acc);stroke-width:2;stroke-linejoin:round;stroke-linecap:round}
svg .dot{fill:var(--acc);stroke:var(--surface);stroke-width:2}
svg .bar{fill:var(--bar)}
svg .bar.zero{fill:var(--grid)}
svg .xh{stroke:var(--ink-3);stroke-width:1;opacity:0}
svg .hit{fill:transparent}
table{border-collapse:collapse;width:100%;font-size:13px;table-layout:fixed}
.grid2 col.c-id{width:44px}.grid2 col.c-when{width:118px}.grid2 col.c-kind{width:62px}
.grid2 col.c-qty{width:60px}.grid2 col.c-cost{width:86px}.grid2 col.c-val{width:96px}
@media (max-width:700px){.grid2 col.c-when{width:78px}.grid2 col.c-kind{width:48px}}
th,td{padding:7px 9px;text-align:left;border-bottom:1px solid var(--rule);white-space:nowrap}
th{font:600 11.5px/1.3 var(--sans);color:var(--ink-2);letter-spacing:.02em}
td.n,th.n{text-align:right;font-family:var(--mono);font-variant-numeric:tabular-nums}
td.mono{font-family:var(--mono);color:var(--ink-2)}
td.desc{white-space:normal;color:var(--ink-2);font-size:12.5px;overflow-wrap:anywhere}
th,td{overflow:hidden;text-overflow:ellipsis}
td.up{color:var(--up)}td.down{color:var(--down)}
tbody tr:last-child td{border-bottom:0}
.grid2{display:grid;grid-template-columns:minmax(0,1fr);gap:16px}
@media (max-width:760px){.grid2{grid-template-columns:1fr}}
.tw{overflow-x:auto}
.wrap{overflow-x:clip}
.muted{color:var(--ink-2)}
.tip{position:absolute;pointer-events:none;background:var(--tip);border:1px solid var(--rule);border-radius:7px;
 box-shadow:var(--shadow);padding:9px 11px;font:12.5px/1.35 var(--sans);display:none;z-index:3;min-width:180px}
.tip .t{font:500 11px/1.3 var(--mono);color:var(--ink-3);margin-bottom:5px}
.tip .r{display:flex;justify-content:space-between;gap:14px}
.tip .r b{font:600 14px/1.2 var(--mono);font-variant-numeric:tabular-nums}
footer{margin-top:26px;padding-top:14px;border-top:1px solid var(--rule);font-size:12.5px;color:var(--ink-2)}
a{color:inherit}
</style>
<div class="wrap">
<header>
  <div><h1>sportstrades · paper trading</h1>
    <div class="sub">Simulated cross-venue arbitrage between Kalshi, Polymarket and sportsbook lines. No real orders.</div></div>
  <div class="live"><i></i>updated __GEN__ · scans every 10 min</div>
</header>

<div class="hero">
  <div class="lab">Live paper ledger · simulated profit</div>
  <div class="big">__TOTAL__</div>
  <div class="note">__REALIZED__ realized on __NSETTLED__ settled hedges, __LOCKED__ already locked in by __NOPEN__ open ones.
  Every hedge buys both sides of an outcome, so a settled trade pays the same whoever wins.
  <br>The historical replay charted below is a <em>separate</em> experiment on past games and is never added to this
  figure. It reached __REPLAY_TOTAL__ over its window.</div>
</div>

<div class="tiles">
  <div class="tile"><div class="v">__REALIZED__</div><div class="l">Realized on settled games (__WINS__ of __NSETTLED__ profitable)</div></div>
  <div class="tile"><div class="v">__LOCKED__</div><div class="l">Locked in by __NOPEN__ open hedges</div></div>
  <div class="tile"><div class="v">__DEPLOYED__</div><div class="l">Capital deployed of __BANKROLL__</div></div>
  <div class="tile"><div class="v">__RET__</div><div class="l">Return on settled capital (__COSTSETTLED__)</div></div>
  <div class="tile"><div class="v">__NSCANS__</div><div class="l">Scans run · __NOPPS__ opportunities seen</div></div>
  <div class="tile"><div class="v">__REPLAY_TOTAL__</div><div class="l">Historical replay over __REPLAY_DAYS__ days — a separate experiment, and an upper bound</div></div>
</div>

<div class="card">
  <h2>Cumulative profit</h2>
  <p class="cap">Three measurements on one timeline. The two replay lines are the same experiment at two scopes:
  major leagues, and major leagues plus the niche ones (ITF and ATP/WTA tennis, Counter-Strike, Dota 2, League of
  Legends, Valorant, KBO, NPB, KHL, second-tier soccer). The gap between them is what niche markets add.
  <b>Every line starts at zero and is measured on its own</b>, never summed: the replays cover past games and are an
  upper bound, assuming zero latency and that each recorded price was executable, while the live ledger is what the
  scanner has actually accumulated since it was switched on.</p>
  <div class="legend" id="lg-main"></div>
  <div id="c-main"></div><div class="tip" id="c-main-tip"></div>
  <details><summary class="muted">Table view · live settled hedges</summary><div class="tw"><table id="curve-table"></table></div></details>
</div>

<div class="card">
  <h2>Scan activity</h2>
  <p class="cap">Opportunities found per scan. Each bar is one pass over both venues; a grey bar is a scan that found nothing, which is the normal result.</p>
  <div id="activity"></div><div class="tip" id="act-tip"></div>
</div>

<div class="grid2">
  <div class="card"><h2>Settled</h2><p class="cap">Resolved against each venue's own result.</p>
    <div class="tw"><table><colgroup><col class=c-id><col class=c-when><col class=c-kind><col class=c-qty><col class=c-cost><col class=c-val><col></colgroup><thead><tr><th>#</th><th>Settled</th><th>Kind</th><th class=n>Qty</th><th class=n>Cost</th><th class=n>P&amp;L</th><th>Trade</th></tr></thead><tbody>__SETTLED_ROWS__</tbody></table></div></div>
  <div class="card"><h2>Open</h2><p class="cap">Hedges waiting on a game to finish, by profit already locked in.</p>
    <div class="tw"><table><colgroup><col class=c-id><col class=c-when><col class=c-kind><col class=c-qty><col class=c-cost><col class=c-val><col></colgroup><thead><tr><th>#</th><th>Opened</th><th>Kind</th><th class=n>Qty</th><th class=n>Cost</th><th class=n>Locked in</th><th>Trade</th></tr></thead><tbody>__OPEN_ROWS__</tbody></table></div></div>
</div>

<div class="card"><h2>By league</h2><p class="cap">Where the simulated money came from.</p>
  <div class="tw"><table><thead><tr><th>League</th><th class=n>Trades</th><th class=n>Cost</th><th class=n>Realized</th><th class=n>Locked in</th><th class=n>Total</th></tr></thead><tbody>__LEAGUE_ROWS__</tbody></table></div></div>

<footer>Built by <code>docs/build_dashboard.py</code> from the ledger on the
<a href="https://github.com/VV1Git/sportstrades/tree/ledger">ledger branch</a>, republished by
<a href="https://github.com/VV1Git/sportstrades/actions">GitHub Actions</a> on every run.
Simulated only: the software places no orders and this is not investment advice.
See the <a href="https://github.com/VV1Git/sportstrades">repository</a> and its analysis write-up for method and limitations.</footer>
</div>
<script id="d" type="application/json">__DATA__</script>
<script>
(function(){
var D=JSON.parse(document.getElementById('d').textContent);
function el(n,a,p){var e=document.createElementNS('http://www.w3.org/2000/svg',n);for(var k in a)e.setAttribute(k,a[k]);if(p)p.appendChild(e);return e}
function tx(e,s){e.textContent=s;return e}
function usd(v){return (v<0?'-$':'$')+Math.abs(v).toFixed(2)}
function step(range,target){var raw=range/target,p=Math.pow(10,Math.floor(Math.log(raw)/Math.LN10)),c=raw/p;return (c<1.5?1:c<3.5?2:c<7.5?5:10)*p}

/* one cumulative-profit chart: two replay scopes and the live ledger, each from zero */
(function(){
var host=document.getElementById('c-main'),tip=document.getElementById('c-main-tip'),leg=document.getElementById('lg-main');
if(!host)return;
var SER=(D.series||[]).map(function(s){
 return {name:s.name,color:s.color,kind:s.kind,
  pts:(s.pts||[]).map(function(r){
   return s.kind==='replay'
    ? {ms:Date.parse(r.d+'T23:59:59Z'),v:r.c,lab:r.d,det:(r.n||0)+' signal'+((r.n||0)===1?'':'s')+' that day'}
    : {ms:r.ms,v:r.v,lab:r.t,det:'trade #'+r.id+' · '+usd(r.p),desc:r.d}})}});
leg.innerHTML='';
SER.forEach(function(s){var sp=document.createElement('span');var i=document.createElement('i');
 i.style.borderColor=s.color;sp.appendChild(i);
 sp.appendChild(document.createTextNode(s.name+(s.pts.length?'':' · none yet')));
 if(!s.pts.length)sp.style.opacity=.55;leg.appendChild(sp)});
var all=[];SER.forEach(function(s){all=all.concat(s.pts)});
if(all.length<2){host.innerHTML='<p class="muted">Not enough data to plot yet.</p>';return}
var W=1040,H=300,m={l:70,r:86,t:16,b:30};
var t0=Math.min.apply(null,all.map(function(p){return p.ms})),t1=Math.max.apply(null,all.map(function(p){return p.ms}));
if(t1<=t0)t1=t0+864e5;
var vmax=Math.max.apply(null,all.map(function(p){return p.v}));
var st=step(Math.max(vmax,1),4),ymax=Math.ceil(vmax/st)*st||st;
var x=function(t){return m.l+(t-t0)/(t1-t0)*(W-m.l-m.r)},y=function(v){return m.t+(1-v/ymax)*(H-m.t-m.b)};
var svg=el('svg',{viewBox:'0 0 '+W+' '+H,role:'img','aria-label':'Cumulative profit: '+SER.map(function(s){
 return s.name+' '+usd(s.pts.length?s.pts[s.pts.length-1].v:0)}).join('; ')},host);
var g=el('g',{'class':'grid'},svg);
for(var v=0;v<=ymax+1e-9;v+=st){el('line',{x1:m.l,x2:W-m.r,y1:y(v),y2:y(v)},g);
 tx(el('text',{x:m.l-8,y:y(v)+4,'text-anchor':'end'},svg),'$'+Math.round(v).toLocaleString())}
el('line',{x1:m.l,x2:W-m.r,y1:H-m.b,y2:H-m.b},el('g',{'class':'axis'},svg));
var tickEvery=Math.max(1,Math.ceil((t1-t0)/864e5/7));
for(var dd=new Date(t0);dd.getTime()<=t1;dd.setUTCDate(dd.getUTCDate()+tickEvery)){
 var X=x(dd.getTime());if(X>W-m.r-42)break;
 tx(el('text',{x:X,y:H-m.b+17,'text-anchor':'middle'},svg),dd.toISOString().slice(5,10))}
tx(el('text',{x:W-m.r,y:H-m.b+17,'text-anchor':'end'},svg),new Date(t1).toISOString().slice(5,10));
var ends=[];
SER.forEach(function(s){
 if(!s.pts.length)return;
 var d='';s.pts.forEach(function(p,i){d+=(i?'L':'M')+x(p.ms).toFixed(1)+' '+y(p.v).toFixed(1)});
 el('path',{d:d,fill:'none',stroke:s.color,'stroke-width':2,'stroke-linejoin':'round','stroke-linecap':'round'},svg);
 var last=s.pts[s.pts.length-1];
 el('circle',{cx:x(last.ms),cy:y(last.v),r:4.5,fill:s.color,stroke:'var(--surface)','stroke-width':2},svg);
 ends.push({p:last,c:s.color})});
ends.sort(function(a,b){return y(a.p.v)-y(b.p.v)});
var prevY=-99;ends.forEach(function(L){var Y=y(L.p.v)+4;if(Y-prevY<13)Y=prevY+13;prevY=Y;
 tx(el('text',{x:W-m.r+8,y:Y,'text-anchor':'start',fill:L.c,'font-weight':'600'},svg),usd(L.p.v))});
var xh=el('line',{'class':'xh',y1:m.t,y2:H-m.b},svg);
var hit=el('rect',{'class':'hit',x:m.l,y:m.t,width:W-m.l-m.r,height:H-m.t-m.b},svg);
hit.addEventListener('pointermove',function(e){
 var rc=svg.getBoundingClientRect(),vx=(e.clientX-rc.left)/rc.width*W,t=t0+(vx-m.l)/(W-m.l-m.r)*(t1-t0);
 xh.setAttribute('x1',x(t));xh.setAttribute('x2',x(t));xh.style.opacity=1;
 tip.innerHTML='';var h=document.createElement('div');h.className='t';
 h.textContent=new Date(t).toISOString().slice(0,10);tip.appendChild(h);
 var note=null;
 SER.forEach(function(s){
  if(!s.pts.length)return;
  var b=null;s.pts.forEach(function(p){if(p.ms<=t+432e5&&(!b||p.ms>b.ms))b=p});
  var r=document.createElement('div');r.className='r';
  var sp=document.createElement('span');var k=document.createElement('i');
  k.style.cssText='display:inline-block;width:14px;border-top:2px solid '+s.color+';margin-right:6px;vertical-align:middle';
  sp.appendChild(k);sp.appendChild(document.createTextNode(s.name.split(' · ')[0]+(s.kind==='replay'?' '+s.name.split(' · ')[1]:'')));
  var bb=document.createElement('b');bb.textContent=b?usd(b.v):'—';
  r.appendChild(sp);r.appendChild(bb);tip.appendChild(r);
  if(b&&b.desc&&s.kind==='live')note=b.desc});
 if(note){var n2=document.createElement('div');n2.className='t';n2.style.marginTop='5px';n2.style.whiteSpace='normal';
  n2.textContent=note;tip.appendChild(n2)}
 tip.style.display='block';var hr=host.getBoundingClientRect(),cr=host.parentNode.getBoundingClientRect();
 var L=e.clientX-cr.left+14;if(L+260>cr.width)L-=288;tip.style.left=L+'px';tip.style.top=(hr.top-cr.top+8)+'px'});
hit.addEventListener('pointerleave',function(){xh.style.opacity=0;tip.style.display='none'});
})();

/* table view of the live settled hedges */
(function(){
var tb=document.getElementById('curve-table');if(!tb)return;
var S=D.live||[];
if(!S.length){tb.innerHTML='<tbody><tr><td class="muted">Nothing settled yet.</td></tr></tbody>';return}
tb.innerHTML='<thead><tr><th>#</th><th>Settled (UTC)</th><th class=n>Trade P&amp;L</th><th class=n>Running total</th><th>Trade</th></tr></thead>';
var body=document.createElement('tbody');
S.slice().reverse().forEach(function(r){var tr=document.createElement('tr');
 [[r.id,'mono'],[r.t,'mono'],[usd(r.p),'n'],[usd(r.v),'n'],[r.d,'desc']].forEach(function(c){
  var td=document.createElement('td');td.className=c[1];td.textContent=c[0];tr.appendChild(td)});body.appendChild(tr)});
tb.appendChild(body)})();

/* scan activity */
(function(){
var A=D.activity,host=document.getElementById('activity'),tip=document.getElementById('act-tip');
if(!A.length){host.innerHTML='<p class="muted">No scans recorded yet.</p>';return}
var W=1040,H=170,m={l:34,r:16,t:12,b:28};
var n=A.length,omax=Math.max(1,Math.max.apply(null,A.map(function(a){return a.o})));
var st=step(omax,3)||1,ymax=Math.max(st,Math.ceil(omax/st)*st);
var bw=Math.max(2,Math.min(16,(W-m.l-m.r)/n-2));
var x=function(i){return m.l+(i+.5)*((W-m.l-m.r)/n)},y=function(v){return m.t+(1-v/ymax)*(H-m.t-m.b)};
var svg=el('svg',{viewBox:'0 0 '+W+' '+H,role:'img','aria-label':'Opportunities found per scan'},host);
var g=el('g',{'class':'grid'},svg);
for(var v=0;v<=ymax+1e-9;v+=st){el('line',{x1:m.l,x2:W-m.r,y1:y(v),y2:y(v)},g);tx(el('text',{x:m.l-7,y:y(v)+4,'text-anchor':'end'},svg),String(v))}
el('line',{x1:m.l,x2:W-m.r,y1:H-m.b,y2:H-m.b},el('g',{'class':'axis'},svg));
A.forEach(function(a,i){var h=a.o?Math.max(3,(H-m.t-m.b)*a.o/ymax):2,X=x(i)-bw/2,Y=a.o?y(a.o):H-m.b-2;
 var r=Math.min(3,bw/2);
 var d=a.o?('M'+X+' '+(H-m.b)+'V'+(Y+r)+'a'+r+' '+r+' 0 0 1 '+r+' -'+r+'H'+(X+bw-r)+'a'+r+' '+r+' 0 0 1 '+r+' '+r+'V'+(H-m.b)+'Z')
  :('M'+X+' '+(H-m.b)+'V'+Y+'H'+(X+bw)+'V'+(H-m.b)+'Z');
 el('path',{'class':'bar'+(a.o?'':' zero'),d:d},svg);
 var hit=el('rect',{'class':'hit',x:X-1,y:m.t,width:bw+2,height:H-m.t-m.b},svg);
 hit.addEventListener('pointerenter',function(e){tip.innerHTML='';
  var hh=document.createElement('div');hh.className='t';hh.textContent='scan #'+a.id+' · '+a.t+'Z';tip.appendChild(hh);
  [['Opportunities',String(a.o)],['Games matched',String(a.g)]].forEach(function(p){var d2=document.createElement('div');d2.className='r';
   var s=document.createElement('span');s.textContent=p[0];var b=document.createElement('b');b.textContent=p[1];d2.appendChild(s);d2.appendChild(b);tip.appendChild(d2)});
  tip.style.display='block';var rc=host.getBoundingClientRect(),cr=host.parentNode.getBoundingClientRect();
  var L=e.clientX-cr.left+14;if(L+200>cr.width)L-=228;tip.style.left=L+'px';tip.style.top=(rc.top-cr.top+6)+'px'});
 hit.addEventListener('pointerleave',function(){tip.style.display='none'})});
tx(el('text',{x:m.l,y:H-m.b+17},svg),A[0].t.slice(5));
tx(el('text',{x:W-m.r,y:H-m.b+17,'text-anchor':'end'},svg),A[A.length-1].t.slice(5)+'Z');
})();

})();
</script>
"""


def combine_curves(reps: list[dict]) -> list[dict]:
    """Daily curves from several replay windows, summed onto one timeline."""
    per_day: dict[str, dict] = {}
    for r in reps:
        prev = 0.0
        for pt in r["curve"]:
            day = per_day.setdefault(pt["d"], {"p": 0.0, "n": 0})
            day["p"] += pt["c"] - prev          # that window's profit on that day
            day["n"] += pt["n"]
            prev = pt["c"]
    out, run = [], 0.0
    for day in sorted(per_day):
        run += per_day[day]["p"]
        out.append({"d": day, "p": round(per_day[day]["p"], 2), "c": round(run, 2), "n": per_day[day]["n"]})
    return out


def chart_payload(d: dict, reps: list[dict]) -> dict:
    """One chart, three series, each measured from zero. The two replay scopes are
    nested (majors is a subset of majors+niche) so they share a hue at two steps;
    the live ledger is a different experiment and gets its own hue."""
    majors = next((r for r in reps if r["name"].lower().startswith("majors")), None)
    combined = combine_curves(reps) if reps else []
    days = max((r["days"] for r in reps), default=0)
    series = []
    if majors:
        series.append({"kind": "replay", "color": "var(--s1)",
                       "name": f"Replay · majors · {majors['days']}d", "pts": majors["curve"]})
    if combined:
        series.append({"kind": "replay", "color": "var(--s2)",
                       "name": f"Replay · majors + niche · {days}d", "pts": combined})
    series.append({"kind": "live", "color": "var(--s3)", "name": "Live ledger · realized", "pts": d["curve"]})
    return {"activity": d["activity"], "series": series, "live": d["curve"]}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/cloud.db")
    ap.add_argument("--out", default="site/index.html")
    ap.add_argument("--bankroll", type=float, default=10_000.0)
    ap.add_argument("--backtest", default="docs/backtest_majors_30d.json,docs/backtest_niche_14d.json",
                    help="comma list of `arb backtest --json` outputs to chart alongside the ledger")
    a = ap.parse_args()
    d = load(Path(a.db), a.bankroll)
    reps = load_replays([x.strip() for x in a.backtest.split(",") if x.strip()])

    league_rows = "".join(
        f"<tr><td>{r['league'].upper()}</td><td class=n>{r['n']}</td><td class=n>{money(r['cost'], 0)}</td>"
        f"<td class='n {'up' if r['realized'] > 0 else ''}'>{money(r['realized'])}</td><td class=n>{money(r['locked'])}</td>"
        f"<td class=n>{money(r['realized'] + r['locked'])}</td></tr>" for r in d["leagues"]) or "<tr><td colspan=6 class=muted>Nothing yet.</td></tr>"
    ret = (d["realized"] / d["cost_settled"] * 100) if d["cost_settled"] else 0.0
    html = TEMPLATE
    for k, v in {
        "__GEN__": d["generated"].strftime("%Y-%m-%d %H:%M UTC"),
        "__TOTAL__": money(d["total"]), "__REALIZED__": money(d["realized"]), "__LOCKED__": money(d["locked"]),
        "__DEPLOYED__": money(d["deployed"], 0), "__BANKROLL__": money(d["bankroll"], 0),
        "__RET__": f"{ret:+.2f}%", "__COSTSETTLED__": money(d["cost_settled"], 0),
        "__NOPEN__": str(d["n_open"]), "__NSETTLED__": str(d["n_settled"]), "__WINS__": str(d["n_wins"]),
        "__NSCANS__": f"{d['n_scans']:,}", "__NOPPS__": f"{d['n_opps']:,}",
        "__SETTLED_ROWS__": rows_html(d["settled_rows"], True),
        "__OPEN_ROWS__": rows_html(d["open_rows"], False),
        "__LEAGUE_ROWS__": league_rows,
        "__REPLAY_TOTAL__": money(sum(r["total"] for r in reps), 0) if reps else "$0",
        "__REPLAY_DAYS__": str(max((r["days"] for r in reps), default=0)),
        "__DATA__": json.dumps(chart_payload(d, reps), separators=(",", ":")).replace("</", "<\\/"),
    }.items():
        html = html.replace(k, v)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html)
    print(f"wrote {out} ({len(html) / 1024:.0f} KB) · profit {money(d['total'])} · {d['n_settled']} settled, "
          f"{d['n_open']} open · {len(reps)} replay series"
          f" · replay {money(sum(r['total'] for r in reps), 0)}")


if __name__ == "__main__":
    main()

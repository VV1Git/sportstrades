"""SQLite persistence for scans, opportunities, paper trades, quotes."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from .models import Leg, Opportunity

SCHEMA = """
CREATE TABLE IF NOT EXISTS scans(
  id INTEGER PRIMARY KEY, ts TEXT, scope TEXT, leagues TEXT, n_kalshi INT, n_poly INT, n_games INT,
  n_pairs INT, n_opps INT, duration_s REAL, notes TEXT);
CREATE TABLE IF NOT EXISTS opportunities(
  id INTEGER PRIMARY KEY, scan_id INT, ts TEXT, kind TEXT, match_key TEXT, description TEXT,
  qty REAL, cost REAL, payout REAL, fees REAL, profit REAL, margin REAL, legs TEXT, taken INT DEFAULT 0);
CREATE TABLE IF NOT EXISTS trades(
  id INTEGER PRIMARY KEY, opp_id INT, scan_id INT, ts TEXT, kind TEXT, match_key TEXT, description TEXT,
  qty REAL, cost REAL, fees REAL, payout_if_complete REAL, legs TEXT, status TEXT DEFAULT 'open',
  settled_ts TEXT, payout REAL, pnl REAL, note TEXT);
CREATE TABLE IF NOT EXISTS quotes(
  id INTEGER PRIMARY KEY, scan_id INT, ts TEXT, match_key TEXT, league TEXT, team TEXT, venue TEXT,
  outcome_id TEXT, yes_bid REAL, yes_ask REAL, bid_size REAL, ask_size REAL);
CREATE TABLE IF NOT EXISTS discrepancies(
  id INTEGER PRIMARY KEY, scan_id INT, ts TEXT, match_key TEXT, league TEXT, game TEXT, team TEXT, venue TEXT,
  bookmaker TEXT, american INT, implied REAL, fair REAL, pm_bid REAL, pm_ask REAL, edge_buy REAL, edge_sell REAL);
CREATE INDEX IF NOT EXISTS ix_trades_status ON trades(status);
CREATE INDEX IF NOT EXISTS ix_trades_key ON trades(match_key);
CREATE INDEX IF NOT EXISTS ix_quotes_scan ON quotes(scan_id);
CREATE INDEX IF NOT EXISTS ix_disc_scan ON discrepancies(scan_id);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def legs_to_json(legs: list[Leg]) -> str:
    return json.dumps([asdict(l) for l in legs])


def legs_from_json(s: str) -> list[Leg]:
    return [Leg(**d) for d in json.loads(s or "[]")]


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.db = sqlite3.connect(str(path))
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    # -- scans ------------------------------------------------------------
    def start_scan(self, scope: str, leagues: list[str]) -> int:
        cur = self.db.execute("INSERT INTO scans(ts, scope, leagues) VALUES (?,?,?)", (now(), scope, ",".join(leagues)))
        self.db.commit()
        return int(cur.lastrowid)

    def finish_scan(self, scan_id: int, **fields) -> None:
        cols = ", ".join(f"{k}=?" for k in fields)
        self.db.execute(f"UPDATE scans SET {cols} WHERE id=?", (*fields.values(), scan_id))
        self.db.commit()

    def last_scan(self) -> sqlite3.Row | None:
        return self.db.execute("SELECT * FROM scans ORDER BY id DESC LIMIT 1").fetchone()

    # -- opportunities ----------------------------------------------------
    def add_opportunities(self, scan_id: int, opps: list[Opportunity]) -> list[int]:
        ids = []
        for o in opps:
            cur = self.db.execute(
                "INSERT INTO opportunities(scan_id, ts, kind, match_key, description, qty, cost, payout, fees, profit, margin, legs)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (scan_id, o.ts.isoformat(timespec="seconds"), o.kind, o.match_key, o.description, o.qty, o.cost,
                 o.payout, o.fees, o.profit, o.margin, legs_to_json(o.legs)))
            ids.append(int(cur.lastrowid))
        self.db.commit()
        return ids

    def mark_taken(self, opp_id: int) -> None:
        self.db.execute("UPDATE opportunities SET taken=1 WHERE id=?", (opp_id,))
        self.db.commit()

    def opportunities(self, limit: int = 50, kind: str | None = None) -> list[sqlite3.Row]:
        q = "SELECT * FROM opportunities" + (" WHERE kind=?" if kind else "") + " ORDER BY id DESC LIMIT ?"
        return self.db.execute(q, ((kind, limit) if kind else (limit,))).fetchall()

    # -- trades -----------------------------------------------------------
    def add_trade(self, opp_id: int | None, scan_id: int, kind: str, match_key: str, description: str,
                  qty: float, cost: float, fees: float, payout_if_complete: float, legs: list[Leg]) -> int:
        cur = self.db.execute(
            "INSERT INTO trades(opp_id, scan_id, ts, kind, match_key, description, qty, cost, fees, payout_if_complete, legs)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (opp_id, scan_id, now(), kind, match_key, description, qty, cost, fees, payout_if_complete, legs_to_json(legs)))
        self.db.commit()
        return int(cur.lastrowid)

    def open_trades(self) -> list[sqlite3.Row]:
        return self.db.execute("SELECT * FROM trades WHERE status='open' ORDER BY id").fetchall()

    def trades(self, status: str | None = None, limit: int = 100) -> list[sqlite3.Row]:
        if status:
            return self.db.execute("SELECT * FROM trades WHERE status=? ORDER BY id DESC LIMIT ?", (status, limit)).fetchall()
        return self.db.execute("SELECT * FROM trades ORDER BY id DESC LIMIT ?", (limit,)).fetchall()

    def settle_trade(self, trade_id: int, payout: float, pnl: float, status: str = "settled", note: str = "") -> None:
        self.db.execute("UPDATE trades SET status=?, settled_ts=?, payout=?, pnl=?, note=? WHERE id=?",
                        (status, now(), payout, pnl, note, trade_id))
        self.db.commit()

    def open_exposure(self, match_key: str) -> float:
        r = self.db.execute("SELECT COALESCE(SUM(cost),0) FROM trades WHERE status='open' AND match_key=?", (match_key,)).fetchone()
        return float(r[0])

    def deployed(self) -> float:
        return float(self.db.execute("SELECT COALESCE(SUM(cost),0) FROM trades WHERE status='open'").fetchone()[0])

    def realized(self) -> float:
        return float(self.db.execute("SELECT COALESCE(SUM(pnl),0) FROM trades WHERE status='settled'").fetchone()[0])

    # -- quotes / discrepancies -------------------------------------------
    def add_quotes(self, scan_id: int, rows: list[tuple]) -> None:
        self.db.executemany(
            "INSERT INTO quotes(scan_id, ts, match_key, league, team, venue, outcome_id, yes_bid, yes_ask, bid_size, ask_size)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)", [(scan_id, now(), *r) for r in rows])
        self.db.commit()

    def last_quote(self, venue: str, outcome_id: str) -> sqlite3.Row | None:
        return self.db.execute("SELECT * FROM quotes WHERE venue=? AND outcome_id=? ORDER BY id DESC LIMIT 1",
                               (venue, outcome_id)).fetchone()

    def add_discrepancies(self, scan_id: int, rows: list[dict]) -> None:
        self.db.executemany(
            "INSERT INTO discrepancies(scan_id, ts, match_key, league, game, team, venue, bookmaker, american, implied,"
            " fair, pm_bid, pm_ask, edge_buy, edge_sell) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [(scan_id, now(), r["match_key"], r["league"], r["game"], r["team"], r["venue"], r["bookmaker"],
              r["american"], r["implied"], r["fair"], r["pm_bid"], r["pm_ask"], r["edge_buy"], r["edge_sell"]) for r in rows])
        self.db.commit()

    def discrepancies_for_scan(self, scan_id: int) -> list[sqlite3.Row]:
        return self.db.execute("SELECT * FROM discrepancies WHERE scan_id=? ORDER BY ABS(COALESCE(edge_buy,0)) DESC",
                               (scan_id,)).fetchall()

    # -- summary ----------------------------------------------------------
    def summary(self) -> dict:
        d: dict = {}
        d["scans"] = int(self.db.execute("SELECT COUNT(*) FROM scans").fetchone()[0])
        d["opportunities"] = int(self.db.execute("SELECT COUNT(*) FROM opportunities").fetchone()[0])
        d["by_kind"] = {r[0]: {"count": r[1], "avg_margin": r[2], "total_profit": r[3]} for r in self.db.execute(
            "SELECT kind, COUNT(*), AVG(margin), SUM(profit) FROM opportunities GROUP BY kind")}
        d["trades_open"] = int(self.db.execute("SELECT COUNT(*) FROM trades WHERE status='open'").fetchone()[0])
        d["trades_settled"] = int(self.db.execute("SELECT COUNT(*) FROM trades WHERE status='settled'").fetchone()[0])
        d["trades_void"] = int(self.db.execute("SELECT COUNT(*) FROM trades WHERE status='void'").fetchone()[0])
        d["deployed"] = self.deployed()
        d["realized_pnl"] = self.realized()
        d["expected_open_profit"] = float(self.db.execute(
            "SELECT COALESCE(SUM(payout_if_complete - cost),0) FROM trades WHERE status='open'").fetchone()[0])
        r = self.db.execute("SELECT SUM(CASE WHEN pnl>0 THEN 1 ELSE 0 END), COUNT(*) FROM trades WHERE status='settled'").fetchone()
        d["settled_wins"] = int(r[0] or 0)
        d["settled_total"] = int(r[1] or 0)
        d["cost_settled"] = float(self.db.execute("SELECT COALESCE(SUM(cost),0) FROM trades WHERE status='settled'").fetchone()[0])
        return d

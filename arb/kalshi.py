"""Kalshi public REST client (no auth needed for market data) + normalisation."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Iterator

from .config import KALSHI_BASE, KALSHI_SERIES_TO_LEAGUE, Settings
from .http import Http
from .models import KALSHI, Book, Level, Outcome

_TICKER_DATE = re.compile(r"^[A-Z0-9]+-(\d{2})([A-Z]{3})(\d{2})([A-Z0-9]*)$")
_MONTHS = {m: i for i, m in enumerate(["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], 1)}


def parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def event_date_from_ticker(event_ticker: str) -> datetime | None:
    """KXNFLGAME-26SEP09NESEA -> 2026-09-09 (UTC noon, approximate)."""
    m = _TICKER_DATE.match(event_ticker)
    if not m:
        return None
    yy, mon, dd, _ = m.groups()
    try:
        return datetime(2000 + int(yy), _MONTHS[mon], int(dd), 12, tzinfo=timezone.utc)
    except (KeyError, ValueError):
        return None


def _price(s) -> float | None:
    try:
        v = float(s)
    except (TypeError, ValueError):
        return None
    return v if 0.0 < v < 1.0 else None


def _num(s) -> float:
    try:
        return float(s)
    except (TypeError, ValueError):
        return 0.0


class Kalshi:
    def __init__(self, settings: Settings):
        self.s = settings
        self.http = Http(KALSHI_BASE, rate=settings.kalshi_rate)
        self._series: dict[str, dict] = {}

    # ---- raw endpoints -------------------------------------------------
    def iter_events(self, status: str = "open", series_ticker: str | None = None,
                    with_nested_markets: bool = True, limit: int = 200) -> Iterator[dict]:
        cursor = None
        while True:
            params = {"status": status, "limit": limit, "with_nested_markets": str(with_nested_markets).lower()}
            if series_ticker:
                params["series_ticker"] = series_ticker
            if cursor:
                params["cursor"] = cursor
            d = self.http.get("events", params)
            evs = d.get("events") or []
            yield from evs
            cursor = d.get("cursor")
            if not cursor or not evs:
                break

    def markets(self, series_ticker: str | None = None, event_ticker: str | None = None,
                status: str = "open", limit: int = 1000) -> list[dict]:
        out: list[dict] = []
        cursor = None
        while True:
            params: dict = {"limit": limit}
            if status:
                params["status"] = status
            if series_ticker:
                params["series_ticker"] = series_ticker
            if event_ticker:
                params["event_ticker"] = event_ticker
            if cursor:
                params["cursor"] = cursor
            d = self.http.get("markets", params)
            ms = d.get("markets") or []
            out.extend(ms)
            cursor = d.get("cursor")
            if not cursor or not ms:
                break
        return out

    def market(self, ticker: str) -> dict:
        return self.http.get(f"markets/{ticker}")["market"]

    def event(self, event_ticker: str) -> dict:
        return self.http.get(f"events/{event_ticker}", {"with_nested_markets": "true"})

    def preload_series(self) -> None:
        """One 16 MB call returns every series (fee_type, fee_multiplier); far
        cheaper than one request per series when crawling all markets."""
        if getattr(self, "_all_series_loaded", False):
            return
        try:
            for sr in self.http.get("series").get("series") or []:
                if sr.get("ticker"):
                    self._series.setdefault(sr["ticker"], sr)
            self._all_series_loaded = True
        except Exception:
            self._all_series_loaded = False

    def series(self, ticker: str) -> dict:
        if ticker not in self._series:
            if getattr(self, "_all_series_loaded", False):
                self._series[ticker] = {}
            else:
                try:
                    self._series[ticker] = self.http.get(f"series/{ticker}").get("series", {})
                except Exception:
                    self._series[ticker] = {}
        return self._series[ticker]

    def orderbook(self, ticker: str, depth: int = 20) -> Book:
        d = self.http.get(f"markets/{ticker}/orderbook", {"depth": depth})
        ob = d.get("orderbook_fp") or {}
        yes_bids = [Level(float(p), float(q)) for p, q in (ob.get("yes_dollars") or []) if float(q) > 0]
        no_bids = [Level(float(p), float(q)) for p, q in (ob.get("no_dollars") or []) if float(q) > 0]
        return Book.from_yes_no_bids(yes_bids, no_bids)

    # ---- normalisation -------------------------------------------------
    def fee_rate_for_series(self, series_ticker: str) -> float:
        info = self.series(series_ticker) if series_ticker else {}
        mult = info.get("fee_multiplier")
        try:
            mult = float(mult) if mult is not None else 1.0
        except (TypeError, ValueError):
            mult = 1.0
        return self.s.kalshi_taker_rate * mult

    def outcome_from_market(self, m: dict, event: dict | None = None) -> Outcome | None:
        if m.get("mve_collection_ticker") or m.get("market_type") not in (None, "binary"):
            return None
        event = event or {}
        event_ticker = m.get("event_ticker") or event.get("event_ticker") or ""
        series_ticker = event.get("series_ticker") or event_ticker.split("-")[0]
        if series_ticker.startswith("KXMVE"):
            return None
        league = KALSHI_SERIES_TO_LEAGUE.get(series_ticker)
        title = m.get("title") or m.get("yes_sub_title") or m.get("ticker")
        game_time = event_date_from_ticker(event_ticker) if league else None
        return Outcome(
            venue=KALSHI,
            id=m["ticker"],
            market_id=m["ticker"],
            event_id=event_ticker,
            title=title,
            event_title=event.get("title") or m.get("title") or "",
            category=event.get("category") or "",
            league=league,
            game_time=game_time,
            close_time=parse_dt(m.get("expected_expiration_time") or m.get("expiration_time") or m.get("close_time")),
            yes_bid=_price(m.get("yes_bid_dollars")),
            yes_ask=_price(m.get("yes_ask_dollars")),
            yes_bid_size=_num(m.get("yes_bid_size_fp")),
            yes_ask_size=_num(m.get("yes_ask_size_fp")),
            fee_rate=self.fee_rate_for_series(series_ticker),
            fee_round_cents=True,
            volume=_num(m.get("volume_fp")),
            url=f"https://kalshi.com/markets/{series_ticker.lower()}/x/{event_ticker.lower()}",
            meta={
                "series_ticker": series_ticker,
                "yes_sub_title": m.get("yes_sub_title") or "",
                "team_code": m["ticker"].rsplit("-", 1)[-1] if "-" in m["ticker"] else "",
                "mutually_exclusive": bool(event.get("mutually_exclusive")),
                "status": m.get("status"),
                "rules": (m.get("rules_primary") or "")[:300],
            },
        )

    def outcomes_from_events(self, events: Iterator[dict] | list[dict]) -> list[Outcome]:
        self.preload_series()
        out: list[Outcome] = []
        for e in events:
            for m in e.get("markets") or []:
                o = self.outcome_from_market(m, e)
                if o is not None:
                    out.append(o)
        return out

    def league_outcomes(self, series_ticker: str) -> list[Outcome]:
        """Open game markets for one league series, grouped into events."""
        ms = self.markets(series_ticker=series_ticker, status="open")
        by_event: dict[str, list[dict]] = {}
        for m in ms:
            by_event.setdefault(m.get("event_ticker", ""), []).append(m)
        out: list[Outcome] = []
        for et, group in by_event.items():
            ev = {"event_ticker": et, "series_ticker": series_ticker, "category": "Sports",
                  "mutually_exclusive": True, "title": et}
            for m in group:
                o = self.outcome_from_market(m, ev)
                if o is not None:
                    out.append(o)
        return out

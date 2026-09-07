"""Polymarket Gamma (metadata) + CLOB (order books) client and normalisation."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Iterator

import httpx

from .config import CLOB_BASE, GAMMA_BASE, POLY_TAG_TO_LEAGUE, Settings
from .fees import poly_rate_for_tags
from .http import Http
from .models import POLYMARKET, Book, Level, Outcome

_WILL_WIN = re.compile(r"^will (?:the )?(.+?) (?:win|beat|defeat)\b", re.I)


def parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    s = s.strip().replace("Z", "+00:00")
    if s.endswith("+00"):
        s = s[:-3] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _json_list(s) -> list:
    if isinstance(s, list):
        return s
    try:
        v = json.loads(s or "[]")
        return v if isinstance(v, list) else []
    except (TypeError, ValueError):
        return []


def _fl(v) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if 0.0 < f < 1.0 else None


class Polymarket:
    def __init__(self, settings: Settings):
        self.s = settings
        self.gamma = Http(GAMMA_BASE, rate=settings.gamma_rate)
        self.clob = Http(CLOB_BASE, rate=settings.clob_rate)
        self._sports: list[dict] | None = None

    # ---- Gamma ------------------------------------------------------------
    def sports(self) -> list[dict]:
        if self._sports is None:
            self._sports = self.gamma.get("sports")
        return self._sports

    def iter_events(self, limit: int = 100, max_offset: int = 4900, **params) -> Iterator[dict]:
        """Offset pagination (Gamma caps offset around 5000)."""
        offset = 0
        while offset <= max_offset:
            q = {"limit": limit, "offset": offset, **params}
            try:
                d = self.gamma.get("events", q)
            except httpx.HTTPStatusError as e:
                if e.response is not None and e.response.status_code == 422:
                    return
                raise
            if not d:
                return
            yield from d
            if len(d) < limit:
                return
            offset += limit

    def iter_all_active_events(self, limit: int = 100, **params) -> Iterator[dict]:
        """Keyset pagination over every open event (Gamma's offset pagination
        is capped at ~5000, /events/keyset + after_cursor is not)."""
        cursor = None
        seen: set[str] = set()
        while True:
            q = {"closed": "false", "limit": limit, **params}
            if cursor:
                q["after_cursor"] = cursor
            d = self.gamma.get("events/keyset", q)
            evs = d.get("events") or []
            new = [e for e in evs if str(e.get("id")) not in seen]
            for e in new:
                seen.add(str(e.get("id")))
            yield from new
            nxt = d.get("next_cursor")
            if not nxt or not new or nxt == cursor:
                return
            cursor = nxt

    def events_for_tag(self, tag_id: int) -> list[dict]:
        return list(self.iter_events(tag_id=tag_id, active="true", closed="false"))

    def events_for_league(self, tag_id: int, sport_key: str | None = None) -> list[dict]:
        """Game events live under the sport's *series* on Polymarket (the league
        tag often only carries futures, e.g. Serie A / UCL), while some leagues
        (NFL) have a stale series id in /sports. Fetch both and de-duplicate."""
        seen: dict[str, dict] = {}
        for e in self.events_for_tag(tag_id):
            seen[str(e.get("id"))] = e
        if sport_key:
            series_ids = {str(sp.get("series")) for sp in self.sports() if sp.get("sport") == sport_key and sp.get("series")}
            for sid in series_ids:
                try:
                    for e in self.iter_events(series_id=sid, active="true", closed="false"):
                        seen.setdefault(str(e.get("id")), e)
                except httpx.HTTPError:
                    continue
        return list(seen.values())

    def market_by_id(self, gamma_id: str) -> dict:
        return self.gamma.get(f"markets/{gamma_id}")

    def market_by_condition(self, condition_id: str) -> dict | None:
        d = self.gamma.get("markets", {"condition_ids": condition_id})
        return d[0] if d else None

    # ---- CLOB -------------------------------------------------------------
    def books(self, token_ids: list[str], chunk: int = 100) -> dict[str, dict]:
        out: dict[str, dict] = {}
        ids = list(dict.fromkeys(t for t in token_ids if t))
        for i in range(0, len(ids), chunk):
            body = [{"token_id": t} for t in ids[i:i + chunk]]
            try:
                res = self.clob.post("books", body)
            except httpx.HTTPError:
                continue
            for b in res or []:
                if b.get("asset_id"):
                    out[b["asset_id"]] = b
        return out

    @staticmethod
    def _levels(raw: list[dict]) -> list[Level]:
        lv = []
        for x in raw or []:
            try:
                p, q = float(x["price"]), float(x["size"])
            except (KeyError, TypeError, ValueError):
                continue
            if q > 0 and 0 < p < 1:
                lv.append(Level(p, q))
        return lv

    def attach_books(self, outcomes: list[Outcome]) -> None:
        """Fetch CLOB books for each outcome's YES token and its complement."""
        ids: list[str] = []
        for o in outcomes:
            ids.append(o.id)
            if o.meta.get("no_token"):
                ids.append(o.meta["no_token"])
        books = self.books(ids)
        for o in outcomes:
            yb = books.get(o.id)
            nb = books.get(o.meta.get("no_token", ""))
            if yb is None and nb is None:
                continue
            yes_asks = self._levels(yb.get("asks")) if yb else []
            yes_bids = self._levels(yb.get("bids")) if yb else []
            no_asks = self._levels(nb.get("asks")) if nb else None
            no_bids = self._levels(nb.get("bids")) if nb else None
            o.book = Book.from_token_books(yes_asks, yes_bids, no_asks, no_bids)
            best_a = o.book.best("yes")
            if best_a:
                o.yes_ask, o.yes_ask_size = best_a.price, best_a.size
            if o.book.yes_bids:
                o.yes_bid, o.yes_bid_size = o.book.yes_bids[0].price, o.book.yes_bids[0].size

    # ---- normalisation ---------------------------------------------------
    def _series_league_map(self) -> dict[str, str]:
        """Polymarket series id -> our league key, from /sports."""
        if not hasattr(self, "_series_map"):
            from .config import LEAGUES
            by_sport = {lg.poly_sport: lg.key for lg in LEAGUES.values()}
            m: dict[str, str] = {}
            try:
                for sp in self.sports():
                    if sp.get("sport") in by_sport and sp.get("series"):
                        m[str(sp["series"])] = by_sport[sp["sport"]]
            except httpx.HTTPError:
                pass
            self._series_map = m
        return self._series_map

    def _league_for_event(self, e: dict) -> str | None:
        for t in e.get("tags") or []:
            try:
                lg = POLY_TAG_TO_LEAGUE.get(int(t.get("id")))
            except (TypeError, ValueError):
                lg = None
            if lg:
                return lg
        smap = self._series_league_map()
        for sr in e.get("series") or []:
            lg = smap.get(str(sr.get("id")))
            if lg:
                return lg
        return None

    def outcomes_from_event(self, e: dict, league: str | None = None, include_closed: bool = False) -> list[Outcome]:
        tags = [t.get("slug", "") for t in (e.get("tags") or [])]
        league = league or self._league_for_event(e)
        out: list[Outcome] = []
        for m in e.get("markets") or []:
            if not include_closed:
                if m.get("closed") or not m.get("active", True) or m.get("enableOrderBook") is False:
                    continue
                if m.get("acceptingOrders") is False:
                    continue
            names = _json_list(m.get("outcomes"))
            tokens = _json_list(m.get("clobTokenIds"))
            prices = _json_list(m.get("outcomePrices"))
            if len(names) != 2 or len(tokens) != 2:
                continue
            rate = poly_rate_for_tags(tags, fees_enabled=bool(m.get("feesEnabled", True)), default=self.s.poly_fee_default)
            bid0, ask0 = _fl(m.get("bestBid")), _fl(m.get("bestAsk"))
            game_time = parse_dt(m.get("gameStartTime"))
            close = parse_dt(m.get("endDate") or e.get("endDate"))
            base_meta = {
                "gamma_id": str(m.get("id")),
                "slug": m.get("slug") or "",
                "event_slug": e.get("slug") or "",
                "question": m.get("question") or "",
                "group_title": m.get("groupItemTitle") or "",
                "sports_type": m.get("sportsMarketType") or "",
                "line": m.get("line"),
                "neg_risk": bool(m.get("negRisk") or e.get("negRisk")),
                "tags": tags,
            }
            url = f"https://polymarket.com/event/{e.get('slug', '')}"
            is_yes_no = [n.lower() for n in names] == ["yes", "no"]
            vol = float(m.get("volumeNum") or 0)
            if is_yes_no:
                q = m.get("question") or ""
                label = m.get("groupItemTitle") or q
                mm = _WILL_WIN.match(q)
                team_text = mm.group(1) if mm else (m.get("groupItemTitle") or "")
                out.append(Outcome(
                    venue=POLYMARKET, id=tokens[0], market_id=m.get("conditionId") or tokens[0],
                    event_id=str(e.get("id")), title=label, event_title=e.get("title") or "",
                    category=",".join(tags), league=league, game_time=game_time, close_time=close,
                    yes_bid=bid0, yes_ask=ask0, fee_rate=rate, volume=vol, url=url,
                    meta={**base_meta, "no_token": tokens[1], "outcome_index": 0, "team_text": team_text,
                          "outcome_name": "Yes"},
                ))
            else:
                # e.g. ["Patriots", "Seahawks"]: each token is its own outcome
                for i, (name, tok) in enumerate(zip(names, tokens)):
                    if i == 0:
                        yb, ya = bid0, ask0
                    else:
                        yb = None if ask0 is None else round(1 - ask0, 4)
                        ya = None if bid0 is None else round(1 - bid0, 4)
                    out.append(Outcome(
                        venue=POLYMARKET, id=tok, market_id=m.get("conditionId") or tok,
                        event_id=str(e.get("id")), title=f"{name}", event_title=e.get("title") or "",
                        category=",".join(tags), league=league, game_time=game_time, close_time=close,
                        yes_bid=yb, yes_ask=ya, fee_rate=rate, volume=vol, url=url,
                        meta={**base_meta, "no_token": tokens[1 - i], "outcome_index": i, "team_text": name,
                              "outcome_name": name},
                    ))
        return out

    def outcomes_from_events(self, events, league: str | None = None, include_closed: bool = False) -> list[Outcome]:
        out: list[Outcome] = []
        for e in events:
            out.extend(self.outcomes_from_event(e, league, include_closed))
        return out

    def closed_events_for_sport(self, sport_key: str, since: datetime, max_pages: int = 40) -> list[dict]:
        """Closed (settled) game events for a sport, newest first, back to `since`."""
        series_ids = [str(sp.get("series")) for sp in self.sports() if sp.get("sport") == sport_key and sp.get("series")]
        out: dict[str, dict] = {}
        for sid in series_ids:
            offset = 0
            for _ in range(max_pages):
                try:
                    d = self.gamma.get("events", {"series_id": sid, "closed": "true", "limit": 100, "offset": offset,
                                                  "order": "endDate", "ascending": "false"})
                except httpx.HTTPStatusError:
                    break
                if not d:
                    break
                stop = False
                for e in d:
                    out[str(e.get("id"))] = e
                    starts = [parse_dt(m.get("gameStartTime")) for m in e.get("markets") or [] if m.get("gameStartTime")]
                    if starts and max(starts) < since:
                        stop = True
                if stop or len(d) < 100:
                    break
                offset += 100
        return list(out.values())

    def price_history(self, token_id: str, start_ts: int, end_ts: int, fidelity: int = 1) -> list[tuple[int, float]]:
        """Historical (roughly per-minute) price points for one token."""
        d = self.clob.get("prices-history", {"market": token_id, "startTs": start_ts, "endTs": end_ts, "fidelity": fidelity})
        return [(int(x["t"]), float(x["p"])) for x in (d.get("history") or []) if x.get("p") is not None]

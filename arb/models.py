"""Venue-agnostic data model.

Everything tradable is reduced to an `Outcome`: a binary contract that pays $1
if the outcome happens. A Kalshi market's YES side is one Outcome; each
Polymarket CLOB token is one Outcome. Buying "NO" on an outcome is expressed
through the outcome's `no_*` prices / `no_asks` ladder, so the arbitrage code
never needs venue-specific branches.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

KALSHI = "kalshi"
POLYMARKET = "polymarket"
SPORTSBOOK = "sportsbook"


@dataclass(slots=True)
class Level:
    price: float
    size: float  # contracts / shares (each pays $1)


@dataclass(slots=True)
class Book:
    """Full ladder for one binary outcome. Asks ascending, bids descending."""

    yes_asks: list[Level] = field(default_factory=list)
    yes_bids: list[Level] = field(default_factory=list)
    no_asks: list[Level] = field(default_factory=list)
    no_bids: list[Level] = field(default_factory=list)

    @staticmethod
    def from_yes_no_bids(yes_bids: list[Level], no_bids: list[Level]) -> "Book":
        """Kalshi publishes only resting bids per side. A NO bid at p is a
        YES ask at 1-p (and vice-versa)."""
        yes_bids = sorted(yes_bids, key=lambda l: -l.price)
        no_bids = sorted(no_bids, key=lambda l: -l.price)
        yes_asks = sorted((Level(round(1 - l.price, 4), l.size) for l in no_bids), key=lambda l: l.price)
        no_asks = sorted((Level(round(1 - l.price, 4), l.size) for l in yes_bids), key=lambda l: l.price)
        return Book(yes_asks=yes_asks, yes_bids=yes_bids, no_asks=no_asks, no_bids=no_bids)

    @staticmethod
    def from_token_books(yes_asks, yes_bids, no_asks=None, no_bids=None) -> "Book":
        """Polymarket: each token has its own asks/bids. If the NO token's book
        is missing, derive it from the YES book (NO ask = 1 - YES bid)."""
        yes_asks = sorted(yes_asks, key=lambda l: l.price)
        yes_bids = sorted(yes_bids, key=lambda l: -l.price)
        if no_asks is None:
            no_asks = [Level(round(1 - l.price, 4), l.size) for l in yes_bids]
        if no_bids is None:
            no_bids = [Level(round(1 - l.price, 4), l.size) for l in yes_asks]
        return Book(
            yes_asks=yes_asks,
            yes_bids=yes_bids,
            no_asks=sorted(no_asks, key=lambda l: l.price),
            no_bids=sorted(no_bids, key=lambda l: -l.price),
        )

    def best(self, side: str) -> Level | None:
        ladder = self.yes_asks if side == "yes" else self.no_asks
        return ladder[0] if ladder else None


@dataclass(slots=True)
class Outcome:
    venue: str
    id: str  # kalshi: market ticker; polymarket: CLOB token id
    market_id: str  # kalshi: ticker; polymarket: conditionId
    event_id: str
    title: str  # short human label, e.g. "Patriots win"
    event_title: str
    category: str = ""
    league: str | None = None
    team: str | None = None  # canonical team id (ESPN id) when this is a game moneyline leg
    game_time: datetime | None = None
    close_time: datetime | None = None
    yes_bid: float | None = None
    yes_ask: float | None = None
    yes_bid_size: float = 0.0
    yes_ask_size: float = 0.0
    fee_rate: float = 0.0  # taker fee coefficient in fee = rate * q * p * (1-p)
    fee_round_cents: bool = False  # Kalshi rounds each order's fee up to the cent
    volume: float = 0.0
    url: str = ""
    meta: dict[str, Any] = field(default_factory=dict)
    book: Book | None = None

    @property
    def no_bid(self) -> float | None:
        return None if self.yes_ask is None else round(1 - self.yes_ask, 4)

    @property
    def no_ask(self) -> float | None:
        return None if self.yes_bid is None else round(1 - self.yes_bid, 4)

    @property
    def mid(self) -> float | None:
        if self.yes_bid is None or self.yes_ask is None:
            return None
        return (self.yes_bid + self.yes_ask) / 2

    def asks(self, side: str) -> list[Level]:
        """Ask ladder for buying `side` ('yes' or 'no'). Falls back to
        top-of-book from the list endpoint when no full book was fetched."""
        if self.book is not None:
            return self.book.yes_asks if side == "yes" else self.book.no_asks
        if side == "yes":
            return [Level(self.yes_ask, self.yes_ask_size)] if self.yes_ask is not None and self.yes_ask_size > 0 else []
        return [Level(self.no_ask, self.yes_bid_size)] if self.no_ask is not None and self.yes_bid_size > 0 else []

    @property
    def key(self) -> str:
        return f"{self.venue}:{self.id}"


@dataclass(slots=True)
class BookOdds:
    """A sportsbook moneyline quote for one team."""

    bookmaker: str
    team: str  # canonical team id
    american: int
    decimal: float

    @property
    def implied(self) -> float:
        return 1.0 / self.decimal


@dataclass(slots=True)
class Game:
    """A matched game across venues. `legs[team]` holds each venue's outcome."""

    key: str  # e.g. nfl:2026-09-10:17-26  (league:date:teamA-teamB sorted)
    league: str
    start: datetime | None
    home: str
    away: str
    home_name: str
    away_name: str
    kalshi: dict[str, Outcome] = field(default_factory=dict)  # team id -> outcome
    polymarket: dict[str, Outcome] = field(default_factory=dict)
    sportsbook: dict[str, list[BookOdds]] = field(default_factory=dict)  # team id -> quotes
    espn_event_id: str | None = None
    espn_state: str = ""  # pre | in | post (when ESPN knows the game)
    exact_start: bool = False  # start came from Polymarket/ESPN (not just Kalshi's date)

    def is_live_or_done(self, now: datetime) -> bool:
        """True once the game has (probably) started. Kalshi-only games carry
        just a date, so they count as started from the following day."""
        if self.espn_state in ("in", "post"):
            return True
        if self.start is None:
            return False
        if self.exact_start:
            return self.start <= now
        return self.start.date() < now.date()

    def team_name(self, team: str) -> str:
        return self.home_name if team == self.home else self.away_name


@dataclass(slots=True)
class Leg:
    venue: str
    outcome_id: str
    market_id: str
    side: str  # 'yes' | 'no' | 'bet'
    label: str
    qty: float  # contracts (payout $ if wins)
    avg_price: float  # per contract (for sportsbook: stake per $1 payout = 1/decimal)
    cost: float  # qty * avg_price
    fee: float
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class Opportunity:
    kind: str  # 'cross' | 'intra' | 'vegas'
    match_key: str
    description: str
    legs: list[Leg]
    qty: float
    cost: float  # total outlay incl. fees
    payout: float  # guaranteed payout (= qty for a complete hedge)
    ts: datetime

    @property
    def fees(self) -> float:
        return sum(l.fee for l in self.legs)

    @property
    def profit(self) -> float:
        return self.payout - self.cost

    @property
    def margin(self) -> float:
        return self.profit / self.cost if self.cost else 0.0

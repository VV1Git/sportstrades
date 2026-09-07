# sportstrades

Simulated (paper-only) arbitrage scanner across **Kalshi**, **Polymarket** and **sportsbook moneylines**.
It answers two questions with live data and a persistent paper-trading ledger:

1. If you had bought every cross-venue mispricing between Kalshi and Polymarket the moment it appeared,
   net of each venue's taker fees and only up to the size actually resting in the order books,
   would you have made money?
2. Where do Vegas lines and the prediction markets disagree, and is any of that disagreement a risk-free hedge?

Nothing here places orders. No API keys or accounts are needed for Kalshi, Polymarket or the sportsbook
feed. All "trades" are rows in a local SQLite file that get settled later against the venues' public
resolution data.

## What it does

```
Kalshi  ──/markets, /orderbook──┐
Polymarket ──Gamma + CLOB books─┼─► normalise to $1-payout outcomes ─► match the same game/event
ESPN (DraftKings lines) ────────┘         across venues ─► walk both books, subtract fees ─► arb?
The Odds API (optional key) ────┘                                            │
                                                     SQLite ledger ◄─ paper fills ─┘ ─► settle ─► P&L
```

**Three kinds of hedge are checked on every scan**

| kind    | legs                                                                                  | risk-free when |
|---------|---------------------------------------------------------------------------------------|----------------|
| `cross` | YES on venue A + NO on venue B for the same outcome                                   | `ask_A + ask_B + fees < 1` |
| `intra` | YES on every outcome of a mutually exclusive set at one venue (both teams, or home/draw/away) | `Σ asks + fees < 1` |
| `vegas` | moneyline bet on a team at a sportsbook + NO on that team at Kalshi/Polymarket        | `1/decimal + no_ask + fee < 1` |

Sizes come from walking the real order-book ladders on both legs simultaneously, stopping at the first
level where the marginal contract stops being profitable. Everything is priced as a **taker** on both
venues (you cannot rest orders and still call it an arb).

**Vegas vs prediction markets.** For every game with a sportsbook line the scanner de-vigs the moneyline
(proportional method), then reports `fair − PM ask` (positive: the prediction market is cheap relative to
Vegas) and `PM bid − fair` (positive: it is rich). Those are pricing disagreements, not arbitrage, but they
are where any edge would live and they are logged every scan for later analysis.

### Fee model

| venue      | taker fee                                             | source |
|------------|-------------------------------------------------------|--------|
| Kalshi     | `0.07 × contracts × p × (1−p)`, rounded **up** to the cent per order; series `fee_multiplier` applied (e.g. MLB game markets 0.5) | Kalshi fee schedule (July 2026) |
| Polymarket | `0.05 × shares × p × (1−p)` for sports (0.07 crypto, 0.04 politics/finance/tech, 0 geopolitics); makers pay nothing | docs.polymarket.com/polymarket-learn/trading/fees |
| Sportsbook | none (the vig is in the price)                        |        |

Both are configurable in `.env` (`KALSHI_TAKER_RATE`, `POLY_FEE_RATE_DEFAULT`).

### Matching

Sports games are matched **structurally**, never by fuzzy text: league (Kalshi series ticker ↔ Polymarket
sport tag ↔ ESPN league) + the two teams (resolved onto ESPN's team registry, which handles Kalshi's
`"New York G"` / `"LAR"` and Polymarket's `"Patriots"`) + the local game date. A team pair that plays twice
on one date (double-header) is skipped as ambiguous rather than guessed. Games that have already started are
excluded by default (`--include-live` to override) because in-play prices move faster than two legs can be
filled.

Non-sports events (Fed decisions, elections, crypto strikes, …) are only reachable with `--scope all`, which
crawls every open market on both venues (~100k Kalshi outcomes, ~260k Polymarket outcomes, about 90 s) and runs a
conservative fuzzy matcher (mean of token-set and token-sort similarity ≥ 85, resolution dates within 45
days, **all numbers in the two titles must agree** so "above $100k" never pairs with "above $110k").
Candidates are printed by `arb match --scope all` for review. By default a fuzzy pair is **never**
paper-traded until you confirm it in `data/pairs.yaml` (pass `--general-threshold 97` to opt in to
auto-trading high-scoring pairs; the first crawl scored "Trump impeached *and removed*" against "Trump
impeached *before his term ends*" at 100 on token-set similarity alone, which is why that is opt-in):

```yaml
pairs:
  - kalshi: KXFEDDECISION-26SEP-C25      # Kalshi market ticker
    polymarket: 0x1189…                  # Polymarket conditionId (or a YES token id)
    relation: same                       # or "complement" if the Kalshi YES is the Polymarket NO
```

## Install

```bash
python3.13 -m venv .venv && .venv/bin/pip install -e ".[dev]"
cp .env.example .env            # optional: add ODDS_API_KEY for multi-book lines
.venv/bin/python -m pytest      # 23 unit tests: odds math, fees, book walking, matching, paper ledger
```

## Use

```bash
arb scan                        # one pass over 13 major leagues, paper-trades anything risk-free, prints Vegas gaps
arb scan --leagues nfl --games  # NFL only, show the matched-games table with all three venues' quotes
arb scan --leagues niche        # 28 niche leagues (ITF/ATP/WTA tennis, CS2, Dota 2, LoL, KBO, NPB, KHL, 2nd-tier soccer)
arb scan --scope all            # also crawl every open market and fuzzy-match non-sports events
arb run --interval 120          # keep scanning; settles open paper trades every 10 scans
arb live --leagues mlb,ncaaf --interval 6 --duration 7200   # sample both venues every 6 s during games
arb live-report                 # pre-game vs in-game decoupling: gaps, phantom crossings, episode lengths
arb vegas --min-edge 0.02       # only the sportsbook comparison, gaps ≥ 2 points, both de-vig methods
arb match --scope all --top 30  # inspect matches and fuzzy pair candidates
arb backtest --leagues mlb --days 30            # replay past games minute by minute (Kalshi candles + Polymarket history)
arb settle                      # settle paper trades against Kalshi/Polymarket results and ESPN finals
arb report                      # P&L summary, open/settled trades, last scan's discrepancies
```

### Running it all the time

```bash
nohup scripts/paper_trader.sh >/dev/null 2>&1 &   # scans every league both venues list, every 60 s, forever
cat data/status.txt                                # one-line answer: what the simulation has made so far
tail -f data/logs/paper_trader.log                 # every scan, every paper fill, every settlement
arb report                                         # full ledger
```

`scripts/paper_trader.sh` restarts `arb run` if it ever dies and rewrites `data/status.json` / `data/status.txt`
after every scan (cash, deployed, realized P&L, locked-in profit on open hedges, last scan's opportunities). To keep
it running across reboots install `scripts/com.sportstrades.paper-trader.plist` as a LaunchAgent (instructions in the
file). Tune with `INTERVAL=90 LEAGUES=all scripts/paper_trader.sh`.

### Historical replay

`arb backtest` pulls, for every settled game matched across the venues in the last N days, Kalshi's one-minute
candlesticks (closing YES bid and ask per minute) and Polymarket's per-minute price history for the same team, aligns
them and asks at each minute whether YES on one venue plus NO on the other would have cost less than $1 after fees.
Polymarket's history is a mid price, so a spread is assumed (`--poly-spread`, default 2¢). Two numbers come out: profit
if a zero-latency taker filled `--size` contracts on every one-minute signal (an upper bound), and profit on signals
that were still there a full minute later (what a human-speed trader could act on).

Two data quirks are handled explicitly, and both would otherwise manufacture arbitrage out of nothing. Polymarket's
history point stamped at minute *m* holds the price at the *start* of *m*, so the replay pairs Kalshi's minute-*m* close
with Polymarket's point stamped *m+1* (verified against both venues' trade prints). And an empty or one-sided Polymarket
book reports a "mid" of 0.50, so a minute only counts when Polymarket's trade tape shows a trade within the previous
three minutes at a price within a dime of the history point. Data is cached under `data/cache/hist/`.

Findings from the first day of scans, including the in-game and niche-market analysis, are written up in
[docs/ANALYSIS.md](docs/ANALYSIS.md).

Knobs (`.env` or flags): `PAPER_BANKROLL` (10 000), `PAPER_MAX_PER_TRADE` (500), `PAPER_MAX_PER_KEY`
(1 000 per game/market), `PAPER_MIN_MARGIN` (0.5 % net), `BOOK_MAX_STAKE` (500, assumed sportsbook
liquidity per bet). Everything is written to `data/paper.db`.

Major leagues (ESPN team registry, DraftKings lines): `nfl ncaaf nba wnba ncaab mlb nhl epl laliga bundesliga seriea
ligue1 mls ucl`. Niche leagues (participants clustered from the venues' own names; some with ESPN lines):
`itf_m itf_w atp wta cs2 dota2 lol valorant r6 khl npb kbo championship league_one ligamx brasileirao brasileirao_b
saudi uel eredivisie kleague jleague usl ligue2 liga_portugal egypt serie_b eerste`. Coverage on any given day
depends on what each venue has listed.

## What the first scans found (6 Sep 2026)

* **Both prediction markets are tight on the same games.** 266 games were live on both Kalshi and
  Polymarket at once (NFL week 1, MLB, NCAAF, EPL, La Liga, Bundesliga, Ligue 1, MLS). On nearly all of
  them the two venues' best bid/ask were within one cent of each other, so `ask + ask` sums to ≥ 1.00
  before fees. The 5–7 % round-trip fee load on a 50/50 contract makes a cross-venue arb need a ≥ 3-point
  gap, which did not exist on any liquid game.
* **The only "arbs" were crumbs**: a 27-contract Mets/Marlins hedge worth $0.23 (0.9 %) in a thin book,
  and a WNBA game 11 days out where a stale DraftKings +100 line against a Polymarket 0.52 quote priced a
  0.75 % hedge, assuming the book actually takes $500 at that number.
* **Vegas vs prediction markets** disagree by 1–3 points on most NFL week-1 games and up to ~4 points on
  WNBA/MLB, but the sign is inconsistent (sometimes the PM is rich, sometimes cheap) and the gap sits
  inside the sportsbook's own vig, so none of it converts to a risk-free hedge with the default fees.
* **Matching quality matters more than the math.** A first version that matched games by team pair within
  ±36 h "found" 18 arbs at 2–7 %; every one was a different game of the same MLB series. Exact local-date
  matching removed all of them.

Run `arb run` for a few days and `arb report` to see whether the crumbs add up to anything after settlement.

## Limitations, honestly

* Fills are simulated at the displayed book depth at scan time. Real execution is two independent orders on
  two venues seconds apart; the second leg can move, leaving you unhedged. The ledger does not model that.
* Kalshi's public orderbook is polled (~8 req/s) rather than streamed, so a scan is a snapshot up to ~1
  minute stale across leagues.
* Sportsbook lines come from ESPN's DraftKings feed by default: one book, moneyline only, no depth. Set
  `ODDS_API_KEY` for best-of-many-books prices. Sportsbook legs assume ties push (stake returned); Kalshi
  and Polymarket pay 0.50 per side on a tie, which the settlement code mirrors.
* Non-sports matching is heuristic and off by default for trading; treat `arb match --scope all` output as
  a review queue.
* This is a research tool. It is not investment advice, and it does not and will not place real orders.

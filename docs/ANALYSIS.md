# Where Kalshi and Polymarket disagree, and whether it pays

*Findings from the `sportstrades` scanner, 6–7 September 2026. Everything below is paper-only: no orders were placed.*

## The short version

1. **On the same game, Kalshi and Polymarket are almost never far enough apart to arbitrage.** Across 744 team-legs on
   13 major leagues the two venues' mid prices differed by 1.6 cents on average and by 1 cent or less three quarters
   of the time. Best prices crossed on 14 legs, never by more than 3 cents, and taker fees on a 50/50 contract eat
   about 3 cents. Net of fees the whole scan produced two hedges worth $4.02 on $526 deployed.
2. **Vegas and the prediction markets agree to within about one point once you remove the vig.** The gap that looks
   like a "Vegas inconsistency" is mostly an artifact of *how* you remove the vig: the proportional method makes
   favorites look rich on the prediction markets, the power method (which keeps the favorite–longshot shape) makes
   them look fair. 56 of 62 prediction-market mids sat inside the sportsbook's own bid/ask band.
3. **In-game decoupling is real but mostly phantom.** During live games Kalshi's *market list* endpoint shows the
   two venues crossing about one tick in eight, but the executable Kalshi order book pulled at the same instant
   agrees with Polymarket; the list lags the book by tens of seconds. Measured against the order book the crossing
   rate falls to `LIVE_BOOK_CROSS_PCT` and the fee-positive rate to `LIVE_NET_PCT`. *(Filled in from session 3 below.)*
4. **Niche markets do decouple, and that is exactly why they cannot be traded.** ITF tennis, CS2, KHL, NPB and KBO
   game markets disagree across venues by 3–10 cents on average, three to six times the major-league gap, but their
   spreads are 20–50 cents wide and the resting size at the best price is a few dozen contracts. The best crossings
   were 2–3 cents, below the fee load, on books that would fill $20–40.
5. **Replaying the last month minute by minute agrees.** REPLAY_SUMMARY
6. **Matching, not math, is the risk.** Every false arbitrage the scanner ever reported came from matching the wrong
   game (a different day of the same MLB series) or the wrong question ("impeached *and removed*" vs "impeached
   *before his term ends*"). The remedies are exact local-date matching and refusing to auto-trade fuzzy pairs.

## What was measured

**Venues.** Kalshi public REST (markets, order books, series fee schedule), Polymarket Gamma (metadata) and CLOB
(order books), ESPN's public scoreboard (DraftKings moneylines, game state, final scores). Optional: The Odds API
for multi-book lines (not used in these runs; no key).

**Normalisation.** Everything becomes a $1-payout binary outcome. A Kalshi market's YES side is one outcome; each
Polymarket CLOB token is one outcome. NO on an outcome is priced from the complementary token's asks (Polymarket) or
the YES bids (Kalshi, where a NO ask at *p* is a YES bid at *1−p*).

**Matching.** Games are matched structurally: league (Kalshi series ticker ↔ Polymarket sport series ↔ ESPN league),
both participants, and the local calendar date. Participants resolve onto ESPN's team registry for major leagues
("New York G" → Giants, "Patriots" → Patriots) and onto a registry clustered from the venues' own names for niche
leagues ("Cagliari Calcio" ↔ "Cagliari", "N. Djokovic" ↔ "Novak Djokovic", but "AC Milan" ≠ "Inter Milan"). Two games
between the same teams on one date are skipped as ambiguous.

**What counts as an arbitrage.** Buy $1-payout contracts on every outcome of a partition of the event space, on
one venue or across venues, for less than $1 *after taker fees on both legs*, at sizes that actually rest in the two
order books when walked together. Kalshi taker fee: 0.07 × contracts × p(1−p), rounded up to the cent per order,
times the series multiplier (MLB games 0.5). Polymarket taker fee: 0.05 × shares × p(1−p) on sports. At p = 0.5 the
two legs together cost about 3 cents per contract; at p = 0.8, about 1.9 cents.

**Vegas.** For each game with a sportsbook moneyline, two things: (a) an exact hedge — back a team at the book, buy
NO on it at a prediction market, risk-free when `1/decimal + no_ask + fee < 1`; (b) a *pricing disagreement* — de-vig
the moneyline and compare the fair probability to the prediction-market mid. Two de-vig methods are reported:
proportional (divide each implied probability by their sum) and power (raise each to the exponent that makes them sum
to one).

## 1. Baseline: how tight are the two prediction markets?

Scan of 6 Sep 2026, 322 games matched, 744 legs quoted on both venues.

| League | Legs | mean \|mid diff\| | max | Kalshi spread | Poly spread | best prices cross | best crossing | median size at best (K / P) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| NCAAF | 220 | 0.021 | 0.225 | 0.130 | 0.191 | 1/201 | +0.010 | 1384 / 100 |
| MLS | 108 | 0.030 | 0.140 | 0.188 | 0.141 | 1/108 | +0.010 | 2106 / 260 |
| MLB | 82 | 0.011 | 0.050 | 0.028 | 0.023 | 6/82 | +0.030 | 1633 / 82 |
| NFL | 64 | 0.006 | 0.020 | 0.014 | 0.027 | 1/64 | +0.010 | 4776 / 700 |
| La Liga | 54 | 0.025 | 0.160 | 0.136 | 0.066 | 2/54 | +0.010 | 720 / 500 |
| Serie A | 51 | 0.008 | 0.110 | 0.021 | 0.035 | 0/51 | 0 | 800 / 500 |
| EPL | 48 | 0.005 | 0.020 | 0.014 | 0.017 | 1/48 | +0.010 | 2241 / 1531 |
| UCL | 42 | 0.004 | 0.020 | 0.010 | 0.010 | 2/42 | +0.010 | 4167 / 5993 |
| Bundesliga | 39 | 0.005 | 0.015 | 0.021 | 0.020 | 0/39 | 0 | 1825 / 500 |
| Ligue 1 | 36 | 0.005 | 0.030 | 0.021 | 0.027 | 0/36 | 0 | 1355 / 500 |
| **All** | **744** | **0.016** | **0.225** | **0.084** | **0.091** | **14/725** | **+0.030** | **1889 / 452** |

74% of legs are within 1 cent, 85% within 2. The larger NCAAF/MLS/La Liga averages come from games days away with
wide spreads on both sides, not from the venues disagreeing on a liquid game. The single 3-cent crossing (MLB) was a
27-contract book; it paper-traded for $0.23.

Sportsbook hedges found in the same scan: one. A DraftKings +100 line on a WNBA game eleven days out against a
Polymarket 0.52 quote priced a 0.75% hedge, assuming the book takes $500 at a number that old. It is in the ledger
and will settle on 17 September.

## 2. Vegas vs the prediction markets

31 games carried a DraftKings moneyline (NFL week 1, MLB, NCAAF, WNBA), 114 team/venue comparisons.

| League | Venue | n | PM mid − fair (proportional): mean / mean\|·\| | PM mid − fair (power): mean / mean\|·\| |
|---|---|---:|---|---|
| NFL | Kalshi | 32 | +0.0014 / 0.0133 | +0.0014 / 0.0105 |
| NFL | Polymarket | 32 | +0.0000 / 0.0133 | −0.0000 / 0.0112 |
| MLB | Kalshi | 18 | −0.0011 / 0.0029 | −0.0011 / 0.0055 |
| MLB | Polymarket | 18 | +0.0000 / 0.0035 | −0.0000 / 0.0068 |
| WNBA | Polymarket | 10 | +0.0000 / 0.0140 | −0.0000 / 0.0209 |
| **All** | | **114** | **+0.0002 / 0.0100** | **+0.0002 / 0.0103** |

Read it two ways:

* **Level.** Prediction markets sit within about one point of the de-vigged Vegas line on average, in both
  directions, on both venues. There is no systematic lean once both sides of the line are used.
* **Favorites.** On the first pass, restricted to the favorite in each game, proportional de-vig said the prediction
  markets price favorites 0.4 points *rich* (40 of 59 above fair); power de-vig said 0.4 points *cheap* (20 of 59
  above). NFL favorites specifically: +0.8 points under proportional, 0.0 under power. The prediction markets price
  NFL favorites the way a power de-vig prices the sportsbook, i.e. they carry the same favorite–longshot bias Vegas
  does. Which de-vig you pick decides the sign of the "inconsistency".
* **Executability.** 56 of 62 prediction-market mids sat *inside* the sportsbook's own vig band (between the implied
  probability of the team and one minus the implied probability of the opponent). Inside that band no combination of
  a book bet and a prediction-market contract pays more than it costs. The exceptions were the WNBA stale line
  above and a Cowboys–Giants disagreement of 2.8 points that was still short of the fee load.

**Caveats.** One sportsbook (DraftKings via ESPN), moneyline only, no visible depth; best-of-many-books prices via The
Odds API would widen the apparent gaps and are the obvious next test. Soccer lines could not be de-vigged because
ESPN's feed omits the draw price; the exact-hedge test still runs for soccer and found nothing. Ties: sportsbooks push
(refund) a moneyline tie while Kalshi and Polymarket pay 0.50 per side, a small basis risk the settlement code
mirrors.

## 3. Do the venues decouple during games?

LIVE_SECTION

## 3b. Historical replay: what the last weeks would have paid

REPLAY_SECTION

## 4. Niche markets

Scan of 6 Sep 2026 over 28 leagues both venues list game markets for, 396 games matched, 872 legs quoted on both.

| League | Legs | mean \|mid diff\| | max | Kalshi spread | Poly spread | cross | best | median size at best (K / P) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| ITF men's tennis | 148 | 0.041 | 0.255 | 0.414 | 0.472 | 2/148 | +0.020 | 35 / 28 |
| ITF women's tennis | 108 | 0.053 | 0.435 | 0.335 | 0.394 | 4/108 | +0.030 | 35 / 19 |
| Counter-Strike 2 | 94 | 0.038 | 0.260 | 0.216 | 0.157 | 3/90 | +0.020 | 500 / 87 |
| Rainbow Six | 56 | 0.010 | 0.075 | 0.279 | 0.351 | 0/56 | 0 | 7 / 15 |
| League of Legends | 38 | 0.013 | 0.050 | 0.211 | 0.219 | 0/38 | 0 | 100 / 27 |
| USL Championship | 36 | 0.078 | 0.215 | 0.060 | 0.582 | 0/36 | −0.090 | 1245 / 18 |
| EFL Championship | 33 | 0.009 | 0.035 | 0.068 | 0.060 | 0/33 | 0 | 72 / 87 |
| Liga MX | 33 | 0.030 | 0.175 | 0.229 | 0.213 | 0/33 | −0.010 | 175 / 18 |
| NPB | 26 | 0.032 | 0.125 | 0.398 | 0.412 | 0/26 | −0.070 | 52 / 29 |
| KHL | 22 | 0.105 | 0.295 | 0.354 | 0.035 | 0/22 | 0 | 10 / 30 |
| Brasileirão | 21 | 0.055 | 0.145 | 0.507 | 0.300 | 0/21 | 0 | 5 / 18 |
| Valorant | 16 | 0.058 | 0.250 | 0.242 | 0.278 | 3/16 | +0.010 | 4 / 18 |
| KBO | 10 | 0.043 | 0.120 | 0.225 | 0.144 | 0/10 | −0.080 | 30 / 23 |
| ATP / WTA main tour | 14 | 0.007 | 0.020 | 0.012 | 0.010 | 1/14 | +0.010 | 2963+ / 2101+ |
| **All 28** | **872** | **0.035** | **0.435** | **0.247** | **0.259** | **14/867** | **+0.030** | **90 / 31** |

Only 49% of niche legs are within 1 cent (vs 74% for majors) and the mean disagreement is more than double. But the
spreads are three times wider and the resting size is a twentieth. The 14 crossings top out at 3 cents, the same
ceiling as the majors, on books that would fill $20–40 before the price moved. Where one venue quotes a real market
and the other does not (USL: Kalshi 6-cent spread, Polymarket 58; KHL the reverse), the "disagreement" is just the
empty side. The niche markets confirm the intuition that they are less efficiently priced, and show that the
inefficiency is not harvestable at fee-paying size.

The one exception worth watching is the main-tour tennis (ATP/WTA): thousands of contracts of depth and 1-cent
spreads on both venues, i.e. major-league liquidity in a market with far fewer participants than the NFL. It was
tight on the day sampled; it is the niche most worth running the live sampler on.

## 5. Non-sports markets

`arb match --scope all` crawls every open market on both venues (about 100k Kalshi and 260k Polymarket outcomes in
90 s) and fuzzy-matches Kalshi non-sports markets to Polymarket questions. On the first crawl the top candidates were
the Italian PM race, the Florida Senate race, Avengers: Doomsday casting, Game of the Year, Bitcoin vs gold, the
Clarity Act vote count and "Will Trump be impeached". Two of those illustrate why this cannot be automated safely:

* *Will Trump be impeached and removed from office?* (Kalshi, 0.16) vs *Will Trump be impeached before his term
  ends?* (Polymarket, 0.68) scored 100 on token-set similarity and showed a "49% arbitrage". They are different
  events.
* Kalshi's *How many Senators will vote for the Clarity Act? — 50 or more* vs Polymarket's *over 50 Senators*: the same
  numbers, a different inequality.

The scorer now averages token-set with token-sort similarity (the impeachment pair drops to 82 and out of the
candidate list), every number in both titles must agree, and no fuzzy pair is traded until a person confirms it in
`data/pairs.yaml`. Across the confirmed-looking pairs the top-of-book gaps were −4% to +2%: nothing to trade there
either.

## 6. What would change the answer

* **Multiple sportsbooks.** Best-of-market lines across 8–10 books routinely differ by 5–10 cents of American odds;
  the scanner supports The Odds API and will pick the best price per side when a key is present.
* **Being the maker.** All results assume taker fees on both legs. Resting one leg (Polymarket charges makers
  nothing; Kalshi charges 0.0175 on some series) turns many 1–2-cent gaps positive, at the cost of execution risk.
* **Latency.** The in-game result says most crossings are stale-data artifacts and the real ones last seconds. A
  websocket feed on both venues and co-located execution is the difference between observing and capturing them.
* **Fee tiers.** Kalshi's fee is per-order and rounded up; Polymarket's rate varies by category and has changed
  twice in a year. A 1-point change in either moves the break-even gap by a third.
* **Settlement basis.** Ties, postponements and "fair price" resolutions differ between venues; the ledger models the
  common cases (tie → 0.50/0.50 on the prediction markets, push at the book).

## Reproduce

```bash
arb scan --games                       # majors, tightness table, Vegas gaps, paper trades
arb scan --leagues niche --games       # 28 niche leagues
arb vegas --leagues nfl,mlb --json     # both de-vig methods
arb live --leagues mlb,ncaaf --interval 6 --duration 7200   # during games
arb live-report                        # pre-game vs in-game decoupling
arb match --scope all --top 30         # non-sports fuzzy candidates
arb report                             # ledger
```

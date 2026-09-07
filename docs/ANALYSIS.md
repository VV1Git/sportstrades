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
   rate falls to 2.2% and the fee-positive rate to 0.32%.
4. **Niche markets do decouple, and that is exactly why they cannot be traded.** ITF tennis, CS2, KHL, NPB and KBO
   game markets disagree across venues by 3–10 cents on average, three to six times the major-league gap, but their
   spreads are 20–50 cents wide and the resting size at the best price is a few dozen contracts. The best crossings
   were 2–3 cents, below the fee load, on books that would fill $20–40.
5. **Replaying the last month minute by minute agrees.** Over the last 30 / 14 (major / niche leagues) days and 3964 games, a zero-latency taker acting on every one-minute crossing would have made $3,778 at 50 contracts a signal; signals that lasted a second minute were worth $682. Pre-game minutes crossed in 6.7% of samples, in-game minutes in 7.2%.
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

**Hypothesis.** The two markets converge over long horizons but come apart in the high-volatility minutes of a live
game, when each order book is re-priced by different people at different speeds.

**Method.** Both venues were sampled every six seconds for every matched game in progress, with soon-to-start games as
the control group, over 02:22–04:19 UTC on 7 September (40,429 venue-pair samples, 58 games).
Two Kalshi quotes were recorded at each tick: the *market-list* endpoint (what a scanner naturally polls) and the
*order-book* endpoint (what an order actually hits). Polymarket's order books were pulled in the same call. "Books
cross" means the best prices on the two venues overlapped before fees; "phantom" means the list endpoint showed a
crossing that the executable book did not.

| Group / phase | Samples | Games | mean \|mid gap\| | p90 | max | books cross | phantom (list only) | net arb after fees | spreads K / P |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Major, in game | 2,984 | 3 | 0.4¢ | 1.0¢ | 12¢ | 5.0% | 18.6% | 0.10% | 1.1¢ / 1.1¢ |
| Niche, before the game | 13,460 | 24 | 2.6¢ | 5.0¢ | 20¢ | 4.5% | 0.0% | 0.00% | 12.1¢ / 11.6¢ |
| Niche, in game | 22,349 | 39 | 1.4¢ | 3.0¢ | 30¢ | 1.8% | 9.2% | 0.35% | 3.7¢ / 6.2¢ |

**Major leagues in game** (3 games, 2,984 samples): executable books 0.4¢ apart on
average (p90 1.0¢), crossing in 5.0% of samples. The list endpoint disagreed with the book by
2.5¢ on average during play and produced a phantom crossing in 18.6% of samples. Fee-positive
windows: 0.10%.

**Niche leagues in game**: 1.4¢ apart while live against 2.6¢ before the start; books crossed in
1.8% of live samples against 4.5% before, and 0.35% of live samples were fee-positive,
all on books a few dozen contracts deep.

**How long a window lasts**

| Window | Phase | Episodes | one tick only | median length (s) | longest (s) | peak |
|---|---|---:|---:|---:|---:|---:|
| best prices cross (before fees) | before the game | 4 | 0 | 1098 | 1812 | 2.0¢ |
| best prices cross (before fees) | in game | 251 | 172 | 6 | 326 | 16.0¢ |
| profitable after fees and depth | before the game | 0 | 0 | - | - | - |
| profitable after fees and depth | in game | 43 | 32 | 6 | 91 | 5.31% |

**Fee-positive windows recorded**

| Outcome | Phase | Start (UTC) | Ticks | Seconds | Peak margin | Game state |
|---|---|---|---:|---:|---:|---|
| Ko Suzuki | live | 04:14:14 | 2 | 8 | +5.31% |  |
| FarmVille | live | 02:52:57 | 1 | 0 | +3.82% |  |
| Nationals | live | 03:05:49 | 1 | 0 | +3.67% | Top 4th 1-1 |
| Beyond Limits | live | 02:23:08 | 1 | 0 | +3.09% |  |
| Jiaqi Wang | live | 03:42:33 | 1 | 0 | +2.82% |  |
| Jerry Roddick | live | 03:58:43 | 1 | 0 | +2.74% |  |
| Nationals | live | 03:10:22 | 1 | 0 | +2.41% | Top 4th 3-1 |
| Jiaqi Wang | live | 03:46:11 | 1 | 0 | +2.12% |  |

**The first-inning example.** At 02:11:54Z Polymarket moved the Dodgers from 0.60 to 0.66 in fifteen seconds; Kalshi's
list endpoint kept showing 0.59/0.61 for another fifty seconds, then jumped to 0.66/0.67. Kalshi's trade tape printed
zero trades in that window. Nobody took the 0.61 ask, because it was not there. Session 1 of the sampler polled only
the list endpoint and "saw" the venues cross in 12% of live samples; sessions 3–4 polled the book as well and the real
figure is the one in the table.

**Reading.** In-game volatility does open gaps that pre-game trading never shows, and the biggest ones are in the
thinnest books. Against executable prices the gaps are rarer, smaller and shorter than a list-endpoint scanner
suggests; the fee-positive windows last seconds and sit on a few dozen contracts. The decoupling is real, but it is a
latency race, not a convergence trade.


## 3b. Historical replay: what the last weeks would have paid

**Method.** For every settled game matched across the venues in the last 30 / 14 (major / niche leagues) days (3964 games,
8194 team-legs, 232,833 pre-game and 460,097 in-game minutes) the replay pulls Kalshi's one-minute
candlesticks (closing YES bid and ask each minute) and Polymarket's per-minute price history for the same team, aligns
them, and asks at every minute whether YES on one venue plus NO on the other would have cost less than $1 after taker
fees, with Polymarket's spread assumed at 2¢ around its recorded price. Two figures come out: a
*zero-latency taker* that fills 50 contracts on every one-minute signal the instant it appears (an upper bound
that also inherits any timing noise in minute data), and *persistent* signals still there a full minute later, roughly
what a person watching two screens could act on.

| League | Games | Minutes pre / live | mean \|mid gap\| pre / live | best prices cross pre / live | 1-min signals pre / live | profit, zero latency pre / live | persisted ≥2 min pre / live | profit, persistent pre / live |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| MLB | 397 | 77,338 / 109,823 | 0.4¢ / 0.5¢ | 1.2% / 3.8% | 0 / 895 | $0 / $720 | 0 / 18 | $0 / $16 |
| Counter-Strike 2 | 656 | 33,912 / 80,833 | 1.8¢ / 1.5¢ | 23.3% / 12.2% | 416 / 1129 | $382 / $641 | 217 / 124 | $255 / $55 |
| ITF Men's tennis | 976 | 2,656 / 41,179 | 1.5¢ / 0.9¢ | 17.8% / 6.1% | 11 / 503 | $6 / $289 | 4 / 32 | $3 / $15 |
| ATP tennis | 150 | 17,756 / 35,529 | 0.7¢ / 0.6¢ | 5.5% / 4.9% | 20 / 283 | $43 / $218 | 14 / 10 | $32 / $8 |
| ITF Women's tennis | 807 | 2,380 / 33,543 | 1.7¢ / 0.9¢ | 20.8% / 6.3% | 20 / 397 | $12 / $298 | 7 / 20 | $2 / $9 |
| League of Legends | 130 | 10,741 / 27,463 | 0.9¢ / 1.1¢ | 10.2% / 11.8% | 20 / 239 | $22 / $198 | 4 / 19 | $21 / $87 |
| WTA tennis | 140 | 9,814 / 25,769 | 0.7¢ / 0.6¢ | 4.3% / 4.9% | 24 / 210 | $60 / $158 | 18 / 9 | $49 / $7 |
| WNBA | 65 | 10,768 / 13,132 | 0.6¢ / 0.6¢ | 4.3% / 7.0% | 0 / 72 | $0 / $45 | 0 / 6 | $0 / $4 |
| Dota 2 | 78 | 2,657 / 13,018 | 1.6¢ / 1.4¢ | 21.1% / 9.9% | 25 / 183 | $23 / $126 | 14 / 22 | $14 / $55 |
| MLS | 59 | 6,852 / 12,755 | 0.6¢ / 0.7¢ | 4.2% / 7.2% | 0 / 124 | $0 / $78 | 0 / 6 | $0 / $2 |
| Valorant | 65 | 4,578 / 11,103 | 0.9¢ / 1.2¢ | 12.8% / 14.7% | 2 / 114 | $2 / $76 | 0 / 9 | $0 / $2 |
| La Liga | 35 | 14,079 / 9,803 | 0.5¢ / 0.6¢ | 1.3% / 4.6% | 0 / 73 | $0 / $66 | 0 / 1 | $0 / $2 |
| Premier League | 30 | 15,284 / 8,836 | 0.4¢ / 0.5¢ | 1.6% / 5.0% | 0 / 36 | $0 / $22 | 0 / 1 | $0 / $0 |
| College Football | 107 | 2,094 / 7,543 | 0.5¢ / 0.9¢ | 4.3% / 9.0% | 3 / 130 | $0 / $55 | 2 / 9 | $4 / $2 |
| Serie A | 25 | 6,364 / 6,700 | 0.5¢ / 0.6¢ | 1.2% / 4.6% | 1 / 46 | $2 / $26 | 0 / 0 | $0 / $0 |
| Ligue 1 | 20 | 3,802 / 4,752 | 0.4¢ / 0.6¢ | 1.7% / 5.2% | 0 / 44 | $0 / $23 | 0 / 2 | $0 / $2 |
| KBO baseball | 49 | 2,319 / 4,149 | 1.1¢ / 1.3¢ | 13.7% / 11.4% | 9 / 97 | $3 / $49 | 0 / 9 | $0 / $1 |
| Bundesliga | 16 | 3,371 / 3,736 | 0.5¢ / 0.5¢ | 0.9% / 3.9% | 0 / 14 | $0 / $11 | 0 / 0 | $0 / $0 |
| NPB baseball | 49 | 724 / 1,991 | 1.1¢ / 1.2¢ | 18.8% / 6.5% | 0 / 25 | $0 / $12 | 0 / 1 | $0 / $0 |
| K League 1 | 12 | 1,285 / 1,809 | 0.6¢ / 1.3¢ | 4.4% / 12.5% | 0 / 63 | $0 / $35 | 0 / 9 | $0 / $6 |
| Liga MX | 8 | 1,350 / 1,799 | 0.5¢ / 0.6¢ | 2.9% / 5.9% | 0 / 8 | $0 / $3 | 0 / 0 | $0 / $0 |
| EFL Championship | 5 | 734 / 836 | 0.4¢ / 0.6¢ | 2.7% / 5.0% | 0 / 4 | $0 / $5 | 0 / 0 | $0 / $0 |
| Egyptian Premier League | 4 | 328 / 636 | 0.8¢ / 0.8¢ | 8.8% / 7.1% | 0 / 12 | $0 / $4 | 0 / 0 | $0 / $0 |
| Liga Portugal | 5 | 352 / 635 | 0.5¢ / 0.6¢ | 3.7% / 2.8% | 0 / 4 | $0 / $1 | 0 / 0 | $0 / $0 |
| Serie B | 8 | 196 / 575 | 0.8¢ / 1.3¢ | 20.4% / 6.6% | 0 / 8 | $0 / $4 | 0 / 0 | $0 / $0 |
| Brasileirão Série B | 5 | 120 / 360 | 0.5¢ / 0.8¢ | 0.0% / 8.1% | 0 / 9 | $0 / $7 | 0 / 1 | $0 / $2 |
| USL Championship | 18 | 197 / 360 | 1.2¢ / 2.9¢ | 23.4% / 33.9% | 0 / 38 | $0 / $35 | 0 / 10 | $0 / $21 |
| Ligue 2 | 3 | 46 / 305 | 0.7¢ / 1.4¢ | 6.5% / 6.2% | 0 / 6 | $0 / $2 | 0 / 0 | $0 / $0 |
| Eerste Divisie | 7 | 93 / 231 | 0.5¢ / 2.3¢ | 6.5% / 10.8% | 0 / 9 | $0 / $5 | 0 / 1 | $0 / $0 |
| EFL League One | 6 | 139 / 224 | 0.7¢ / 1.7¢ | 9.4% / 6.7% | 0 / 6 | $0 / $2 | 0 / 0 | $0 / $0 |
| Rainbow Six | 21 | 154 / 217 | 1.8¢ / 3.1¢ | 15.6% / 8.3% | 1 / 4 | $0 / $3 | 0 / 2 | $0 / $4 |
| Eredivisie | 1 | 149 / 166 | 0.4¢ / 0.5¢ | 3.4% / 4.2% | 0 / 3 | $0 / $0 | 0 / 0 | $0 / $0 |
| Brasileirão Série A | 1 | 79 / 161 | 0.4¢ / 0.7¢ | 0.0% / 7.5% | 0 / 4 | $0 / $4 | 0 / 0 | $0 / $0 |
| J1 League | 1 | 34 / 83 | 0.9¢ / 1.0¢ | 5.9% / 7.2% | 0 / 2 | $0 / $1 | 0 / 0 | $0 / $0 |
| KHL hockey | 5 | 88 / 43 | 4.1¢ / 10.9¢ | 5.7% / 25.6% | 0 / 3 | $0 / $0 | 0 / 0 | $0 / $0 |

**Two data quirks decide this result.** Polymarket's history point stamped at minute *m* holds the price at the start
of *m*; Kalshi's candle holds the close. Read naively, every sharp in-game move looks like a 20-cent arbitrage for one
minute, and the first replay "found" $343 in six games that way. And an empty or one-sided Polymarket book reports a
mid of 0.50, which next to a Kalshi quote of 0.98 looks like a 48-cent arbitrage; a 30-day replay without the check
reported $5,354. The table pairs Kalshi's close with Polymarket's next-minute point and only counts minutes where
Polymarket's trade tape shows a trade within three minutes at a price within a dime of the history point.

**Reading.** Pre-game minutes cross essentially never. In-game minutes cross in 7.2% of samples with the gap
between midpoints rising from 0.7¢ to 0.9¢; a zero-latency taker would have booked $3,778
over 30 / 14 (major / niche leagues) days at 50 contracts a signal, and the signals that survived a second minute were worth
$682. The month-long replay says the same thing the one-night sampler said: the venues do come apart
during games, for about a minute at a time, and the money in it is a latency race measured in tens of dollars a day, not
a convergence trade.


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

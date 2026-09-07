from datetime import datetime, timezone

from arb.backtest import Backtester
from arb.config import Settings
from arb.models import KALSHI, POLYMARKET, Game, Outcome


def _game(start_ts: int) -> Game:
    k = Outcome(venue=KALSHI, id="KX-A", market_id="KX-A", event_id="e", title="A wins", event_title="A vs B",
                league="mlb", fee_rate=0.035, fee_round_cents=True)
    p = Outcome(venue=POLYMARKET, id="tokA", market_id="0xc", event_id="pe", title="A", event_title="A vs. B",
                league="mlb", fee_rate=0.05, meta={"outcome_index": 0})
    g = Game(key="mlb:d:1-2", league="mlb", start=datetime.fromtimestamp(start_ts, tz=timezone.utc), home="2", away="1",
             home_name="B", away_name="A", kalshi={"1": k}, polymarket={"1": p})
    g.exact_start = True
    return g


def test_replay_aligns_polymarket_one_minute_later_and_prices_fees(monkeypatch):
    s = Settings(max_workers=1)
    bt = Backtester(s, ["mlb"], days=1, poly_spread=0.02, size=100, quiet=True)
    start = 1_800_000_000 - (1_800_000_000 % 60)
    # Kalshi candles: minutes -6..+5 around start. At live minute +2 Kalshi drops to 0.40/0.41 (a big play).
    candles = []
    for i in range(-6, 6):
        end = start + (i + 1) * 60
        bid, ask = (0.40, 0.41) if i >= 2 else (0.50, 0.51)
        candles.append({"ts": end, "bid": bid, "ask": ask, "volume": 10.0, "price": ask})
    # Polymarket history stamped at the *start* of each minute: the move shows up one stamp later than Kalshi's close.
    # Minutes (start+2) and (start+3) still show 0.505 at their stamps; the aligned point for candle minute m is m+1.
    hist = []
    for i in range(-6, 7):
        t = start + i * 60
        # true price during minute i: 0.505 before the play (i < 2), 0.405 after
        hist.append((t, 0.505 if i <= 2 else 0.405))  # stamp at start of minute i holds price at start of i
    monkeypatch.setattr(bt, "leg_series", lambda lg, g, team: (candles, hist))
    g = _game(start)
    r = bt.replay_leg("mlb", g, "1")
    assert r is not None
    assert r.minutes == 12 and r.live_minutes == 6
    # perfectly aligned after the shift: no gross crossing anywhere
    assert r.gross_pre == 0 and r.gross_live == 0 and r.episodes_live == 0
    assert r.mid_gap_live_sum / r.live_minutes < 0.006

    # now break the alignment the way the raw data does: Polymarket lags a minute -> phantom signal at the drop
    hist_lagged = [(t, p) for t, p in hist]
    hist_lagged[8] = (hist_lagged[8][0], 0.505)  # stamp for minute +2 (start of minute) still at old level
    hist_lagged[9] = (hist_lagged[9][0], 0.505)  # and the next one too -> aligned point for candle +2 is stale
    monkeypatch.setattr(bt, "leg_series", lambda lg, g, team: (candles, hist_lagged))
    r2 = bt.replay_leg("mlb", g, "1")
    assert r2.gross_live >= 1  # stale Polymarket point vs fresh Kalshi close reads as a crossing
    assert r2.persist_live == 0 or r2.persist_live <= r2.episodes_live


def test_replay_prices_a_real_crossing_after_fees(monkeypatch):
    s = Settings(max_workers=1)
    bt = Backtester(s, ["mlb"], days=1, poly_spread=0.02, size=100, quiet=True)
    start = 1_800_000_000 - (1_800_000_000 % 60)
    candles = [{"ts": start + (i + 1) * 60, "bid": 0.40, "ask": 0.41, "volume": 1.0, "price": 0.41} for i in range(0, 12)]
    # Polymarket at 0.47 mid (bid 0.46): YES@K 0.41 + NO@P 0.54 = 0.95 gross -> positive after ~2.3c fees
    hist = [(start + i * 60, 0.47) for i in range(0, 14)]
    monkeypatch.setattr(bt, "leg_series", lambda lg, g, team: (candles, hist))
    r = bt.replay_leg("mlb", _game(start), "1")
    assert r.gross_live == 12 and r.net_live == 12
    assert r.episodes_live == 1  # one episode of four consecutive minutes -> one trade
    assert r.persist_live == 1  # ...and it persisted, so it counts as actionable
    expected_net = 1 - (0.41 + 0.54) - (0.035 * 0.41 * 0.59 + 0.05 * 0.54 * 0.46)
    assert abs(r.profit_live - expected_net * 100) < 1e-6
    assert r.signals[0]["side"] == "YES@K+NO@P"

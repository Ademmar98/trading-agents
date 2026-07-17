"""Quant/Forex-specific family: synthetic-OHLC fire/silence proofs for the
implementable catalog entries in research/quant_forex_specific.md."""
import math
import random
from datetime import datetime, timedelta, timezone

from core.strats import quant_forex_specific as qfx

UTC = timezone.utc
DAY0 = datetime(2024, 1, 1, tzinfo=UTC)  # a Monday


def _mk(closes, start=DAY0, step_secs=86400, spread=0.3):
    base = int(start.timestamp())
    return [{"open": c, "high": c + spread, "low": c - spread, "close": c,
             "volume": 100.0, "ts": base + i * step_secs}
            for i, c in enumerate(closes)]


def _trend(n, start=100.0, pct=0.002):
    return [start * (1 + pct) ** i for i in range(n)]


def _flat(n, price=100.0, amp=0.1):
    return [price + (amp if i % 2 else -amp) for i in range(n)]


def _valid(sig):
    assert sig is None or (
        sig["action"] in ("BUY", "SELL")
        and 0 < sig["confidence"] <= 1
        and isinstance(sig["reasons"], list) and sig["reasons"]
        and sig["strategy"].startswith("Quant - ")
    )


# --- #4 TSMOM classic -------------------------------------------------------

def test_tsmom_classic_fires_on_uptrend():
    sig = qfx.tsmom_classic(_mk(_trend(260)))
    assert sig and sig["action"] == "BUY"


def test_tsmom_classic_sell_on_downtrend():
    closes = _trend(260, start=200.0, pct=-0.002)
    sig = qfx.tsmom_classic(_mk(closes))
    assert sig and sig["action"] == "SELL"


def test_tsmom_classic_silent_on_flat_and_short_history():
    assert qfx.tsmom_classic(_mk(_flat(260))) is None  # r == 0
    assert qfx.tsmom_classic(_mk(_trend(120))) is None  # < lookback


# --- #5 TSMOM ensemble ------------------------------------------------------

def test_tsmom_ensemble_fires_and_stays_silent():
    assert qfx.tsmom_ensemble(_mk(_trend(200)))["action"] == "BUY"
    # 180d and 90d negative, 30d positive -> score -1/3 -> SELL
    closes = ([100 + i * 1.0 for i in range(51)]            # 100 -> 150
              + [150 - (i + 1) * 0.45 for i in range(120)]  # -> ~96
              + [96 + (i + 1) * 0.05 for i in range(29)])   # -> ~97.5
    sig = qfx.tsmom_ensemble(_mk(closes))
    assert sig and sig["action"] == "SELL"
    assert qfx.tsmom_ensemble(_mk(_trend(100))) is None  # < 180 lookback


# --- #10 OU mean reversion --------------------------------------------------

def _ou_series(n=200, b=-0.08, sigma=0.005, seed=7):
    rnd = random.Random(seed)
    x = [0.0]
    for _ in range(n - 1):
        x.append(x[-1] + b * x[-1] + rnd.gauss(0, sigma))
    return [100 * math.exp(v) for v in x]


def test_ou_mean_reversion_fires_on_deep_discount():
    closes = _ou_series(199)
    sma_now = sum(closes[-20:]) / 20
    closes.append(sma_now * math.exp(-0.15))  # 15% below SMA: deep discount
    sig = qfx.ou_mean_reversion(_mk(closes), sma_period=20, fit_window=120)
    assert sig and sig["action"] == "BUY"
    assert "half-life" in sig["reasons"][0]


def test_ou_mean_reversion_silent_at_equilibrium():
    closes = _ou_series(200)
    closes[-1] = sum(closes[-21:-1]) / 20  # sit exactly on the SMA
    sig = qfx.ou_mean_reversion(_mk(closes), sma_period=20, fit_window=120)
    assert sig is None


# --- #14 Range grid ----------------------------------------------------------

def test_range_grid_buy_low_sell_high_silent_trending():
    ohlc = _mk(_flat(130, amp=0.7))  # oscillates 99.3 <-> 100.7
    ohlc[-1]["close"] = 99.5
    assert qfx.range_grid(ohlc)["action"] == "BUY"
    ohlc[-1]["close"] = 100.6
    assert qfx.range_grid(ohlc)["action"] == "SELL"
    # fresh wide range (width >> its own average) -> grid disabled
    closes = _flat(129, amp=0.2) + [130.0]
    ohlc2 = _mk(closes)
    assert qfx.range_grid(ohlc2) is None


# --- #15 Infinity grid -------------------------------------------------------

def test_infinity_grid_steps_and_circuit_breaker():
    closes = _flat(130, amp=0.1)
    ohlc = _mk(closes)
    ohlc[-1]["close"] = ohlc[-2]["close"] * 0.985  # -1.5% bar
    assert qfx.infinity_grid(ohlc)["action"] == "BUY"
    ohlc[-1]["close"] = ohlc[-2]["close"] * 1.015  # +1.5% bar
    assert qfx.infinity_grid(ohlc)["action"] == "SELL"
    ohlc[-1]["close"] = ohlc[-2]["close"] * 1.002  # sub-step move
    assert qfx.infinity_grid(ohlc) is None
    # crash regime: vol spike over the last 20 bars -> buying paused
    closes2 = _flat(110, amp=0.1) + [100 * (1.03 if i % 2 else 0.97)
                                     for i in range(20)]
    ohlc2 = _mk(closes2)
    ohlc2[-1]["close"] = ohlc2[-2]["close"] * 0.985
    assert qfx.infinity_grid(ohlc2) is None


# --- #18 Weekend drift --------------------------------------------------------

def test_weekend_drift_calendar():
    friday = DAY0 + timedelta(days=4)        # 2024-01-05 (Friday) 00:00
    wednesday = DAY0 + timedelta(days=2)
    sunday = DAY0 + timedelta(days=6)
    assert qfx.weekend_drift(_mk(_flat(30), start=friday - timedelta(days=29)))["action"] == "BUY"
    assert qfx.weekend_drift(_mk(_flat(30), start=wednesday - timedelta(days=29))) is None
    assert qfx.weekend_drift(_mk(_flat(30), start=sunday - timedelta(days=29)))["action"] == "SELL"
    # intraday Friday 20:00 UTC bar also fires; missing ts stays silent
    intraday = _mk(_flat(30), start=datetime(2024, 1, 5, 20, tzinfo=UTC)
                   - timedelta(hours=29), step_secs=3600)
    assert qfx.weekend_drift(intraday)["action"] == "BUY"
    no_ts = [{"open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}]
    assert qfx.weekend_drift(no_ts) is None


# --- #19 Turn of month --------------------------------------------------------

def test_turn_of_month():
    jan31 = datetime(2024, 1, 31, tzinfo=UTC)
    jan30 = datetime(2024, 1, 30, tzinfo=UTC)
    sig = qfx.turn_of_month(_mk(_flat(30), start=jan31 - timedelta(days=29)))
    assert sig and sig["action"] == "BUY"
    assert qfx.turn_of_month(_mk(_flat(30), start=jan30 - timedelta(days=29))) is None


# --- #20 Time of day ----------------------------------------------------------

def test_time_of_day():
    def ending_at(hour):
        start = datetime(2024, 3, 1, hour, tzinfo=UTC) - timedelta(hours=39)
        return _mk(_flat(40), start=start, step_secs=3600)
    assert qfx.time_of_day(ending_at(13))["action"] == "BUY"
    assert qfx.time_of_day(ending_at(21))["action"] == "SELL"
    assert qfx.time_of_day(ending_at(10)) is None


# --- #21 Tokyo range / London breakout ---------------------------------------

def _london_day(day, asian_mid=100.0, drift_after=None):
    """One UTC day of hourly bars; Asian session flat around asian_mid."""
    bars = []
    for h in range(24):
        c = asian_mid + (0.05 if h % 2 else -0.05)
        if drift_after is not None and h >= drift_after[0]:
            c = drift_after[1]
        bars.append({"open": c, "high": c + 0.1, "low": c - 0.1, "close": c,
                     "volume": 100.0,
                     "ts": int((day + timedelta(hours=h)).timestamp())})
    return bars


def test_london_breakout():
    bars = []
    for d in range(10):
        bars += _london_day(DAY0 + timedelta(days=d))
    today = DAY0 + timedelta(days=10)
    # series ends AT the 07:00 trigger bar (scan evaluates the last bar)
    bars += _london_day(today, drift_after=(7, 101.5))[:8]
    sig = qfx.tokyo_london_breakout(bars)
    assert sig and sig["action"] == "BUY"
    # same day but the 07:00 bar closes inside the range -> silent
    bars2 = bars[:-8] + _london_day(today, drift_after=(7, 100.02))[:8]
    assert qfx.tokyo_london_breakout(bars2) is None


# --- #22 London-NY overlap ORB ------------------------------------------------

def test_overlap_orb():
    bars = []
    day = DAY0
    for h in range(0, 13):  # series ends at the 12:30 trigger bar
        for m in (0, 15, 30, 45):
            if (h, m) > (12, 30):
                break
            c = 100.0 + (0.02 if (h + m) % 2 else -0.02)
            if (h, m) in ((12, 0), (12, 15)):
                c = 100.0  # opening-range bars: 99.9 - 100.1
            if (h, m) == (12, 30):
                c = 100.9  # breakout bar
            bars.append({"open": c, "high": c + 0.1, "low": c - 0.1,
                         "close": c, "volume": 100.0,
                         "ts": int((day + timedelta(hours=h, minutes=m)).timestamp())})
    sig = qfx.overlap_orb(bars)
    assert sig and sig["action"] == "BUY"
    bars2 = [dict(b) for b in bars]
    bars2[-1]["close"] = 100.0  # back inside the OR
    assert qfx.overlap_orb(bars2) is None


# --- #23 NY close / fix mean reversion ---------------------------------------

def _ny_day(day, closes_by_hour=None):
    bars = []
    for h in range(24):
        c = 100.0 + (0.05 if h % 2 else -0.05)
        if closes_by_hour and h in closes_by_hour:
            c = closes_by_hour[h]
        bars.append({"open": c, "high": c + 0.1, "low": c - 0.1, "close": c,
                     "volume": 100.0,
                     "ts": int((day + timedelta(hours=h)).timestamp())})
    return bars


def test_ny_close_reversion():
    bars = []
    for d in range(24):
        bars += _ny_day(DAY0 + timedelta(days=d))
    # today: London-open 100 -> 21:00 close 98.5 (move -1.5 >> 1.2 x avg TR)
    plan = {7: 100.0, 21: 98.5}
    for h in range(8, 21):
        plan[h] = 100.0 - (h - 7) * 0.1
    bars += _ny_day(DAY0 + timedelta(days=24), plan)[:22]  # end at the 21:00 bar
    sig = qfx.ny_close_reversion(bars)
    assert sig and sig["action"] == "BUY"
    # small move -> silent
    bars2 = bars[:-22] + _ny_day(DAY0 + timedelta(days=24), {21: 100.1})[:22]
    assert qfx.ny_close_reversion(bars2) is None


# --- #25 Post-event drift -----------------------------------------------------

def test_post_event_drift():
    ohlc = _mk(_flat(30, amp=0.4))
    last = ohlc[-1]
    last.update({"high": 110.0, "low": 99.0, "close": 109.9})  # top-decile spike
    sig = qfx.post_event_drift(ohlc)
    assert sig and sig["action"] == "BUY"
    last.update({"close": 99.1})  # bottom-decile close
    assert qfx.post_event_drift(ohlc)["action"] == "SELL"
    assert qfx.post_event_drift(_mk(_flat(30, amp=0.4))) is None  # no shock


# --- #26 ADX regime switch ----------------------------------------------------

def test_adx_regime_switch_trend_and_mr():
    trend = _mk(_trend(60, pct=0.01))
    sig = qfx.adx_regime_switch(trend)
    assert sig and sig["action"] == "BUY" and "TREND" in sig["reasons"][0]
    # flat oscillation + sharp dip: low ADX, z < -2 -> MR buy
    closes = _flat(59, amp=0.1) + [97.0]
    sig2 = qfx.adx_regime_switch(_mk(closes))
    assert sig2 and sig2["action"] == "BUY" and "MR" in sig2["reasons"][0]
    # flat oscillation, nothing extreme -> silent
    assert qfx.adx_regime_switch(_mk(_flat(60, amp=0.1))) is None


# --- #27 Vol percentile regime -------------------------------------------------

def test_vol_percentile_regime():
    rnd = random.Random(3)
    hi_vol = [100 * (1 + rnd.choice((0.01, -0.01))) ** (i % 7) for i in range(270)]
    # compress: last 30 bars nearly pinned, then a Donchian-breaking close
    tail = [100.0 + (0.02 if i % 2 else -0.02) for i in range(29)] + [100.9]
    ohlc = _mk(hi_vol + tail)
    sig = qfx.vol_percentile_regime(ohlc)
    assert sig and sig["action"] == "BUY" and "COMPRESSION" in sig["reasons"][0]
    # uniform vol: percentile mid-band -> meta stays silent
    steady = [100 * (1.01 if i % 2 else 0.99) for i in range(300)]
    assert qfx.vol_percentile_regime(_mk(steady)) is None


# --- #28 Hurst regime -----------------------------------------------------------

def test_hurst_regime():
    rnd = random.Random(11)
    persistent = [100 + i * 0.3 + rnd.gauss(0, 0.05) for i in range(120)]
    sig = qfx.hurst_regime(_mk(persistent))
    assert sig and sig["action"] == "BUY" and "persistent" in sig["reasons"][0]
    anti = [100 + (1.0 if i % 2 else -1.0) + rnd.gauss(0, 0.02) for i in range(119)]
    anti.append(97.0)  # deep dip vs SMA -> z < -2
    sig2 = qfx.hurst_regime(_mk(anti))
    assert sig2 and sig2["action"] == "BUY" and "anti-persistent" in sig2["reasons"][0]
    assert qfx.hurst_regime(_mk(_flat(60))) is None  # < window


# --- #29 overlay + registry ----------------------------------------------------

def test_vol_target_multiplier_is_overlay_not_signal():
    calm = _mk(_flat(60, amp=0.05))
    wild = _mk([100 * (1.05 if i % 2 else 0.95) for i in range(60)])
    m_calm = qfx.vol_target_multiplier(calm, target_vol_ann=0.30)
    m_wild = qfx.vol_target_multiplier(wild, target_vol_ann=0.30)
    assert m_calm == 2.0            # capped at cap_hi
    assert 0.25 <= m_wild < 1.0     # de-levered in high vol
    names = [n for n, _ in qfx.STRATEGIES]
    assert "Vol Target" not in " ".join(names)


def test_registry_contract_and_unique_tags():
    assert len(qfx.STRATEGIES) == 15
    names = [n for n, _ in qfx.STRATEGIES]
    assert len(set(names)) == len(names)
    rnd = random.Random(42)
    closes = [100 + rnd.gauss(0, 1) for _ in range(400)]
    closes = [max(c, 1.0) for c in closes]
    for name, fn in qfx.STRATEGIES:
        sig = fn(_mk(closes))
        _valid(sig)
        if sig:
            assert sig["strategy"] == name

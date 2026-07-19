"""Volume & Order-Flow family (core/strats/volume_orderflow.py).

Synthetic OHLCV series prove the top implementable strategies FIRE on
constructed volume patterns and stay SILENT on low-volume noise. Bars are
hourly (ts spaced 3600s) so UTC-session logic is exercised.
"""
import pytest

from core.strats import volume_orderflow as vo

BASE_TS = 1_700_000_000  # 2023-11-14T22:13:20Z


def _bar(o, h, l, c, v, i):
    return {"open": o, "high": h, "low": l, "close": c, "volume": v,
            "ts": BASE_TS + i * 3600}


def _flat(n, price=100.0, vol=100.0, spread=0.4, start=0):
    """Low-volume noise: every bar closes where it opened (no drift, no RVOL,
    CLV/CVD/FI all ~0)."""
    return [_bar(price, price + spread, price - spread, price, vol, start + i)
            for i in range(n)]


def _trend(n, start=50.0, step=0.5, vol=100.0, vol_step=0.0, start_i=0):
    bars = []
    for i in range(n):
        c = start + i * step
        o = c - step if i else c - step * 0.5
        bars.append(_bar(o, max(o, c) + 0.2, min(o, c) - 0.2, c,
                         vol + i * vol_step, start_i + i))
    return bars


def _assert_sig(sig, action, tag):
    assert sig is not None, f"{tag} did not fire"
    assert sig["action"] == action
    assert sig["strategy"] == tag
    assert 0 < sig["confidence"] <= 0.95
    assert sig["price"] > 0
    assert sig["reasons"] and all(tag in r for r in sig["reasons"])


# --------------------------------------------------------------------------
# Fires on constructed volume patterns
# --------------------------------------------------------------------------

def test_rvol_breakout_fires():
    bars = _flat(60, vol=100.0)
    # close breaks the 20-bar high on 3x volume, closing at 92% of its range
    bars.append(_bar(100.0, 103.5, 99.8, 103.2, 300.0, 60))
    _assert_sig(vo.rvol_breakout(bars), "BUY", "VO - RVOL Breakout")


def test_rvol_breakout_silent_without_volume():
    bars = _flat(60, vol=100.0)
    bars.append(_bar(100.0, 103.5, 99.8, 103.2, 100.0, 60))  # same break, no RVOL
    assert vo.rvol_breakout(bars) is None


def test_climactic_reversal_fires():
    bars = _flat(30, vol=100.0)
    for i in range(5):  # 5 consecutive down closes
        bars.append(_bar(99 - i, 99.2 - i, 98.6 - i, 98.5 - i, 100.0, 30 + i))
    # selling climax: 4x volume, ~3x ATR range, closes in its top 40%
    bars.append(_bar(93.7, 96.0, 93.0, 95.7, 400.0, 35))
    bars.append(_bar(95.7, 96.2, 95.3, 95.9, 120.0, 36))  # holds climax midpoint
    _assert_sig(vo.climactic_reversal(bars), "BUY", "VO - Climactic Reversal")


def test_climactic_reversal_silent_without_volume():
    bars = _flat(30, vol=100.0)
    for i in range(5):
        bars.append(_bar(99 - i, 99.2 - i, 98.6 - i, 98.5 - i, 100.0, 30 + i))
    bars.append(_bar(93.7, 96.0, 93.0, 95.7, 100.0, 35))  # climax shape, no volume
    bars.append(_bar(95.7, 96.2, 95.3, 95.9, 100.0, 36))
    assert vo.climactic_reversal(bars) is None


def test_obv_trend_confirm_fires():
    # rising closes on rising volume: OBV > EMA21(OBV), OBV new 20-bar high
    bars = _trend(80, start=50.0, step=0.5, vol=100.0, vol_step=3.0)
    _assert_sig(vo.obv_trend_confirm(bars), "BUY", "VO - OBV Trend Confirm")


def test_obv_breakout_lead_fires():
    # price chops inside its 20-bar range while OBV climbs (volume leads)
    bars = []
    for i in range(30):
        if i % 2 == 0:
            bars.append(_bar(100.0, 102.2, 99.8, 102.0, 400.0, i))
        else:
            bars.append(_bar(102.0, 102.2, 99.8, 100.0, 100.0, i))
    # final accumulation bar: OBV new 20-bar high, close still below price high
    bars.append(_bar(100.0, 101.3, 99.8, 101.2, 400.0, 30))
    _assert_sig(vo.obv_breakout_lead(bars), "BUY", "VO - OBV Breakout Lead")


def test_volume_dryup_pullback_fires():
    bars = _trend(70, start=50.0, step=0.5, vol=200.0)
    # 3-bar pullback on declining volume, final bar < 0.6x SMA(volume,20)
    for j, v in enumerate((150.0, 120.0, 90.0)):
        o = 84.5 - j * 0.5
        bars.append(_bar(o, o + 0.3, o - 0.7, o - 0.5, v, 70 + j))
    # trigger: close breaks the pullback high
    bars.append(_bar(83.0, 85.6, 82.8, 85.5, 220.0, 73))
    _assert_sig(vo.volume_dryup_pullback(bars), "BUY", "VO - Volume Dry-Up")


def test_cvd_proxy_divergence_fires():
    bars = _flat(20, vol=100.0)
    # first half: heavy-volume sell-off to 92 (CVD dives)
    for i in range(10):
        o = 100.0 - i * 0.8
        c = o - 0.8
        bars.append(_bar(o, o + 0.1, c - 0.4, c, 300.0, 20 + i))
    # second half: price grinds to a marginally lower low on tiny sell volume
    # while up bars carry big volume (CVD makes a higher low)
    legs = [(92.0, 93.0, 250), (93.0, 92.3, 40), (92.3, 93.1, 200),
            (93.1, 92.4, 40), (92.4, 93.2, 200), (93.2, 92.5, 40),
            (92.5, 93.3, 200), (93.3, 91.8, 50), (91.8, 92.6, 200),
            (92.6, 93.0, 150)]
    for j, (o, c, v) in enumerate(legs):
        bars.append(_bar(o, max(o, c) + 0.1, min(o, c) - 0.4, c, float(v), 30 + j))
    # trigger: close above the prior bar's high
    bars.append(_bar(93.0, 94.0, 92.9, 93.8, 260.0, 40))
    _assert_sig(vo.cvd_proxy_divergence(bars), "BUY", "VO - CVD Proxy Divergence")


def test_force_index_pullback_fires():
    bars = _trend(70, start=50.0, step=0.5, vol=100.0)
    # signal bar: sharp down-close on heavy volume -> FI(2) dips below zero
    bars.append(_bar(84.5, 84.7, 83.3, 83.5, 600.0, 70))
    # trigger: close breaks the signal bar's high
    bars.append(_bar(83.5, 85.0, 83.4, 84.9, 150.0, 71))
    _assert_sig(vo.force_index_pullback(bars), "BUY", "VO - Force Index Pullback")


def test_cmf_filter_fires():
    bars = _flat(80, price=100.0, vol=100.0)  # CLV = 0 -> CMF pinned at 0
    # first accumulation bar: closes near its high on 2x volume -> CMF > +0.05
    bars.append(_bar(100.0, 101.2, 100.0, 101.1, 200.0, 80))
    _assert_sig(vo.cmf_filter(bars), "BUY", "VO - CMF Filter")


def test_poc_retest_fires():
    # 100-bar profile: volume concentrated at 100 (POC), then price lifts
    bars = [_bar(100.0, 100.2, 99.8, 100.0, 500.0, i) for i in range(96)]
    for j in range(5):
        o = 100.5 + j * 0.8
        bars.append(_bar(o, o + 0.5, o - 0.2, o + 0.4, 100.0, 96 + j))
    # pullback dips into the POC row and closes back above it
    bars.append(_bar(103.7, 103.9, 100.05, 103.5, 150.0, 101))
    _assert_sig(vo.poc_retest(bars), "BUY", "VO - POC Retest")


def test_mfi_extremes_fires():
    # heavy-volume grind down pins MFI near 0, then two strong up bars
    bars = []
    for i in range(30):
        o = 100.0 - i * 0.1
        bars.append(_bar(o, o + 0.05, o - 0.15, o - 0.1, 200.0, i))
    bars.append(_bar(97.0, 97.8, 96.9, 97.7, 400.0, 30))
    bars.append(_bar(97.7, 98.6, 97.6, 98.5, 400.0, 31))
    _assert_sig(vo.mfi_extremes(bars), "BUY", "VO - MFI Extremes")


# --------------------------------------------------------------------------
# Low-volume noise: NOTHING in the family may fire
# --------------------------------------------------------------------------

def test_flat_low_volume_noise_silent_everywhere():
    noise = _flat(130, vol=100.0)
    for name, fn in vo.STRATEGIES:
        assert fn(noise) is None, f"{name} fired on low-volume noise"


def test_choppy_low_volume_noise_silent_everywhere():
    bars = []
    for i in range(130):  # aimless chop, uniform tiny volume
        c = 100.0 + (0.3 if i % 2 else -0.3)
        bars.append(_bar(100.0, 100.5, 99.5, c, 100.0, i))
    for name, fn in vo.STRATEGIES:
        assert fn(bars) is None, f"{name} fired on choppy low-volume noise"


# --------------------------------------------------------------------------
# Registry contract
# --------------------------------------------------------------------------

def test_registry_shape_and_robustness():
    tags = [name for name, _ in vo.STRATEGIES]
    assert len(tags) == 21
    assert len(set(tags)) == 21  # per-strategy stats stay attributable
    for _, fn in vo.STRATEGIES:
        assert fn([]) is None
        assert fn(_flat(5)) is None  # too little history -> no crash, no signal


def test_skipped_data_needs_documented():
    doc = vo.__doc__
    assert "SKIPPED_DATA_NEEDS" in doc
    for feed in ("orderbook", "funding", "open_interest", "liquidations",
                 "onchain_flows", "multi-symbol"):
        assert feed in doc

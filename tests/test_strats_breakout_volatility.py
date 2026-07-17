"""Breakout & Volatility family tests (core/strats/breakout_volatility.py).

Synthetic OHLC(V) series prove the catalog's core strategies fire on
constructed breakouts/squeezes and stay silent on chop, and that the 11
session strategies work on timestamped bars but degrade gracefully (return
None, never crash) when 'ts' is absent.
"""
from datetime import datetime, timedelta, timezone

import pytest

import core.strats.breakout_volatility as bv

MON = 1783900800  # 2026-07-13 00:00 UTC (Monday, ISO week 29)


def _bar(o, h, l, c, v=100.0, ts=None):
    b = {"open": o, "high": h, "low": l, "close": c, "volume": v}
    if ts is not None:
        b["ts"] = ts
    return b


def _flat(n, base=100.0, spread=0.4, vol=100.0, ts0=None, step=300):
    """Two-step chop around `base`; Donchian-stable, never trends."""
    bars = []
    for i in range(n):
        c = base + (0.05 if i % 2 else -0.05)
        ts = ts0 + i * step if ts0 is not None else None
        bars.append(_bar(c, c + spread, c - spread, c, vol, ts))
    return bars


def _chop(n=250, ts0=None):
    """Longer sideways series for silence assertions."""
    return _flat(n, ts0=ts0)


def _days(day_specs, start=MON, step=3600):
    """Build timestamped intraday bars. day_specs = list of days; each day is
    a list of (o, h, l, c) or (o, h, l, c, v) tuples."""
    bars = []
    ts = start
    for day in day_specs:
        for spec in day:
            o, h, l, c = spec[:4]
            v = spec[4] if len(spec) > 4 else 100.0
            bars.append(_bar(o, h, l, c, v, ts))
            ts += step
    return bars


def _plain_day(hi=101.5, lo=98.5, close=100.0, n=24):
    return [(close, hi, lo, close)] * n


def _sig_ok(sig, action):
    assert sig is not None
    assert sig["action"] == action
    assert 0 < sig["confidence"] <= 0.95
    assert sig["strategy"]
    assert sig["reasons"] and all(sig["strategy"] in r for r in sig["reasons"])


# ---------------------------------------------------------------------------
# registry / contract
# ---------------------------------------------------------------------------


class TestRegistry:
    def test_all_29_catalog_entries_registered_with_unique_tags(self):
        assert len(bv.STRATEGIES) == 29
        names = [n for n, _ in bv.STRATEGIES]
        assert len(set(names)) == 29
        for name, fn in bv.STRATEGIES:
            assert callable(fn)

    def test_signal_contract_shape(self):
        bars = _flat(30) + [_bar(100, 102.2, 99.8, 102.0)]
        sig = bv.donchian_20_turtle_s1(bars)
        _sig_ok(sig, "BUY")

    def test_no_crashes_on_empty_short_or_chop(self):
        for bars in ([], _flat(5), _flat(40), _chop(250)):
            for name, fn in bv.STRATEGIES:
                assert fn(bars) is None or isinstance(fn(bars), dict), name


# ---------------------------------------------------------------------------
# #1/#2 Donchian Turtle S1/S2
# ---------------------------------------------------------------------------


class TestDonchian:
    def test_s1_fires_on_fresh_20bar_break(self):
        bars = _flat(24) + [_bar(100, 102.2, 99.8, 102.0)]
        _sig_ok(bv.donchian_20_turtle_s1(bars), "BUY")

    def test_s1_silent_inside_channel_and_on_stale_break(self):
        assert bv.donchian_20_turtle_s1(_flat(25)) is None
        # breakout bar rolls INTO the channel; a later close that fails to
        # clear the new (higher) channel is not a fresh cross -> silent
        bars = _flat(23) + [_bar(100, 102.2, 99.8, 102.0),
                            _bar(102, 102.1, 101.4, 101.8)]
        assert bv.donchian_20_turtle_s1(bars) is None

    def test_s2_fires_on_55bar_break_and_short_mirror(self):
        bars = _flat(59) + [_bar(100, 102.2, 99.8, 102.0)]
        _sig_ok(bv.donchian_55_turtle_s2(bars), "BUY")
        bars = _flat(59) + [_bar(100, 100.3, 97.8, 98.0)]
        _sig_ok(bv.donchian_55_turtle_s2(bars), "SELL")


# ---------------------------------------------------------------------------
# #3 N-Day High + trend filter
# ---------------------------------------------------------------------------


def _trend_consolidation():
    """Rise, then 25-bar sideways hold, ready for a filtered break."""
    bars = [_bar(99 + i * 0.1, 99.4 + i * 0.1, 98.9 + i * 0.1, 99.2 + i * 0.1)
            for i in range(190)]
    top = bars[-1]["close"]
    bars += _flat(25, base=top - 0.3, spread=0.3)
    return bars


class TestNDayTrendFiltered:
    def test_fires_with_rising_ema200(self):
        bars = _trend_consolidation()
        breakout = _bar(bars[-1]["close"], bars[-1]["close"] + 1.4,
                        bars[-1]["close"] - 0.2, bars[-1]["close"] + 1.2)
        _sig_ok(bv.nday_high_trend_filtered(bars + [breakout]), "BUY")

    def test_silent_without_breakout(self):
        assert bv.nday_high_trend_filtered(_trend_consolidation()) is None

    def test_silent_when_ema200_falling(self):
        bars = [_bar(300 - i * 0.1, 300.4 - i * 0.1, 299.4 - i * 0.1,
                     300.1 - i * 0.1) for i in range(215)]
        # a long-shaped jump against a falling EMA200 must stay silent
        jump = _bar(bars[-1]["close"], bars[-1]["close"] + 3.0,
                    bars[-1]["close"] - 0.2, bars[-1]["close"] + 2.8)
        assert bv.nday_high_trend_filtered(bars + [jump]) is None


# ---------------------------------------------------------------------------
# #13 TTM Squeeze
# ---------------------------------------------------------------------------


def _squeeze_series(fire=False):
    bars = [_bar(99 + (i % 3) * 0.5, 101.5, 98.5, 100 + (i % 3) * 0.4)
            for i in range(22)]                       # wide: builds ATR
    bars += [_bar(100.0, 100.10, 99.90, 100.02 + (i % 2) * 0.03)
             for i in range(22)]                       # tight: BB inside KC
    if fire:
        bars.append(_bar(100.05, 105.8, 99.9, 105.5))  # volatility release
    return bars


class TestTtmSqueeze:
    def test_fires_up_on_release(self):
        sig = bv.ttm_squeeze(_squeeze_series(fire=True))
        _sig_ok(sig, "BUY")

    def test_silent_while_squeezed_and_on_chop(self):
        assert bv.ttm_squeeze(_squeeze_series(fire=False)) is None
        assert bv.ttm_squeeze(_chop(120)) is None


# ---------------------------------------------------------------------------
# #14 Bollinger Band Walk
# ---------------------------------------------------------------------------


def _bandwalk_series(fire=False):
    """20-bar alternating high/low close-dispersion blocks give the bandwidth
    percentile a mid-range reading; the final leg expands."""
    bars = []
    for blk in range(7):  # 7 blocks x 20 bars = 140
        amp = 1.5 if blk % 2 == 0 else 0.1
        for i in range(20):
            c = 100 + (amp if i % 2 else -amp)
            bars.append(_bar(c, c + 0.3, c - 0.3, c))
    for i in range(11):  # expansion leg: rising closes, rising bandwidth
        c = 100.2 + i * 0.2
        bars.append(_bar(c - 0.1, c + 0.35, c - 0.45, c))
    if fire:
        lc = bars[-1]["close"]
        bars.append(_bar(lc - 0.2, lc + 1.9, lc - 0.5, lc + 1.7))
    else:
        lc = bars[-1]["close"]
        bars.append(_bar(lc - 0.1, lc + 0.2, lc - 0.3, lc - 0.05))
    return bars


class TestBollingerBandWalk:
    def test_fires_on_expanding_band_ride(self):
        sig = bv.bollinger_band_walk(_bandwalk_series(fire=True))
        _sig_ok(sig, "BUY")

    def test_silent_without_band_break(self):
        assert bv.bollinger_band_walk(_bandwalk_series(fire=False)) is None
        assert bv.bollinger_band_walk(_chop(150)) is None


# ---------------------------------------------------------------------------
# #15 Keltner Channel Breakout (ADX gate)
# ---------------------------------------------------------------------------


def _keltner_series(fire=True):
    bars = [_bar(99 + i * 0.45, 99.7 + i * 0.45, 98.9 + i * 0.45,
                 99.3 + i * 0.45) for i in range(52)]   # trend: ADX up
    top = bars[-1]["close"]
    # tight shelf BELOW the Keltner upper band so the jump is a fresh cross
    bars += [_bar(top - 1.0, top - 0.6, top - 1.6, top - 1.2) for _ in range(8)]
    if fire:
        bars.append(_bar(top - 1.1, top + 1.9, top - 1.3, top + 1.6))
    return bars


class TestKeltner:
    def test_fires_with_adx_support(self):
        _sig_ok(bv.keltner_breakout(_keltner_series(fire=True)), "BUY")

    def test_adx_gate_blocks_chop_break(self):
        bars = _chop(80)
        c = bars[-1]["close"] + 3.0
        bars = bars + [_bar(c - 2.8, c + 0.2, c - 3.0, c)]
        assert bv.keltner_breakout(bars) is None


# ---------------------------------------------------------------------------
# #16 ATR Volatility Expansion
# ---------------------------------------------------------------------------


def _atr_expansion_series(expanding=True):
    bars = _flat(80, spread=0.5)
    if expanding:
        for i in range(5):  # wide-range sideways bars: ATR surges
            c = 100.5 if i % 2 else 99.5
            bars.append(_bar(100, 102, 98, c))
    bars.append(_bar(100, 103.2, 99.8, 103.0))
    return bars


class TestAtrExpansion:
    def test_fires_on_surge_plus_break(self):
        _sig_ok(bv.atr_expansion_breakout(_atr_expansion_series(True)), "BUY")

    def test_silent_on_quiet_break(self):
        # same breakout shape but no volatility surge behind it
        assert bv.atr_expansion_breakout(_atr_expansion_series(False)) is None


# ---------------------------------------------------------------------------
# #17/#18 NR4 / NR7
# ---------------------------------------------------------------------------


class TestNR:
    def test_nr4_fires_and_stays_silent(self):
        bars = _flat(12, spread=0.45)
        bars += [_bar(100, 101.0, 99.0, 100.5),      # range 2.0
                 _bar(100.5, 101.3, 99.8, 100.9),    # range 1.5
                 _bar(100.9, 101.05, 100.65, 100.8)]  # NR4 bar, range 0.4
        fire = bars + [_bar(100.8, 101.6, 100.7, 101.5)]
        _sig_ok(bv.nr4_breakout(fire), "BUY")
        quiet = bars + [_bar(100.8, 101.0, 100.6, 100.9)]
        assert bv.nr4_breakout(quiet) is None

    def test_nr4_silent_when_not_narrowest(self):
        bars = _flat(12, spread=0.45)
        bars += [_bar(100, 100.2, 99.9, 100.05),     # range 0.3 (narrower)
                 _bar(100.2, 101.3, 99.9, 100.9),    # range 1.4
                 _bar(100.9, 101.05, 100.65, 100.8),  # range 0.4: NOT NR4
                 _bar(100.8, 101.6, 100.7, 101.5)]
        assert bv.nr4_breakout(bars) is None

    def test_nr7_fires(self):
        bars = _flat(12, spread=0.45)
        bars += [_bar(100, 101.2, 99.1, 100.4),
                 _bar(100.4, 101.4, 99.7, 100.8),
                 _bar(100.8, 101.2, 99.9, 100.6),
                 _bar(100.6, 101.05, 100.65, 100.8)]  # narrowest of 7
        fire = bars + [_bar(100.8, 101.6, 100.7, 101.5)]
        _sig_ok(bv.nr7_breakout(fire), "BUY")
        assert bv.nr7_breakout(bars + [_bar(100.8, 101.0, 100.7, 100.9)]) is None


# ---------------------------------------------------------------------------
# #19/#20 Inside Bar (mother levels) / NRIB
# ---------------------------------------------------------------------------


class TestInsideFamily:
    def test_inside_bar_mother_fires(self):
        bars = _flat(15, spread=0.5)
        bars += [_bar(99.6, 101.0, 99.0, 100.5),      # mother
                 _bar(100.2, 100.7, 99.6, 100.4)]     # inside bar
        fire = bars + [_bar(100.4, 101.5, 100.3, 101.3)]
        _sig_ok(bv.inside_bar_mother(fire), "BUY")
        quiet = bars + [_bar(100.4, 100.9, 100.1, 100.6)]
        assert bv.inside_bar_mother(quiet) is None

    def test_inside_bar_uses_mother_not_inside_levels(self):
        # close beyond the INSIDE bar's high but not the mother's -> silent
        bars = _flat(15, spread=0.5)
        bars += [_bar(99.6, 101.0, 99.0, 100.5),
                 _bar(100.2, 100.7, 99.6, 100.4)]
        probe = bars + [_bar(100.4, 100.95, 100.3, 100.9)]
        assert bv.inside_bar_mother(probe) is None

    def test_nrib_fires_on_coil(self):
        bars = _flat(15, spread=0.5)                 # flat range = 1.0
        bars += [_bar(99.7, 100.9, 99.2, 100.4),      # mother range 1.7
                 _bar(100.2, 100.6, 99.8, 100.3)]     # inside, range 0.8
        fire = bars + [_bar(100.3, 101.3, 100.2, 101.1)]
        _sig_ok(bv.nrib_breakout(fire), "BUY")
        quiet = bars + [_bar(100.3, 100.8, 100.1, 100.5)]
        assert bv.nrib_breakout(quiet) is None


# ---------------------------------------------------------------------------
# #27 Volume-Confirmed Donchian
# ---------------------------------------------------------------------------


class TestVolumeConfirmedDonchian:
    def test_fires_on_volume_surge(self):
        bars = _flat(23, vol=100.0)
        bars.append(_bar(100, 102.2, 99.8, 102.0, v=220.0))
        _sig_ok(bv.volume_confirmed_donchian(bars), "BUY")

    def test_silent_on_average_volume(self):
        bars = _flat(23, vol=100.0)
        bars.append(_bar(100, 102.2, 99.8, 102.0, v=100.0))
        assert bv.volume_confirmed_donchian(bars) is None

    def test_silent_when_close_weak_in_bar(self):
        bars = _flat(23, vol=100.0)
        # big range, volume surge, but close mid-bar (rejection wick)
        bars.append(_bar(100, 103.0, 99.5, 100.9, v=300.0))
        assert bv.volume_confirmed_donchian(bars) is None


# ---------------------------------------------------------------------------
# #25 Turtle Soup / #24 Swing Failure Fade
# ---------------------------------------------------------------------------


class TestFades:
    def test_turtle_soup_fires_on_failed_breakdown(self):
        bars = _flat(14, spread=0.4)
        bars.append(_bar(100, 100.4, 98.0, 99.0))      # fresh 20-bar low set here
        bars += _flat(16, spread=0.4)                   # 10+ bars away: level fresh
        bars.append(_bar(99.0, 99.2, 97.8, 98.7))       # wick through, close back
        _sig_ok(bv.turtle_soup(bars), "BUY")

    def test_turtle_soup_silent_when_close_confirms_break(self):
        bars = _flat(14, spread=0.4)
        bars.append(_bar(100, 100.4, 98.0, 99.0))
        bars += _flat(16, spread=0.4)
        bars.append(_bar(99.0, 99.1, 97.6, 97.7))       # closes THROUGH the level
        assert bv.turtle_soup(bars) is None

    def test_sfp_fires_on_reclaimed_swing_low(self):
        # low-ADX chop with a real swing-low lattice; the last bar wicks
        # through the most recent confirmed swing low and closes back above
        import math
        bars = []
        for i in range(31):
            c = 100 + 0.4 * math.sin(i * 1.1)
            bars.append(_bar(c, c + 0.35, c - 0.35, c))
        bars.append(_bar(99.7, 100.0, 98.5, 99.8))       # wick below, reclaim
        _sig_ok(bv.swing_failure_fade(bars), "BUY")

    def test_sfp_silent_without_reclaim(self):
        bars = _flat(31, spread=0.4)
        bars.append(_bar(99.5, 99.6, 98.6, 98.7))        # breaks, closes weak
        assert bv.swing_failure_fade(bars) is None


# ---------------------------------------------------------------------------
# #26 Breakout-Retest Continuation
# ---------------------------------------------------------------------------


class TestBreakoutRetest:
    def test_fires_after_held_retest(self):
        bars = _flat(30, spread=0.4)                    # channel high ~100.45
        bars.append(_bar(100, 101.3, 99.9, 101.1))      # breakout leg
        bars.append(_bar(101.1, 101.2, 100.45, 100.3))  # retest touch, holds
        bars.append(_bar(100.3, 101.0, 100.2, 100.9))   # reclaim close
        _sig_ok(bv.breakout_retest(bars), "BUY")

    def test_void_when_retest_undercuts_level(self):
        bars = _flat(30, spread=0.4)
        bars.append(_bar(100, 101.3, 99.9, 101.1))
        bars.append(_bar(101.1, 101.2, 99.5, 99.7))     # closes 0.5*ATR through
        bars.append(_bar(99.7, 101.0, 99.6, 100.9))
        assert bv.breakout_retest(bars) is None

    def test_silent_without_retest(self):
        bars = _flat(30, spread=0.4)
        bars.append(_bar(100, 101.3, 99.9, 101.1))
        bars.append(_bar(101.1, 101.8, 101.0, 101.6))   # runs away, no touch
        assert bv.breakout_retest(bars) is None


# ---------------------------------------------------------------------------
# #21 VCP (Minervini geometric core)
# ---------------------------------------------------------------------------


def _vcp_series(fire=True):
    bars = []
    # base uptrend into the first pivot high
    for i in range(15):
        c = 100 + i * 0.95
        bars.append(_bar(c - 0.3, c + 0.65, c - 0.75, c))
    c = bars[-1]["close"]
    for _ in range(5):   # T1: deepest contraction
        c += -1.25
        bars.append(_bar(c - 0.2, c + 0.5, c - 0.55, c))
    for _ in range(5):   # recovery to a lower high (H1)
        c += 0.95
        bars.append(_bar(c - 0.2, c + 0.5, c - 0.55, c))
    for _ in range(5):   # T2: shallower
        c += -0.5
        bars.append(_bar(c - 0.2, c + 0.5, c - 0.55, c))
    for j in range(5):   # recovery to H2, tight last bar
        c += 0.4
        if j == 4:
            bars.append(_bar(c - 0.1, c + 0.25, c - 0.25, c))
        else:
            bars.append(_bar(c - 0.2, c + 0.5, c - 0.55, c))
    for _ in range(5):   # T3: tightest, 5 bars so the trough confirms
        c += -0.06
        bars.append(_bar(c - 0.05, c + 0.25, c - 0.25, c))
    for j in range(36):  # monotonic right-side base, volume dried up
        c2 = 111.06 + j * 0.004
        bars.append(_bar(c2 - 0.02, c2 + 0.25, c2 - 0.25, c2, v=80.0))
    for j in range(19, 29):
        bars[j]["volume"] = 200.0   # heavy volume into the H1 pivot area
    if fire:
        bars.append(_bar(111.2, 112.5, 110.95, 112.2, v=320.0))
    else:
        bars.append(_bar(111.1, 111.4, 110.9, 111.15, v=90.0))
    return bars


class TestVcp:
    def test_fires_on_pivot_break_with_volume(self):
        sig = bv.vcp_minervini(_vcp_series(fire=True))
        _sig_ok(sig, "BUY")

    def test_silent_without_breakout(self):
        assert bv.vcp_minervini(_vcp_series(fire=False)) is None


# ---------------------------------------------------------------------------
# #28 Volatility Regime Gate / #29 Chaikin Expansion
# ---------------------------------------------------------------------------


def _regime_series(fire=True):
    bars = []
    for blk in range(11):  # alternating wide/narrow TR blocks, flat close path
        spread = 1.0 if blk % 2 == 0 else 0.3
        for i in range(10):
            c = 100 + (0.08 if i % 2 else -0.08)
            bars.append(_bar(c, c + spread, c - spread, c))
    for i in range(12):    # volatility dip: ATR percentile falls to the floor
        c = 100 + (0.03 if i % 2 else -0.03)
        bars.append(_bar(c, c + 0.15, c - 0.15, c))
    for i in range(8):     # vol rising into the break
        c = 100 + i * 0.05
        bars.append(_bar(c, c + 0.75, c - 0.75, c))
    if fire:
        bars.append(_bar(100.35, 102.0, 100.1, 101.8))
    else:
        bars.append(_bar(100.35, 100.9, 100.0, 100.5))
    return bars


class TestRegimeAndChaikin:
    def test_regime_gate_fires(self):
        _sig_ok(bv.regime_gate_breakout(_regime_series(True)), "BUY")

    def test_regime_gate_silent_without_break(self):
        assert bv.regime_gate_breakout(_regime_series(False)) is None

    def test_chaikin_fires_on_expansion_break(self):
        bars = _flat(31, spread=0.4)
        for i in range(14):  # widening H-L ranges, flat closes
            c = 100 + (0.1 if i % 2 else -0.1)
            r = 0.5 + i * 0.18
            bars.append(_bar(c, c + r, c - r, c))
        bars.append(_bar(100, 104.2, 99.5, 103.9))
        _sig_ok(bv.chaikin_expansion(bars), "BUY")

    def test_chaikin_silent_in_flat_range(self):
        assert bv.chaikin_expansion(_flat(45, spread=0.4)) is None


# ---------------------------------------------------------------------------
# session strategies — timestamped 1h/15m bars, Monday 2026-07-13 anchor
# ---------------------------------------------------------------------------


def _six_days_one_h(final_day):
    days = [_plain_day() for _ in range(5)]
    days.append(final_day)
    return _days(days, step=3600)


class TestPdhPdl:
    def test_fires_above_prev_day_high(self):
        final = [(100.0, 101.0, 99.0, 100.0)] * 20
        final += [(100.0, 102.0, 99.8, 100.4)] * 3
        final.append((100.4, 103.0, 100.3, 102.6, 160.0))  # volume-gated break
        bars = _six_days_one_h(final)
        _sig_ok(bv.pdh_pdl_breakout(bars), "BUY")

    def test_silent_when_never_clears_level(self):
        final = [(100.0, 101.4, 99.0, 100.2)] * 24
        assert bv.pdh_pdl_breakout(_six_days_one_h(final)) is None

    def test_silent_without_timestamps(self):
        bars = _flat(60)
        assert bv.pdh_pdl_breakout(bars) is None


class TestOpeningRanges:
    def test_orb30_fires(self):
        days = [_plain_day(n=96) for _ in range(5)]
        final = [(100.0, 100.5, 99.5, 100.1), (100.1, 100.5, 99.6, 100.2)]
        final += [(100.2, 100.6, 99.8, 100.3)] * 6
        final.append((100.3, 101.0, 100.2, 100.9))      # 02:00 bar clears range
        days.append(final)
        bars = _days(days, step=900)
        _sig_ok(bv.orb_30(bars), "BUY")

    def test_asian_range_fires_in_trade_window(self):
        days = [_plain_day(n=96) for _ in range(5)]
        final = [(100.0, 100.4, 99.6, 100.0)] * 28      # 00:00-07:00 box
        final += [(100.0, 100.6, 99.8, 100.3)] * 4      # 07:00-08:00 drift
        final.append((100.3, 100.9, 100.2, 100.8))      # 08:00 bar breaks
        days.append(final)
        bars = _days(days, step=900)
        _sig_ok(bv.asian_range_breakout(bars), "BUY")

    def test_asian_range_silent_outside_window(self):
        days = [_plain_day(n=96) for _ in range(5)]
        final = [(100.0, 100.4, 99.6, 100.0)] * 28
        final += [(100.0, 100.45, 99.7, 100.2)] * 4     # nothing breaks by 08:00
        days.append(final)
        bars = _days(days, step=900)
        assert bv.asian_range_breakout(bars) is None

    def test_weekly_opening_range_fires_monday(self):
        monday = [(100.0, 101.0, 99.0, 100.0)] * 12     # 00:00-12:00 range
        monday += [(100.0, 100.8, 99.6, 100.3)] * 8     # afternoon drift
        monday.append((100.3, 101.7, 100.2, 101.5))     # 20:00 break, day 1
        bars = _days([monday], step=3600)
        _sig_ok(bv.weekly_opening_range(bars), "BUY")

    def test_crabel_stretch_fires(self):
        days = []
        for _ in range(5):
            days.append([(100.0, 100.8, 99.0, 100.2)] * 24)  # stretch 0.8/day
        final = [(100.0, 100.5, 99.6, 100.2)] * 20
        final.append((100.2, 101.2, 100.1, 101.0))      # close > open + stretch
        days.append(final)
        bars = _days(days, step=3600)
        _sig_ok(bv.crabel_stretch(bars), "BUY")

    def test_london_open_fires_with_pivot_bias(self):
        days = [_plain_day(hi=102.0, lo=98.0, close=99.0) for _ in range(5)]
        final = [(99.0, 99.5, 98.8, 99.2)] * 7          # pre-London drift
        final.append((99.2, 101.0, 99.0, 100.4))        # 07:00 range bar
        final.append((100.4, 101.6, 100.3, 101.4))      # 08:00 break above pivot
        days.append(final)
        bars = _days(days, step=3600)
        _sig_ok(bv.london_open_breakout(bars), "BUY")

    def test_ny_open_fires_with_day_trend(self):
        days = [_plain_day() for _ in range(5)]
        final = [(99.5, 100.0, 99.2, 99.8)] * 12        # morning drift up
        final += [(99.8, 101.0, 99.6, 100.6)] * 2       # 12:00-14:00 pre-NY range
        final.append((100.6, 101.8, 100.5, 101.5))      # 14:00 bar: trend + break
        days.append(final)
        bars = _days(days, step=3600)
        _sig_ok(bv.ny_open_breakout(bars), "BUY")


class TestPivotsAndGaps:
    def test_daily_pivot_r1_break(self):
        days = [_plain_day(hi=102.0, lo=98.0, close=100.0) for _ in range(5)]
        final = [(100.0, 101.0, 99.4, 100.2)] * 20
        final.append((100.2, 102.8, 100.1, 102.6))      # R1 = 102 -> break
        days.append(final)
        bars = _days(days, step=3600)
        _sig_ok(bv.daily_pivot_r1s1(bars), "BUY")

    def test_camarilla_h4_break(self):
        days = [_plain_day(hi=102.0, lo=98.0, close=101.0) for _ in range(5)]
        final = [(101.0, 102.0, 100.6, 101.2)] * 20
        final.append((101.2, 103.8, 101.1, 103.5))      # H4 = 103.2 -> break
        days.append(final)
        bars = _days(days, step=3600)
        _sig_ok(bv.camarilla_h4l4(bars), "BUY")

    def test_gap_fill_fades_gap_down_open(self):
        days = [_plain_day(hi=101.0, lo=99.0, close=100.0) for _ in range(5)]
        final = [(97.5, 98.0, 97.2, 97.8)]              # gap-down 00:00 bar
        days.append(final)
        bars = _days(days, step=3600)
        _sig_ok(bv.gap_fill(bars), "BUY")

    def test_gap_and_go_fires_when_gap_holds(self):
        days = [_plain_day(hi=101.0, lo=99.0, close=100.0, n=96) for _ in range(5)]
        final = [(101.8, 102.0, 101.4, 101.7),          # gap up, first 15min
                 (101.7, 102.2, 101.5, 101.9),
                 (101.9, 102.6, 101.8, 102.4)]          # go: clears 15min high
        days.append(final)
        bars = _days(days, step=900)
        _sig_ok(bv.gap_and_go(bars), "BUY")


class TestSessionDefensive:
    """Assignment: no 'ts' -> skip session logic gracefully, never crash."""

    SESSION_FNS = [
        bv.pdh_pdl_breakout, bv.weekly_opening_range, bv.orb_30,
        bv.crabel_stretch, bv.asian_range_breakout, bv.london_open_breakout,
        bv.ny_open_breakout, bv.daily_pivot_r1s1, bv.camarilla_h4l4,
        bv.gap_and_go, bv.gap_fill,
    ]

    def test_all_session_strategies_silent_without_ts(self):
        bars = _flat(120)
        for fn in self.SESSION_FNS:
            assert fn(bars) is None, fn.__name__

    def test_session_strategies_silent_with_partial_ts(self):
        bars = _flat(120, ts0=MON, step=900)
        for b in bars[-5:]:
            del b["ts"]  # one missing timestamp poisons the session grouping
        for fn in self.SESSION_FNS:
            assert fn(bars) is None, fn.__name__

    def test_iso_date_string_fallback_works(self):
        base = datetime(2026, 7, 13, tzinfo=timezone.utc)
        days = [_plain_day(hi=102.0, lo=98.0, close=100.0) for _ in range(3)]
        final = [(100.0, 101.0, 99.4, 100.2)] * 20
        final.append((100.2, 102.8, 100.1, 102.6))
        days.append(final)
        bars = _days(days, step=3600)
        for i, b in enumerate(bars):
            dt = base + timedelta(hours=i)
            b["date"] = dt.isoformat()
            del b["ts"]  # force the ISO 'date' fallback path
        _sig_ok(bv.daily_pivot_r1s1(bars), "BUY")

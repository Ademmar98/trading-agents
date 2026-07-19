"""Tests for core/strats/trend_momentum.py.

Synthetic OHLC series prove the catalog's strategies fire on constructed
patterns and stay silent on flat noise. Cross/freshness conventions match
research/trend_momentum.md ("Conventions" section) and core/strategies.py.
"""
import pytest

from core.strats import trend_momentum as tm


# ---------------------------------------------------------------- builders

def _mk(closes, highs=None, lows=None, opens=None, vols=None, ts0=1_700_000_000, step=86_400):
    out = []
    for i, c in enumerate(closes):
        o = opens[i] if opens else (closes[i - 1] if i else c)
        h = highs[i] if highs else max(o, c) + abs(c) * 0.001
        l = lows[i] if lows else min(o, c) - abs(c) * 0.001
        v = vols[i] if vols else 1_000.0
        out.append({"date": str(ts0 + i * step), "open": o, "high": h,
                    "low": l, "close": c, "volume": v, "ts": ts0 + i * step})
    return out


def _flat(n=260):
    """Perfectly flat tape: every strategy must stay silent."""
    return _mk([100.0] * n,
               highs=[100.01] * n, lows=[99.99] * n, opens=[100.0] * n)


def _rally(n_flat=210, n_up=50):
    """Long flat base at 100, then a strong steady rally — fresh crosses
    for every MA/MACD/breakout/trend system with full warmup."""
    closes = [100.0] * n_flat + [100.0 * 1.004 ** i for i in range(1, n_up + 1)]
    return _mk(closes)


def _golden_cross():
    """Long flat base, decline (SMA50 sinks below SMA200), then a long rally
    so the 50/200 cross lands deep in the series (idx ~218) with ADX ~90 —
    the catalog's ADX>=20 gate is meaningless right after a flat base."""
    closes = ([100.0] * 110 + [100.0 - 0.04 * i for i in range(1, 61)]
              + [97.6 + 0.08 * i for i in range(1, 131)])
    return _mk(closes)


def _vshape(n_base=40, n_down=25, n_up=45):
    """Flat base, sharp downtrend, sharp reversal up — DI/PSAR/ROC/Aroon/
    Vortex/KST/Supertrend crosses while ADX is still elevated from the
    down leg, with enough warmup bars for 50-60 bar indicators."""
    closes = ([100.0] * n_base + [100.0 - 0.2 * i for i in range(1, n_down + 1)]
              + [95.0 + 0.35 * i for i in range(1, n_up + 1)])
    return _mk(closes)


def _adx_dmi_pattern():
    """Choppy range -> decline -> strong rally (seeded, deterministic).
    Built so +DI recrosses -DI while ADX(14) is >25 AND rising vs the
    prior bar — the catalog's exact gate (Wilder smoothing makes this
    timing impossible on purely linear synthetic trends)."""
    import random
    rng = random.Random(0)
    closes = []
    c = 100.0
    for _ in range(60):            # choppy range
        c += rng.uniform(-0.35, 0.35)
        closes.append(c)
    for _ in range(8):             # decline: -DI takes over
        c += rng.uniform(-0.55, -0.05)
        closes.append(c)
    for _ in range(16):            # strong rally: +DI recross, ADX rising
        c += rng.uniform(0.15, 0.75)
        closes.append(c)
    return _mk(closes)


def _pullback_resume(n_up=90, n_down=8, n_res=10):
    """Steady uptrend, short pullback, resumption — ribbon / EMA20 /
    ADX-pullback / MACD-hist-reversal / ROC patterns."""
    up = [100.0 + 0.15 * i for i in range(n_up)]
    down = [up[-1] - 0.25 * i for i in range(1, n_down + 1)]
    res = [down[-1] + 0.3 * i for i in range(1, n_res + 1)]
    return _mk(up + down + res)


def _trix_pattern():
    """Uptrend (TRIX > 0), dip deep enough to drag TRIX under its signal,
    resumption — the recross then happens with TRIX still above zero."""
    closes = ([100.0 + 0.15 * i for i in range(70)]
              + [110.35 - 0.4 * i for i in range(1, 9)]
              + [107.15 + 0.4 * i for i in range(1, 16)])
    return _mk(closes)


def _kumo_pattern():
    """Slow uptrend (future kumo bullish), sharp 5-bar dip below the cloud
    top, resumption — fresh close back above the kumo with spanA > spanB."""
    closes = ([100.0 + 0.1 * i for i in range(100)]
              + [110.0 - 0.8 * i for i in range(1, 6)]
              + [106.0 + 0.8 * i for i in range(1, 13)])
    return _mk(closes)


def _mass_index_bulge():
    """Range expansion then contraction — Mass Index reversal bulge with
    EMA9 rising at completion."""
    bars = []
    # 30 bars narrow range
    for i in range(30):
        c = 100.0
        bars.append({"open": c, "high": c + 0.25, "low": c - 0.25, "close": c,
                     "volume": 1_000.0, "ts": 1_700_000_000 + i * 3_600,
                     "date": str(i)})
    # 14 bars violent range expansion, price drifting up
    for j in range(14):
        c = 100.0 + 0.3 * (j + 1)
        bars.append({"open": c - 0.2, "high": c + 2.0, "low": c - 2.0,
                     "close": c, "volume": 1_000.0,
                     "ts": 1_700_000_000 + (30 + j) * 3_600, "date": str(30 + j)})
    # 13 bars contraction, still gently rising (EMA9 up at completion)
    for k in range(13):
        c = 104.2 + 0.15 * (k + 1)
        bars.append({"open": c - 0.05, "high": c + 0.25, "low": c - 0.25,
                     "close": c, "volume": 1_000.0,
                     "ts": 1_700_000_000 + (44 + k) * 3_600, "date": str(44 + k)})
    return bars


def _dual_thrust_pattern():
    """6 UTC days of hourly bars; prior 4 days range ~2, final day breaks
    above open + 0.5*Range only at the last bar."""
    ts0 = 1_700_000_000
    ts0 -= ts0 % 86_400  # align to UTC midnight
    bars = []
    i = 0
    for d in range(5):
        for h in range(24):
            c = 100.0 + (0.9 if h % 2 == 0 else -0.9)
            bars.append({"open": 100.0, "high": c + 0.6, "low": c - 0.6,
                         "close": c, "volume": 1_000.0,
                         "ts": ts0 + (d * 24 + h) * 3_600, "date": str(i)})
            i += 1
    # final day: 23 quiet bars, then the thrust bar
    for h in range(23):
        bars.append({"open": 100.0, "high": 100.4, "low": 99.6, "close": 100.0,
                     "volume": 1_000.0, "ts": ts0 + (5 * 24 + h) * 3_600,
                     "date": str(i)})
        i += 1
    bars.append({"open": 100.2, "high": 102.2, "low": 100.1, "close": 102.0,
                 "volume": 1_000.0, "ts": ts0 + (5 * 24 + 23) * 3_600,
                 "date": str(i)})
    return bars


def _scan(fn, ohlc, min_len=60):
    """Run fn over every closed-bar prefix; collect the signals it emits."""
    hits = []
    for i in range(min_len, len(ohlc) + 1):
        sig = fn(ohlc[:i])
        if sig:
            hits.append((i, sig))
    return hits


def _assert_fires(fn, ohlc, action, tag):
    hits = _scan(fn, ohlc)
    assert hits, f"{tag} never fired on constructed pattern"
    assert any(s["action"] == action for _, s in hits), \
        f"{tag} fired but never {action}: {[s['action'] for _, s in hits]}"
    for _, s in hits:
        assert s["strategy"] == tag
        assert 0 < s["confidence"] <= 1
        assert isinstance(s["reasons"], list) and s["reasons"]


# ---------------------------------------------------------------- contract

class TestRegistry:
    def test_twenty_nine_catalog_entries(self):
        assert len(tm.TREND_MOMENTUM_STRATEGIES) == 29
        assert len(tm.STRATEGIES) == 29

    def test_unique_tags_and_names(self):
        tags = [t for t, _ in tm.TREND_MOMENTUM_STRATEGIES]
        names = [n for n, _ in tm.STRATEGIES]
        assert len(set(tags)) == 29
        assert len(set(names)) == 29

    def test_scan_helper_returns_per_tag_dicts(self):
        sigs = tm.scan_trend_momentum(_rally())
        assert isinstance(sigs, list)
        for s in sigs:
            assert s["strategy"].startswith("tm_")
            assert s["action"] in ("BUY", "SELL")

    def test_short_series_returns_none(self):
        tiny = _flat(10)
        for _, fn in tm.TREND_MOMENTUM_STRATEGIES:
            assert fn(tiny) is None


class TestFlatSilence:
    """Every strategy must stay silent on flat noise at FULL warmup —
    this is what keeps the paper cycle's stats meaningful."""

    @pytest.mark.parametrize("idx", range(29))
    def test_no_signal_on_flat(self, idx):
        tag, fn = tm.TREND_MOMENTUM_STRATEGIES[idx]
        hits = _scan(fn, _flat(), min_len=2)
        assert hits == [], f"{tag} fired on flat noise: {hits[:3]}"


# ---------------------------------------------------------------- fire tests
# At least the top 10 catalog strategies are proven to fire; in practice
# the patterns below cover the large majority of the family.

class TestMACrossFamily:
    def test_sma_50_200_golden_cross(self):
        _assert_fires(tm.sig_sma_cross_50_200, _golden_cross(), "BUY", "tm_sma_cross_50_200")

    def test_ema_9_21_cross(self):
        _assert_fires(tm.sig_ema_cross_9_21, _rally(), "BUY", "tm_ema_cross_9_21")

    def test_hma_cross(self):
        _assert_fires(tm.sig_hma_cross_16_55, _rally(), "BUY", "tm_hma_cross_16_55")

    def test_tema_cross(self):
        _assert_fires(tm.sig_tema_cross_12_30, _rally(), "BUY", "tm_tema_cross_12_30")

    def test_vwma_cross(self):
        _assert_fires(tm.sig_vwma_cross_20_50, _rally(), "BUY", "tm_vwma_cross_20_50")

    def test_triple_ema_ribbon(self):
        _assert_fires(tm.sig_triple_ema_ribbon, _pullback_resume(), "BUY",
                      "tm_ema_ribbon_8_13_21")


class TestMACDFamily:
    def test_macd_signal_cross_sma200(self):
        _assert_fires(tm.sig_macd_signal_cross, _rally(), "BUY", "tm_macd_signal_cross")

    def test_macd_zero_cross(self):
        _assert_fires(tm.sig_macd_zero_cross, _rally(), "BUY", "tm_macd_zero_cross")

    def test_macd_hist_reversal(self):
        _assert_fires(tm.sig_macd_hist_reversal, _pullback_resume(), "BUY",
                      "tm_macd_hist_reversal")


class TestADXFamily:
    def test_adx_dmi_cross(self):
        _assert_fires(tm.sig_adx_dmi_cross, _adx_dmi_pattern(), "BUY", "tm_adx_dmi_cross")

    def test_adx_pullback(self):
        _assert_fires(tm.sig_adx_pullback, _pullback_resume(), "BUY", "tm_adx_pullback")


class TestBreakoutFamily:
    def test_turtle_s1_fires(self):
        _assert_fires(tm.sig_turtle_s1, _rally(), "BUY", "tm_turtle_s1_20_10")

    def test_turtle_s2_fires(self):
        _assert_fires(tm.sig_turtle_s2, _rally(), "BUY", "tm_turtle_s2_55_20")

    def test_keltner_breakout(self):
        _assert_fires(tm.sig_keltner_breakout, _rally(), "BUY",
                      "tm_keltner_breakout_20_2")

    def test_turtle_s1_last_breakout_filter_loss_allows_reentry(self):
        # breakout #1 loses immediately (2*N stop) -> breakout #2 allowed
        closes = [100.0] * 40 + [102.0] + [97.0] * 4 + [97.0] * 25 + [98.5]
        highs = [100.5] * 40 + [102.5] + [97.5] * 29 + [99.0]
        lows = [99.5] * 40 + [101.0] + [96.5] * 29 + [97.8]
        ohlc = _mk(closes, highs=highs, lows=lows)
        sig = tm.sig_turtle_s1(ohlc)
        assert sig is not None and sig["action"] == "BUY"

    def test_turtle_s1_last_breakout_filter_win_skips_reentry(self):
        # breakout #1 exits PROFITABLE (10-bar Donchian exit) -> the next
        # same-direction S1 breakout must be skipped (catalog Turtle rule)
        closes = ([100.0] * 50 + [102.0] + [102.0 + 0.5 * i for i in range(1, 15)]
                  + [109.0 - 0.5 * i for i in range(1, 11)]
                  + [104.0] * 25 + [106.5])
        highs = [c + 0.5 for c in closes]
        lows = [c - 0.5 for c in closes]
        ohlc = _mk(closes, highs=highs, lows=lows)
        first = tm.sig_turtle_s1(ohlc[:51])
        assert first is not None and first["action"] == "BUY"  # breakout #1 fired
        assert tm.sig_turtle_s1(ohlc) is None  # breakout #2 filtered out


class TestIchimokuFamily:
    def test_tk_cross(self):
        _assert_fires(tm.sig_ichimoku_tk_cross, _rally(), "BUY", "tm_ichimoku_tk_cross")

    def test_kumo_breakout(self):
        _assert_fires(tm.sig_ichimoku_kumo_breakout, _kumo_pattern(), "BUY",
                      "tm_ichimoku_kumo_breakout")

    def test_full_confluence(self):
        _assert_fires(tm.sig_ichimoku_full_confluence, _rally(), "BUY",
                      "tm_ichimoku_full_confluence")


class TestStopReverseFamily:
    def test_supertrend_flip(self):
        _assert_fires(tm.sig_supertrend, _vshape(), "BUY", "tm_supertrend_10_3")

    def test_parabolic_sar_with_adx_gate(self):
        _assert_fires(tm.sig_parabolic_sar, _vshape(), "BUY", "tm_parabolic_sar")


class TestMomentumFamily:
    def test_roc_momentum(self):
        _assert_fires(tm.sig_roc_momentum, _pullback_resume(), "BUY", "tm_roc_momentum_12")

    def test_linreg_slope(self):
        _assert_fires(tm.sig_linreg_slope, _rally(), "BUY", "tm_linreg_slope_20")

    def test_aroon(self):
        _assert_fires(tm.sig_aroon, _vshape(), "BUY", "tm_aroon_25")

    def test_vortex(self):
        _assert_fires(tm.sig_vortex, _vshape(), "BUY", "tm_vortex_14")

    def test_trix(self):
        _assert_fires(tm.sig_trix, _trix_pattern(), "BUY", "tm_trix_15_9")

    def test_kst(self):
        _assert_fires(tm.sig_kst, _vshape(), "BUY", "tm_kst")

    def test_elder_impulse(self):
        _assert_fires(tm.sig_elder_impulse, _rally(), "BUY", "tm_elder_impulse")

    def test_mass_index_bulge(self):
        # completion must land exactly on the last closed bar (stateless
        # two-stage), so assert on the full series rather than scanning
        ohlc = _mass_index_bulge()
        sig = tm.sig_mass_index_bulge(ohlc)
        assert sig is not None and sig["action"] == "BUY"
        assert sig["strategy"] == "tm_mass_index_bulge"
        # one bar earlier the bulge is not yet complete -> silent
        assert tm.sig_mass_index_bulge(ohlc[:-1]) is None


class TestSessionAndPullback:
    def test_dual_thrust(self):
        ohlc = _dual_thrust_pattern()
        sig = tm.sig_dual_thrust(ohlc)
        assert sig is not None and sig["action"] == "BUY"
        assert sig["strategy"] == "tm_dual_thrust"
        # quiet bar before the thrust must NOT fire
        assert tm.sig_dual_thrust(ohlc[:-1]) is None

    def test_dual_thrust_requires_ts(self):
        ohlc = _dual_thrust_pattern()
        for c in ohlc:
            del c["ts"]
        assert tm.sig_dual_thrust(ohlc) is None

    def test_trend_pullback_ema20(self):
        _assert_fires(tm.sig_trend_pullback_ema20, _pullback_resume(), "BUY",
                      "tm_trend_pullback_ema20")


def test_vortex_flat_bars_no_crash():
    """Flat/stale symbols produce zero true range -> None holes MID-series in
    the vortex lines. The cross helpers must treat a gap as 'no cross', not
    crash comparing None < None (live prod error on Trend - Vortex 14)."""
    from core.strats.trend_momentum import sig_vortex, _crossed_up, _crossed_down
    flat = [{"high": 5.0, "low": 5.0, "close": 5.0, "volume": 0, "date": "d"}
            for _ in range(40)]
    assert sig_vortex(flat) is None
    # Trailing None (gap on the most recent bar) must not raise either.
    series_a = [0.9] * 37 + [1.0, 1.1, None]
    series_b = [1.1] * 37 + [1.0, 0.9, None]
    assert _crossed_up(series_a, series_b) is False
    assert _crossed_down(series_a, series_b) is False

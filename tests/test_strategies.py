import logging

import pytest

import core.strategies as strategies_mod
from core.strategies import (
    detect_fvg, detect_order_block, detect_liquidity_sweep, detect_bos_choch,
    detect_ote, detect_market_structure, detect_sma_crossover, detect_rsi_divergence,
    detect_macd, detect_bollinger, detect_atr_breakout, detect_engulfing,
    detect_pin_bar, detect_inside_bar, detect_double_top_bottom,
    detect_ema_cross, _vwap, detect_ichimoku, detect_keltner,
    detect_stochastic_rsi, detect_volume_breakout, detect_support_resistance,
    detect_donchian, detect_heikin_ashi, detect_mfi, detect_adx,
    _adx_single, _ichimoku, _heikin_ashi,
    scan_symbol, strategies_for_regime, ALL_STRATEGIES, REGIME_STRATEGIES,
)


def _make_ohlc(closes, highs=None, lows=None, volumes=None):
    ohlc = []
    for i, c in enumerate(closes):
        h = highs[i] if highs else c * 1.02
        l = lows[i] if lows else c * 0.98
        v = volumes[i] if volumes else 1000.0
        ohlc.append({
            "date": f"2025-01-{i+1:02d}", "open": c * 0.99, "high": h,
            "low": l, "close": c, "volume": v, "ts": i,
        })
    return ohlc


class TestFVG:
    def test_bullish_fvg(self):
        closes = [100, 101, 102, 103, 104]
        ohlc = _make_ohlc(closes)
        result = detect_fvg(ohlc)
        assert result is None or result["action"] in ("BUY", "SELL")

    def test_not_enough_data(self):
        assert detect_fvg([]) is None
        assert detect_fvg(_make_ohlc([100, 101, 102])) is None

    def test_short_data_returns_none(self):
        ohlc = _make_ohlc([100] * 4)
        assert detect_fvg(ohlc) is None


class TestEngulfing:
    def test_bullish_engulfing(self):
        ohlc = _make_ohlc([105, 100, 108], highs=[106, 101, 112], lows=[104, 99, 98])
        ohlc[-2]["close"] = 100
        ohlc[-2]["open"] = 105
        ohlc[-1]["close"] = 108
        ohlc[-1]["open"] = 95
        result = detect_engulfing(ohlc)
        assert result is not None
        assert result["action"] == "BUY"

    def test_not_enough_data(self):
        assert detect_engulfing([]) is None


class TestPinBar:
    def test_bullish_pin_bar(self):
        ohlc = _make_ohlc([100, 101, 102, 103, 99])
        ohlc[-1]["high"] = 102
        ohlc[-1]["low"] = 90
        ohlc[-1]["open"] = 101
        ohlc[-1]["close"] = 99
        result = detect_pin_bar(ohlc)
        if result:
            assert result["action"] == "BUY"

    def test_not_enough_data(self):
        assert detect_pin_bar([]) is None


class TestInsideBar:
    def test_inside_bar_breakout(self):
        closes = [100 + i for i in range(6)]
        ohlc = _make_ohlc(closes)
        result = detect_inside_bar(ohlc)
        assert result is None or result["action"] in ("BUY", "SELL")

    def test_not_enough_data(self):
        assert detect_inside_bar(_make_ohlc([100] * 3)) is None


class TestSmaCrossover:
    def test_not_enough_data(self):
        assert detect_sma_crossover(_make_ohlc([100] * 40)) is None

    def test_enough_data_returns(self):
        ohlc = _make_ohlc([100 + i * 0.5 for i in range(60)])
        result = detect_sma_crossover(ohlc)
        assert result is None or result["action"] in ("BUY", "SELL")


class TestBollinger:
    def test_not_enough_data(self):
        assert detect_bollinger(_make_ohlc([100] * 15)) is None

    def test_enough_data_returns(self):
        ohlc = _make_ohlc([100] * 30)
        result = detect_bollinger(ohlc)
        assert result is None or result["action"] in ("BUY", "SELL")


class TestMacd:
    def test_not_enough_data(self):
        assert detect_macd(_make_ohlc([100] * 25)) is None


class TestDoubleTopBottom:
    def test_not_enough_data(self):
        assert detect_double_top_bottom(_make_ohlc([100] * 20)) is None


class TestEmaCross:
    def test_not_enough_data(self):
        assert detect_ema_cross(_make_ohlc([100] * 40)) is None

    def test_enough_data_returns(self):
        ohlc = _make_ohlc([100 + i * 0.3 for i in range(60)])
        result = detect_ema_cross(ohlc)
        assert result is None or result["action"] in ("BUY", "SELL")


class TestVWAP:
    def test_not_enough_data(self):
        assert _vwap(_make_ohlc([100] * 10)) is None

    def test_enough_data(self):
        ohlc = _make_ohlc([100] * 25, volumes=[1000] * 25)
        result = _vwap(ohlc)
        assert result is None or result["action"] in ("BUY", "SELL")


class TestSupportResistance:
    def test_not_enough_data(self):
        assert detect_support_resistance(_make_ohlc([100] * 20)) is None

    def test_enough_data(self):
        ohlc = _make_ohlc([100 + i * 0.1 for i in range(35)])
        result = detect_support_resistance(ohlc)
        assert result is None or result["action"] in ("BUY", "SELL")


class TestIchimoku:
    def test_not_enough_data(self):
        assert detect_ichimoku(_make_ohlc([100] * 40)) is None

    def test_enough_data(self):
        ohlc = _make_ohlc([100 + i * 0.2 for i in range(55)])
        result = detect_ichimoku(ohlc)
        assert result is None or result["action"] in ("BUY", "SELL")


class TestKeltner:
    def test_not_enough_data(self):
        assert detect_keltner(_make_ohlc([100] * 15)) is None


class TestStochasticRSI:
    def test_not_enough_data(self):
        assert detect_stochastic_rsi(_make_ohlc([100] * 20)) is None


class TestVolumeBreakout:
    def test_not_enough_data(self):
        assert detect_volume_breakout(_make_ohlc([100] * 20)) is None


class TestScanSymbol:
    def test_scan_empty(self):
        assert scan_symbol([]) == []

    def test_scan_short(self):
        assert scan_symbol(_make_ohlc([100] * 5)) == []

    def test_scan_returns_list(self):
        ohlc = _make_ohlc([100 + i * 0.5 for i in range(80)])
        results = scan_symbol(ohlc)
        assert isinstance(results, list)

    def test_strategies_count(self):
        assert len(ALL_STRATEGIES) >= 22


class TestRsiDivergence:
    def test_not_enough_data(self):
        assert detect_rsi_divergence(_make_ohlc([100] * 15)) is None


class TestATRBreakout:
    def test_not_enough_data(self):
        assert detect_atr_breakout(_make_ohlc([100] * 10)) is None


class TestOrderBlock:
    def test_not_enough_data(self):
        assert detect_order_block(_make_ohlc([100] * 15)) is None


class TestLiquiditySweep:
    def test_not_enough_data(self):
        assert detect_liquidity_sweep(_make_ohlc([100] * 20)) is None


class TestBOSCHoCH:
    def test_not_enough_data(self):
        assert detect_bos_choch(_make_ohlc([100] * 20)) is None


class TestOTE:
    def test_not_enough_data(self):
        assert detect_ote(_make_ohlc([100] * 15)) is None


class TestMarketStructure:
    def test_not_enough_data(self):
        assert detect_market_structure(_make_ohlc([100] * 20)) is None


class TestDonchian:
    def test_breakout_buy(self):
        ohlc = _make_ohlc([100] * 30)
        ohlc[-1]["close"] = 105
        sig = detect_donchian(ohlc)
        assert sig is not None
        assert sig["action"] == "BUY"

    def test_breakdown_sell(self):
        ohlc = _make_ohlc([100] * 30)
        ohlc[-1]["close"] = 95
        sig = detect_donchian(ohlc)
        assert sig is not None
        assert sig["action"] == "SELL"

    def test_insufficient_data(self):
        assert detect_donchian(_make_ohlc([100] * 5)) is None


class TestHeikinAshi:
    def test_returns_dict_or_none(self):
        ohlc = _make_ohlc([105] * 15)
        sig = detect_heikin_ashi(ohlc)
        assert sig is None or (isinstance(sig, dict) and "action" in sig)

    def test_needs_min_data(self):
        assert detect_heikin_ashi(_make_ohlc([100] * 5)) is None


class TestMFI:
    def test_insufficient_data(self):
        assert detect_mfi(_make_ohlc([100] * 10)) is None

    def test_returns_none_or_signal(self):
        ohlc = _make_ohlc([100] * 40)
        sig = detect_mfi(ohlc)
        assert sig is None or sig["action"] in ("BUY", "SELL")
# ---------------------------------------------------------------------------
# Corrected indicator math (integration audit fixes)
# ---------------------------------------------------------------------------

def _make_bars(spec):
    """spec: list of (open, high, low, close[, volume]) tuples."""
    ohlc = []
    for i, b in enumerate(spec):
        o, h, l, cl = b[0], b[1], b[2], b[3]
        v = b[4] if len(b) > 4 else 1000.0
        ohlc.append({"date": f"2025-02-{i+1:02d}", "open": o, "high": h,
                     "low": l, "close": cl, "volume": v, "ts": i})
    return ohlc


class TestDonchianFixed:
    def test_breakout_fires_with_consistent_bars(self):
        # Prior channel: highs 102 / lows 98. Current bar closes at 105 with
        # its own high at 106. With the current bar inside the channel (old
        # bug) the upper band became 106 and the breakout could never fire.
        ohlc = _make_bars([(99, 102, 98, 100)] * 29 + [(100, 106, 99, 105)])
        sig = detect_donchian(ohlc)
        assert sig is not None and sig["action"] == "BUY"

    def test_no_fire_when_close_inside_channel(self):
        ohlc = _make_bars([(99, 102, 98, 100)] * 29 + [(100, 103, 99, 101)])
        assert detect_donchian(ohlc) is None

    def test_channel_excludes_current_bar(self):
        # The current bar posts the range extreme itself; the channel must
        # come from the prior 20 bars only, so a close of 101 is NOT a
        # breakout above the prior upper band of 102.
        ohlc = _make_bars([(99, 102, 98, 100)] * 29 + [(100, 130, 99, 101)])
        assert detect_donchian(ohlc) is None


class TestHeikinAshiFixed:
    def test_recursion_uses_prev_open_and_close(self):
        spec = [(99 + i, 101.5 + i, 98.5 + i, 100 + i) for i in range(12)]
        ha = _heikin_ashi(_make_bars(spec))
        assert ha[0]["open"] == pytest.approx((spec[0][0] + spec[0][3]) / 2)
        for i in range(1, len(ha)):
            assert ha[i]["open"] == pytest.approx(
                (ha[i - 1]["open"] + ha[i - 1]["close"]) / 2)
            o, h, l, cl = spec[i]
            assert ha[i]["close"] == pytest.approx((o + h + l + cl) / 4)

    def test_open_not_frozen(self):
        # Under the old bug every ha_open equalled the first bar's ha_open.
        spec = [(99 + i, 101.5 + i, 98.5 + i, 100 + i) for i in range(12)]
        ha = _heikin_ashi(_make_bars(spec))
        assert ha[5]["open"] != pytest.approx(ha[0]["open"])
        assert ha[5]["open"] > ha[0]["open"]  # drifts with the trend


class TestAdxFixed:
    def test_smoothing_uses_whole_series(self):
        head = [100 + i * 0.3 for i in range(25)]
        base = _make_ohlc(head + [100 + i * 0.3 for i in range(25, 40)])
        tail_modified = _make_ohlc(head + [90 + i for i in range(15)])
        # The old code averaged only the FIRST 14 TR/DM values, so bars after
        # index 14 never changed the result.
        assert _adx_single(base, 14) != _adx_single(tail_modified, 14)

    def test_strong_trend_high_dx(self):
        ohlc = _make_ohlc([100 + i for i in range(40)])
        assert _adx_single(ohlc, 14) > 80


class TestRsiDivergenceFixed:
    def test_bullish_non_overlapping_windows(self):
        # Prior 5-bar window: sharp drop (price low 90, RSI capitulates).
        # Recent 5-bar window: marginally lower price low (89.5) but RSI
        # holds higher -> bullish divergence. The old sliding windows
        # overlapped by 4 bars and could not isolate this.
        closes = ([100 - i * 0.2 for i in range(20)]
                  + [94, 92, 90, 91, 92]
                  + [89.5, 90.5, 91.5, 92.5, 93.5])
        sig = detect_rsi_divergence(_make_ohlc(closes))
        assert sig is not None and sig["action"] == "BUY"


class TestVWAPFixed:
    def test_uses_typical_price(self):
        # Asymmetric bars: typical price (108+98+100)/3 = 102, close = 100.
        # A current close of 96.9 is below the typical-price VWAP lower band
        # (97.0) -> BUY; with the old close-based VWAP (100, band low 95)
        # no signal fired.
        spec = ([(100, 108, 98, 100, 1000.0)] * 24
                + [(100, 108, 98, 96.9, 1000.0)])
        sig = _vwap(_make_bars(spec))
        assert sig is not None and sig["action"] == "BUY"


class TestIchimokuFixed:
    def test_needs_78_bars(self):
        # Senkou B at the latest bar needs 52 + 26 = 78 bars (26-bar forward
        # displacement), so a 60-bar series must return Nones.
        assert _ichimoku(_make_ohlc([100 + i * 0.2 for i in range(60)])) == (None,) * 5

    def test_senkou_displaced_26_bars(self):
        ohlc = _make_ohlc([100 + i * 0.2 for i in range(80)])
        tenkan, kijun, senkou_a, senkou_b, chikou = _ichimoku(ohlc)
        highs = [b["high"] for b in ohlc]
        lows = [b["low"] for b in ohlc]
        tenkan_then = (max(highs[-35:-26]) + min(lows[-35:-26])) / 2
        kijun_then = (max(highs[-52:-26]) + min(lows[-52:-26])) / 2
        assert senkou_a == pytest.approx((tenkan_then + kijun_then) / 2)
        assert senkou_b == pytest.approx(
            (max(highs[-78:-26]) + min(lows[-78:-26])) / 2)
        # Tenkan/kijun themselves are NOT displaced.
        assert tenkan == pytest.approx((max(highs[-9:]) + min(lows[-9:])) / 2)
        assert kijun == pytest.approx((max(highs[-26:]) + min(lows[-26:])) / 2)


# ---------------------------------------------------------------------------
# Family registry wiring + regime map + signal contract
# ---------------------------------------------------------------------------

class TestFamilyRegistryWiring:
    def test_total_strategies(self):
        assert len(ALL_STRATEGIES) == 28 + 146  # legacy + six new families

    def test_names_unique(self):
        names = [n for n, _ in ALL_STRATEGIES]
        assert len(names) == len(set(names))

    def test_each_family_present(self):
        names = {n for n, _ in ALL_STRATEGIES}
        for sample in (
            "Trend - Supertrend 10/3",   # trend_momentum (display-name registry)
            "MR - ConnorsRSI",           # mean_reversion
            "Breakout - ORB-30",         # breakout_volatility
            "VO - OBV Trend Confirm",    # volume_orderflow
            "ict_pa_fvg_retrace",        # ict_smc (machine-tag registry)
            "Quant - TSMOM Classic",     # quant_forex_specific
        ):
            assert sample in names

    def test_family_counts(self):
        names = [n for n, _ in ALL_STRATEGIES]
        assert sum(n.startswith("Trend - ") for n in names) == 29
        assert sum(n.startswith("MR - ") for n in names) == 26
        assert sum(n.startswith(("Breakout - ", "Volatility - "))
                   for n in names) == 29
        assert sum(n.startswith("VO - ") for n in names) == 21
        assert sum(n.startswith("ict_pa_") for n in names) == 26
        assert sum(n.startswith("Quant - ") for n in names) == 15


class TestRegimeMap:
    def test_every_strategy_listed_somewhere(self):
        known = set()
        for names in REGIME_STRATEGIES.values():
            known.update(names)
        for n, _ in ALL_STRATEGIES:
            assert n in known, f"{n} not listed in any regime"

    def test_no_phantom_regime_names(self):
        all_names = {n for n, _ in ALL_STRATEGIES}
        for regime, names in REGIME_STRATEGIES.items():
            for n in names:
                assert n in all_names, f"{n} in {regime} but not ALL_STRATEGIES"

    def test_regime_filter_still_filters(self):
        trend = {n for n, _ in strategies_for_regime("trending_up")}
        assert "Trend - Supertrend 10/3" in trend
        assert "MR - ConnorsRSI" not in trend  # mean reversion stays ranging

    def test_fail_open_for_unlisted_strategy(self, caplog):
        probe = ("TEST - Unlisted Probe", lambda ohlc: None)
        strategies_mod.ALL_STRATEGIES.append(probe)
        strategies_mod._REGIME_KNOWN_NAMES = None
        strategies_mod._fail_open_noted.clear()
        try:
            with caplog.at_level(logging.WARNING, logger="strategies"):
                selected = {n for n, _ in strategies_for_regime("ranging")}
            assert "TEST - Unlisted Probe" in selected
            assert any("failing open" in r.message for r in caplog.records)
        finally:
            strategies_mod.ALL_STRATEGIES.remove(probe)
            strategies_mod._REGIME_KNOWN_NAMES = None
            strategies_mod._fail_open_noted.clear()


class TestSignalContractAllStrategies:
    def _synthetic(self, n=120):
        closes = []
        px = 100.0
        for i in range(n):
            px += ((i * 37) % 11 - 5) * 0.35
            closes.append(round(px, 4))
        return _make_ohlc(closes,
                          volumes=[1000.0 + (i % 7) * 250 for i in range(n)])

    def test_every_strategy_runs_and_returns_contract(self):
        ohlc = self._synthetic()
        for name, fn in ALL_STRATEGIES:
            result = fn(ohlc)  # must not raise
            if result is None:
                continue
            assert isinstance(result, dict), name
            assert result["action"] in ("BUY", "SELL"), name
            assert isinstance(result["confidence"], (int, float)), name
            assert 0 < result["confidence"] <= 1, name
            assert isinstance(result["reasons"], list), name

    def test_scan_symbol_attribution(self):
        ohlc = self._synthetic()
        all_names = {n for n, _ in ALL_STRATEGIES}
        for combined in scan_symbol(ohlc):
            assert combined["action"] in ("BUY", "SELL")
            assert combined["confidence"] <= 0.95
            assert combined["strategies"], "per-strategy attribution missing"
            for n in combined["strategies"]:
                assert n in all_names

    def test_scan_symbol_with_each_regime_runs(self):
        ohlc = self._synthetic()
        for regime in list(REGIME_STRATEGIES) + [None, "unknown"]:
            assert isinstance(scan_symbol(ohlc, regime=regime), list)

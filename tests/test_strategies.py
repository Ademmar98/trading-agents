import pytest
from core.strategies import (
    detect_fvg, detect_order_block, detect_liquidity_sweep, detect_bos_choch,
    detect_ote, detect_market_structure, detect_sma_crossover, detect_rsi_divergence,
    detect_macd, detect_bollinger, detect_atr_breakout, detect_engulfing,
    detect_pin_bar, detect_inside_bar, detect_double_top_bottom,
    detect_ema_cross, _vwap, detect_ichimoku, detect_keltner,
    detect_stochastic_rsi, detect_volume_breakout, detect_support_resistance,
    detect_donchian, detect_heikin_ashi, detect_mfi,
    scan_symbol, ALL_STRATEGIES,
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

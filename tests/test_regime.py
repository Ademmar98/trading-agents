import math
from core.regime import detect_regime, _adx, _bb, _volume_ratio


def _make_ohlc(closes, volatility=2):
    return [{
        "high": c + volatility * 0.5,
        "low": c - volatility * 0.5,
        "close": c,
        "volume": 1000,
    } for c in closes]


def test_unknown_regime_too_few_candles():
    ohlc = _make_ohlc([100] * 50)
    result = detect_regime(ohlc)
    assert result == "unknown"


def test_trending_up_regime():
    closes = [100 + i * 0.5 for i in range(80)]
    ohlc = _make_ohlc(closes, volatility=1)
    result = detect_regime(ohlc)
    assert isinstance(result, dict)
    assert result["regime"] == "trending_up"
    assert result["adx"] > 25
    assert result["sma_20_50_cross"] == "bullish"


def test_trending_down_regime():
    closes = [140 - i * 0.5 for i in range(80)]
    ohlc = _make_ohlc(closes, volatility=1)
    result = detect_regime(ohlc)
    assert isinstance(result, dict)
    assert result["regime"] == "trending_down"
    assert result["sma_20_50_cross"] == "bearish"


def test_ranging_regime():
    closes = [100 + math.sin(i * 0.5) * 2 for i in range(80)]
    ohlc = _make_ohlc(closes, volatility=1)
    result = detect_regime(ohlc)
    assert isinstance(result, dict)
    assert result["regime"] == "ranging"
    assert result["adx"] < 25


def test_volatile_via_bb():
    closes = [100 + (i % 20 < 5) * 30 for i in range(80)]
    ohlc = [{
        "high": c + 5,
        "low": c - 5,
        "close": c,
        "volume": 1000,
    } for c in closes]
    result = detect_regime(ohlc)
    if result != "unknown":
        assert result["regime"] == "volatile"


def test_volatile_via_atr():
    closes = [100 + (i % 3) * 10 for i in range(80)]
    ohlc = [{
        "high": c + 8,
        "low": c - 8,
        "close": c,
        "volume": 1000,
    } for c in closes]
    result = detect_regime(ohlc)
    if result != "unknown":
        assert result["regime"] == "volatile"


def test_generic_trending():
    closes = [100 + i * 0.3 for i in range(80)]
    closes[-20:] = [c + 5 for c in closes[-20:]]
    ohlc = _make_ohlc(closes, volatility=1)
    result = detect_regime(ohlc)
    assert isinstance(result, dict)
    assert "trend" in result["regime"]


def test_adx_insufficient_data():
    result = _adx([100, 101], [99, 100], [100, 101])
    assert result is None


def test_bb_insufficient_data():
    width, avg = _bb([100] * 5, period=20)
    assert width == 0
    assert avg == 0


def test_volume_ratio_insufficient_data():
    result = _volume_ratio([{"volume": 100}] * 5)
    assert result == 1.0


def test_volume_ratio_elevated():
    data = [{"volume": 100}] * 10 + [{"volume": 1000}] * 5
    result = _volume_ratio(data)
    assert result > 1.0


def test_volume_ratio_zero_avg():
    data = [{"volume": 0}] * 10 + [{"volume": 100}] * 5
    result = _volume_ratio(data)
    assert result == 1.0


def test_detect_regime_returns_all_keys():
    closes = [100 + i * 0.5 for i in range(80)]
    ohlc = _make_ohlc(closes, volatility=1)
    result = detect_regime(ohlc)
    expected_keys = {"regime", "adx", "atr_pct", "volatility", "trend_strength",
                     "bb_position", "volume_ratio", "sma_20_50_cross", "price_vs_sma"}
    assert set(result.keys()) == expected_keys

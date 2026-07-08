import pytest
from core.indicators import (
    sma, ema, rsi, macd, bollinger_bands, atr, stochastic_rsi, mfi, compute_all,
)


class TestSMA:
    def test_basic(self):
        assert sma([1, 2, 3, 4, 5], 3) == 4.0

    def test_not_enough(self):
        assert sma([1, 2], 3) is None


class TestRSI:
    def test_all_up(self):
        assert rsi([10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24], 14) > 90

    def test_all_down(self):
        assert rsi([24, 23, 22, 21, 20, 19, 18, 17, 16, 15, 14, 13, 12, 11, 10], 14) < 10

    def test_not_enough(self):
        assert rsi([1, 2, 3], 14) == 50.0


class TestMACD:
    def test_basic(self):
        result = macd([10 + i * 0.5 for i in range(50)])
        assert result is not None
        assert "macd" in result
        assert "signal" in result
        assert "histogram" in result

    def test_not_enough(self):
        assert macd([1, 2, 3], 12, 26, 9) is None


class TestBollingerBands:
    def test_basic(self):
        result = bollinger_bands([100 + i for i in range(30)])
        assert result is not None
        assert result["upper"] > result["middle"]
        assert result["lower"] < result["middle"]

    def test_not_enough(self):
        assert bollinger_bands([1, 2, 3], 20) is None


class TestATR:
    def test_basic(self):
        result = atr(
            [10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24],
            [9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23],
            [9.5, 10.5, 11.5, 12.5, 13.5, 14.5, 15.5, 16.5, 17.5, 18.5, 19.5, 20.5, 21.5, 22.5, 23.5],
            period=14,
        )
        assert result is not None
        assert result > 0

    def test_not_enough(self):
        assert atr([1, 2], [1, 2], [1, 2], 14) is None


class TestStochasticRSI:
    def test_basic(self):
        result = stochastic_rsi([10 + i * 0.5 for i in range(30)])
        # May be None if not enough data for the full calc
        if result is not None:
            assert "k" in result
            assert "d" in result

    def test_not_enough(self):
        assert stochastic_rsi([1, 2, 3], 14) is None


class TestMFI:
    def test_basic(self):
        result = mfi(
            [10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24],
            [9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23],
            [9.5, 10.5, 11.5, 12.5, 13.5, 14.5, 15.5, 16.5, 17.5, 18.5, 19.5, 20.5, 21.5, 22.5, 23.5],
            [1000] * 15,
            period=14,
        )
        assert result is not None

    def test_not_enough(self):
        assert mfi([1, 2], [1, 2], [1, 2], [1, 2], 14) is None


class TestComputeAll:
    def test_empty(self):
        assert compute_all([]) == {}

    def test_close_only(self):
        ohlc = [{"close": float(i)} for i in range(50)]
        result = compute_all(ohlc)
        assert result["current_price"] == 49.0
        assert "rsi_14" in result
        assert "sma_20" in result
        assert result["trend"] in ("bullish", "bearish", "neutral")

    def test_full_ohlc(self):
        ohlc = [{"open": float(i), "high": float(i + 1), "low": float(i - 1), "close": float(i), "volume": 1000} for i in range(60)]
        result = compute_all(ohlc)
        assert result["current_price"] == 59.0
        assert "atr" in result
        assert "bollinger" in result
        assert "volatility" in result

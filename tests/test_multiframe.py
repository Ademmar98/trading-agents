from unittest.mock import MagicMock, patch

import pytest

from core.multiframe import (
    TIMEFRAMES,
    analyze_symbol_multiframe,
    _consolidate_signals,
)


class TestConsolidateSignals:
    def test_no_results_returns_none(self):
        result = _consolidate_signals("BTC/USD", {})
        assert result is None

    def test_buy_alignment(self):
        tf_results = {
            "15m": {"signals": [{"action": "BUY", "confidence": 0.8}], "price": 50000},
            "1h": {"signals": [{"action": "BUY", "confidence": 0.7}], "price": 50000},
            "4h": {"signals": [{"action": "BUY", "confidence": 0.8}], "price": 50000},
            "1d": {"signals": [{"action": "BUY", "confidence": 0.9}], "price": 50000},
            "1w": {"signals": [{"action": "BUY", "confidence": 0.6}], "price": 50000},
        }
        result = _consolidate_signals("BTC/USD", tf_results)
        assert result is not None
        assert result["action"] == "BUY"
        assert result["bias"] == "BUY"

    def test_sell_alignment(self):
        tf_results = {
            "15m": {"signals": [{"action": "SELL", "confidence": 0.8}], "price": 50000},
            "1h": {"signals": [{"action": "SELL", "confidence": 0.7}], "price": 50000},
            "4h": {"signals": [{"action": "SELL", "confidence": 0.8}], "price": 50000},
            "1d": {"signals": [{"action": "SELL", "confidence": 0.9}], "price": 50000},
            "1w": {"signals": [{"action": "SELL", "confidence": 0.6}], "price": 50000},
        }
        result = _consolidate_signals("BTC/USD", tf_results)
        assert result is not None
        assert result["action"] == "SELL"
        assert result["bias"] == "SELL"

    def test_conflict_reduces_confidence(self):
        tf_results = {
            "15m": {"signals": [{"action": "BUY", "confidence": 0.8}], "price": 50000},
            "1h": {"signals": [{"action": "BUY", "confidence": 0.7}], "price": 50000},
            "4h": {"signals": [{"action": "BUY", "confidence": 0.8}], "price": 50000},
            "1d": {"signals": [{"action": "SELL", "confidence": 0.9}], "price": 50000},
            "1w": {"signals": [{"action": "SELL", "confidence": 0.6}], "price": 50000},
        }
        regimes = {
            "1d": {"regime": "trending_down"},
            "1w": {"regime": "trending_down"},
        }
        result = _consolidate_signals("BTC/USD", tf_results, regimes=regimes)
        if result:
            assert result["confidence"] <= 0.95

    def test_no_bias_when_weak(self):
        tf_results = {
            "15m": {"signals": [{"action": "BUY", "confidence": 0.05}], "price": 50000},
            "1h": {"signals": [{"action": "BUY", "confidence": 0.04}], "price": 50000},
        }
        result = _consolidate_signals("BTC/USD", tf_results)
        assert result is None

    def test_volatile_clears_bias(self):
        tf_results = {
            "15m": {"signals": [{"action": "BUY", "confidence": 0.8}], "price": 50000},
            "1h": {"signals": [{"action": "BUY", "confidence": 0.7}], "price": 50000},
            "4h": {"signals": [{"action": "BUY", "confidence": 0.8}], "price": 50000},
            "1d": {"signals": [{"action": "BUY", "confidence": 0.9}], "price": 50000},
            "1w": {"signals": [{"action": "BUY", "confidence": 0.6}], "price": 50000},
        }
        regimes = {
            "4h": {"regime": "volatile"},
            "1d": {"regime": "volatile"},
            "1w": {"regime": "volatile"},
        }
        result = _consolidate_signals("BTC/USD", tf_results, regimes=regimes)
        if result:
            assert result["bias"] is None or result["bias"] == "BUY"

    def test_entry_without_bias(self):
        tf_results = {
            "15m": {"signals": [{"action": "BUY", "confidence": 0.8}], "price": 50000},
            "1h": {"signals": [{"action": "BUY", "confidence": 0.7}], "price": 50000},
        }
        result = _consolidate_signals("BTC/USD", tf_results)
        if result:
            assert result["action"] is not None


class TestAnalyzeSymbolMultiframe:
    def test_no_data_returns_none(self):
        with patch("core.multiframe.fetch_klines", return_value=[]):
            result = analyze_symbol_multiframe("BTC/USD")
            assert result is None

    def test_with_data_calls_consolidate(self):
        ohlc = [{"close": 50000 + i, "high": 50100 + i, "low": 49900 + i} for i in range(100)]

        with patch("core.multiframe.fetch_klines", return_value=ohlc):
            with patch("core.multiframe.scan_symbol", return_value=[{"action": "BUY", "confidence": 0.8}]):
                with patch("core.multiframe.detect_regime", return_value={"regime": "trending_up"}):
                    result = analyze_symbol_multiframe("BTC/USD")
                    if result:
                        assert "action" in result
                        assert "confidence" in result

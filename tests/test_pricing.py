import pytest

from core.pricing import compute_pricing, REGIME_PRICING, DEFAULT_PRICING


BASE_DATA = {
    "volatility": 2.0,
    "bid": 50000,
    "ask": 50010,
    "sma_20": 49500,
    "sma_50": 49000,
}


class TestRegimePricingDicts:
    def test_all_regimes_present(self):
        assert "trending_up" in REGIME_PRICING
        assert "trending_down" in REGIME_PRICING
        assert "trending" in REGIME_PRICING
        assert "volatile" in REGIME_PRICING
        assert "ranging" in REGIME_PRICING

    def test_default_pricing_keys(self):
        for k in ("sl_mult", "tp_mult", "entry_slip", "risk_mult"):
            assert k in DEFAULT_PRICING


class TestComputePricingBuy:
    def test_basic_buy(self):
        result = compute_pricing("BTC/USD", "BUY", 50000, BASE_DATA)
        assert result["symbol"] == "BTC/USD"
        assert result["action"] == "BUY"
        assert result["entry_price"] > 0
        assert result["stop_loss"] < result["entry_price"] < result["take_profit"]

    def test_buy_with_regime(self):
        result = compute_pricing("ETH/USD", "BUY", 3000, BASE_DATA, regime="volatile")
        assert result["regime"] == "volatile"
        assert result["stop_loss"] < result["entry_price"] < result["take_profit"]

    def test_buy_sma_fallback(self):
        data = {"volatility": 1.0, "bid": 50000, "ask": 50010}
        result = compute_pricing("BTC/USD", "BUY", 50000, data)
        assert result["entry_price"] > 0

    def test_buy_zero_volatility(self):
        data = {"volatility": 0, "bid": 50000, "ask": 50010}
        result = compute_pricing("BTC/USD", "BUY", 50000, data)
        assert result["sl_pct"] >= 0.5

    def test_buy_price_below_sma(self):
        data = dict(BASE_DATA, bid=48000, sma_20=49500, sma_50=49000)
        result = compute_pricing("BTC/USD", "BUY", 48000, data)
        assert result["entry_price"] == 48000

    def test_buy_price_above_sma_uses_sma(self):
        data = dict(BASE_DATA, bid=51000, sma_20=49500)
        result = compute_pricing("BTC/USD", "BUY", 51000, data)
        assert result["entry_price"] >= 49500


class TestComputePricingSell:
    def test_basic_sell(self):
        result = compute_pricing("BTC/USD", "SELL", 50000, BASE_DATA)
        assert result["action"] == "SELL"
        assert result["take_profit"] < result["entry_price"] < result["stop_loss"]

    def test_sell_price_above_sma(self):
        data = dict(BASE_DATA, ask=51000, sma_20=49500)
        result = compute_pricing("BTC/USD", "SELL", 51000, data)
        assert result["entry_price"] >= 49500


class TestComputePricingEdgeCases:
    def test_price_is_zero(self):
        result = compute_pricing("BTC/USD", "BUY", 0, BASE_DATA)
        assert result["entry_price"] >= 0
        assert result["sl_pct"] >= 0

    def test_atr_provided(self):
        result = compute_pricing("BTC/USD", "BUY", 50000, BASE_DATA, atr_val=1000)
        assert result["atr_pct"] > 0

    def test_unknown_regime_defaults(self):
        result = compute_pricing("BTC/USD", "BUY", 50000, BASE_DATA, regime="unknown_regime")
        assert result["risk_rationale"].startswith("unknown_regime")

    def test_risk_capped(self):
        result = compute_pricing("SOL/USD", "BUY", 150, BASE_DATA)
        assert result["calculated_risk_pct"] <= 3.0

    def test_no_regime_uses_default(self):
        data = {"volatility": 2.0, "bid": 50000, "ask": 50010}
        result = compute_pricing("BTC/USD", "BUY", 50000, data)
        assert result["sl_mult"] == DEFAULT_PRICING["sl_mult"]

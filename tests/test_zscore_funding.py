"""
Tests for Z-Score Funding Squeeze Strategy
"""
import pytest
import pandas as pd
import numpy as np
from datetime import datetime, timezone

from core.strategies.zscore_funding_squeeze import (
    ZScoreFundingSqueezeStrategy,
    StrategyParams,
    BacktestResult,
)


@pytest.fixture
def sample_data():
    """Generate sample market data for testing."""
    np.random.seed(42)
    n = 100

    timestamps = pd.date_range(
        start="2026-01-01", periods=n, freq="8h"
    )

    # Simulate price with uptrend
    base_price = 100
    returns = np.random.normal(0.001, 0.02, n)
    prices = base_price * np.cumprod(1 + returns)

    # Simulate funding rates (mostly positive, occasionally negative)
    funding = np.random.normal(0.0001, 0.0005, n)
    funding[20:25] = -0.002  # Force negative spike
    funding[50:55] = -0.003  # Another negative spike

    # Perp prices (slightly different from spot)
    perp_prices = prices * (1 + np.random.normal(0, 0.001, n))

    df = pd.DataFrame({
        "timestamp": timestamps,
        "spot_price": prices,
        "perp_price": perp_prices,
        "perp_funding_rate": funding,
    })

    return df


@pytest.fixture
def strategy():
    """Create strategy with default params."""
    return ZScoreFundingSqueezeStrategy(StrategyParams())


def test_strategy_initialization():
    """Test strategy initializes with correct params."""
    params = StrategyParams(
        z_score_threshold=-2.5,
        atr_period=20,
    )
    strat = ZScoreFundingSqueezeStrategy(params)

    assert strat.params.z_score_threshold == -2.5
    assert strat.params.atr_period == 20


def test_compute_indicators(strategy, sample_data):
    """Test indicator computation."""
    df = strategy.compute_indicators(sample_data)

    assert 'funding_zscore' in df.columns
    assert 'atr14' in df.columns
    assert 'sma50' in df.columns
    assert 'tr' in df.columns


def test_backtest_returns_result(strategy, sample_data):
    """Test backtest returns valid result."""
    result = strategy.run_backtest(sample_data, symbol="TESTUSDT")

    assert isinstance(result, BacktestResult)
    assert result.symbol == "TESTUSDT"
    assert result.bars == len(sample_data)
    assert 0 <= result.win_rate <= 100
    assert result.profit_factor >= 0


def test_backtest_no_trades_on_flat_data():
    """Test backtest handles data with no signals."""
    n = 50
    df = pd.DataFrame({
        "timestamp": pd.date_range("2026-01-01", periods=n, freq="8h"),
        "spot_price": [100.0] * n,
        "perp_price": [100.0] * n,
        "perp_funding_rate": [0.0001] * n,  # Always positive, no entry
    })

    strat = ZScoreFundingSqueezeStrategy()
    result = strat.run_backtest(df, symbol="FLATUSDT")

    assert result.total_trades == 0
    assert result.net_return == 0.0


def test_exit_conditions(strategy, sample_data):
    """Test exit conditions trigger correctly."""
    df = strategy.compute_indicators(sample_data)

    # Create a mock trade
    from core.strategies.zscore_funding_squeeze import TradeRecord

    trade = TradeRecord(
        symbol="TEST",
        entry_price=100.0,
        entry_atr=1.0,
        entry_funding=-0.001,
        entry_zscore=-2.5,
        entry_idx=0,
        entry_time="2026-01-01",
        sl_price=98.5,  # 1.5 * ATR
        tp_price=103.0,  # 3.0 * ATR
    )

    # Test TP hit
    row_tp = df.iloc[0].copy()
    row_tp['spot_price'] = 103.5
    assert strategy.check_exit_conditions(row_tp, trade) == 'TAKE_PROFIT'

    # Test SL hit
    row_sl = df.iloc[0].copy()
    row_sl['spot_price'] = 98.0
    assert strategy.check_exit_conditions(row_sl, trade) == 'STOP_LOSS'

    # Test funding neutral
    row_fn = df.iloc[0].copy()
    row_fn['perp_funding_rate'] = 0.0001
    assert strategy.check_exit_conditions(row_fn, trade) == 'FUNDING_NEUTRAL'


def test_status_returns_dict(strategy):
    """Test status method returns expected structure."""
    status = strategy.status()

    assert "strategy" in status
    assert "params" in status
    assert "backtested_pf" in status
    assert status["backtested_pf"] == 2.93


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

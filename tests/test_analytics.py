import os
import tempfile
from pathlib import Path

import pytest

import config as app_config
from core.database import init_db, execute
from core.analytics import compute_analytics, get_analytics, _compute_strategy_stats, _value_at_risk, _rolling_drawdown, _trade_duration_stats


@pytest.fixture(autouse=True)
def sandbox_data_dir(monkeypatch):
    tmp = Path(tempfile.mkdtemp(prefix="trading-test-"))
    monkeypatch.setattr(app_config, "DATA_DIR", tmp)
    monkeypatch.setenv("TRADING_DATA_DIR", str(tmp))
    init_db()
    yield
    import shutil
    shutil.rmtree(str(tmp), ignore_errors=True)


def seed_trades(count=10, strategy="FVG"):
    for i in range(count):
        pnl = 50.0 if i % 2 == 0 else -20.0
        execute(
            "INSERT INTO trades (symbol, side, qty, entry_price, exit_price, pnl, pnl_pct, reason, strategy, opened_at, closed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ["BTC/USD", "BUY", 0.1, 60000.0, 61000.0, pnl, 1.0, "TP", strategy,
             "2024-01-01T00:00:00", "2024-01-02T00:00:00"],
        )


def test_empty_analytics():
    result = compute_analytics()
    assert result["total_trades"] == 0
    assert result["win_rate"] == 0
    assert result["strategy_breakdown"] == []


def test_compute_analytics_with_trades():
    seed_trades(10)
    result = compute_analytics()
    assert result["total_trades"] == 10
    assert result["win_rate"] == 50.0
    assert result["total_pnl"] == 150.0
    assert result["var_95"] > 0


def test_partial_exit_legs_merge_into_one_trade():
    """Regression: a partial_tp row + runner row for the same position_id are
    ONE trade with net pnl. Ungrouped they counted as 2 trades (1 win / 1
    loss here), skewing win rate and profit factor."""
    execute(
        "INSERT INTO positions (id, symbol, side, quantity, entry_price, current_price, initial_risk) "
        "VALUES (1, 'BTC/USD', 'BUY', 0.1, 100.0, 100.0, 5.0)")
    execute(
        "INSERT INTO trades (position_id, symbol, side, qty, entry_price, exit_price, pnl, pnl_pct, reason, strategy, opened_at, closed_at) "
        "VALUES (1, 'BTC/USD', 'BUY', 0.05, 100.0, 110.0, 30.0, 10.0, 'partial_tp', 'FVG', '2024-01-01T00:00:00', '2024-01-02T00:00:00')")
    execute(
        "INSERT INTO trades (position_id, symbol, side, qty, entry_price, exit_price, pnl, pnl_pct, reason, strategy, opened_at, closed_at) "
        "VALUES (1, 'BTC/USD', 'BUY', 0.05, 100.0, 96.0, -10.0, -4.0, 'runner', 'FVG', '2024-01-01T00:00:00', '2024-01-03T00:00:00')")
    # A second, single-exit losing position.
    execute(
        "INSERT INTO positions (id, symbol, side, quantity, entry_price, current_price, initial_risk) "
        "VALUES (2, 'ETH/USD', 'BUY', 1.0, 100.0, 100.0, 5.0)")
    execute(
        "INSERT INTO trades (position_id, symbol, side, qty, entry_price, exit_price, pnl, pnl_pct, reason, strategy, opened_at, closed_at) "
        "VALUES (2, 'ETH/USD', 'BUY', 1.0, 100.0, 95.0, -40.0, -5.0, 'stop_loss', 'MACD', '2024-01-01T00:00:00', '2024-01-02T00:00:00')")
    result = compute_analytics()
    assert result["total_trades"] == 2          # not 3
    assert result["win_rate"] == 50.0           # net +20 winner, not 1/3
    assert result["total_pnl"] == -20.0
    assert result["profit_factor"] == 0.5       # 20 / 40


def test_strategy_breakdown():
    seed_trades(5, "ICT-FVG")
    seed_trades(5, "MACD")
    result = compute_analytics()
    assert len(result["strategy_breakdown"]) == 2
    names = [s["strategy"] for s in result["strategy_breakdown"]]
    assert "ICT-FVG" in names
    assert "MACD" in names


def test_get_analytics_empty():
    result = get_analytics()
    assert result["total_trades"] == 0


def test_get_analytics_cached():
    seed_trades(5)
    compute_analytics()
    result = get_analytics()
    assert result["total_trades"] == 5
    assert 0 < result["win_rate"] <= 100


def test_value_at_risk():
    pnls = [100, 200, -50, -300, 150, -100, 50, 75, -25, 10]
    var = _value_at_risk(pnls, 0.95)
    assert var > 0


def test_value_at_risk_short():
    assert _value_at_risk([1, 2], 0.95) == 0


def test_rolling_drawdown():
    # Newest-first (as stored): chronological is -300, -50, +200, +100.
    # Equity from 10k: 9700 -> 9650 -> 9850 -> 9950; max dd = 350/10000.
    pnls = [100, 200, -50, -300]
    max_dd, rolling = _rolling_drawdown(pnls, initial_balance=10000)
    assert max_dd == pytest.approx(0.035)
    assert len(rolling) == 4


def test_drawdown_not_inflated_by_small_wins():
    """Two wins in a row is zero drawdown — the old per-trade-pnl math
    called a $10 win followed by a $2 win an 80% drawdown."""
    max_dd, _ = _rolling_drawdown([2.0, 10.0], initial_balance=10000)
    assert max_dd == 0.0


def test_trade_duration_stats():
    from datetime import datetime, timezone
    trades = [
        {"opened_at": "2024-01-01T00:00:00", "closed_at": "2024-01-02T00:00:00"},
        {"opened_at": "2024-01-01T00:00:00", "closed_at": "2024-01-01T06:00:00"},
    ]
    stats = _trade_duration_stats(trades)
    assert stats["count"] == 2
    assert stats["avg_hours"] == 15.0


def test_trade_duration_stats_empty():
    assert _trade_duration_stats([])["count"] == 0


def test_trade_duration_stats_no_dates():
    trades = [{"pnl": 100}, {"pnl": -50}]
    assert _trade_duration_stats(trades)["count"] == 0

"""Tests for the scaled-exit framework (from the exit-strategies skill).

At PARTIAL_TP_R x initial risk in profit, the manager banks
PARTIAL_TP_FRACTION of the position and moves the stop to breakeven; the
remainder trails R-based (arm at TRAILING_ACTIVATION_R, give back
TRAILING_STOP_R from peak) instead of the old percent-of-price trail that
clipped winners at a fraction of a full stop-loss.
"""
import tempfile
from pathlib import Path

import pytest

import config as app_config
import core.positions as core_positions
from core.database import init_db
from core.positions import PositionManager

FEE = app_config.TRADE_FEE_PCT / 100.0


@pytest.fixture(autouse=True)
def sandbox_data_dir(monkeypatch):
    tmp = Path(tempfile.mkdtemp(prefix="trading-test-"))
    monkeypatch.setattr(app_config, "DATA_DIR", tmp)
    monkeypatch.setenv("TRADING_DATA_DIR", str(tmp))
    init_db()
    yield
    import shutil
    shutil.rmtree(str(tmp), ignore_errors=True)


@pytest.fixture
def pos_mgr():
    return PositionManager()


BUFFER = app_config.BREAKEVEN_BUFFER_PCT / 100.0


def test_partial_tp_banks_fraction_and_goes_breakeven(pos_mgr):
    pos_id = pos_mgr.open_position("BTC/USD", "BUY", 1.0, 100.0, sl=95.0, tp=115.0)
    # risk = 5, partial target = 100 + 5 * 1.5 = 107.5
    triggered = pos_mgr.update_prices({"BTC/USD": {"price": 107.5}})

    assert len(triggered) == 1
    t = triggered[0]
    assert t["reason"] == "partial_tp"
    assert t["qty"] == pytest.approx(0.5)
    expected_pnl = (107.5 - 100.0) * 0.5 - (100.0 + 107.5) * 0.5 * FEE
    assert t["pnl"] == pytest.approx(round(expected_pnl, 2))

    open_pos = pos_mgr.get_open_positions()
    assert len(open_pos) == 1
    assert open_pos[0]["quantity"] == pytest.approx(0.5)
    # Runner stop = entry + buffer so a stopped runner still clears fees
    assert open_pos[0]["stop_loss"] == pytest.approx(100.0 * (1 + BUFFER))
    assert open_pos[0]["partial_taken"] == 1
    assert pos_id == open_pos[0]["id"]


def test_runner_stopped_at_breakeven_keeps_banked_profit(pos_mgr):
    pos_mgr.open_position("BTC/USD", "BUY", 1.0, 100.0, sl=95.0, tp=115.0)
    banked = pos_mgr.update_prices({"BTC/USD": {"price": 107.5}})
    # Price collapses back through breakeven: runner exits at ~entry
    stopped = pos_mgr.update_prices({"BTC/USD": {"price": 99.9}})

    assert stopped[0]["reason"] == "stop_loss"
    assert stopped[0]["qty"] == pytest.approx(0.5)
    total = banked[0]["pnl"] + stopped[0]["pnl"]
    assert total > 0  # the banked half keeps the whole trade positive
    assert pos_mgr.get_open_positions() == []


def test_runner_holds_after_breakeven_no_trail(pos_mgr):
    """Once breakeven has moved the stop past entry, trailing is suppressed:
    the runner either reaches TP or exits at the buffered breakeven stop."""
    pos_mgr.open_position("ETH/USD", "BUY", 1.0, 100.0, sl=95.0, tp=115.0)
    # +1.2R: breakeven fires (activation = 1R), stop -> entry + buffer
    assert pos_mgr.update_prices({"ETH/USD": {"price": 106.0}}) == []
    open_pos = pos_mgr.get_open_positions()[0]
    assert open_pos["stop_loss"] == pytest.approx(100.0 * (1 + BUFFER))
    # Pullback that would have hit the old R-trail must NOT exit now
    assert pos_mgr.update_prices({"ETH/USD": {"price": 103.4}}) == []
    assert len(pos_mgr.get_open_positions()) == 1
    # Full retrace exits at the buffered stop, not a loss
    stopped = pos_mgr.update_prices({"ETH/USD": {"price": 100.2}})
    assert stopped[0]["reason"] == "stop_loss"


def test_trailing_active_when_breakeven_disabled(monkeypatch, pos_mgr):
    """The R-based trail still protects positions when breakeven is off."""
    monkeypatch.setattr(core_positions, "BREAKEVEN_ENABLED", False)
    pos_mgr.open_position("ETH/USD", "BUY", 1.0, 100.0, sl=95.0, tp=115.0)
    assert pos_mgr.update_prices({"ETH/USD": {"price": 106.0}}) == []
    # SL untouched (still 95), trail armed at >=1R: 0.5R giveback exits
    triggered = pos_mgr.update_prices({"ETH/USD": {"price": 103.4}})
    assert len(triggered) == 1
    assert triggered[0]["reason"] == "trailing_stop"


def test_trailing_not_armed_below_activation(pos_mgr):
    pos_mgr.open_position("ETH/USD", "BUY", 1.0, 100.0, sl=95.0, tp=115.0)
    # +0.8R peak: old %-trail would have armed at +0.8% and exited; R-trail must not
    assert pos_mgr.update_prices({"ETH/USD": {"price": 104.0}}) == []
    assert pos_mgr.update_prices({"ETH/USD": {"price": 103.0}}) == []
    assert len(pos_mgr.get_open_positions()) == 1


def test_partial_tp_can_be_disabled(monkeypatch, pos_mgr):
    monkeypatch.setattr(core_positions, "PARTIAL_TP_ENABLED", False)
    pos_mgr.open_position("BTC/USD", "BUY", 1.0, 100.0, sl=95.0, tp=115.0)
    triggered = pos_mgr.update_prices({"BTC/USD": {"price": 107.5}})
    assert triggered == []
    assert pos_mgr.get_open_positions()[0]["quantity"] == pytest.approx(1.0)


def test_pct_fallback_when_initial_risk_unknown():
    # Legacy rows (initial_risk=0) keep the old percent-of-price behavior
    hit = PositionManager._trailing_stop_hit("BUY", 100.0, 101.0, 100.4, 0)
    assert hit  # run-up 1% >= 0.8% activation; 100.4 <= 101 * 0.995
    not_hit = PositionManager._trailing_stop_hit("BUY", 100.0, 100.5, 100.3, 0)
    assert not not_hit  # run-up 0.5% < 0.8% activation


def test_scaled_exit_counts_as_one_trade_in_stats(pos_mgr):
    """Partial + runner rows must aggregate into one logical trade, or split
    rows deflate win rates and the unprofitable-strategy filter disables
    profitable strategies."""
    pos_mgr.open_position("BTC/USD", "BUY", 1.0, 100.0, sl=95.0, tp=115.0, strategy="ema_cross")
    pos_mgr.update_prices({"BTC/USD": {"price": 107.5}})   # banks the partial win
    pos_mgr.update_prices({"BTC/USD": {"price": 99.9}})    # runner stops at ~breakeven

    assert len(pos_mgr.get_recent_trades(10)) == 2  # raw rows: partial + runner

    from core.analytics import compute_analytics
    stats = compute_analytics()
    assert stats["total_trades"] == 1
    assert stats["win_rate"] == 100.0  # net pnl positive -> one winning trade
    breakdown = {s["strategy"]: s for s in stats["strategy_breakdown"]}
    assert breakdown["ema_cross"]["trades"] == 1
    assert breakdown["ema_cross"]["pnl"] > 0

    from core.webserver import _live_pnl_stats
    cnt, total, wins = _live_pnl_stats()
    assert cnt == 1 and wins == 1 and total > 0


def test_sell_side_partial_and_trail(pos_mgr):
    pos_mgr.open_position("BTC/USD", "SELL", 1.0, 100.0, sl=105.0, tp=85.0)
    # risk = 5; partial target = 100 - 7.5 = 92.5
    triggered = pos_mgr.update_prices({"BTC/USD": {"price": 92.5}})
    assert triggered[0]["reason"] == "partial_tp"
    open_pos = pos_mgr.get_open_positions()[0]
    assert open_pos["stop_loss"] == pytest.approx(100.0 * (1 - BUFFER))
    assert open_pos["quantity"] == pytest.approx(0.5)

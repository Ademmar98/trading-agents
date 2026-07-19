import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import config as app_config
from core.database import init_db, execute
from core.positions import PositionManager


@pytest.fixture(autouse=True)
def sandbox_data_dir(monkeypatch):
    """Isolate the sqlite ledger — this file previously ran against the real
    DATA_DIR/trading.db and deleted live positions/trades rows."""
    tmp = Path(tempfile.mkdtemp(prefix="trading-test-"))
    monkeypatch.setattr(app_config, "DATA_DIR", tmp)
    monkeypatch.setenv("TRADING_DATA_DIR", str(tmp))
    init_db()
    yield
    import shutil
    shutil.rmtree(str(tmp), ignore_errors=True)


def _clean_db():
    init_db()
    execute("DELETE FROM positions")
    execute("DELETE FROM trades")


def _age_open_positions(seconds=3600):
    """Backdate opened_at so the 1-bar minimum-hold guard (audit fix: exits
    were filling seconds after entry, booking same-bar moves as profit) does
    not block the exit checks below. 3600s >> one 5m bar."""
    past = (datetime.now(timezone.utc) - timedelta(seconds=seconds)).strftime("%Y-%m-%d %H:%M:%S")
    execute("UPDATE positions SET opened_at=? WHERE status='open'", [past])


@pytest.fixture
def pos_mgr():
    _clean_db()
    return PositionManager()


class TestPositionManager:
    def test_open_and_close_position(self, pos_mgr):
        pos_id = pos_mgr.open_position("BTC/USD", "BUY", 0.1, 50000, sl=48000, tp=55000)
        assert pos_id > 0
        open_positions = pos_mgr.get_open_positions()
        assert len(open_positions) == 1
        assert open_positions[0]["symbol"] == "BTC/USD"

        result = pos_mgr.close_position(pos_id, 55000, reason="TP")
        assert result is not None
        # 500 gross minus round-trip fees on both legs
        from config import TRADE_FEE_PCT
        fees = (50000 + 55000) * 0.1 * (TRADE_FEE_PCT / 100.0)
        assert result["pnl"] == pytest.approx(round(500.0 - fees, 2))

    def test_has_position(self, pos_mgr):
        pos_mgr.open_position("BTC/USD", "BUY", 0.1, 50000)
        assert pos_mgr.has_position("BTC/USD")
        assert not pos_mgr.has_position("ETH/USD")

    def test_close_nonexistent(self, pos_mgr):
        result = pos_mgr.close_position(999, 50000, reason="test")
        assert result is None

    def test_close_twice(self, pos_mgr):
        _clean_db()
        pos_mgr2 = PositionManager()
        pos_id = pos_mgr2.open_position("BTC/USD", "BUY", 0.1, 50000)
        pos_mgr2.close_position(pos_id, 55000, reason="TP")
        result = pos_mgr2.close_position(pos_id, 55000, reason="TP")
        assert result is None

    def test_trailing_stop_not_triggered_without_sl_tp(self, pos_mgr):
        _clean_db()
        mgr = PositionManager()
        mgr.open_position("BTC/USD", "BUY", 0.1, 50000)
        triggered = mgr.update_prices({"BTC/USD": {"price": 52000}})
        assert len(triggered) == 0

    def test_stop_loss_hit(self, pos_mgr):
        _clean_db()
        mgr = PositionManager()
        mgr.open_position("BTC/USD", "BUY", 0.1, 50000, sl=49000)
        _age_open_positions()
        triggered = mgr.update_prices({"BTC/USD": {"price": 48900}})
        assert len(triggered) == 1
        assert triggered[0]["reason"] == "stop_loss"
        # Stop-market realism: fill at the stop OR WORSE — price gapped to
        # 48900, so the fill must not be better than 49000 (and slips below
        # even the traded price).
        assert triggered[0]["exit_price"] <= 49000
        assert triggered[0]["exit_price"] < 48900

    def test_take_profit_hit(self, pos_mgr):
        _clean_db()
        mgr = PositionManager()
        mgr.open_position("BTC/USD", "BUY", 0.1, 50000, tp=55000)
        _age_open_positions()
        triggered = mgr.update_prices({"BTC/USD": {"price": 55100}})
        assert len(triggered) == 1
        assert triggered[0]["reason"] == "take_profit"
        # Limit realism: price overshot to 55100 but a resting limit fills AT
        # the TP price, never better (the old code booked the 55100 overshoot).
        assert triggered[0]["exit_price"] == 55000

    def test_get_positions_summary_empty(self, pos_mgr):
        _clean_db()
        mgr = PositionManager()
        summary = mgr.get_positions_summary()
        assert summary["count"] == 0

    def test_get_positions_summary_with_positions(self, pos_mgr):
        _clean_db()
        mgr = PositionManager()
        mgr.open_position("BTC/USD", "BUY", 0.1, 50000)
        mgr.open_position("ETH/USD", "SELL", 1.0, 1800)
        summary = mgr.get_positions_summary()
        assert summary["count"] == 2

    def test_get_recent_trades(self, pos_mgr):
        _clean_db()
        mgr = PositionManager()
        pos_id = mgr.open_position("BTC/USD", "BUY", 0.1, 50000)
        mgr.close_position(pos_id, 55000, reason="TP")
        trades = mgr.get_recent_trades(10)
        assert len(trades) == 1

    def test_filter_new_signals(self, pos_mgr):
        _clean_db()
        mgr = PositionManager()
        mgr.open_position("BTC/USD", "BUY", 0.1, 50000)
        opps = [
            {"symbol": "BTC/USD", "action": "BUY"},
            {"symbol": "ETH/USD", "action": "BUY"},
        ]
        filtered = mgr.filter_new_signals(opps)
        assert len(filtered) == 1
        assert filtered[0]["symbol"] == "ETH/USD"

    def test_update_prices_no_trigger(self, pos_mgr):
        _clean_db()
        mgr = PositionManager()
        mgr.open_position("BTC/USD", "BUY", 0.1, 50000)
        triggered = mgr.update_prices({"BTC/USD": {"price": 51000}})
        assert len(triggered) == 0

    def test_short_stop_loss(self, pos_mgr):
        _clean_db()
        mgr = PositionManager()
        mgr.open_position("BTC/USD", "SELL", 0.1, 50000, sl=51000)
        _age_open_positions()
        triggered = mgr.update_prices({"BTC/USD": {"price": 51100}})
        assert len(triggered) == 1
        assert triggered[0]["reason"] == "stop_loss"
        # Short stop: fill at the stop or WORSE (higher) — 51100 traded, slip up.
        assert triggered[0]["exit_price"] >= 51000
        assert triggered[0]["exit_price"] > 51100

    def test_short_take_profit(self, pos_mgr):
        _clean_db()
        mgr = PositionManager()
        mgr.open_position("BTC/USD", "SELL", 0.1, 50000, tp=49000)
        _age_open_positions()
        triggered = mgr.update_prices({"BTC/USD": {"price": 48900}})
        assert len(triggered) == 1
        assert triggered[0]["reason"] == "take_profit"
        # Overshoot to 48900 still fills AT the 49000 limit.
        assert triggered[0]["exit_price"] == 49000

    def test_no_exit_within_one_bar_of_entry(self, pos_mgr):
        """Audit fix: positions were closing +1.1% 19s after open. No exit
        (SL/TP/partial) may fill less than 1 bar after entry."""
        _clean_db()
        mgr = PositionManager()
        mgr.open_position("BTC/USD", "BUY", 0.1, 50000, sl=49000, tp=55000)
        # Both SL and TP "hit" instantly after open: neither may fill.
        assert mgr.update_prices({"BTC/USD": {"price": 55100}}) == []
        assert mgr.update_prices({"BTC/USD": {"price": 48900}}) == []
        assert len(mgr.get_open_positions()) == 1
        # Once a full bar has passed, the same prices fill normally.
        _age_open_positions()
        triggered = mgr.update_prices({"BTC/USD": {"price": 48900}})
        assert len(triggered) == 1
        assert triggered[0]["reason"] == "stop_loss"
        assert triggered[0]["exit_price"] <= 49000

    def test_get_recent_trades_empty(self, pos_mgr):
        _clean_db()
        mgr = PositionManager()
        assert mgr.get_recent_trades(10) == []

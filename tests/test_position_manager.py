import pytest

from core.database import init_db, execute
from core.positions import PositionManager


def _clean_db():
    init_db()
    execute("DELETE FROM positions")
    execute("DELETE FROM trades")


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
        triggered = mgr.update_prices({"BTC/USD": {"price": 48900}})
        assert len(triggered) == 1
        assert triggered[0]["reason"] == "stop_loss"

    def test_take_profit_hit(self, pos_mgr):
        _clean_db()
        mgr = PositionManager()
        mgr.open_position("BTC/USD", "BUY", 0.1, 50000, tp=55000)
        triggered = mgr.update_prices({"BTC/USD": {"price": 55100}})
        assert len(triggered) == 1
        assert triggered[0]["reason"] == "take_profit"

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
        triggered = mgr.update_prices({"BTC/USD": {"price": 51100}})
        assert len(triggered) == 1
        assert triggered[0]["reason"] == "stop_loss"

    def test_short_take_profit(self, pos_mgr):
        _clean_db()
        mgr = PositionManager()
        mgr.open_position("BTC/USD", "SELL", 0.1, 50000, tp=49000)
        triggered = mgr.update_prices({"BTC/USD": {"price": 48900}})
        assert len(triggered) == 1
        assert triggered[0]["reason"] == "take_profit"

    def test_get_recent_trades_empty(self, pos_mgr):
        _clean_db()
        mgr = PositionManager()
        assert mgr.get_recent_trades(10) == []

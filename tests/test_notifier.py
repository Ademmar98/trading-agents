from unittest.mock import patch, MagicMock

from core.notifier import Notifier


def test_init_disabled_without_token():
    assert Notifier(bot_token="", chat_id="123")._enabled is False

def test_init_disabled_without_chat():
    assert Notifier(bot_token="abc", chat_id="")._enabled is False

def test_init_enabled():
    assert Notifier(bot_token="abc", chat_id="123")._enabled is True


def test_send_disabled():
    Notifier().send("test")

def test_send_enabled():
    n = Notifier(bot_token="abc", chat_id="123")
    with patch("requests.post") as mock_post:
        n.send("hello")
        mock_post.assert_called_once()

def test_send_exception_swallowed():
    n = Notifier(bot_token="abc", chat_id="123")
    with patch("requests.post", side_effect=Exception("fail")):
        n.send("hello")


def test_on_trade_disabled():
    Notifier().on_trade({"symbol": "BTC/USD"})

def test_on_trade_enabled_filled():
    n = Notifier(bot_token="abc", chat_id="123")
    with patch.object(n, "send") as mock_send:
        n.on_trade({"symbol": "BTC/USD", "side": "BUY", "qty": 1, "price": 50000, "stop_loss": 49000, "take_profit": 55000, "status": "filled"})
        mock_send.assert_called_once()

def test_on_trade_enabled_not_filled():
    n = Notifier(bot_token="abc", chat_id="123")
    with patch.object(n, "send") as mock_send:
        n.on_trade({"symbol": "BTC/USD", "qty": 1, "status": "pending"})
        mock_send.assert_not_called()


def test_on_error_disabled():
    n = Notifier()
    n.on_error("fail")

def test_on_error_enabled():
    n = Notifier(bot_token="abc", chat_id="123")
    with patch.object(n, "send") as mock_send:
        n.on_error("fail")
        mock_send.assert_called_once()


def test_daily_summary_disabled():
    n = Notifier()
    n.daily_summary({"date": "2024-01-01", "day_pnl_pct": 1.0, "equity": 10000, "total_pnl_pct": 5.0, "trades_closed": 3, "win_rate": 66.7, "pnl_closed": 150, "open_positions": 2, "cash": 5000})

def test_daily_summary_enabled():
    n = Notifier(bot_token="abc", chat_id="123")
    with patch.object(n, "send") as mock_send:
        n.daily_summary({"date": "2024-01-01", "day_pnl_pct": -1.0, "equity": 10000, "total_pnl_pct": 5.0, "trades_closed": 3, "win_rate": 66.7, "pnl_closed": 150, "open_positions": 2, "cash": 5000})
        mock_send.assert_called_once()


def test_on_sl_tp_disabled():
    n = Notifier()
    n.on_sl_tp({"symbol": "BTC/USD", "reason": "TP", "pnl": 100, "pnl_pct": 5.0, "side": "BUY", "exit_price": 55000})

def test_on_sl_tp_enabled():
    n = Notifier(bot_token="abc", chat_id="123")
    with patch.object(n, "send") as mock_send:
        n.on_sl_tp({"symbol": "BTC/USD", "reason": "TP", "pnl": 100, "pnl_pct": 5.0, "side": "BUY", "exit_price": 55000})
        mock_send.assert_called_once()


def test_on_agent_action_disabled():
    n = Notifier()
    with patch.object(n, "send") as mock_send:
        n.on_agent_action("analyst", "signal")
        mock_send.assert_not_called()

def test_on_agent_action_quiet_drops_chatter():
    # Quiet mode (default): per-cycle agent chatter never reaches Telegram
    n = Notifier(bot_token="abc", chat_id="123")
    with patch.object(n, "send") as mock_send:
        n.on_agent_action("analyst", "signal")
        mock_send.assert_not_called()


def test_on_agent_action_halt_breaks_through_quiet():
    n = Notifier(bot_token="abc", chat_id="123")
    with patch.object(n, "send") as mock_send:
        n.on_agent_action("compliance", "HALTED — daily loss breaker")
        mock_send.assert_called_once()


def test_on_agent_action_verbose_mode():
    n = Notifier(bot_token="abc", chat_id="123")
    with patch("core.notifier.TELEGRAM_QUIET", False), patch.object(n, "send") as mock_send:
        n.on_agent_action("analyst", "signal")
        mock_send.assert_called_once()


def test_on_rejected_signal_alerts():
    n = Notifier(bot_token="abc", chat_id="123")
    with patch.object(n, "send") as mock_send:
        n.on_rejected_signal("BTC/USD", "SL 25.0% from entry (> 20% sanity bound)")
        mock_send.assert_called_once()
        assert "REJECTED BTC/USD" in mock_send.call_args[0][0]


def test_on_rejected_signal_disabled():
    n = Notifier()
    with patch.object(n, "send") as mock_send:
        n.on_rejected_signal("BTC/USD", "whatever")
        mock_send.assert_not_called()


def test_portfolio_snapshot_disabled():
    n = Notifier()
    with patch.object(n, "send") as mock_send:
        n.portfolio_snapshot({"total_pnl": 100, "total_value": 10000, "cash": 5000, "open_positions": []})
        mock_send.assert_not_called()

def test_portfolio_snapshot_quiet_suppressed():
    n = Notifier(bot_token="abc", chat_id="123")
    with patch.object(n, "send") as mock_send:
        n.portfolio_snapshot({"total_pnl": 100, "total_value": 10000, "cash": 5000, "open_positions": []})
        mock_send.assert_not_called()

def test_portfolio_snapshot_verbose_mode():
    n = Notifier(bot_token="abc", chat_id="123")
    with patch("core.notifier.TELEGRAM_QUIET", False), patch.object(n, "send") as mock_send:
        n.portfolio_snapshot({"total_pnl": 100, "total_value": 10000, "cash": 5000, "open_positions": []})
        mock_send.assert_called_once()

def test_portfolio_snapshot_with_positions():
    n = Notifier(bot_token="abc", chat_id="123")
    with patch("core.notifier.TELEGRAM_QUIET", False), patch.object(n, "send") as mock_send:
        n.portfolio_snapshot({
            "total_pnl": 100, "total_value": 10000, "cash": 5000,
            "open_positions": [{"symbol": "BTC/USD", "side": "LONG", "entry_price": 50000, "current_price": 51000}],
        })
        mock_send.assert_called_once()

def test_portfolio_snapshot_positions_key():
    n = Notifier(bot_token="abc", chat_id="123")
    with patch("core.notifier.TELEGRAM_QUIET", False), patch.object(n, "send") as mock_send:
        n.portfolio_snapshot({
            "total_pnl": 100, "total_value": 10000, "cash": 5000,
            "positions": [{"symbol": "ETH/USD", "side": "SHORT", "entry_price": 3000, "current_price": 2900}],
        })
        mock_send.assert_called_once()


class TestTelegramAuthorization:
    """The command bot must ignore strangers entirely — no data access, and
    critically no adoption of their chat_id for future notifications."""

    def _notifier(self):
        from core.notifier import Notifier
        return Notifier(bot_token="t", chat_id="5542937176")

    def test_stranger_message_ignored_and_chat_not_hijacked(self):
        n = self._notifier()
        calls = []
        n._handlers = {"/pnl": lambda m: calls.append("pnl")}
        n._handle_update({"message": {
            "chat": {"id": 999999}, "from": {"id": 999999}, "text": "/pnl"}})
        assert calls == []
        assert n.chat_id == "5542937176"  # destination unchanged

    def test_owner_message_handled(self):
        n = self._notifier()
        calls = []
        n._handlers = {"/pnl": lambda m: calls.append("pnl")}
        n._handle_update({"message": {
            "chat": {"id": 5542937176}, "from": {"id": 5542937176}, "text": "/pnl"}})
        assert calls == ["pnl"]

    def test_stranger_callback_ignored(self):
        n = self._notifier()
        calls = []
        n._handlers = {"/positions": lambda m: calls.append("pos")}
        n._handle_callback({"id": "cb1", "data": "/positions",
                            "from": {"id": 424242},
                            "message": {"chat": {"id": 424242}}})
        assert calls == []

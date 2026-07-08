from unittest.mock import patch, MagicMock

from core.equity import snapshot_equity, get_equity_history, day_start_equity, daily_loss_pct, build_daily_summary, pop_completed_day


def _mock_portfolio(equity=10000, cash=5000, positions_value=5000, exposure_pct=50.0, total_pnl_pct=5.0, positions=None):
    p = MagicMock()
    p.equity = equity
    p.cash = cash
    p.positions_value = positions_value
    p.exposure_pct = exposure_pct
    p.total_pnl_pct = total_pnl_pct
    p.positions = positions or []
    return p


class TestSnapshotEquity:
    def test_inserts_equity(self):
        with patch("core.equity.load_portfolio", return_value=_mock_portfolio()):
            with patch("core.equity.execute") as mock_exec:
                result = snapshot_equity()
                mock_exec.assert_called_once()
                args = mock_exec.call_args[0]
                assert "INSERT INTO equity_history" in args[0]
                assert result == 10000.0


class TestGetEquityHistory:
    def test_returns_reversed_rows(self):
        rows = [
            {"equity": 10000, "snapped_at": "2024-01-01T00:00:00"},
            {"equity": 10100, "snapped_at": "2024-01-02T00:00:00"},
        ]
        with patch("core.equity.fetchall", return_value=rows):
            result = get_equity_history(limit=10)
            assert len(result) == 2
            assert result[0]["equity"] == 10100

    def test_returns_empty_when_no_data(self):
        with patch("core.equity.fetchall", return_value=[]):
            result = get_equity_history(limit=10)
            assert result == []


class TestDayStartEquity:
    def test_returns_equity_when_found(self):
        with patch("core.equity.fetchone", return_value={"equity": 10000}):
            result = day_start_equity("2024-01-01")
            assert result == 10000

    def test_returns_none_when_not_found(self):
        with patch("core.equity.fetchone", return_value=None):
            result = day_start_equity("2024-01-01")
            assert result is None


class TestDailyLossPct:
    def test_returns_zero_when_no_start(self):
        p = _mock_portfolio()
        with patch("core.equity.day_start_equity", return_value=None), \
             patch("core.equity.load_portfolio", return_value=p):
            assert daily_loss_pct() == 0.0

    def test_computes_change(self):
        p = _mock_portfolio(equity=10500)
        with patch("core.equity.day_start_equity", return_value=10000), \
             patch("core.equity.load_portfolio", return_value=p):
            assert daily_loss_pct() == 5.0


class TestBuildDailySummary:
    def test_returns_summary(self):
        p = _mock_portfolio(equity=10500, total_pnl_pct=5.0, positions=[{"symbol": "BTC/USD"}])
        with patch("core.equity.load_portfolio", return_value=p), \
             patch("core.equity.fetchall", return_value=[{"pnl": 100}, {"pnl": -50}]), \
             patch("core.equity.day_start_equity", return_value=10000), \
             patch("core.equity.fetchone", return_value={"equity": 10500}):
            result = build_daily_summary("2024-01-01")
            assert result["date"] == "2024-01-01"
            assert result["equity"] == 10500.0
            assert result["trades_closed"] == 2
            assert result["win_rate"] == 50.0
            assert result["day_pnl_pct"] == 5.0

    def test_no_end_equity(self):
        p = _mock_portfolio(equity=10000)
        with patch("core.equity.load_portfolio", return_value=p), \
             patch("core.equity.fetchall", return_value=[]), \
             patch("core.equity.day_start_equity", return_value=10000), \
             patch("core.equity.fetchone", return_value=None):
            result = build_daily_summary("2024-01-01")
            assert result["day_pnl_pct"] == 0.0


class TestPopCompletedDay:
    def test_first_run_returns_none(self):
        with patch("core.equity._utc_today", return_value="2026-07-09"):
            with patch("core.equity.get_meta", return_value=None):
                with patch("core.equity.set_meta") as mock_set:
                    result = pop_completed_day()
                    assert result is None
                    mock_set.assert_called_once_with("last_daily_summary", "2026-07-09")

    def test_same_day_returns_none(self):
        with patch("core.equity._utc_today", return_value="2026-07-09"):
            with patch("core.equity.get_meta", return_value="2026-07-09"):
                with patch("core.equity.set_meta") as mock_set:
                    result = pop_completed_day()
                    assert result is None
                    mock_set.assert_not_called()

    def test_new_day_returns_previous(self):
        with patch("core.equity._utc_today", return_value="2026-07-10"):
            with patch("core.equity.get_meta", return_value="2026-07-09"):
                with patch("core.equity.set_meta") as mock_set:
                    result = pop_completed_day()
                    assert result == "2026-07-09"
                    mock_set.assert_called_once_with("last_daily_summary", "2026-07-10")

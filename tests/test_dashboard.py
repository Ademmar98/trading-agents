from unittest.mock import MagicMock, patch

from rich.console import Console
from rich.panel import Panel
from rich.layout import Layout

from core.dashboard import (
    make_positions_panel,
    make_status_panel,
    make_trades_panel,
    make_analytics_panel,
    make_backtest_panel,
    make_activity_panel,
    make_market_panel,
    make_opportunities_panel,
    make_risk_panel,
    make_layout,
)


def _render(renderable):
    # rich >= 14 only honors an explicit width when BOTH width and height are
    # given (Console.size falls back to 80x25 terminal detection otherwise),
    # which silently collapses table columns to "...".
    console = Console(width=200, height=100, force_terminal=True, color_system=None)
    with console.capture() as cap:
        console.print(renderable)
    return cap.get()


def _mock_pos_mgr(positions=None, trades=None):
    m = MagicMock()
    summary = {"count": len(positions) if positions else 0, "positions": positions or []}
    m.get_positions_summary.return_value = summary
    m.get_recent_trades.return_value = trades or []
    m.filter_new_signals.side_effect = lambda x: x
    return m


def _mock_portfolio():
    p = MagicMock()
    p.equity = 25000.0
    p.total_pnl = 5000.0
    p.total_pnl_pct = 25.0
    p.cash = 15000.0
    p.exposure_pct = 40.0
    return p


def _mock_memory(logs=None):
    m = MagicMock()
    m.get_recent_logs.return_value = logs or []
    m.read.return_value = None
    return m


class TestMakePositionsPanel:
    def test_empty(self):
        panel = make_positions_panel(_mock_pos_mgr())
        assert "No open positions" in _render(panel)

    def test_with_positions(self):
        pos_mgr = _mock_pos_mgr([{"symbol": "BTC/USD", "side": "BUY", "quantity": 0.5,
                                  "entry_price": 50000, "current_price": 51000,
                                  "stop_loss": 49000, "take_profit": 55000, "pnl": 500}])
        panel = make_positions_panel(pos_mgr)
        output = _render(panel)
        assert "BTC/USD" in output
        assert "BUY" in output


class TestMakeStatusPanel:
    def test_no_broker(self):
        panel = make_status_panel(_mock_portfolio(), None)
        output = _render(panel)
        assert "$25,000" in output
        assert "+25.00%" in output

    def test_with_connected_broker(self):
        broker = MagicMock()
        broker.connected = True
        broker.get_account_info.return_value = {"balance": 100000}
        panel = make_status_panel(_mock_portfolio(), broker)
        output = _render(panel)
        assert "$100,000" in output

    def test_broker_exception(self):
        broker = MagicMock()
        broker.connected = True
        broker.get_account_info.side_effect = Exception("fail")
        panel = make_status_panel(_mock_portfolio(), broker)
        output = _render(panel)
        assert "Paper Equity" in output


class TestMakeTradesPanel:
    def test_no_trades(self):
        panel = make_trades_panel(_mock_pos_mgr())
        assert "No trades yet" in _render(panel)

    def test_with_trades(self):
        trades = [{"symbol": "BTC/USD", "side": "BUY", "qty": 0.5,
                   "entry_price": 50000, "exit_price": 52000,
                   "pnl": 1000, "closed_at": "2025-01-01T12:00:00", "reason": "tp"}]
        panel = make_trades_panel(_mock_pos_mgr(trades=trades))
        output = _render(panel)
        assert "BTC/USD" in output
        assert "+1000" in output


class TestMakeAnalyticsPanel:
    def test_no_data(self):
        with patch("core.dashboard.get_analytics", return_value={"total_trades": 0}):
            panel = make_analytics_panel()
            assert "No trade data yet" in _render(panel)

    def test_with_data(self):
        analytics = {
            "total_trades": 50, "win_rate": 60.0, "total_pnl": 5000.0,
            "profit_factor": 1.8, "sharpe_ratio": "1.2", "max_drawdown_pct": 8.0,
            "expectancy": 100.0, "strategy_breakdown": [],
        }
        with patch("core.dashboard.get_analytics", return_value=analytics):
            panel = make_analytics_panel()
            output = _render(panel)
            assert "60%" in output
            assert "P&L" in output

    def test_with_strategy_breakdown(self):
        analytics = {
            "total_trades": 50, "win_rate": 60.0, "total_pnl": 5000.0,
            "profit_factor": 1.8, "sharpe_ratio": "1.2", "max_drawdown_pct": 8.0,
            "expectancy": 100.0,
            "strategy_breakdown": [{"strategy": "momentum", "trades": 30, "win_rate": 70, "pnl": 3000}],
        }
        with patch("core.dashboard.get_analytics", return_value=analytics):
            panel = make_analytics_panel()
            output = _render(panel)
            assert "momentum" in output


class TestMakeBacktestPanel:
    def test_no_results(self):
        with patch("core.dashboard.get_backtest_results", return_value=[]):
            panel = make_backtest_panel()
            assert "No backtest data" in _render(panel)

    def test_with_results(self):
        results = [{"symbol": "BTC/USD", "total_return": 12.5, "total_trades": 20,
                    "win_rate": 60.0, "profit_factor": 1.5, "sharpe_ratio": "1.1",
                    "max_drawdown": 8.0}]
        with patch("core.dashboard.get_backtest_results", return_value=results):
            panel = make_backtest_panel()
            output = _render(panel)
            assert "+12.5%" in output
            assert "BTC/USD" in output


class TestMakeActivityPanel:
    def test_no_logs(self):
        panel = make_activity_panel(_mock_memory())
        assert isinstance(panel, Panel)

    def test_with_logs(self):
        logs = [{"agent": "trader", "message": "executed BUY 0.1 BTC"},
                {"agent": "analyst", "message": "scanned markets"}]
        panel = make_activity_panel(_mock_memory(logs=logs))
        output = _render(panel)
        assert "trader" in output
        assert "analyst" in output


class TestMakeMarketPanel:
    def test_no_prices(self):
        with patch("core.dashboard.websocket_prices.get_all_prices", return_value={}):
            panel = make_market_panel()
            assert "Waiting for price data" in _render(panel)

    def test_with_prices(self):
        prices = {"BTC/USD": {"price": 50000, "change_24h": 2.5, "volume_24h": 1e9}}
        with patch("core.dashboard.websocket_prices.get_all_prices", return_value=prices):
            panel = make_market_panel()
            output = _render(panel)
            assert "$50,000" in output
            assert "+2.50%" in output


class TestMakeOpportunitiesPanel:
    def test_no_analysis(self):
        mem = _mock_memory()
        mem.read.return_value = None
        panel = make_opportunities_panel(mem, _mock_pos_mgr())
        assert "No data yet" in _render(panel)

    def test_no_opportunities(self):
        mem = _mock_memory()
        mem.read.return_value = {"opportunities": []}
        panel = make_opportunities_panel(mem, _mock_pos_mgr())
        assert "No opportunities found" in _render(panel)

    def test_with_opportunities(self):
        mem = _mock_memory()
        opps = [{"symbol": "ETH/USD", "action": "BUY", "price": 3000,
                 "confidence": 0.85, "reasons": ["trend"]}]
        mem.read.return_value = {"opportunities": opps}
        pos_mgr = _mock_pos_mgr()
        pos_mgr.filter_new_signals = MagicMock(return_value=opps)
        panel = make_opportunities_panel(mem, pos_mgr)
        assert "ETH/USD" in _render(panel)

    def test_all_filtered_out(self):
        mem = _mock_memory()
        opps = [{"symbol": "ETH/USD", "action": "BUY", "price": 3000,
                 "confidence": 0.85, "reasons": ["trend"]}]
        mem.read.return_value = {"opportunities": opps}
        pos_mgr = _mock_pos_mgr()
        pos_mgr.filter_new_signals = MagicMock(return_value=[])
        panel = make_opportunities_panel(mem, pos_mgr)
        assert "open positions" in _render(panel)


class TestMakeRiskPanel:
    def test_no_risk(self):
        mem = _mock_memory()
        mem.read.return_value = None
        panel = make_risk_panel(mem)
        assert "No risk data" in _render(panel)

    def test_with_risk(self):
        mem = _mock_memory()
        mem.read.return_value = {"verdict": "low", "exposure_pct": 25,
                                 "max_trade_size": 500, "risks": []}
        panel = make_risk_panel(mem)
        assert "low" in _render(panel).lower()

    def test_high_risk(self):
        mem = _mock_memory()
        mem.read.return_value = {"verdict": "high_risk", "exposure_pct": 80,
                                 "max_trade_size": 200, "risks": ["Overexposed"]}
        panel = make_risk_panel(mem)
        output = _render(panel)
        assert "high_risk" in output.lower()
        assert "Overexposed" in output


class TestMakeLayout:
    def test_returns_layout(self):
        pos_mgr = _mock_pos_mgr()
        portfolio = _mock_portfolio()
        mem = _mock_memory()
        with patch("core.dashboard.websocket_prices.get_all_prices", return_value={}):
            with patch("core.dashboard.get_analytics", return_value={"total_trades": 0}):
                with patch("core.dashboard.get_backtest_results", return_value=[]):
                    layout = make_layout(portfolio, pos_mgr, mem, None)
                    assert isinstance(layout, Layout)
                    assert layout["header"] is not None

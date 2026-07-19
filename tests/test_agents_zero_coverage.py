import time
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def mock_memory():
    mem = MagicMock()
    mem.read.return_value = None
    mem.get_recent_logs.return_value = []
    mem.get_recent_errors.return_value = []
    return mem


class TestOrchestrator:
    def test_run_basic(self, mock_memory):
        with patch("agents.orchestrator.load_portfolio") as mock_load:
            portfolio = MagicMock()
            portfolio.cash = 15000
            portfolio.equity = 25000
            portfolio.total_pnl_pct = 25.0
            portfolio.positions = []
            mock_load.return_value = portfolio

            from agents.orchestrator import Orchestrator
            agent = Orchestrator()
            agent.memory = mock_memory
            result = agent.run()

            assert result["portfolio"]["equity"] == 25000
            assert result["portfolio"]["cash"] == 15000

    def test_run_high_risk(self, mock_memory):
        with patch("agents.orchestrator.load_portfolio") as mock_load:
            portfolio = MagicMock()
            portfolio.cash = 5000
            portfolio.equity = 10000
            portfolio.total_pnl_pct = -10.0
            portfolio.positions = [MagicMock()]
            mock_load.return_value = portfolio

            mock_memory.read.side_effect = lambda prefix, key: {
                ("analyses", "market_scan"): {"summary": "ok"},
                ("decisions", "risk_assessment"): {"verdict": "high_risk"},
                ("reports", "audit"): {},
            }.get((prefix, key))

            from agents.orchestrator import Orchestrator
            agent = Orchestrator()
            agent.memory = mock_memory
            result = agent.run()

            assert result["risk_status"] == "high_risk"

    def test_run_writes_instructions(self, mock_memory):
        with patch("agents.orchestrator.load_portfolio") as mock_load:
            portfolio = MagicMock()
            portfolio.cash = 5000
            portfolio.equity = 10000
            portfolio.total_pnl_pct = -5.0
            portfolio.positions = []
            mock_load.return_value = portfolio

            from agents.orchestrator import Orchestrator
            agent = Orchestrator()
            agent.memory = mock_memory
            agent.run()

            write_calls = [c for c in mock_memory.write.call_args_list if c[0][0] == "decisions" and c[0][1] == "instructions"]
            assert len(write_calls) >= 1
            instructions = write_calls[-1][0][2]
            assert "instructions" in instructions


class TestHealthMonitor:
    def test_ok_status(self, mock_memory):
        mock_memory.read.return_value = {"timestamp": time.time(), "summary": "ok"}
        mock_memory.get_recent_logs.return_value = [
            {"agent": a, "message": "ok"}
            for a in ["orchestrator", "analyst", "sentiment", "regime", "risk_manager",
                       "portfolio_manager", "compliance", "execution", "trader", "auditor"]
        ]
        with patch("agents.health_monitor.websocket_prices.get_all_prices", return_value={"BTC/USD": {}}):
            with patch("agents.health_monitor.load_portfolio") as mock_load:
                portfolio = MagicMock()
                portfolio.cash = 10000
                portfolio.positions = ["BTC/USD"]
                mock_load.return_value = portfolio

                from agents.health_monitor import HealthMonitor
                agent = HealthMonitor()
                agent.memory = mock_memory
                result = agent.run()

                assert result["halted"] is False

    def test_stalled_scan_halts_but_slow_scan_only_warns(self, mock_memory):
        # Same scan timestamp as the previous health check + past the hard
        # ceiling = the scanner stopped producing -> halt.
        stalled_ts = time.time() - 3600
        reports = {
            ("analyses", "market_scan"): {"timestamp": stalled_ts},
            ("reports", "health"): {"last_scan_ts": stalled_ts},
        }
        mock_memory.read.side_effect = lambda cat, name: reports.get((cat, name), {})
        with patch("agents.health_monitor.websocket_prices.get_all_prices", return_value={}):
            with patch("agents.health_monitor.load_portfolio") as mock_load:
                portfolio = MagicMock()
                portfolio.cash = 10000
                portfolio.positions = ["BTC/USD"]
                mock_load.return_value = portfolio

                from agents.health_monitor import HealthMonitor
                agent = HealthMonitor()
                agent.memory = mock_memory
                result = agent.run()

                assert result["halted"] is True
                assert any("stalled" in i for i in result["issues"])

    def test_slow_but_advancing_scan_does_not_halt(self, mock_memory):
        # The scan is older than 2x interval but NEWER than the one the
        # previous health check saw — the scanner is making progress, the
        # cycle is just slower than the nominal interval. Warn, never halt
        # (halting on wall-clock age froze all entries for 38h in prod).
        scan_ts = time.time() - 150
        reports = {
            ("analyses", "market_scan"): {"timestamp": scan_ts},
            ("reports", "health"): {"last_scan_ts": scan_ts - 150},
        }
        mock_memory.read.side_effect = lambda cat, name: reports.get((cat, name), {})
        with patch("agents.health_monitor.websocket_prices.get_all_prices", return_value={"BTC/USD": {}}):
            with patch("agents.health_monitor.load_portfolio") as mock_load:
                portfolio = MagicMock()
                portfolio.cash = 10000
                portfolio.positions = ["BTC/USD"]
                mock_load.return_value = portfolio

                from agents.health_monitor import HealthMonitor
                agent = HealthMonitor()
                agent.memory = mock_memory
                result = agent.run()

                assert result["halted"] is False
                assert any("slow" in w for w in result["warnings"])
                assert result["last_scan_ts"] == scan_ts

    def test_many_errors_halts(self, mock_memory):
        mock_memory.read.return_value = {"timestamp": time.time(), "summary": "ok"}
        mock_memory.get_recent_errors.return_value = [
            {"time": time.time(), "source": "test", "message": "err", "traceback": ""}
            for _ in range(10)
        ]
        with patch("agents.health_monitor.websocket_prices.get_all_prices", return_value={"BTC/USD": {}}):
            with patch("agents.health_monitor.load_portfolio") as mock_load:
                portfolio = MagicMock()
                portfolio.cash = 10000
                portfolio.positions = ["BTC/USD"]
                mock_load.return_value = portfolio

                from agents.health_monitor import HealthMonitor
                agent = HealthMonitor()
                agent.memory = mock_memory
                result = agent.run()

                assert result["halted"] is True

    def test_missing_agents_warning(self, mock_memory):
        mock_memory.read.return_value = {"timestamp": time.time(), "summary": "ok"}
        mock_memory.get_recent_logs.return_value = [{"agent": "analyst", "message": "done"}]
        with patch("agents.health_monitor.websocket_prices.get_all_prices", return_value={"BTC/USD": {}}):
            with patch("agents.health_monitor.load_portfolio") as mock_load:
                portfolio = MagicMock()
                portfolio.cash = 10000
                portfolio.positions = ["BTC/USD"]
                mock_load.return_value = portfolio

                from agents.health_monitor import HealthMonitor
                agent = HealthMonitor()
                agent.memory = mock_memory
                result = agent.run()

                assert "warning" in result["status"] or result["warnings"]

    def test_no_cash_no_positions(self, mock_memory):
        mock_memory.read.return_value = {"timestamp": time.time(), "summary": "ok"}
        with patch("agents.health_monitor.websocket_prices.get_all_prices", return_value={}):
            with patch("agents.health_monitor.load_portfolio") as mock_load:
                portfolio = MagicMock()
                portfolio.cash = 0
                portfolio.positions = []
                mock_load.return_value = portfolio

                from agents.health_monitor import HealthMonitor
                agent = HealthMonitor()
                agent.memory = mock_memory
                result = agent.run()

                assert result["price_feed_alive"] is False


class TestResearchAnalyst:
    def test_no_prices(self, mock_memory):
        with patch("agents.analyst.MarketData") as MockMarket:
            market = MagicMock()
            market.fetch_prices.return_value = {}
            MockMarket.return_value = market

            from agents.analyst import ResearchAnalyst
            agent = ResearchAnalyst()
            agent.memory = mock_memory
            result = agent.run()

            assert result is None

    def test_with_prices(self, mock_memory):
        prices = {"BTC/USD": {"price": 50000, "change_24h": 2.5, "volume_24h": 1e9, "bid": 49990, "ask": 50010}}
        mock_memory.read.return_value = {}

        with patch("agents.analyst.MarketData") as MockMarket:
            market = MagicMock()
            market.fetch_prices.return_value = prices
            market.get_ohlc.return_value = [{"close": 50000}] * 100
            market.get_historical.return_value = []
            market.compute_indicators.return_value = {"trend": "up", "rsi_14": 55, "volatility": 2.0, "atr": 1000}
            MockMarket.return_value = market

            with patch("agents.analyst.analyze_symbol_multiframe", return_value=None):
                with patch("agents.analyst.analyze_symbol_mtf", return_value={"action": "HOLD", "confidence": 0, "mtf_details": {}}):
                    with patch("agents.analyst.scan_symbol", return_value=[
                        {"action": "BUY", "confidence": 0.8, "reasons": ["trend"], "strategies": ["FVG"],
                         "symbol": "BTC/USD"}
                    ]):
                            with patch("agents.analyst.get_unprofitable_strategies", return_value=[]):

                                from agents.analyst import ResearchAnalyst
                                agent = ResearchAnalyst()
                                agent.memory = mock_memory
                                result = agent.run()

                                assert result is not None
                                assert "BTC/USD" in result
                                assert result["BTC/USD"]["price"] == 50000

    def test_logs_summary(self, mock_memory):
        prices = {"BTC/USD": {"price": 50000, "change_24h": 2.5, "volume_24h": 1e9, "bid": 49990, "ask": 50010}}
        mock_memory.read.return_value = {}

        with patch("agents.analyst.MarketData") as MockMarket:
            market = MagicMock()
            market.fetch_prices.return_value = prices
            market.get_ohlc.return_value = [{"close": 50000}] * 100
            market.get_historical.return_value = []
            market.compute_indicators.return_value = {"trend": "up", "rsi_14": 55, "volatility": 2.0, "atr": 1000}
            MockMarket.return_value = market

            with patch("agents.analyst.analyze_symbol_multiframe", return_value=None):
                with patch("agents.analyst.analyze_symbol_mtf", return_value={"action": "HOLD", "confidence": 0, "mtf_details": {}}):
                        with patch("agents.analyst.scan_symbol", return_value=[]):
                            with patch("agents.analyst.get_unprofitable_strategies", return_value=[]):

                                from agents.analyst import ResearchAnalyst
                                agent = ResearchAnalyst()
                                agent.memory = mock_memory
                                agent.run()

                                logged = [c for c in mock_memory.log.call_args_list if "0 opportunities" in str(c)]
                                assert logged

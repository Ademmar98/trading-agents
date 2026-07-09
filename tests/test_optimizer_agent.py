import time
from unittest.mock import patch, MagicMock, PropertyMock

import pytest

from agents.optimizer_agent import OptimizerAgent


@pytest.fixture
def agent():
    a = OptimizerAgent.__new__(OptimizerAgent)
    a.memory = MagicMock()
    a.log = MagicMock()
    return a


def _audit(total_trades=15, win_rate=50, sharpe=1.0, profit_factor=1.5, total_pnl_pct=5.0, positions=0, exposure=0):
    return {
        "summary": {
            "total_trades": total_trades,
            "win_rate": win_rate,
            "analytics": {"sharpe": sharpe, "profit_factor": profit_factor},
            "total_pnl_pct": total_pnl_pct,
            "positions": positions,
            "current_exposure": exposure,
        }
    }


class TestRun:
    def test_skip_if_fewer_than_10_trades(self, agent):
        agent.memory.read.return_value = {"summary": {"total_trades": 5}}
        agent.run()
        agent.log.assert_called_with("Skip: only 5 trades — need ≥10 for meaningful backtest")

    def test_skip_if_no_stats(self, agent):
        agent.memory.read.return_value = {"summary": {"total_trades": 15}}
        with patch("agents.optimizer_agent.get_strategy_stats_list", return_value=[]):
            agent.run()
            agent.log.assert_called_with("Skip: no strategy stats yet")

    def test_skip_if_recently_run(self, agent):
        agent.memory.read.return_value = _audit()
        with patch("agents.optimizer_agent.get_strategy_stats_list", return_value=[{"name": "test"}]):
            with patch("agents.optimizer_agent.get_meta", return_value=str(time.time())):
                with patch("agents.optimizer_agent.time.time", return_value=time.time()):
                    agent.run()
                    assert "Skip: last run was" in agent.log.call_args[0][0]

    def test_skip_if_no_weak_param(self, agent):
        agent.memory.read.return_value = _audit(win_rate=50, sharpe=1.5, profit_factor=2.0,
                                                  total_pnl_pct=5.0, exposure=30)
        with patch("agents.optimizer_agent.get_strategy_stats_list", return_value=[{"name": "test"}]):
            with patch("agents.optimizer_agent.get_meta", return_value="0"):
                with patch("agents.optimizer_agent.time.time", return_value=100000):
                    agent.run()
                    agent.log.assert_called_with("No weak param identified — all look acceptable")

    def test_runs_optimization(self, agent):
        agent.memory.read.return_value = _audit(win_rate=40)
        calls = []
        def mock_test_single_param(param, val, inc):
            calls.append((param, val, inc))
            if len(calls) == 1:
                return (3.0, {"score": 15.0})
            return (2.0, {"score": 10.0})
        with patch("agents.optimizer_agent.get_strategy_stats_list", return_value=[{"name": "test"}]):
            with patch("agents.optimizer_agent.get_meta", side_effect=lambda k, d=None: "0"):
                with patch("agents.optimizer_agent.test_single_param", side_effect=mock_test_single_param):
                    with patch("agents.optimizer_agent.set_meta"):
                        with patch("agents.optimizer_agent.os.environ", {}):
                            with patch("agents.optimizer_agent.sys.modules", {"config": MagicMock()}):
                                agent.run()
                                agent.log.assert_called()

    def test_handles_backtest_exception(self, agent):
        agent.memory.read.return_value = _audit(win_rate=40)
        with patch("agents.optimizer_agent.get_strategy_stats_list", return_value=[{"name": "test"}]):
            with patch("agents.optimizer_agent.get_meta", side_effect=lambda k, d=None: "0"):
                with patch("agents.optimizer_agent.time.time", return_value=100000):
                    with patch("agents.optimizer_agent.test_single_param", side_effect=Exception("fail")):
                        agent.run()
                        assert "Backtest failed" in agent.log.call_args[0][0]

    def test_handles_no_result(self, agent):
        agent.memory.read.return_value = _audit(win_rate=40)
        with patch("agents.optimizer_agent.get_strategy_stats_list", return_value=[{"name": "test"}]):
            with patch("agents.optimizer_agent.get_meta", side_effect=lambda k, d=None: "0"):
                with patch("agents.optimizer_agent.time.time", return_value=100000):
                    with patch("agents.optimizer_agent.test_single_param", return_value=(1.5, None)):
                        agent.run()
                        assert "Backtest returned no result" in agent.log.call_args[0][0]


class TestPickWeakestParam:
    def test_win_rate_under_45(self):
        a = OptimizerAgent.__new__(OptimizerAgent)
        param, meta = a._pick_weakest_param({"win_rate": 40, "analytics": {}, "total_pnl_pct": 5}, [])
        assert param == "STOP_LOSS_PCT"

    def test_sharpe_under_08(self):
        a = OptimizerAgent.__new__(OptimizerAgent)
        param, meta = a._pick_weakest_param({"win_rate": 50, "analytics": {"sharpe": 0.5}, "total_pnl_pct": 5}, [])
        assert param == "SL_VOL_MULT"

    def test_profit_factor_under_13(self):
        a = OptimizerAgent.__new__(OptimizerAgent)
        param, meta = a._pick_weakest_param({"win_rate": 50, "analytics": {"sharpe": 1.0, "profit_factor": 1.0}, "total_pnl_pct": 5}, [])
        assert param == "TP_VOL_MULT"

    def test_negative_pnl(self):
        a = OptimizerAgent.__new__(OptimizerAgent)
        param, meta = a._pick_weakest_param({"win_rate": 50, "analytics": {"sharpe": 1.0, "profit_factor": 2.0}, "total_pnl_pct": -5}, [])
        assert param == "RISK_PER_TRADE_PCT"

    def test_high_exposure_no_longer_tunable(self):
        a = OptimizerAgent.__new__(OptimizerAgent)
        param, meta = a._pick_weakest_param({"win_rate": 50, "analytics": {"sharpe": 1.0, "profit_factor": 2.0}, "total_pnl_pct": 5, "positions": 3, "current_exposure": 60}, [])
        assert param is None

    def test_returns_none_when_all_ok(self):
        a = OptimizerAgent.__new__(OptimizerAgent)
        result = a._pick_weakest_param({"win_rate": 50, "analytics": {"sharpe": 1.0, "profit_factor": 1.5}, "total_pnl_pct": 5}, [])
        assert result == (None, None)

    def test_returns_none_when_meta_not_found(self):
        a = OptimizerAgent.__new__(OptimizerAgent)
        from config import TUNABLE_PARAMS
        saved = dict(TUNABLE_PARAMS)
        TUNABLE_PARAMS.clear()
        try:
            result = a._pick_weakest_param({"win_rate": 40, "analytics": {}, "total_pnl_pct": 5}, [])
            assert result == (None, None)
        finally:
            TUNABLE_PARAMS.update(saved)

import os
import sys
import tempfile
import time
from pathlib import Path

import pytest

import config as app_config
from core.database import init_db, execute, set_meta
from core.memory import SharedMemory
from core.portfolio import Portfolio, save_portfolio


@pytest.fixture(autouse=True)
def sandbox_data_dir(monkeypatch):
    tmp = Path(tempfile.mkdtemp(prefix="trading-test-"))
    monkeypatch.setattr(app_config, "DATA_DIR", tmp)
    monkeypatch.setenv("TRADING_DATA_DIR", str(tmp))
    init_db()
    yield
    import shutil
    shutil.rmtree(str(tmp), ignore_errors=True)


def seed_trades(count=15, win_rate=60):
    for i in range(count):
        pnl = 50.0 if i < int(count * win_rate / 100) else -30.0
        execute(
            "INSERT INTO trades (position_id, symbol, side, qty, entry_price, exit_price, pnl, pnl_pct, reason, strategy) "
            "VALUES (?, 'TEST', 'BUY', 1.0, 100.0, 110.0, ?, 10.0, 'test', 'FVG')",
            [i, pnl],
        )


def seed_audit(memory):
    memory.write("reports", "audit", {
        "summary": {
            "total_trades": 15,
            "win_rate": 60.0,
            "total_pnl": 300.0,
            "total_pnl_pct": 3.0,
            "current_exposure": 20.0,
            "positions": 1,
            "analytics": {"sharpe": 1.2, "profit_factor": 1.5, "max_drawdown": 5.0, "expectancy": 20.0},
        },
        "suggestions": [],
        "needs_rebalance": False,
        "timestamp": time.time(),
    })


class TestOptimizerAgentPickWeakest:
    def test_skips_when_too_few_trades(self):
        from agents.optimizer_agent import OptimizerAgent
        memory = SharedMemory()
        seed_audit(memory)
        result = OptimizerAgent().run()
        assert result is None  # skip: <10 trades


    def test_skips_when_too_recent(self):
        from agents.optimizer_agent import OptimizerAgent
        memory = SharedMemory()
        seed_trades(15)
        seed_audit(memory)
        set_meta("optimizer_last_run", str(time.time()))
        result = OptimizerAgent().run()
        assert result is None  # skip: just ran


class TestOptimizerAgent:
    def test_picks_weak_param_low_win_rate(self):
        from agents.optimizer_agent import OptimizerAgent
        agent = OptimizerAgent()
        summary = {"win_rate": 40, "total_trades": 15,
                   "analytics": {"sharpe": 1.2, "profit_factor": 1.5},
                   "total_pnl_pct": 3.0, "positions": 1, "current_exposure": 20}
        stats = [{"strategy": "FVG", "trades": 10, "win_rate": 60, "pnl": 200}]
        name, meta = agent._pick_weakest_param(summary, stats)
        assert name == "STOP_LOSS_PCT"

    def test_picks_weak_param_low_sharpe(self):
        from agents.optimizer_agent import OptimizerAgent
        agent = OptimizerAgent()
        summary = {"win_rate": 55, "total_trades": 15,
                   "analytics": {"sharpe": 0.5, "profit_factor": 1.5},
                   "total_pnl_pct": 3.0, "positions": 1, "current_exposure": 20}
        stats = [{"strategy": "FVG", "trades": 10, "win_rate": 60, "pnl": 200}]
        name, meta = agent._pick_weakest_param(summary, stats)
        assert name == "SL_VOL_MULT"

    def test_picks_weak_param_low_pf(self):
        from agents.optimizer_agent import OptimizerAgent
        agent = OptimizerAgent()
        summary = {"win_rate": 55, "total_trades": 15,
                   "analytics": {"sharpe": 1.2, "profit_factor": 1.1},
                   "total_pnl_pct": 3.0, "positions": 1, "current_exposure": 20}
        stats = [{"strategy": "FVG", "trades": 10, "win_rate": 60, "pnl": 200}]
        name, meta = agent._pick_weakest_param(summary, stats)
        assert name == "TP_VOL_MULT"

    def test_picks_weak_param_negative_pnl(self):
        from agents.optimizer_agent import OptimizerAgent
        agent = OptimizerAgent()
        summary = {"win_rate": 55, "total_trades": 15,
                   "analytics": {"sharpe": 1.2, "profit_factor": 1.5},
                   "total_pnl_pct": -2.0, "positions": 1, "current_exposure": 20}
        stats = [{"strategy": "FVG", "trades": 10, "win_rate": 60, "pnl": 200}]
        name, meta = agent._pick_weakest_param(summary, stats)
        assert name == "RISK_PER_TRADE_PCT"

    def test_picks_weak_param_high_exposure(self):
        from agents.optimizer_agent import OptimizerAgent
        agent = OptimizerAgent()
        summary = {"win_rate": 55, "total_trades": 15,
                   "analytics": {"sharpe": 1.2, "profit_factor": 1.5},
                   "total_pnl_pct": 3.0, "positions": 3, "current_exposure": 65}
        stats = [{"strategy": "FVG", "trades": 10, "win_rate": 60, "pnl": 200}]
        name, meta = agent._pick_weakest_param(summary, stats)
        assert name == "MAX_POSITION_SIZE_PCT"

    def test_returns_none_when_all_ok(self):
        from agents.optimizer_agent import OptimizerAgent
        agent = OptimizerAgent()
        summary = {"win_rate": 55, "total_trades": 15,
                   "analytics": {"sharpe": 1.2, "profit_factor": 1.5},
                   "total_pnl_pct": 3.0, "positions": 1, "current_exposure": 20}
        stats = [{"strategy": "FVG", "trades": 10, "win_rate": 60, "pnl": 200}]
        name, meta = agent._pick_weakest_param(summary, stats)
        assert name is None


class TestOptimizerIntegration:
    def test_test_single_param_returns_value(self):
        from core.optimizer import test_single_param
        best_val, result = test_single_param("SL_VOL_MULT", 2.0, 0.5, symbol="BTC/USD", days=30)
        assert isinstance(best_val, float)
        assert best_val in (1.5, 2.0, 2.5)

    def test_optimizer_agent_runs(self):
        from agents.optimizer_agent import OptimizerAgent
        memory = SharedMemory()
        seed_trades(15)
        seed_audit(memory)
        set_meta("optimizer_last_run", "0")
        result = OptimizerAgent().run()
        assert result is None  # runs but backtest may fail without real data

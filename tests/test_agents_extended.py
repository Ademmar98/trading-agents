import os
import tempfile
import time
from pathlib import Path

import pytest

import config as app_config
from core.database import init_db, execute, fetchall
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


def seed_market_scan(memory):
    all_analyses = {
        "BTC/USD": {"price": 60000.0, "bid": 59999.0, "ask": 60001.0,
                     "change_24h": 1.2, "volume_24h": 5e8, "volatility": 2.0},
        "ETH/USD": {"price": 1800.0, "bid": 1799.9, "ask": 1800.1,
                     "change_24h": 0.8, "volume_24h": 3e8, "volatility": 2.5},
    }
    opportunities = [
        {"symbol": "BTC/USD", "action": "BUY", "confidence": 0.9, "price": 60000.0,
         "reasons": ["test signal"], "strategies": ["test"],
         "indicators": {"trend": "up", "rsi": 55, "volatility": 2.0}},
        {"symbol": "ETH/USD", "action": "SELL", "confidence": 0.9, "price": 1800.0,
         "reasons": ["test signal"], "strategies": ["test"],
         "indicators": {"trend": "down", "rsi": 45, "volatility": 2.5}},
    ]
    memory.write("analyses", "market_scan", {
        "summary": "canned", "opportunities": opportunities,
        "all_analyses": all_analyses, "timestamp": time.time(),
    })


def seed_regime_scan(memory):
    memory.write("analyses", "regime_scan", {
        "symbols": {
            "BTC/USD": {"regime": "trending_up", "favored_action": "BUY",
                        "confidence_multiplier": 1.10, "size_multiplier": 1.00},
            "ETH/USD": {"regime": "ranging", "size_multiplier": 0.70},
        },
        "summary": {"trending_up": 1, "ranging": 1},
        "timestamp": time.time(),
    })


def test_sentiment_agent_basic():
    from agents.sentiment_agent import SentimentAgent
    memory = SharedMemory()
    save_portfolio(Portfolio(cash=10000.0, initial_balance=10000.0))
    seed_market_scan(memory)
    result = SentimentAgent().run()
    assert "market_mood" in result
    assert "symbols" in result
    assert "BTC/USD" in result["symbols"]
    assert "fear_greed" in result


def test_sentiment_agent_empty():
    from agents.sentiment_agent import SentimentAgent
    memory = SharedMemory()
    save_portfolio(Portfolio(cash=10000.0, initial_balance=10000.0))
    result = SentimentAgent().run()
    assert result.get("market_mood") is not None


def test_regime_agent():
    from agents.regime_agent import RegimeAgent
    result = RegimeAgent().run()
    assert "symbols" in result
    assert "summary" in result


def test_execution_agent_rejects_when_halted():
    from agents.execution_agent import ExecutionAgent
    memory = SharedMemory()
    memory.write("decisions", "compliance_gate", {
        "halted": True, "approved_opportunities": [], "timestamp": time.time(),
    })
    result = ExecutionAgent().run()
    assert result["status"] == "halted"
    assert result["orders"] == []


def test_execution_agent_no_approved():
    from agents.execution_agent import ExecutionAgent
    memory = SharedMemory()
    memory.write("decisions", "compliance_gate", {
        "halted": False, "approved_opportunities": [], "timestamp": time.time(),
    })
    result = ExecutionAgent().run()
    assert result["status"] == "ready"
    assert result["orders"] == []


def test_compliance_agent_no_opportunities():
    from agents.compliance_agent import ComplianceAgent
    memory = SharedMemory()
    memory.write("decisions", "portfolio_decision", {"allocations": [], "timestamp": time.time()})
    result = ComplianceAgent().run()
    assert result["halted"] is False


def test_auditor_no_orders():
    from agents.auditor import Auditor
    result = Auditor().run()
    assert "timestamp" in result


def test_auditor_with_trades():
    from agents.auditor import Auditor
    memory = SharedMemory()
    seed_market_scan(memory)
    memory.write("orders", "execution_plan", {
        "status": "ready", "orders": [{"symbol": "BTC/USD", "action": "BUY", "qty": 0.1, "price": 60000.0, "execution_ok": True, "plan_id": "test"}],
        "timestamp": time.time(),
    })
    result = Auditor().run()
    assert "summary" in result
    assert "suggestions" in result


def test_auditor_credits_each_contributor_of_combined_signal():
    """Pipe-joined strategy tags ("a|b") on a trade must be split so every
    contributing strategy accumulates its own strategy_stats row — this feeds
    the unprofitable-strategy exclusion loop."""
    from agents.auditor import Auditor
    save_portfolio(Portfolio(cash=10000.0, initial_balance=10000.0))
    execute("INSERT INTO trades (position_id, symbol, side, qty, entry_price, exit_price, pnl, pnl_pct, reason, strategy) "
            "VALUES (1, 'BTC/USD', 'BUY', 0.1, 100.0, 110.0, 9.0, 9.0, 'take_profit', 'mom_burst|fvg_entry')")
    execute("INSERT INTO trades (position_id, symbol, side, qty, entry_price, exit_price, pnl, pnl_pct, reason, strategy) "
            "VALUES (2, 'ETH/USD', 'BUY', 1.0, 100.0, 90.0, -11.0, -11.0, 'stop_loss', 'mom_burst')")
    Auditor().run()
    stats = {r["strategy"]: dict(r)
             for r in fetchall("SELECT strategy, trades, win_rate, pnl FROM strategy_stats")}
    assert stats["mom_burst"]["trades"] == 2
    assert stats["mom_burst"]["pnl"] == pytest.approx(-2.0)
    assert stats["mom_burst"]["win_rate"] == pytest.approx(50.0)
    assert stats["fvg_entry"]["trades"] == 1
    assert stats["fvg_entry"]["win_rate"] == pytest.approx(100.0)


def test_pipeline_downstream():
    from agents.sentiment_agent import SentimentAgent
    from agents.risk_manager import RiskManager
    from agents.position_sizer import PositionSizer
    from agents.portfolio_manager import PortfolioManagerAgent
    from agents.compliance_agent import ComplianceAgent
    from agents.execution_agent import ExecutionAgent
    memory = SharedMemory()
    save_portfolio(Portfolio(cash=10000.0, initial_balance=10000.0))
    seed_market_scan(memory)
    seed_regime_scan(memory)

    SentimentAgent().run()
    result = RiskManager().run()
    assert "verdict" in result

    PositionSizer().run()
    PortfolioManagerAgent().run()

    gate = ComplianceAgent().run()
    assert gate["halted"] is False
    btc = [o for o in gate["approved_opportunities"] if o["symbol"] == "BTC/USD"]
    assert len(btc) > 0

    plan = ExecutionAgent().run()
    assert plan["status"] == "ready"
    assert len(plan["orders"]) >= 1


def test_strategy_selector():
    from core.strategies import strategies_for_regime
    all_names = {n for n, _ in strategies_for_regime(None)}
    trend = {n for n, _ in strategies_for_regime("trending_up")}
    range_s = {n for n, _ in strategies_for_regime("ranging")}
    volatile = {n for n, _ in strategies_for_regime("volatile")}
    unknown = {n for n, _ in strategies_for_regime("unknown")}

    assert trend.issubset(all_names)
    assert range_s.issubset(all_names)
    assert volatile.issubset(all_names)
    assert unknown == all_names
    assert len(trend) < len(all_names)
    assert len(range_s) < len(all_names)
    assert len(volatile) < len(all_names)

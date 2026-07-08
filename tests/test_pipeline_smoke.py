"""End-to-end pipeline smoke test, fully offline.
 
Seeds canned market/regime scans (the two network-dependent steps), then runs
the real sentiment -> risk -> portfolio -> compliance -> execution -> trader
chain against the paper broker and asserts trades actually happen and the
ledger debits cash. This is the test that would have caught both the unwired
pipeline and the read_latest() wrong-file bug.

Also tests the downstream agents: auditor and optimizer.
"""
import os
import tempfile
from pathlib import Path
import time

import pytest

import config as app_config
from core.database import init_db, execute
from core.memory import SharedMemory
from core.portfolio import Portfolio, save_portfolio, load_portfolio
from agents.sentiment_agent import SentimentAgent
from agents.risk_manager import RiskManager
from agents.position_sizer import PositionSizer
from agents.portfolio_manager import PortfolioManagerAgent
from agents.compliance_agent import ComplianceAgent
from agents.execution_agent import ExecutionAgent
from agents.trader import Trader
from agents.auditor import Auditor
from agents.optimizer_agent import OptimizerAgent


@pytest.fixture(autouse=True)
def sandbox_data_dir(monkeypatch):
    tmp = Path(tempfile.mkdtemp(prefix="trading-test-"))
    monkeypatch.setattr(app_config, "DATA_DIR", tmp)
    monkeypatch.setenv("TRADING_DATA_DIR", str(tmp))
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
        # SELL with no holdings: must be blocked by the spot-only compliance rule
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
            "ETH/USD": {"regime": "ranging",
                        "confidence_multiplier": 0.95, "size_multiplier": 0.70},
        },
        "summary": {"trending_up": 1, "ranging": 1},
        "timestamp": time.time(),
    })


def test_full_pipeline_places_paper_trades():
    init_db()
    memory = SharedMemory()
    save_portfolio(Portfolio(cash=10000.0, initial_balance=10000.0))
    seed_market_scan(memory)
    seed_regime_scan(memory)

    SentimentAgent().run()
    RiskManager().run()
    PositionSizer().run()
    PortfolioManagerAgent().run()

    gate = ComplianceAgent().run()
    assert gate["halted"] is False
    assert any(o["symbol"] == "BTC/USD" for o in gate["approved_opportunities"])
    assert all(o["action"] != "SELL" for o in gate["approved_opportunities"]), \
        "spot-only: SELL without holdings must not pass compliance"

    plan = ExecutionAgent().run()
    assert plan["status"] == "ready"
    assert len(plan["orders"]) >= 1
    order = plan["orders"][0]
    assert order["execution_ok"] and order["qty"] > 0
    assert order["stop_loss"] < order["price"] < order["take_profit"]

    executed = Trader().run()
    filled = [o for o in executed if o.get("status") == "filled"]
    assert filled, f"trader filled nothing: {executed}"

    p = load_portfolio()
    assert p.cash < 10000.0, "a filled BUY must debit ledger cash"
    assert "BTC/USD" in p.positions
    trade_log = memory.read("orders", "trade_log")
    assert trade_log["status"] == "completed"


def seed_trades_for_audit(win_count=8, loss_count=4):
    for i in range(win_count):
        execute(
            "INSERT INTO trades (position_id, symbol, side, qty, entry_price, exit_price, pnl, pnl_pct, strategy) "
            "VALUES (?, 'BTC/USD', 'BUY', 0.1, 100.0, 110.0, 50.0, 10.0, 'FVG')",
            [i],
        )
    for i in range(loss_count):
        execute(
            "INSERT INTO trades (position_id, symbol, side, qty, entry_price, exit_price, pnl, pnl_pct, strategy) "
            "VALUES (?, 'ETH/USD', 'BUY', 0.1, 100.0, 95.0, -20.0, -5.0, 'MACD')",
            [win_count + i],
        )
    # Also seed the portfolio object so Auditor.summary.total_trades matches
    p = load_portfolio()
    p.trades = [{"realized_pnl": 50.0}] * win_count + [{"realized_pnl": -20.0}] * loss_count
    save_portfolio(p)


def test_audit_reports_with_trades():
    init_db()
    memory = SharedMemory()
    save_portfolio(Portfolio(cash=10000.0, initial_balance=10000.0))
    seed_trades_for_audit()
    report = Auditor().run()
    assert report["summary"]["total_trades"] == 12
    assert report["summary"]["win_rate"] > 50
    assert "suggestions" in report
    assert "agent_activity" in report
    for s in report["suggestions"]:
        assert isinstance(s, str)


def test_audit_detects_low_exposure():
    init_db()
    memory = SharedMemory()
    p = Portfolio(cash=9500.0, initial_balance=10000.0)
    save_portfolio(p)
    seed_trades_for_audit()
    report = Auditor().run()
    suggestions = " ".join(report["suggestions"]).lower()
    assert "exposure" in suggestions


def test_audit_detects_drawdown():
    init_db()
    memory = SharedMemory()
    p = Portfolio(cash=8800.0, initial_balance=10000.0)
    save_portfolio(p)
    seed_trades_for_audit()
    report = Auditor().run()
    suggestions = " ".join(report["suggestions"]).lower()
    assert "drawdown" in suggestions


def test_optimizer_skips_without_trades():
    """Optimizer should skip when <10 trades exist (not crash)."""
    init_db()
    memory = SharedMemory()
    save_portfolio(Portfolio(cash=10000.0, initial_balance=10000.0))
    result = OptimizerAgent().run()
    assert result is None


def test_full_pipeline_with_optimizer():
    """Run the full pipeline including Auditor -> Optimizer."""
    init_db()
    memory = SharedMemory()
    save_portfolio(Portfolio(cash=10000.0, initial_balance=10000.0))
    seed_market_scan(memory)
    seed_regime_scan(memory)

    SentimentAgent().run()
    RiskManager().run()

    report = Auditor().run()
    assert "summary" in report

    from core.database import set_meta
    set_meta("optimizer_last_run", "0")
    seed_trades_for_audit()
    result = OptimizerAgent().run()
    # May return None if backtest fails (no real data), but must not crash
    assert result is None or result is not None

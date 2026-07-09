"""Tests for the fee-awareness and trade-frequency profitability improvements.

The paper broker and position ledger must charge TRADE_FEE_PCT per side so
paper results match what the backtester (and a real exchange) would produce;
the execution agent must reject take-profits that can't clear round-trip
costs; and the compliance agent must cap entries per UTC day.
"""
import tempfile
import time
from pathlib import Path

import pytest

import config as app_config
from core.database import init_db, execute, get_unprofitable_strategies
from core.memory import SharedMemory
from core.portfolio import Portfolio, save_portfolio, load_portfolio

FEE = app_config.TRADE_FEE_PCT / 100.0


@pytest.fixture(autouse=True)
def sandbox_data_dir(monkeypatch):
    tmp = Path(tempfile.mkdtemp(prefix="trading-test-"))
    monkeypatch.setattr(app_config, "DATA_DIR", tmp)
    monkeypatch.setenv("TRADING_DATA_DIR", str(tmp))
    init_db()
    yield
    import shutil
    shutil.rmtree(str(tmp), ignore_errors=True)


def test_paper_broker_charges_fees_both_sides():
    from core.broker import PaperBroker

    save_portfolio(Portfolio(cash=10000.0, initial_balance=10000.0))
    broker = PaperBroker()

    buy = broker.place_order("BTC/USD", "BUY", 0.1, 50000.0)
    assert buy["status"] == "filled"
    assert buy["fee"] == pytest.approx(5000.0 * FEE)
    assert broker.portfolio.cash == pytest.approx(10000.0 - 5000.0 * (1 + FEE))

    sell = broker.place_order("BTC/USD", "SELL", 0.1, 50000.0)
    assert sell["fee"] == pytest.approx(5000.0 * FEE)
    # Flat-price round trip must cost exactly the two fees
    assert broker.portfolio.cash == pytest.approx(10000.0 - 2 * 5000.0 * FEE)
    assert sell["realized_pnl"] == pytest.approx(-5000.0 * FEE, abs=0.01)


def test_position_pnl_is_net_of_fees():
    """A gross win smaller than round-trip fees must book as a loss."""
    from core.positions import PositionManager

    pos_mgr = PositionManager()
    pos_id = pos_mgr.open_position("BTC/USD", "BUY", 1.0, 100.0, sl=95.0, tp=110.0)
    result = pos_mgr.close_position(pos_id, 100.1, reason="take_profit")
    gross = 0.1
    fees = (100.0 + 100.1) * FEE
    assert result["pnl"] == pytest.approx(round(gross - fees, 2))
    assert result["pnl"] < 0


def _seed_execution(memory, tp_pct, take_profit):
    memory.write("analyses", "market_scan", {
        "all_analyses": {"BTC/USD": {"price": 50000.0, "bid": 49999.0,
                                     "ask": 50001.0, "volatility": 2.0}},
        "timestamp": time.time(),
    })
    memory.write("decisions", "pricing", {"pricing_map": {"BTC/USD": {
        "symbol": "BTC/USD", "action": "BUY", "entry_price": 50000.0,
        "stop_loss": 49500.0, "take_profit": take_profit,
        "sl_pct": 1.0, "tp_pct": tp_pct, "calculated_risk_pct": 1.0,
    }}, "timestamp": time.time()})
    memory.write("decisions", "compliance_gate", {
        "halted": False,
        "approved_opportunities": [{
            "symbol": "BTC/USD", "action": "BUY", "confidence": 0.9,
            "price": 50000.0, "max_qty": 0.01, "reasons": [], "strategies": ["test"],
            "indicators": {"volatility": 2.0},
        }],
        "timestamp": time.time(),
    })


def test_execution_rejects_tp_below_cost_floor():
    from agents.execution_agent import ExecutionAgent

    memory = SharedMemory()
    save_portfolio(Portfolio(cash=10000.0, initial_balance=10000.0))
    # 0.3% TP cannot clear 0.2% fees + spread with margin
    _seed_execution(memory, tp_pct=0.3, take_profit=50150.0)

    plan = ExecutionAgent().run()
    assert plan["orders"] == []
    assert len(plan["rejected"]) == 1
    assert "TP too small" in plan["rejected"][0]["execution_reasons"][0]


def test_execution_accepts_tp_above_cost_floor():
    from agents.execution_agent import ExecutionAgent

    memory = SharedMemory()
    save_portfolio(Portfolio(cash=10000.0, initial_balance=10000.0))
    _seed_execution(memory, tp_pct=2.0, take_profit=51000.0)

    plan = ExecutionAgent().run()
    assert len(plan["orders"]) == 1


def test_daily_trade_cap_blocks_new_entries(monkeypatch):
    import agents.compliance_agent as ca
    from core.positions import PositionManager

    monkeypatch.setattr(ca, "MAX_TRADES_PER_DAY", 2)
    memory = SharedMemory()
    save_portfolio(Portfolio(cash=10000.0, initial_balance=10000.0))

    pos_mgr = PositionManager()
    pos_mgr.open_position("BTC/USD", "BUY", 0.01, 50000.0)
    pos_mgr.open_position("ETH/USD", "BUY", 0.1, 1800.0)

    memory.write("decisions", "portfolio_plan", {
        "approved_opportunities": [{
            "symbol": "SOL/USD", "action": "BUY", "confidence": 0.9,
            "price": 150.0, "max_qty": 1.0, "risk_ok": True,
            "reasons": [], "strategies": ["test"],
        }],
        "timestamp": time.time(),
    })

    report = ca.ComplianceAgent().run()
    assert report["approved_opportunities"] == []
    assert any("Daily trade cap" in w for w in report["warnings"])


def test_hourly_pacing_cap_blocks_burst(monkeypatch):
    """The hourly cap keeps the daily budget from being spent in one burst."""
    import agents.compliance_agent as ca
    from core.positions import PositionManager

    monkeypatch.setattr(ca, "MAX_TRADES_PER_HOUR", 1)
    memory = SharedMemory()
    save_portfolio(Portfolio(cash=10000.0, initial_balance=10000.0))
    PositionManager().open_position("BTC/USD", "BUY", 0.01, 50000.0)

    memory.write("decisions", "portfolio_plan", {
        "approved_opportunities": [{
            "symbol": "SOL/USD", "action": "BUY", "confidence": 0.9,
            "price": 150.0, "max_qty": 1.0, "risk_ok": True,
            "reasons": [], "strategies": ["test"],
        }],
        "timestamp": time.time(),
    })

    report = ca.ComplianceAgent().run()
    assert report["approved_opportunities"] == []
    assert any("Hourly pacing" in w for w in report["warnings"])


def test_daily_trade_cap_allows_entries_under_cap():
    from agents.compliance_agent import ComplianceAgent

    memory = SharedMemory()
    save_portfolio(Portfolio(cash=10000.0, initial_balance=10000.0))
    memory.write("decisions", "portfolio_plan", {
        "approved_opportunities": [{
            "symbol": "SOL/USD", "action": "BUY", "confidence": 0.9,
            "price": 150.0, "max_qty": 1.0, "risk_ok": True,
            "reasons": [], "strategies": ["test"],
        }],
        "timestamp": time.time(),
    })

    report = ComplianceAgent().run()
    assert len(report["approved_opportunities"]) == 1


def test_position_open_risk_math():
    from agents.compliance_agent import _position_open_risk
    # Breakeven'd runner (stop past entry) carries zero risk
    assert _position_open_risk({"side": "BUY", "entry_price": 100.0, "stop_loss": 100.3, "quantity": 1.0}) == 0.0
    assert _position_open_risk({"side": "BUY", "entry_price": 100.0, "stop_loss": 95.0, "quantity": 2.0}) == 10.0
    assert _position_open_risk({"side": "SELL", "entry_price": 100.0, "stop_loss": 105.0, "quantity": 1.0}) == 5.0


def test_portfolio_heat_cap_blocks_entries(monkeypatch):
    import agents.compliance_agent as ca
    from core.positions import PositionManager

    monkeypatch.setattr(ca, "MAX_OPEN_RISK_PCT", 2.0)
    memory = SharedMemory()
    save_portfolio(Portfolio(cash=10000.0, initial_balance=10000.0))
    # Open risk = (50000-45000) * 0.05 = $250 = 2.5% of $10k equity -> over the 2% cap
    PositionManager().open_position("BTC/USD", "BUY", 0.05, 50000.0, sl=45000.0)
    memory.write("decisions", "portfolio_plan", {
        "approved_opportunities": [{
            "symbol": "SOL/USD", "action": "BUY", "confidence": 0.9,
            "price": 150.0, "max_qty": 1.0, "risk_ok": True,
            "reasons": [], "strategies": ["test"],
        }],
        "timestamp": time.time(),
    })

    report = ca.ComplianceAgent().run()
    assert report["approved_opportunities"] == []
    assert any("Portfolio heat" in w for w in report["warnings"])


def test_cluster_cap_blocks_same_cluster(monkeypatch):
    import agents.compliance_agent as ca
    from core.positions import PositionManager

    monkeypatch.setattr(ca, "MAX_POSITIONS_PER_CLUSTER", 1)
    memory = SharedMemory()
    save_portfolio(Portfolio(cash=10000.0, initial_balance=10000.0))
    PositionManager().open_position("BTC/USD", "BUY", 0.01, 50000.0)  # no SL -> zero heat
    memory.write("decisions", "portfolio_plan", {
        "approved_opportunities": [{
            "symbol": "SOL/USD", "action": "BUY", "confidence": 0.9,
            "price": 150.0, "max_qty": 1.0, "risk_ok": True,
            "reasons": [], "strategies": ["test"],
        }],
        "timestamp": time.time(),
    })

    report = ca.ComplianceAgent().run()
    assert report["approved_opportunities"] == []
    rejected = report["rejected_opportunities"][0]
    assert any("Cluster 'crypto'" in r for r in rejected["compliance_reasons"])


def test_unprofitable_strategies_include_negative_pnl():
    """A losing strategy with a decent win rate must still be excluded."""
    execute("INSERT INTO strategy_stats (strategy, trades, win_rate, pnl) VALUES ('BadRR', 10, 55.0, -120.0)")
    execute("INSERT INTO strategy_stats (strategy, trades, win_rate, pnl) VALUES ('Good', 10, 55.0, 200.0)")
    bad = get_unprofitable_strategies()
    assert "BadRR" in bad
    assert "Good" not in bad

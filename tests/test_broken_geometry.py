"""Broken-geometry guard: corrupt SL/TP must never reach the broker.
For a BUY — TP at/below entry, SL at/above entry, or an SL farther than
BROKEN_SL_PCT (20%) from entry is data corruption, not a trade.
"""
import tempfile
import time
from pathlib import Path

import pytest

import config as app_config
from core.database import init_db
from core.memory import SharedMemory
from core.portfolio import Portfolio, save_portfolio


@pytest.fixture(autouse=True)
def sandbox(monkeypatch):
    tmp = Path(tempfile.mkdtemp(prefix="trading-test-"))
    monkeypatch.setattr(app_config, "DATA_DIR", tmp)
    monkeypatch.setenv("TRADING_DATA_DIR", str(tmp))
    init_db()
    yield
    import shutil
    shutil.rmtree(str(tmp), ignore_errors=True)


def _seed(memory, entry=100.0, sl=99.0, tp=101.5):
    memory.write("analyses", "market_scan", {
        "all_analyses": {"BTC/USD": {"price": entry, "bid": entry - 0.01,
                                     "ask": entry + 0.01, "volatility": 1.0}},
        "timestamp": time.time(),
    })
    opp = {
        "symbol": "BTC/USD", "action": "BUY", "confidence": 0.7,
        "price": entry, "entry_price": entry, "stop_loss": sl,
        "take_profit": tp,
        "sl_pct": abs(entry - sl) / entry * 100,
        "tp_pct": abs(tp - entry) / entry * 100,
        "calculated_risk_pct": 1.0,
        "max_qty": 5.0, "reasons": [], "strategies": ["test"],
        "indicators": {"volatility": 1.0},
    }
    memory.write("decisions", "pricing", {"pricing_map": {"BTC/USD": opp},
                                          "timestamp": time.time()})
    memory.write("decisions", "compliance_gate", {
        "halted": False, "approved_opportunities": [opp], "timestamp": time.time(),
    })


def _run():
    from agents.execution_agent import ExecutionAgent
    return ExecutionAgent().run()


def _rejected_reason(plan):
    return " ".join(plan["rejected"][0]["execution_reasons"])


class TestBrokenGeometryGuard:
    def test_tp_below_entry_rejected(self):
        save_portfolio(Portfolio(cash=10000.0, initial_balance=10000.0))
        _seed(SharedMemory(), entry=100.0, sl=98.0, tp=95.0)
        plan = _run()
        assert plan["orders"] == []
        assert "Broken geometry" in _rejected_reason(plan)
        assert "TP" in _rejected_reason(plan)

    def test_sl_above_entry_rejected(self):
        save_portfolio(Portfolio(cash=10000.0, initial_balance=10000.0))
        _seed(SharedMemory(), entry=100.0, sl=103.0, tp=106.0)
        plan = _run()
        assert plan["orders"] == []
        assert "Broken geometry" in _rejected_reason(plan)
        assert "SL" in _rejected_reason(plan)

    def test_sl_wider_than_20pct_rejected(self):
        save_portfolio(Portfolio(cash=10000.0, initial_balance=10000.0))
        _seed(SharedMemory(), entry=100.0, sl=75.0, tp=106.0)  # 25% stop
        plan = _run()
        assert plan["orders"] == []
        assert "sanity bound" in _rejected_reason(plan)

    def test_sane_scalp_geometry_passes(self):
        save_portfolio(Portfolio(cash=10000.0, initial_balance=10000.0))
        _seed(SharedMemory(), entry=100.0, sl=99.0, tp=101.5)  # 1% SL, 1.5% TP
        plan = _run()
        assert len(plan["orders"]) == 1
        assert plan["orders"][0]["stop_loss"] == pytest.approx(99.0)
        assert plan["orders"][0]["take_profit"] == pytest.approx(101.5)

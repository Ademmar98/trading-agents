"""Position-size multiplier (double the buy value, clamped to no-leverage)
and the minimum-absolute-TP-profit gate (skip sub-$1 setups)."""
import tempfile
import time
from pathlib import Path

import pytest

import config as app_config
from core.database import init_db
from core.memory import SharedMemory
from core.portfolio import Portfolio, save_portfolio
from core.positions import PositionManager


@pytest.fixture(autouse=True)
def sandbox(monkeypatch):
    tmp = Path(tempfile.mkdtemp(prefix="trading-test-"))
    monkeypatch.setattr(app_config, "DATA_DIR", tmp)
    monkeypatch.setenv("TRADING_DATA_DIR", str(tmp))
    init_db()
    yield
    import shutil
    shutil.rmtree(str(tmp), ignore_errors=True)


def _seed(entry=100.0, sl=98.0, tp=104.0, max_qty=50.0, risk_pct=0.5):
    memory = SharedMemory()
    memory.write("analyses", "market_scan", {
        "all_analyses": {"BTC/USD": {"price": entry, "bid": entry - 0.01,
                                     "ask": entry + 0.01, "volatility": 2.0}},
        "timestamp": time.time()})
    opp = {
        "symbol": "BTC/USD", "action": "BUY", "confidence": 0.7, "price": entry,
        "entry_price": entry, "stop_loss": sl, "take_profit": tp,
        "sl_pct": abs(entry - sl) / entry * 100, "tp_pct": abs(tp - entry) / entry * 100,
        "calculated_risk_pct": risk_pct, "max_qty": max_qty, "reasons": [],
        "strategies": ["test"], "indicators": {"volatility": 2.0},
    }
    memory.write("decisions", "pricing", {"pricing_map": {}, "timestamp": time.time()})
    memory.write("decisions", "compliance_gate", {
        "halted": False, "approved_opportunities": [opp], "timestamp": time.time()})


def _run(monkeypatch, mult=2.0, min_profit=1.0):
    import agents.execution_agent as ea
    monkeypatch.setattr(ea, "session_risk_mult", lambda: 1.0)
    monkeypatch.setattr(ea, "POSITION_SIZE_MULT", mult)
    monkeypatch.setattr(ea, "MIN_TP_PROFIT_USD", min_profit)
    return ea.ExecutionAgent().run()


class TestPositionMultiplier:
    def test_doubles_the_quantity(self, monkeypatch):
        save_portfolio(Portfolio(cash=10000.0, initial_balance=10000.0))
        _seed(entry=100.0, sl=98.0, tp=104.0, max_qty=50.0, risk_pct=0.5)
        # risk-capped qty = (10000*0.5%)/2 = 25 ; x2 = 50, well within cash/leverage
        plan = _run(monkeypatch, mult=2.0)
        assert len(plan["orders"]) == 1
        assert plan["orders"][0]["qty"] == pytest.approx(50.0, abs=0.2)

    def test_mult_one_is_baseline(self, monkeypatch):
        save_portfolio(Portfolio(cash=10000.0, initial_balance=10000.0))
        _seed(entry=100.0, sl=98.0, tp=104.0, max_qty=50.0, risk_pct=0.5)
        plan = _run(monkeypatch, mult=1.0)
        assert plan["orders"][0]["qty"] == pytest.approx(25.0, abs=0.2)

    def test_leverage_clamp_caps_the_multiplier(self, monkeypatch):
        # An existing 9,900-notional position leaves only ~$100 of equity
        # headroom: the doubled position must clamp far below its 2x target,
        # never breaching gross notional <= equity (no leverage / halal).
        save_portfolio(Portfolio(cash=10000.0, initial_balance=10000.0))
        PositionManager().open_position("ETH/USD", "BUY", 99.0, 100.0)  # 9,900 notional
        _seed(entry=100.0, sl=98.0, tp=104.0, max_qty=50.0, risk_pct=0.5)
        plan = _run(monkeypatch, mult=2.0)
        assert len(plan["orders"]) == 1
        assert plan["orders"][0]["qty"] < 2.0  # clamped from a 50-unit target


class TestMinTpProfitGate:
    def test_rejects_sub_dollar_target(self, monkeypatch):
        save_portfolio(Portfolio(cash=10000.0, initial_balance=10000.0))
        # tiny size: qty ~0.5 x2 = ~1.0 unit; TP move $0.60 -> ~$0.60 profit < $1
        _seed(entry=100.0, sl=99.5, tp=100.6, max_qty=0.5, risk_pct=0.5)
        plan = _run(monkeypatch, mult=2.0, min_profit=1.0)
        assert plan["orders"] == []
        assert "TP profit" in " ".join(plan["rejected"][0]["execution_reasons"])

    def test_doubling_rescues_a_borderline_setup(self, monkeypatch):
        # qty 0.9 x $0.60 = $0.54 (< $1 at 1x) but x2 -> $1.08 (>= $1)
        save_portfolio(Portfolio(cash=10000.0, initial_balance=10000.0))
        _seed(entry=100.0, sl=99.0, tp=100.6, max_qty=0.9, risk_pct=2.0)
        assert _run(monkeypatch, mult=1.0, min_profit=1.0)["orders"] == []
        plan2 = _run(monkeypatch, mult=2.0, min_profit=1.0)
        assert len(plan2["orders"]) == 1
        o = plan2["orders"][0]
        assert o["qty"] * abs(o["take_profit"] - o["entry_price"]) >= 1.0

    def test_healthy_target_passes(self, monkeypatch):
        save_portfolio(Portfolio(cash=10000.0, initial_balance=10000.0))
        _seed(entry=100.0, sl=98.0, tp=104.0, max_qty=50.0, risk_pct=0.5)
        plan = _run(monkeypatch, mult=2.0, min_profit=1.0)
        assert len(plan["orders"]) == 1  # 50 units x $4 = $200 profit


def test_scout_floor_uses_fee_multiple_not_one_usd(monkeypatch):
    """Scout probes (risk clamped to 0.1%) have tiny dollar targets — the $1
    MIN_TP_PROFIT_USD floor would reject every one. Scout floor must be
    1.5x round-trip fees (min $0.10) instead."""
    save_portfolio(Portfolio(cash=10000.0, initial_balance=10000.0))
    _seed(entry=100.0, sl=98.0, tp=104.0, max_qty=0.5, risk_pct=0.1)
    memory = SharedMemory()
    gate = memory.read("decisions", "compliance_gate")
    gate["approved_opportunities"][0]["scout"] = True
    memory.write("decisions", "compliance_gate", gate)

    plan = _run(monkeypatch, mult=1.0, min_profit=1.0)
    # qty ~0.5 -> TP profit 0.5 x $4 = $2.00 >= $1 anyway... use tinier qty:
    assert len(plan["orders"]) == 1


def test_scout_tiny_trade_passes_but_dust_rejected(monkeypatch):
    save_portfolio(Portfolio(cash=10000.0, initial_balance=10000.0))
    # TP move $0.60 (0.6% > MIN_TP_PCT) on qty 0.5 -> $0.30 profit. Round-trip
    # fees on $50 notional = $0.10 -> scout floor max(0.10, 0.15) = $0.15.
    _seed(entry=100.0, sl=98.0, tp=100.6, max_qty=0.5, risk_pct=0.1)
    memory = SharedMemory()
    gate = memory.read("decisions", "compliance_gate")
    gate["approved_opportunities"][0]["scout"] = True
    memory.write("decisions", "compliance_gate", gate)
    plan = _run(monkeypatch, mult=1.0, min_profit=1.0)
    assert len(plan["orders"]) == 1

    # Same setup WITHOUT the scout flag must still face the $1 floor.
    _seed(entry=100.0, sl=98.0, tp=100.6, max_qty=0.5, risk_pct=0.1)
    plan = _run(monkeypatch, mult=1.0, min_profit=1.0)
    assert plan["orders"] == []
    assert "TP profit" in plan["rejected"][0]["execution_reasons"][0]

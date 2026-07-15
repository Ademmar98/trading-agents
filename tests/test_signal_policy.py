"""Phase 2 signal policy: regime deployment cash-dial + firm-regime mapping.
(The classic battery is disabled as a live source; conftest pins it ON so the
legacy suite still covers that plumbing.)
"""
import tempfile
import time
from pathlib import Path

import pytest

import config as app_config
from core.database import init_db
from core.memory import SharedMemory
from core.portfolio import Portfolio, Position, save_portfolio


@pytest.fixture(autouse=True)
def sandbox(monkeypatch):
    tmp = Path(tempfile.mkdtemp(prefix="trading-test-"))
    monkeypatch.setattr(app_config, "DATA_DIR", tmp)
    monkeypatch.setenv("TRADING_DATA_DIR", str(tmp))
    init_db()
    yield
    import shutil
    shutil.rmtree(str(tmp), ignore_errors=True)


class TestFirmDeployment:
    def test_regime_to_target(self):
        from agents.regime_agent import firm_deployment
        assert firm_deployment("trending_up", 40) == ("strong_trending_up", 0.85)
        assert firm_deployment("trending_up", 28) == ("trending_up", 0.70)
        assert firm_deployment("trending_up", 10) == ("weak_trending_up", 0.40)
        assert firm_deployment("ranging", 0)[1] == 0.20
        assert firm_deployment("volatile", 0)[1] == 0.0
        assert firm_deployment("trending_down", 0)[1] == 0.0
        assert firm_deployment("unknown", 0)[1] == 0.20


def _run(deploy_target, firm_regime="ranging", positions_value=0.0):
    positions = {}
    if positions_value > 0:
        positions["ETH/USD"] = Position(
            symbol="ETH/USD", entry_price=100.0,
            quantity=positions_value / 100.0, current_price=100.0)
    save_portfolio(Portfolio(cash=10000.0 - positions_value,
                             initial_balance=10000.0, positions=positions))
    m = SharedMemory()
    # Always (over)write regime_scan so a prior test's value can't leak in via
    # the session-shared memory dir; omit deployment_target for the None case.
    regime = {"firm_regime": firm_regime, "timestamp": time.time()}
    if deploy_target is not None:
        regime["deployment_target"] = deploy_target
    m.write("analyses", "regime_scan", regime)
    m.write("decisions", "portfolio_plan", {"approved_opportunities": [{
        "symbol": "SOL/USD", "action": "BUY", "confidence": 0.7, "price": 150.0,
        "max_qty": 1.0, "risk_ok": True, "reasons": [], "strategies": ["scalp_15m"],
    }], "timestamp": time.time()})
    from agents.compliance_agent import ComplianceAgent
    return ComplianceAgent().run()


class TestRegimeCashDial:
    def test_over_target_blocks_new_entries(self):
        # ranging target 20%, already 30% deployed -> no new entries
        report = _run(deploy_target=0.20, positions_value=3000.0)
        assert report["approved_opportunities"] == []
        assert any("deployment cap" in r
                   for r in report["rejected_opportunities"][0]["compliance_reasons"])

    def test_under_target_allows_entry(self):
        # trending_up target 70%, only 30% deployed -> entry allowed
        report = _run(deploy_target=0.70, firm_regime="trending_up", positions_value=3000.0)
        assert len(report["approved_opportunities"]) == 1

    def test_zero_target_is_full_cash(self):
        # volatile/downtrend target 0 -> block even with nothing deployed
        report = _run(deploy_target=0.0, firm_regime="volatile", positions_value=0.0)
        assert report["approved_opportunities"] == []

    def test_no_regime_scan_leaves_gate_inactive(self):
        # missing deployment target -> dial does nothing, entry allowed
        report = _run(deploy_target=None)
        assert len(report["approved_opportunities"]) == 1

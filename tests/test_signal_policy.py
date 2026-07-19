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
    """The SMA200 dial — the only mechanism with evidence behind it
    (analysis/edge_hunt.py, 6.6y incl. the 2022 bear)."""

    def test_above_sma200_deploys(self):
        from agents.regime_agent import firm_deployment
        closes = [100.0] * 199 + [120.0]        # SMA200 = 100.1, price 120
        assert firm_deployment(closes) == ("risk_on", 0.85)

    def test_below_sma200_is_full_cash(self):
        from agents.regime_agent import firm_deployment
        closes = [100.0] * 199 + [80.0]         # SMA200 = 99.9, price 80
        assert firm_deployment(closes) == ("risk_off", 0.0)

    def test_insufficient_history_is_minimal_not_permissive(self):
        from agents.regime_agent import firm_deployment
        regime, target = firm_deployment([100.0] * 50)
        assert regime == "unknown"
        assert target == 0.20

    def test_no_data_does_not_deploy_freely(self):
        from agents.regime_agent import firm_deployment
        assert firm_deployment([])[1] == 0.20
        assert firm_deployment(None)[1] == 0.20


class TestBollingerWidthBug:
    """AUDIT.md sec. 4: _bb divided by min(n,1)==1, making avg_width a SUM ~20x
    too large, so detect_regime's volatile trigger could never fire."""

    def test_avg_width_is_a_mean_not_a_sum(self):
        from core.regime import _bb
        closes = [100 + (i % 7) for i in range(120)]
        width, avg_width = _bb(closes, 20)
        assert avg_width < width * 5, "avg_width still looks like a sum"
        assert avg_width > 0


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

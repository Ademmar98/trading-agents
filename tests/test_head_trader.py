"""Tests for the Hermes-powered HeadTrader review agent.

The agent is advisory-only: it writes a memo plus per-strategy confidence
multipliers clamped to [0.8, 1.1], consumed (and re-clamped) by the
PortfolioManager, and it must degrade to a no-op on any API failure.
"""
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import config as app_config
from core.database import init_db, set_meta
from core.memory import SharedMemory
from core.portfolio import Portfolio, save_portfolio

MEMO = (
    "Winners are being clipped early; scalping bleeds after fees.\n"
    "Hold size steady elsewhere.\n"
    '{"scalping_mtf": 0.85, "fvg": 1.9, "junk": "not-a-number"}'
)


@pytest.fixture(autouse=True)
def sandbox_data_dir(monkeypatch):
    tmp = Path(tempfile.mkdtemp(prefix="trading-test-"))
    monkeypatch.setattr(app_config, "DATA_DIR", tmp)
    monkeypatch.setenv("TRADING_DATA_DIR", str(tmp))
    init_db()
    yield
    import shutil
    shutil.rmtree(str(tmp), ignore_errors=True)


def _agent(monkeypatch, key="test-key"):
    import agents.head_trader as ht
    monkeypatch.setattr(ht, "HERMES_API_KEY", key)
    return ht, ht.HeadTrader()


def test_noop_without_key(monkeypatch):
    ht, agent = _agent(monkeypatch, key="")
    assert agent.run() is None


def test_writes_memo_clamps_confidence_and_throttles(monkeypatch):
    ht, agent = _agent(monkeypatch)
    resp = MagicMock()
    resp.json.return_value = {"choices": [{"message": {"content": MEMO}}]}
    monkeypatch.setattr(ht.requests, "post", MagicMock(return_value=resp))
    save_portfolio(Portfolio(cash=10000.0, initial_balance=10000.0))

    report = agent.run()
    assert report is not None
    conf = report["strategy_confidence"]
    assert conf["scalping_mtf"] == 0.85
    assert conf["fvg"] == 1.1          # clamped down from 1.9
    assert "junk" not in conf          # non-numeric dropped
    stored = agent.memory.read("reports", "head_trader")
    assert stored["memo"].startswith("Winners")
    # Second run inside the interval must no-op
    assert agent.run() is None


def test_falls_back_to_free_model(monkeypatch):
    ht, agent = _agent(monkeypatch)
    responses = [
        {"status": 404, "message": "requires available credits"},
        {"choices": [{"message": {"content": "brief memo\n{}"}}]},
    ]

    def fake_post(*args, **kwargs):
        m = MagicMock()
        m.json.return_value = responses.pop(0)
        return m

    monkeypatch.setattr(ht.requests, "post", fake_post)
    save_portfolio(Portfolio(cash=10000.0, initial_balance=10000.0))
    set_meta("head_trader_last_run", "0")

    report = agent.run()
    assert report is not None
    assert report["model"] == ht.HERMES_FALLBACK_MODEL


def test_api_failure_degrades_to_noop(monkeypatch):
    ht, agent = _agent(monkeypatch)
    monkeypatch.setattr(ht.requests, "post", MagicMock(side_effect=OSError("network down")))
    save_portfolio(Portfolio(cash=10000.0, initial_balance=10000.0))
    assert agent.run() is None  # logged, no crash, no report


def test_extract_confidence_variants():
    from agents.head_trader import HeadTrader
    f = HeadTrader._extract_confidence
    assert f('text before {"a": 0.9}') == {"a": 0.9}
    assert f("no json at all") == {}
    assert f('{"a": 2.5}')["a"] == 1.1
    assert f('{"a": 0.1}')["a"] == 0.8
    assert f("") == {}


def _seed_pm_inputs(memory, strategy, ht_timestamp):
    # Overwrite shared-memory keys other test files may have left behind —
    # sentiment/regime multipliers would skew the confidence math here.
    memory.write("analyses", "sentiment_scan", {"symbols": {}, "timestamp": time.time()})
    memory.write("analyses", "regime_scan", {"symbols": {}, "timestamp": time.time()})
    memory.write("reports", "head_trader", {
        "memo": "x", "strategy_confidence": {strategy: 0.8},
        "timestamp": ht_timestamp,
    })
    memory.write("decisions", "position_sizing", {
        "sized_opportunities": [{
            "symbol": "BTC/USD", "action": "BUY", "confidence": 0.9,
            "max_qty": 0.1, "risk_ok": True, "reasons": [],
            "strategies": [strategy],
        }],
        "timestamp": time.time(),
    })


def test_portfolio_manager_applies_fresh_head_confidence():
    from agents.portfolio_manager import PortfolioManagerAgent

    memory = SharedMemory()
    save_portfolio(Portfolio(cash=10000.0, initial_balance=10000.0))
    _seed_pm_inputs(memory, "ht_strat", ht_timestamp=time.time())

    report = PortfolioManagerAgent().run()
    item = report["approved_opportunities"][0]
    assert any("Head trader ht_strat" in r for r in item["reasons"])
    assert item["confidence"] == pytest.approx(0.9 * 0.8, abs=0.01)


def test_portfolio_manager_ignores_stale_memo():
    from agents.portfolio_manager import PortfolioManagerAgent

    memory = SharedMemory()
    save_portfolio(Portfolio(cash=10000.0, initial_balance=10000.0))
    _seed_pm_inputs(memory, "ht_stale_strat", ht_timestamp=time.time() - 7 * 3600)

    report = PortfolioManagerAgent().run()
    item = report["approved_opportunities"][0]
    assert not any("Head trader" in r for r in item["reasons"])

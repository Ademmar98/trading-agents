"""Tests for the adversarial DebateAgent (bull -> bear -> arbiter).

The agent debates the top-N portfolio candidates each cycle but its power is
strictly bounded: it can never create a trade, never raise confidence, and
never widen size. LLM trouble falls back to a deterministic judging engine;
only if that engine itself breaks does the plan fail open unchanged.
"""
import json
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import config as app_config
from core.database import init_db
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


def _agent(monkeypatch, key="test-key"):
    import agents.debate_agent as da
    monkeypatch.setattr(da, "HERMES_API_KEY", key)
    return da, da.DebateAgent()


def _mock_llm(monkeypatch, da, arbiter_for):
    """Mock the Hermes chat call. Bull/bear get canned arguments; the arbiter
    reply comes from `arbiter_for` — a plain string used for every candidate,
    or a callable(user_msg) -> string for per-symbol verdicts."""
    calls = {"n": 0}

    def fake_post(url, headers=None, json=None, timeout=None):
        calls["n"] += 1
        system = json["messages"][0]["content"]
        user = json["messages"][1]["content"]
        if "ARBITER" in system:
            content = arbiter_for(user) if callable(arbiter_for) else arbiter_for
        elif "BEAR" in system:
            content = "Bear: 0.2% round-trip fees eat a third of R; 2-trade sample is noise."
        else:
            content = "Bull: regime aligned, R:R 2.0, strategy net-positive over 30 trades."
        resp = MagicMock()
        resp.json.return_value = {"choices": [{"message": {"content": content}}]}
        return resp

    monkeypatch.setattr(da.requests, "post", fake_post)
    return calls


def _opp(symbol="BTC/USD", confidence=0.9, risk_ok=True):
    return {
        "symbol": symbol, "action": "BUY", "confidence": confidence,
        "price": 60000.0, "max_qty": 0.1, "risk_ok": risk_ok,
        "reasons": ["test signal"], "strategies": ["test_strat"],
        "portfolio_notes": [], "entry_price": 60000.0,
        "stop_loss": 59000.0, "take_profit": 62000.0,
    }


def _seed_plan(memory, opps):
    memory.write("decisions", "portfolio_plan", {
        "approved_opportunities": opps,
        "portfolio_exposure_pct": 0.0, "cash": 10000.0, "positions": 0,
        "notes": [], "timestamp": time.time(),
    })


# (a) REJECT removes the candidate from the plan
def test_reject_removes_candidate(monkeypatch):
    da, agent = _agent(monkeypatch)

    def arbiter(user):
        if "BTC/USD" in user:
            return '{"verdict": "REJECT", "rationale": "fees eat the entire edge"}'
        return '{"verdict": "APPROVE", "rationale": "bull case dominates"}'

    _mock_llm(monkeypatch, da, arbiter)
    memory = SharedMemory()
    _seed_plan(memory, [_opp("BTC/USD", 0.9), _opp("ETH/USD", 0.8)])

    plan = agent.run()
    symbols = [o["symbol"] for o in plan["approved_opportunities"]]
    assert "BTC/USD" not in symbols
    assert "ETH/USD" in symbols

    stored = memory.read("decisions", "portfolio_plan")
    assert [o["symbol"] for o in stored["approved_opportunities"]] == symbols

    report = memory.read("reports", "debate")
    assert report["verdicts"]["REJECT"] == 1
    assert report["verdicts"]["APPROVE"] == 1
    rec = report["debates"][0]
    assert rec["symbol"] == "BTC/USD" and rec["verdict"] == "REJECT"
    # The full arguments must be recorded for the owner to read.
    assert "Bull:" in rec["bull_argument"]
    assert "Bear:" in rec["bear_argument"]
    assert "fees" in rec["arbiter_rationale"]

    # And appended to the JSONL audit log.
    log_file = memory.dirs["logs"] / "debate_log.jsonl"
    lines = [json.loads(l) for l in log_file.read_text().splitlines() if l.strip()]
    debated = [l for l in lines if l["symbol"] == "BTC/USD"]
    assert debated and debated[-1]["verdict"] == "REJECT"
    assert debated[-1]["bull_argument"] and debated[-1]["bear_argument"]


# (b) DOWNGRADE multiplies confidence and downstream gates see the lowered value
def test_downgrade_lowers_confidence_and_gates_see_it(monkeypatch):
    da, agent = _agent(monkeypatch)
    _mock_llm(monkeypatch, da, '{"verdict": "DOWNGRADE", "rationale": "mixed evidence, trim conviction"}')
    memory = SharedMemory()
    save_portfolio(Portfolio(cash=10000.0, initial_balance=10000.0))
    _seed_plan(memory, [_opp("BTC/USD", 0.9)])
    # Neutral scans: nothing else may move confidence or halt the gate.
    memory.write("analyses", "sentiment_scan", {"symbols": {}, "timestamp": time.time()})
    memory.write("analyses", "regime_scan", {"symbols": {}, "summary": {}, "timestamp": time.time()})
    memory.write("analyses", "market_scan", {"all_analyses": {}, "bellwether_moves": {}, "timestamp": time.time()})
    memory.write("reports", "health", {"halted": False, "issues": [], "timestamp": time.time()})
    memory.write("decisions", "risk_assessment", {"verdict": "low", "risks": [], "timestamp": time.time()})

    plan = agent.run()
    new_conf = plan["approved_opportunities"][0]["confidence"]
    assert new_conf == pytest.approx(round(0.9 * 0.85, 4))
    assert new_conf < 0.9

    # The downstream compliance gate reads the SAME memory key — it must see
    # the debated-down confidence, not the original.
    from agents.compliance_agent import ComplianceAgent
    gate = ComplianceAgent().run()
    assert gate["halted"] is False
    assert len(gate["approved_opportunities"]) == 1
    assert gate["approved_opportunities"][0]["confidence"] == pytest.approx(new_conf)


# (c) LLM exception -> deterministic engine takes over (no blind fail-open)
def test_llm_failure_falls_back_to_deterministic_engine(monkeypatch):
    da, agent = _agent(monkeypatch)
    monkeypatch.setattr(da.requests, "post",
                        MagicMock(side_effect=OSError("credits exhausted")))
    memory = SharedMemory()
    opps = [_opp("BTC/USD", 0.9), _opp("ETH/USD", 0.8)]
    _seed_plan(memory, opps)

    plan = agent.run()
    report = memory.read("reports", "debate")
    assert report["candidates_debated"] == 2
    # The engine judged them: model is stamped, real bull/bear text exists,
    # and a thin (zero-trade) track record means DOWNGRADE, not blind approve.
    for rec in report["debates"]:
        assert rec["model"] == "deterministic-engine"
        assert rec["verdict"] == "DOWNGRADE"
        assert "deterministic" in rec["arbiter_rationale"]
        assert rec["bull_argument"] and rec["bear_argument"]
        assert rec["confidence_after"] < rec["confidence_before"]
    # Downgraded confidence is what the plan (and downstream gates) see.
    for opp in plan["approved_opportunities"]:
        assert opp["confidence"] == pytest.approx(
            round((0.9 if opp["symbol"] == "BTC/USD" else 0.8) * 0.85, 4))


# (c2) deterministic engine REJECTs bad geometry (R:R below the 1.2 floor)
def test_engine_rejects_poor_risk_reward(monkeypatch):
    da, agent = _agent(monkeypatch)
    monkeypatch.setattr(da.requests, "post",
                        MagicMock(side_effect=OSError("llm down")))
    memory = SharedMemory()
    bad = _opp("BTC/USD", 0.9)
    bad.update({"stop_loss": 59500.0, "take_profit": 60500.0,
                "entry_price": 60000.0, "price": 60000.0})  # R:R = 1.0
    _seed_plan(memory, [bad, _opp("ETH/USD", 0.8)])

    plan = agent.run()
    symbols = [o["symbol"] for o in plan["approved_opportunities"]]
    assert "BTC/USD" not in symbols
    assert "ETH/USD" in symbols
    report = memory.read("reports", "debate")
    rec = report["debates"][0]
    assert rec["verdict"] == "REJECT"
    assert rec["model"] == "deterministic-engine"
    assert "deterministic reject" in rec["arbiter_rationale"]


# (c3) engine itself broken -> true fail-open pass-through
def test_engine_failure_is_true_fail_open(monkeypatch):
    da, agent = _agent(monkeypatch)
    monkeypatch.setattr(da.requests, "post",
                        MagicMock(side_effect=OSError("llm down")))
    monkeypatch.setattr(da.DebateAgent, "_deterministic_debate",
                        MagicMock(side_effect=RuntimeError("engine broken")))
    memory = SharedMemory()
    opps = [_opp("BTC/USD", 0.9)]
    _seed_plan(memory, opps)
    before = memory.read("decisions", "portfolio_plan")["approved_opportunities"]

    plan = agent.run()
    assert plan["approved_opportunities"] == before
    report = memory.read("reports", "debate")
    rec = report["debates"][0]
    assert rec["verdict"] == "APPROVE"
    assert "fail-open" in rec["arbiter_rationale"]
    assert rec["confidence_after"] == rec["confidence_before"]


# (c4) every LLM request excludes hidden reasoning (free-model fix)
def test_llm_payload_excludes_reasoning(monkeypatch):
    import types
    da, agent = _agent(monkeypatch)
    payloads = []

    def fake_post(url, headers=None, json=None, timeout=None):
        payloads.append(json)
        return types.SimpleNamespace(json=lambda: {
            "choices": [{"message": {"content": "ok"}}]})

    monkeypatch.setattr(da.requests, "post", fake_post)
    agent._llm("sys", "user", time.time() + 30)
    assert payloads and all(
        p.get("reasoning") == {"exclude": True} for p in payloads)


# (d) disabled flag -> no-op pass-through, zero LLM calls
def test_disabled_flag_is_noop(monkeypatch):
    da, agent = _agent(monkeypatch)
    monkeypatch.setattr(da, "DEBATE_ENABLED", False)
    calls = _mock_llm(monkeypatch, da, '{"verdict": "REJECT", "rationale": "x"}')
    memory = SharedMemory()
    opps = [_opp("BTC/USD", 0.9)]
    _seed_plan(memory, opps)
    report_before = memory.read("reports", "debate")

    plan = agent.run()
    assert plan["approved_opportunities"] == opps
    assert calls["n"] == 0
    # Disabled: the debate report must be untouched by this run.
    assert memory.read("reports", "debate") == report_before


# (e) the arbiter can NEVER raise confidence — malicious payloads are clamped
def test_arbiter_can_never_raise_confidence(monkeypatch):
    da, agent = _agent(monkeypatch)
    # Malicious arbiter: smuggles a confidence pump inside the verdict payload.
    _mock_llm(monkeypatch, da,
              '{"verdict": "APPROVE", "confidence": 0.99, "rationale": "raise confidence to 0.99"}')
    memory = SharedMemory()
    _seed_plan(memory, [_opp("BTC/USD", 0.9)])

    plan = agent.run()
    assert plan["approved_opportunities"][0]["confidence"] == 0.9

    # Even a misconfigured multiplier > 1 must clamp DOWN, never up.
    monkeypatch.setattr(da, "DEBATE_DOWNGRADE_MULT", 1.5)
    _mock_llm(monkeypatch, da,
              '{"verdict": "DOWNGRADE", "confidence": 0.99, "rationale": "pump it"}')
    _seed_plan(memory, [_opp("ETH/USD", 0.8)])

    plan2 = agent.run()
    conf2 = plan2["approved_opportunities"][0]["confidence"]
    assert conf2 <= 0.8
    assert conf2 == pytest.approx(0.8)

    # An out-of-vocabulary verdict is unparseable -> APPROVE, unchanged.
    _mock_llm(monkeypatch, da, '{"verdict": "STRONG_BUY", "confidence": 0.99}')
    _seed_plan(memory, [_opp("SOL/USD", 0.7)])
    plan3 = agent.run()
    assert plan3["approved_opportunities"][0]["confidence"] == 0.7


# (f) empty plan -> no LLM calls at all
def test_empty_plan_makes_no_llm_calls(monkeypatch):
    da, agent = _agent(monkeypatch)
    calls = _mock_llm(monkeypatch, da, '{"verdict": "REJECT", "rationale": "x"}')
    memory = SharedMemory()
    _seed_plan(memory, [])

    plan = agent.run()
    assert plan["approved_opportunities"] == []
    assert calls["n"] == 0


def test_empty_plan_also_skips_when_all_blocked(monkeypatch):
    """Plan exists but every candidate is risk_ok=False -> nothing tradable,
    so nothing to debate and no LLM spend."""
    da, agent = _agent(monkeypatch)
    calls = _mock_llm(monkeypatch, da, '{"verdict": "REJECT", "rationale": "x"}')
    memory = SharedMemory()
    _seed_plan(memory, [_opp("BTC/USD", 0.9, risk_ok=False)])

    plan = agent.run()
    assert len(plan["approved_opportunities"]) == 1  # untouched
    assert calls["n"] == 0


def test_no_api_key_passes_through(monkeypatch):
    da, agent = _agent(monkeypatch, key="")
    calls = _mock_llm(monkeypatch, da, '{"verdict": "REJECT", "rationale": "x"}')
    memory = SharedMemory()
    opps = [_opp("BTC/USD", 0.9)]
    _seed_plan(memory, opps)

    plan = agent.run()
    assert plan["approved_opportunities"] == opps
    assert calls["n"] == 0


def test_parse_verdict_strict_and_fail_open():
    from agents.debate_agent import DebateAgent
    f = DebateAgent._parse_verdict
    assert f('judgment\n{"verdict": "DOWNGRADE", "rationale": "thin sample"}') == \
        ("DOWNGRADE", "thin sample")
    assert f('{"verdict": "reject", "rationale": "bad"}')[0] == "REJECT"
    assert f("no json here") == ("APPROVE", "arbiter reply unparseable — fail-open pass-through")
    assert f("") [0] == "APPROVE"
    assert f('{"verdict": "NUKE_IT", "rationale": "x"}')[0] == "APPROVE"
    # Last valid JSON block wins (model may emit scratch JSON first).
    assert f('{"note": 1} then {"verdict": "APPROVE", "rationale": "ok"}')[0] == "APPROVE"


def test_skip_when_deployment_capped(monkeypatch):
    """SMA200 dial at 0% deployment (risk_off, scout off): compliance will
    reject every candidate anyway — the debate must be skipped entirely with
    zero LLM spend and the plan passed through untouched."""
    da, agent = _agent(monkeypatch)
    calls = _mock_llm(monkeypatch, da, '{"verdict": "REJECT", "rationale": "x"}')
    memory = SharedMemory()
    opps = [_opp("BTC/USD", 0.9)]
    _seed_plan(memory, opps)
    memory.write("analyses", "regime_scan", {
        "firm_regime": "risk_off", "deployment_target": 0.0,
        "symbols": {}, "timestamp": time.time()})

    plan = agent.run()
    assert calls["n"] == 0
    assert plan["approved_opportunities"] == opps


def test_scout_mode_nonzero_target_does_not_skip(monkeypatch):
    """Scout mode raises the floor to 10% deployment — debates must run."""
    da, agent = _agent(monkeypatch)
    calls = _mock_llm(monkeypatch, da, '{"verdict": "APPROVE", "rationale": "ok"}')
    memory = SharedMemory()
    _seed_plan(memory, [_opp("BTC/USD", 0.9)])
    memory.write("analyses", "regime_scan", {
        "firm_regime": "risk_off", "deployment_target": 0.10,
        "scout_mode": True, "symbols": {}, "timestamp": time.time()})

    agent.run()
    assert calls["n"] > 0


def test_primary_credits_dead_skips_primary_afterwards(monkeypatch):
    """A credits/balance failure on the primary model is sticky for the
    process: subsequent calls go straight to the fallback instead of paying
    the timeout for the same refusal on every bull/bear/arbiter round."""
    import types
    da, agent = _agent(monkeypatch)
    da.DebateAgent._primary_credits_dead = False
    attempted = []

    def fake_post(url, headers=None, json=None, timeout=None):
        model = json["model"]
        attempted.append(model)
        if model == da.HERMES_MODEL:
            return types.SimpleNamespace(json=lambda: {
                "message": "Model requires available credits. Your account balance is too low"})
        return types.SimpleNamespace(json=lambda: {
            "choices": [{"message": {"content": "ok"}}]})

    monkeypatch.setattr(da.requests, "post", fake_post)
    try:
        _, model = agent._llm("sys", "user", time.time() + 30)
        assert model == da.HERMES_FALLBACK_MODEL
        assert attempted == [da.HERMES_MODEL, da.HERMES_FALLBACK_MODEL]

        attempted.clear()
        _, model = agent._llm("sys", "user", time.time() + 30)
        assert model == da.HERMES_FALLBACK_MODEL
        assert attempted == [da.HERMES_FALLBACK_MODEL]  # primary never retried
    finally:
        da.DebateAgent._primary_credits_dead = False

"""Tests for the multi-agent desk: message bus, negotiation protocol,
concurrent deliberation, persistent state, and outcome attribution."""
import asyncio
import time
import uuid

import pytest

from core.bus import MessageBus, Message
from core.database import (
    init_db, execute, fetchone, get_agent_state, set_agent_state,
    get_message_thread,
)
from agents.protocol import (
    TOPIC_PROPOSAL, TOPIC_REVIEW_REQ, TOPIC_REVISION_REQ, TOPIC_VERDICT,
    TOPIC_EXECUTE, TOPIC_EXECUTED,
    STANCE_APPROVE, STANCE_COUNTER, STANCE_REJECT, STANCE_VETO,
    DEFENSE_CONCEDE,
    make_review, make_defense, apply_counters, merge_reviews, tally_votes,
)
from agents.runtime import AsyncAgent
from agents.desk import (
    DeskChair, ReviewerMixin, AsyncAnalyst, AsyncAuditor, AsyncTrader,
    AsyncExecution, AsyncPositionSizer, AsyncHealthMonitor,
)


def _wipe_desk_tables():
    execute("DELETE FROM agent_state")
    execute("DELETE FROM agent_messages")
    execute("DELETE FROM trades")
    execute("DELETE FROM positions")


@pytest.fixture(autouse=True)
def _clean_db():
    init_db()
    _wipe_desk_tables()
    yield
    # Leave the shared test DB as we found it — seeded losing trades would
    # otherwise trip other modules' compliance circuit breakers.
    _wipe_desk_tables()


# ────────────────────────── helpers ──────────────────────────
def _proposal(**over):
    p = {
        "proposal_id": f"prop_{uuid.uuid4().hex[:8]}",
        "proposed_by": "analyst",
        "symbol": "BTC/USD", "action": "BUY",
        "price": 100.0, "qty": 10.0, "confidence": 0.8,
        "reasons": ["test setup"], "strategies": ["test_strat"],
        "regime": "trending_up", "multi_timeframe": True,
        "indicators": {"volatility": 2.0},
        "sl": 94.0, "tp": 112.0, "sl_pct": 6.0, "tp_pct": 12.0,
    }
    p.update(over)
    return p


def make_reviewer(agent_name, script, delay=0.0):
    """Reviewer answering from a per-round script: {round: kwargs} with
    '*' as fallback. kwargs = dict(stance=..., reasons=[...], qty_mult=...)."""
    class Scripted(ReviewerMixin, AsyncAgent):
        name = agent_name

        def review(self, proposal, rnd):
            if delay:
                time.sleep(delay)
            entry = script.get(rnd) or script.get("*") or {"stance": STANCE_APPROVE,
                                                           "reasons": ["ok"]}
            kwargs = {k: v for k, v in entry.items() if k not in ("stance", "reasons")}
            return make_review(self.name, entry["stance"], entry["reasons"], **kwargs)
    return Scripted


class ExecStub(ReviewerMixin, AsyncAgent):
    name = "execution"

    def review(self, proposal, rnd):
        return make_review(self.name, STANCE_APPROVE, ["executable"])

    async def on_other_message(self, msg):
        if msg.topic == TOPIC_EXECUTE:
            p = msg.payload["proposal"]
            await self.respond(msg, {"ok": True, "order": {
                "plan_id": "", "symbol": p["symbol"], "action": p["action"],
                "qty": p["qty"], "price": p["price"],
                "sl": p.get("sl", 0), "tp": p.get("tp", 0), "strategy": "",
            }})


class TraderStub(AsyncAgent):
    name = "trader"

    def __init__(self, bus, services=None):
        super().__init__(bus, services)
        self.orders = []

    async def on_message(self, msg):
        if msg.topic == TOPIC_EXECUTE:
            order = msg.payload["order"]
            self.orders.append(order)
            await self.respond(msg, {"status": "filled",
                                     "price": order["price"], "qty": order["qty"]})


class ConcedeAnalyst(AsyncAgent):
    name = "analyst"

    async def on_message(self, msg):
        if msg.topic == TOPIC_REVISION_REQ:
            await self.respond(msg, make_defense(DEFENSE_CONCEDE, ["accepting"]))


async def _start(agents):
    return [asyncio.create_task(a.run_loop()) for a in agents]


async def _stop(bus, agents, tasks):
    for a in agents:
        a.request_stop()
        await bus.send("test", a.name, "__stop__", {}, persist=False)
    await asyncio.gather(*tasks, return_exceptions=True)


async def _wait_for(cond, timeout=8.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        await asyncio.sleep(0.05)
    return False


# ────────────────────────── bus ──────────────────────────
@pytest.mark.asyncio
async def test_bus_pubsub_fanout_skips_sender():
    bus = MessageBus(persist=False)
    qa, qb = bus.register("a"), bus.register("b")
    bus.subscribe("a", "news")
    bus.subscribe("b", "news")
    await bus.publish("a", "news", {"n": 1})
    assert (await asyncio.wait_for(qb.get(), 1)).payload == {"n": 1}
    assert qa.empty()   # publishers don't hear their own broadcast


@pytest.mark.asyncio
async def test_bus_request_reply_roundtrip():
    bus = MessageBus(persist=False)
    inbox = bus.register("svc")
    bus.register("cli")

    async def responder():
        msg = await inbox.get()
        await bus.respond(msg, "svc", {"pong": msg.payload["ping"] + 1})

    task = asyncio.create_task(responder())
    reply = await bus.request("cli", "svc", "ping", {"ping": 41}, timeout=2)
    await task
    assert reply.payload == {"pong": 42}
    assert reply.sender == "svc"


@pytest.mark.asyncio
async def test_bus_request_timeout_returns_none():
    bus = MessageBus(persist=False)
    bus.register("deaf")
    bus.register("cli")
    reply = await bus.request("cli", "deaf", "ping", {}, timeout=0.1)
    assert reply is None


@pytest.mark.asyncio
async def test_bus_gather_collects_partial_answers():
    bus = MessageBus(persist=False)
    fast_inbox = bus.register("fast")
    bus.register("silent")
    bus.register("chair")

    async def fast():
        msg = await fast_inbox.get()
        await bus.respond(msg, "fast", {"ok": True})

    task = asyncio.create_task(fast())
    replies = await bus.gather("chair", ["fast", "silent"], "q", {}, timeout=0.3)
    await task
    assert set(replies) == {"fast"}


@pytest.mark.asyncio
async def test_bus_persists_messages_to_db():
    bus = MessageBus()
    bus.register("listener")
    bus.subscribe("listener", "topic.x")
    corr = f"corr_{uuid.uuid4().hex[:8]}"
    await bus.publish("someone", "topic.x", {"v": 7}, correlation_id=corr)
    thread = get_message_thread(corr)
    assert len(thread) == 1
    assert thread[0]["topic"] == "topic.x"
    assert thread[0]["payload"] == {"v": 7}


# ────────────────────────── protocol (pure) ──────────────────────────
def test_apply_counters_compounds_and_takes_tightest_stops():
    p = _proposal(qty=10.0, confidence=0.8, sl=94.0, tp=112.0)
    reviews = [
        make_review("risk_manager", STANCE_COUNTER, ["big"], qty_mult=0.5, sl=97.0),
        make_review("position_sizer", STANCE_COUNTER, ["kelly"], qty_mult=0.5,
                    confidence_delta=-0.1, tp=108.0),
        make_review("compliance", STANCE_APPROVE, ["fine"]),
    ]
    out = apply_counters(p, reviews)
    assert out["qty"] == 2.5              # 10 * 0.5 * 0.5 — both concerns are real
    assert out["confidence"] == 0.7
    assert out["sl"] == 97.0              # tighter stop for a long wins
    assert out["tp"] == 108.0             # nearer target wins


def test_apply_counters_sell_side_stops():
    p = _proposal(action="SELL", sl=106.0, tp=88.0)
    out = apply_counters(p, [
        make_review("risk_manager", STANCE_COUNTER, ["r"], sl=103.0, tp=92.0),
    ])
    assert out["sl"] == 103.0             # tighter stop for a short = lower SL
    assert out["tp"] == 92.0


def test_merge_veto_kills_trade_regardless_of_votes():
    p = _proposal()
    reviews = [
        make_review("compliance", STANCE_VETO, ["circuit breaker tripped"]),
        make_review("risk_manager", STANCE_APPROVE, ["fine"]),
        make_review("sentiment", STANCE_APPROVE, ["bullish"]),
    ]
    v = merge_reviews(p, reviews, rounds_left=1)
    assert v["decision"] == "rejected"
    assert "circuit breaker tripped" in v["reasons"]


def test_merge_veto_without_power_is_demoted_to_reject():
    p = _proposal()
    reviews = [
        make_review("sentiment", STANCE_VETO, ["I feel strongly"]),
        make_review("risk_manager", STANCE_APPROVE, ["fine"]),
        make_review("compliance", STANCE_APPROVE, ["fine"]),
    ]
    v = merge_reviews(p, reviews, rounds_left=0)
    # 2 approvals (+2) vs 1 demoted reject (-1): the desk outvotes the diva.
    assert v["decision"] == "approved"


def test_merge_offers_revision_while_rounds_remain():
    p = _proposal()
    reviews = [make_review("risk_manager", STANCE_REJECT, ["overexposed"]),
               make_review("compliance", STANCE_APPROVE, ["fine"])]
    assert merge_reviews(p, reviews, rounds_left=1)["decision"] == "revise"
    assert merge_reviews(p, reviews, rounds_left=0)["decision"] == "rejected"


def test_merge_earned_weights_swing_the_vote():
    p = _proposal()
    reviews = [make_review("risk_manager", STANCE_REJECT, ["no"]),
               make_review("sentiment", STANCE_APPROVE, ["yes"])]
    # Equal weights: -1 + 1 = 0 -> not approved.
    assert merge_reviews(p, reviews, rounds_left=0)["decision"] == "rejected"
    # Sentiment has earned trust; risk manager has been wrong a lot.
    v = merge_reviews(p, reviews, weights={"sentiment": 1.5, "risk_manager": 0.6},
                      rounds_left=0)
    assert v["decision"] == "approved"
    assert v["score"] == pytest.approx(0.9)


def test_merge_rejects_when_counters_zero_out_size_or_confidence():
    p = _proposal(qty=10.0, confidence=0.6)
    gutted = [make_review("risk_manager", STANCE_COUNTER, ["no headroom"], qty_mult=0.0),
              make_review("compliance", STANCE_APPROVE, ["fine"])]
    v = merge_reviews(p, gutted, rounds_left=0)
    assert v["decision"] == "rejected"
    assert any("size to zero" in r for r in v["reasons"])

    doubted = [make_review("sentiment", STANCE_COUNTER, ["mood is grim"],
                           confidence_delta=-0.2),
               make_review("compliance", STANCE_APPROVE, ["fine"])]
    v2 = merge_reviews(p, doubted, min_confidence=0.55, rounds_left=0)
    assert v2["decision"] == "rejected"


# ────────────────────────── deliberation (async) ──────────────────────────
@pytest.mark.asyncio
async def test_desk_negotiates_size_down_then_executes(monkeypatch):
    """Risk counters the size in round 1, the analyst concedes, round 2
    approves — the trader receives the negotiated qty, not the proposed."""
    monkeypatch.setattr(DeskChair, "REVIEWERS",
                        ("risk_manager", "compliance", "execution"))
    bus = MessageBus()
    chair = DeskChair(bus)
    risk = make_reviewer("risk_manager", {
        1: {"stance": STANCE_COUNTER, "reasons": ["size exceeds risk budget"],
            "qty_mult": 0.5},
        2: {"stance": STANCE_APPROVE, "reasons": ["size now acceptable"]},
    })(bus)
    comp = make_reviewer("compliance", {"*": {"stance": STANCE_APPROVE,
                                              "reasons": ["clean"]}})(bus)
    execu, trader, analyst = ExecStub(bus), TraderStub(bus), ConcedeAnalyst(bus)
    agents = [chair, risk, comp, execu, trader, analyst]
    tasks = await _start(agents)
    try:
        p = _proposal(qty=10.0)
        await bus.publish("test-feed", TOPIC_PROPOSAL, p,
                          correlation_id=p["proposal_id"])
        assert await _wait_for(lambda: trader.orders)
        assert trader.orders[0]["qty"] == 5.0   # 10 * 0.5 negotiated down

        # The chair records trade.executed a beat after the fill lands.
        assert await _wait_for(
            lambda: any(m["topic"] == TOPIC_EXECUTED
                        for m in get_message_thread(p["proposal_id"])))
        thread = get_message_thread(p["proposal_id"])
        topics = [m["topic"] for m in thread]
        assert TOPIC_VERDICT in topics
        verdict = next(m["payload"] for m in thread if m["topic"] == TOPIC_VERDICT)
        assert verdict["decision"] == "approved"
        assert verdict["rounds"] == 2
        assert verdict["tally"]["risk_manager"]["stance"] == STANCE_APPROVE
    finally:
        await _stop(bus, agents, tasks)


@pytest.mark.asyncio
async def test_desk_veto_blocks_execution(monkeypatch):
    monkeypatch.setattr(DeskChair, "REVIEWERS", ("compliance", "risk_manager"))
    bus = MessageBus()
    chair = DeskChair(bus)
    comp = make_reviewer("compliance", {"*": {
        "stance": STANCE_VETO, "reasons": ["daily loss circuit breaker"]}})(bus)
    risk = make_reviewer("risk_manager", {"*": {"stance": STANCE_APPROVE,
                                                "reasons": ["fine"]}})(bus)
    trader = TraderStub(bus)
    agents = [chair, comp, risk, trader]
    tasks = await _start(agents)
    try:
        p = _proposal()
        await bus.publish("test-feed", TOPIC_PROPOSAL, p,
                          correlation_id=p["proposal_id"])
        assert await _wait_for(
            lambda: any(m["topic"] == TOPIC_VERDICT
                        for m in get_message_thread(p["proposal_id"])))
        verdict = next(m["payload"] for m in get_message_thread(p["proposal_id"])
                       if m["topic"] == TOPIC_VERDICT)
        assert verdict["decision"] == "rejected"
        assert verdict["vetoes"] == ["compliance"]
        await asyncio.sleep(0.2)
        assert trader.orders == []   # a veto means the trader never hears about it
    finally:
        await _stop(bus, agents, tasks)


@pytest.mark.asyncio
async def test_analyst_defends_high_conviction_and_wins(monkeypatch):
    """Round 1 draws a judgment-call rejection; the real analyst defends
    (+0.05 confidence), and the desk comes around in round 2."""
    monkeypatch.setattr(DeskChair, "REVIEWERS", ("risk_manager", "execution"))
    bus = MessageBus()
    chair = DeskChair(bus)
    risk = make_reviewer("risk_manager", {
        1: {"stance": STANCE_REJECT, "reasons": ["setup quality looks weak"]},
        2: {"stance": STANCE_APPROVE, "reasons": ["persuaded by the defense"]},
    })(bus)
    execu, trader = ExecStub(bus), TraderStub(bus)
    analyst = AsyncAnalyst(bus)   # the real one argues its own case
    agents = [chair, risk, execu, trader, analyst]
    tasks = await _start(agents)
    try:
        p = _proposal(confidence=0.75, multi_timeframe=True)
        await bus.publish("test-feed", TOPIC_PROPOSAL, p,
                          correlation_id=p["proposal_id"])
        assert await _wait_for(lambda: trader.orders)
        verdict = next(m["payload"] for m in get_message_thread(p["proposal_id"])
                       if m["topic"] == TOPIC_VERDICT)
        assert verdict["decision"] == "approved"
        assert verdict["rounds"] == 2
        assert verdict["proposal"]["confidence"] == pytest.approx(0.8)  # 0.75 + 0.05
        assert verdict["proposal"]["defense"]    # the argument is on the record
    finally:
        await _stop(bus, agents, tasks)


@pytest.mark.asyncio
async def test_reviews_run_concurrently_not_sequentially(monkeypatch):
    """Four reviewers that each take 0.5s must deliberate in ~0.5s wall
    time, not ~2s — the desk argues in parallel."""
    names = ("risk_manager", "position_sizer", "portfolio_manager", "sentiment")
    monkeypatch.setattr(DeskChair, "REVIEWERS", names)
    bus = MessageBus(persist=False)
    chair = DeskChair(bus)
    reviewers = [make_reviewer(n, {"*": {"stance": STANCE_APPROVE,
                                         "reasons": ["ok"]}}, delay=0.5)(bus)
                 for n in names]
    execu, trader, analyst = ExecStub(bus), TraderStub(bus), ConcedeAnalyst(bus)
    agents = [chair, *reviewers, execu, trader, analyst]
    tasks = await _start(agents)
    try:
        start = time.monotonic()
        await chair.deliberate(_proposal())
        elapsed = time.monotonic() - start
        assert elapsed < 1.5, f"reviews ran sequentially ({elapsed:.2f}s)"
        assert trader.orders   # and the approved trade went through
    finally:
        await _stop(bus, agents, tasks)


# ────────────────────────── persistent state ──────────────────────────
@pytest.mark.asyncio
async def test_agent_state_survives_restart():
    bus = MessageBus(persist=False)

    class Counter(AsyncAgent):
        name = "counter_test"

    first = Counter(bus)
    first.state["cycles"] = 41
    first.state["beliefs"] = {"BTC/USD": "overbought"}
    await first.save_state()

    reborn = Counter(MessageBus(persist=False))   # fresh bus, fresh process-life
    assert reborn.state["cycles"] == 41
    assert reborn.state["beliefs"]["BTC/USD"] == "overbought"


def test_auditor_moves_reviewer_weights_on_real_outcomes():
    """Back a loser -> weight down. Oppose a loser -> weight up."""
    bus = MessageBus(persist=False)
    auditor = AsyncAuditor(bus)
    auditor.state["decisions"]["p1"] = {
        "symbol": "BTC/USD", "action": "BUY", "decision": "approved",
        "stances": {"risk_manager": STANCE_APPROVE, "sentiment": STANCE_REJECT},
        "ts": time.time(), "executed": True,
    }
    execute("""INSERT INTO trades (symbol, side, qty, entry_price, exit_price,
                                   pnl, pnl_pct, reason, closed_at)
               VALUES ('BTC/USD', 'BUY', 1.0, 100.0, 95.0, -5.0, -5.0, 'SL',
                       datetime('now'))""")
    auditor._attribute_outcomes()
    weights = auditor.state["reviewer_weights"]
    assert weights["risk_manager"] == pytest.approx(0.95)   # backed a loser
    assert weights["sentiment"] == pytest.approx(1.05)      # called it right
    assert auditor.state["decisions"]["p1"]["attributed"] is True


# ────────────────────────── fail-closed safety gates ──────────────────────────
@pytest.mark.asyncio
async def test_silent_veto_power_fails_closed(monkeypatch):
    """Compliance never answers — its silence must become a veto, never consent."""
    monkeypatch.setattr(DeskChair, "REVIEWERS", ("compliance", "risk_manager"))
    monkeypatch.setattr("agents.desk.REVIEW_TIMEOUT_SECONDS", 0.3)
    bus = MessageBus()
    chair = DeskChair(bus)
    risk = make_reviewer("risk_manager", {"*": {"stance": STANCE_APPROVE,
                                                "reasons": ["fine"]}})(bus)
    trader = TraderStub(bus)
    bus.register("compliance")   # the inbox exists; nobody ever reads it
    agents = [chair, risk, trader]
    tasks = await _start(agents)
    try:
        p = _proposal()
        await bus.publish("test-feed", TOPIC_PROPOSAL, p,
                          correlation_id=p["proposal_id"])
        assert await _wait_for(
            lambda: any(m["topic"] == TOPIC_VERDICT
                        for m in get_message_thread(p["proposal_id"])))
        verdict = next(m["payload"] for m in get_message_thread(p["proposal_id"])
                       if m["topic"] == TOPIC_VERDICT)
        assert verdict["decision"] == "rejected"
        assert "compliance" in verdict["vetoes"]
        await asyncio.sleep(0.1)
        assert trader.orders == []
    finally:
        await _stop(bus, agents, tasks)


@pytest.mark.asyncio
async def test_crashing_veto_power_fails_closed(monkeypatch):
    """A compliance crash must veto the trade, not abstain it into approval."""
    monkeypatch.setattr(DeskChair, "REVIEWERS", ("compliance", "risk_manager"))

    class CrashingCompliance(ReviewerMixin, AsyncAgent):
        name = "compliance"

        def review(self, proposal, rnd):
            raise RuntimeError("database is locked")

    bus = MessageBus()
    chair = DeskChair(bus)
    comp = CrashingCompliance(bus)
    risk = make_reviewer("risk_manager", {"*": {"stance": STANCE_APPROVE,
                                                "reasons": ["fine"]}})(bus)
    trader = TraderStub(bus)
    agents = [chair, comp, risk, trader]
    tasks = await _start(agents)
    try:
        p = _proposal()
        await bus.publish("test-feed", TOPIC_PROPOSAL, p,
                          correlation_id=p["proposal_id"])
        assert await _wait_for(
            lambda: any(m["topic"] == TOPIC_VERDICT
                        for m in get_message_thread(p["proposal_id"])))
        verdict = next(m["payload"] for m in get_message_thread(p["proposal_id"])
                       if m["topic"] == TOPIC_VERDICT)
        assert verdict["decision"] == "rejected"
        assert "compliance" in verdict["vetoes"]
        assert trader.orders == []
    finally:
        await _stop(bus, agents, tasks)


@pytest.mark.asyncio
async def test_zero_negotiation_rounds_still_yields_verdict(monkeypatch):
    monkeypatch.setattr("agents.desk.NEGOTIATION_ROUNDS", 0)
    monkeypatch.setattr(DeskChair, "REVIEWERS", ("risk_manager",))
    bus = MessageBus()
    chair = DeskChair(bus)
    risk = make_reviewer("risk_manager", {"*": {"stance": STANCE_APPROVE,
                                                "reasons": ["ok"]}})(bus)
    execu, trader, analyst = ExecStub(bus), TraderStub(bus), ConcedeAnalyst(bus)
    agents = [chair, risk, execu, trader, analyst]
    tasks = await _start(agents)
    try:
        p = _proposal()
        await chair.deliberate(p)
        verdict = next(m["payload"] for m in get_message_thread(p["proposal_id"])
                       if m["topic"] == TOPIC_VERDICT)
        assert verdict["decision"] == "approved"
        assert verdict["rounds"] == 1   # clamped to at least one round
    finally:
        await _stop(bus, agents, tasks)


# ────────────────────────── spot-only (halal) gates ──────────────────────────
def test_trader_rejects_sell_without_holdings():
    trader = AsyncTrader(MessageBus(persist=False))
    res = trader._place({"symbol": "BTC/USD", "action": "SELL",
                         "qty": 1.0, "price": 100.0})
    assert res["status"] == "rejected"
    assert any("spot-only" in r for r in res["reasons"])


def test_trader_sell_closes_position_and_clamps_qty():
    bus = MessageBus(persist=False)
    trader = AsyncTrader(bus)
    pid = trader._pos_mgr.open_position("ETH/USD", "BUY", 2.0, 100.0,
                                        sl=90.0, tp=120.0, strategy="s")

    class BrokerStub:
        def place_order(self, symbol, action, qty, price, sl=0, tp=0):
            self.last = {"symbol": symbol, "action": action, "qty": qty}
            return {"status": "filled", "price": price, "quantity": qty}

    trader._broker = BrokerStub()
    res = trader._place({"symbol": "ETH/USD", "action": "SELL",
                         "qty": 99.0, "price": 100.0})
    assert res["status"] == "filled"
    assert trader._broker.last["qty"] == 2.0       # clamped to actual holdings
    row = fetchone("SELECT status FROM positions WHERE id=?", [pid])
    assert row["status"] == "closed"
    trade = fetchone("SELECT reason FROM trades WHERE symbol='ETH/USD'")
    assert trade["reason"] == "desk_exit"


def test_trader_still_rejects_duplicate_buy():
    bus = MessageBus(persist=False)
    trader = AsyncTrader(bus)
    trader._pos_mgr.open_position("SOL/USD", "BUY", 1.0, 50.0)
    res = trader._place({"symbol": "SOL/USD", "action": "BUY",
                         "qty": 1.0, "price": 50.0})
    assert res["status"] == "rejected"


def test_execution_prepare_sell_requires_holding():
    execu = AsyncExecution(MessageBus(persist=False))
    res = execu._prepare(_proposal(action="SELL", symbol="ADA/USD"))
    assert not res["ok"]
    assert any("spot-only" in r for r in res["reasons"])


def test_execution_prepare_sell_closes_full_holding():
    execu = AsyncExecution(MessageBus(persist=False))
    execu._pos_mgr.open_position("ADA/USD", "BUY", 3.5, 1.0, sl=0.9, tp=1.2)
    res = execu._prepare(_proposal(action="SELL", symbol="ADA/USD",
                                   price=1.0, qty=99.0, sl=1.1, tp=0.9))
    assert res["ok"]
    assert res["order"]["qty"] == 3.5


# ────────────────────────── reviewer fixes ──────────────────────────
def test_kelly_zero_floors_position_size(monkeypatch):
    from agents.position_sizer import PositionSizer
    monkeypatch.setattr(PositionSizer, "_kelly_fraction",
                        staticmethod(lambda: 0.0))
    sizer = AsyncPositionSizer(MessageBus(persist=False))
    r = sizer.review(_proposal(), 1)
    assert r["stance"] == STANCE_COUNTER
    assert r["qty_mult"] == 0.25   # no edge -> minimum size, never full


def test_confidence_delta_applies_on_approvals():
    p = _proposal(confidence=0.6)
    out = apply_counters(p, [
        make_review("regime", STANCE_APPROVE, ["regime favors BUY"],
                    confidence_delta=0.03),
    ])
    assert out["confidence"] == pytest.approx(0.63)


def test_analyst_sizes_sell_from_holdings_not_cash():
    from config import MAX_POSITION_SIZE_PCT

    class Pos:
        quantity = 1.5

    class Pf:
        cash = 100000.0
        positions = {"BTC/USD": Pos()}

    assert AsyncAnalyst._proposal_qty("SELL", "BTC/USD", Pf(), 100.0) == 1.5
    assert AsyncAnalyst._proposal_qty("SELL", "ETH/USD", Pf(), 100.0) == 0.0
    expected_buy = round(100000.0 * (MAX_POSITION_SIZE_PCT / 100) / 100.0, 8)
    assert AsyncAnalyst._proposal_qty("BUY", "ETH/USD", Pf(), 100.0) == expected_buy


@pytest.mark.asyncio
async def test_halt_rejection_skips_cooldown():
    bus = MessageBus(persist=False)
    analyst = AsyncAnalyst(bus)
    halt_verdict = Message(topic=TOPIC_VERDICT, sender="orchestrator", payload={
        "symbol": "BTC/USD", "action": "BUY", "decision": "rejected",
        "reasons": ["desk halted: health"],
        "proposal": {"proposed_by": "analyst"},
    })
    await analyst.on_message(halt_verdict)
    assert analyst.state["cooldowns"] == {}   # halt is not a lost argument

    lost_verdict = Message(topic=TOPIC_VERDICT, sender="orchestrator", payload={
        "symbol": "BTC/USD", "action": "BUY", "decision": "rejected",
        "reasons": ["vote failed at -1.00"],
        "proposal": {"proposed_by": "analyst"},
    })
    await analyst.on_message(lost_verdict)
    assert "BTC/USD:BUY" in analyst.state["cooldowns"]


def test_auditor_attributes_same_second_trades():
    """Two losses closing in the same second must both move weights —
    the cursor is by rowid, not by timestamp."""
    bus = MessageBus(persist=False)
    auditor = AsyncAuditor(bus)
    for pid, sym in (("p1", "BTC/USD"), ("p2", "ETH/USD")):
        auditor.state["decisions"][pid] = {
            "symbol": sym, "action": "BUY", "decision": "approved",
            "stances": {"risk_manager": STANCE_APPROVE},
            "ts": time.time(), "executed": True,
        }
    for sym in ("BTC/USD", "ETH/USD"):
        execute("""INSERT INTO trades (symbol, side, qty, entry_price, exit_price,
                                       pnl, pnl_pct, reason, closed_at)
                   VALUES (?, 'BUY', 1.0, 100.0, 95.0, -5.0, -5.0, 'SL',
                           '2026-07-17 10:00:00')""", [sym])
    auditor._attribute_outcomes()
    assert auditor.state["decisions"]["p1"].get("attributed") is True
    assert auditor.state["decisions"]["p2"].get("attributed") is True
    # backed two losers -> 1.0 - 0.05 - 0.05
    assert auditor.state["reviewer_weights"]["risk_manager"] == pytest.approx(0.90)


# ────────────────────────── health watchdog ──────────────────────────
class _FakeAgent:
    def __init__(self, name, interval, delay=0.0):
        self.name = name
        self.tick_interval = interval
        self.tick_delay = delay


class _FakeRuntime:
    def __init__(self, agents, beats, dead=()):
        self._agents = {a.name: a for a in agents}
        self.beats = beats
        self._dead = list(dead)

    def heartbeats(self):
        return dict(self.beats)

    def agent(self, name):
        return self._agents.get(name)

    def dead_agents(self):
        return list(self._dead)


class _FakeMemory:
    """In-memory SharedMemory stand-in. The real one writes reports/health.json
    into the shared test data dir — and ComplianceAgent halts on a lingering
    halted=true, poisoning every later compliance test in the session."""

    def __init__(self):
        self.reports = {}

    def get_recent_errors(self, n=50):
        return []

    def write(self, category, name, data):
        self.reports[(category, name)] = data

    def read(self, category, name):
        return self.reports.get((category, name))

    def log(self, agent, message):
        pass


@pytest.mark.asyncio
async def test_health_judges_agents_by_their_own_cadence():
    """Message-driven agents and the 2h-cadence optimizer must not read as
    'silent' to a 3-tick rule sized for 1-minute agents."""
    now = time.time()
    runtime = _FakeRuntime(
        agents=[_FakeAgent("trader", None), _FakeAgent("optimizer", 7200, 600),
                _FakeAgent("analyst", 60)],
        beats={"trader": now - 90000, "optimizer": now - 3600, "analyst": now - 10},
    )
    bus = MessageBus(persist=False)
    health = AsyncHealthMonitor(bus, services={"runtime": runtime})
    health.memory = _FakeMemory()
    await health.tick()
    report = health.memory.read("reports", "health")
    assert report["halted"] is False
    assert report["issues"] == []


@pytest.mark.asyncio
async def test_health_flags_stale_tickers_and_dead_tasks():
    now = time.time()
    runtime = _FakeRuntime(
        agents=[_FakeAgent("analyst", 60), _FakeAgent("trader", None)],
        beats={"analyst": now - 9999, "trader": now - 5},
        dead=["trader"],
    )
    bus = MessageBus(persist=False)
    health = AsyncHealthMonitor(bus, services={"runtime": runtime})
    health.memory = _FakeMemory()
    await health.tick()
    report = health.memory.read("reports", "health")
    assert report["halted"] is True
    assert any("analyst silent" in i for i in report["issues"])
    assert any("trader" in i and "died" in i for i in report["issues"])

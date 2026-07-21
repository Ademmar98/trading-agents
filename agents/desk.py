"""The trading desk: every agent is a concurrent actor with persistent
state, and every trade must survive an argument before it executes.

Roles (names match the legacy agents so auditor trust weights and veto
powers line up):

  analyst            proposes trades on its own cadence, defends them
  orchestrator       desk chair: runs deliberations, tallies votes
  risk_manager       counters size, vetoes on drawdown breach
  position_sizer     counters with Kelly/volatility sizing
  portfolio_manager  counters on exposure/duplicates/strategy weights
  compliance         hard vetoes: circuit breakers, spot-only, market hours
  sentiment          argues market mood (its scan is its evidence)
  regime             argues regime fit
  execution          feasibility: spread, fee-viability; prepares final order
  trader             places approved orders with the broker
  auditor            remembers every argument, scores reviewers on real
                     trade outcomes, adjusts their voting weight
  health             watchdog over real heartbeats; can halt the desk
  optimizer          slow loop tuning parameters (unchanged logic)

Legacy `run()` pipelines still exist for the dashboard's report files and
for tests; the desk is the decision path in MULTI_AGENT_MODE.
"""
import time
import uuid

from config import (
    MAX_POSITION_SIZE_PCT, MAX_PORTFOLIO_RISK_PCT, TRADING_INTERVAL_MINUTES,
    NEGOTIATION_ROUNDS, REVIEW_TIMEOUT_SECONDS, PROPOSALS_PER_SCAN,
    PROPOSAL_COOLDOWN_TICKS, RISK_PER_TRADE_PCT, TRADE_FEE_PCT, MIN_TP_PCT,
    MAX_TRADES_PER_DAY, MAX_CONSECUTIVE_LOSSES, DAILY_LOSS_LIMIT_PCT,
    LEVERAGE_ENABLED, BROKER_TYPE,
    VOL_THROTTLE_ENABLED, VOL_THROTTLE_TARGET_VOL, VOL_THROTTLE_FLOOR,
)
from agents.runtime import AsyncAgent
from agents.protocol import (
    TOPIC_CONTEXT, TOPIC_PROPOSAL, TOPIC_REVIEW_REQ, TOPIC_REVISION_REQ,
    TOPIC_VERDICT, TOPIC_EXECUTE, TOPIC_EXECUTED, TOPIC_HALT, TOPIC_TUNING,
    STANCE_APPROVE, STANCE_COUNTER, STANCE_REJECT, STANCE_VETO, STANCE_ABSTAIN,
    DEFENSE_CONCEDE, DEFENSE_DEFEND, DEFENSE_WITHDRAW, VETO_POWERS,
    make_review, make_defense, merge_reviews,
)
from agents.analyst import ResearchAnalyst
from agents.sentiment_agent import SentimentAgent
from agents.regime_agent import RegimeAgent
from agents.risk_manager import RiskManager
from agents.position_sizer import PositionSizer
from agents.auditor import Auditor
from agents.optimizer_agent import OptimizerAgent
from agents.compliance_agent import MIN_CONFIDENCE
from agents.execution_agent import MAX_SPREAD_PCT
from core.database import (
    fetchall, fetchone, get_agent_state, save_plan, update_plan_status,
)
from core.equity import daily_loss_pct
from core.market import is_market_open
from core.portfolio import load_portfolio
from core.positions import PositionManager

_TICK = TRADING_INTERVAL_MINUTES * 60


def _pid():
    return f"prop_{uuid.uuid4().hex[:12]}"


class ReviewerMixin:
    """Answer the chair's review requests; domain logic runs in a thread."""

    async def on_message(self, msg):
        if msg.topic == TOPIC_REVIEW_REQ:
            proposal = msg.payload.get("proposal") or {}
            rnd = msg.payload.get("round", 1)
            try:
                review = await self.work(self.review, proposal, rnd)
            except Exception as e:
                # A crashing reviewer must not stall the desk — but a broken
                # safety gate cannot silently consent either: veto powers
                # fail closed, everyone else abstains.
                if self.name in VETO_POWERS:
                    review = make_review(self.name, STANCE_VETO,
                                         [f"review crashed ({e}) — failing closed"])
                else:
                    review = make_review(self.name, STANCE_ABSTAIN,
                                         [f"review crashed, abstaining: {e}"])
            self.state["reviews_given"] = self.state.get("reviews_given", 0) + 1
            await self.respond(msg, review)
        else:
            await self.on_other_message(msg)

    async def on_other_message(self, msg):
        pass

    def review(self, proposal, rnd):
        raise NotImplementedError


# ────────────────────────── the chair ──────────────────────────
class DeskChair(AsyncAgent):
    """Moderates deliberations. Does not vote — the desk decides."""

    name = "orchestrator"
    subscriptions = (TOPIC_PROPOSAL, TOPIC_HALT)
    REVIEWERS = ("risk_manager", "position_sizer", "portfolio_manager",
                 "compliance", "sentiment", "regime", "execution")

    def __init__(self, bus, services=None):
        super().__init__(bus, services)
        self._halts = {}   # source -> halted?

    @property
    def halted(self):
        return any(self._halts.values())

    async def on_message(self, msg):
        if msg.topic == TOPIC_HALT:
            self._halts[msg.payload.get("source", "unknown")] = bool(msg.payload.get("halted"))
            return
        if msg.topic == TOPIC_PROPOSAL:
            await self.deliberate(msg.payload)
            return
        if (msg.topic == f"{TOPIC_EXECUTE}.reply" and msg.sender == "trader"
                and msg.payload.get("status") == "filled"):
            # The trader's reply arrived after our request timed out, but the
            # fill is real — correct the record so the auditor attributes it.
            await self.publish(TOPIC_EXECUTED, {
                "proposal_id": msg.correlation_id, "late_fill": True,
                **msg.payload,
            }, correlation_id=msg.correlation_id)

    async def deliberate(self, proposal):
        corr = proposal.get("proposal_id") or _pid()
        symbol = proposal.get("symbol", "?")
        if self.halted:
            await self._verdict(corr, proposal, {
                "decision": "rejected", "proposal": proposal, "vetoes": [],
                "objections": [], "score": 0.0, "tally": {},
                "reasons": ["desk halted: " + ", ".join(k for k, v in self._halts.items() if v)],
            }, rounds_used=0)
            return

        weights = (await self.work(get_agent_state, "auditor")).get("reviewer_weights", {})
        current = proposal
        verdict = None
        rnd = 0
        total_rounds = max(1, NEGOTIATION_ROUNDS)
        for rnd in range(1, total_rounds + 1):
            replies = await self.bus.gather(
                self.name, list(self.REVIEWERS), TOPIC_REVIEW_REQ,
                {"proposal": current, "round": rnd},
                correlation_id=corr, timeout=REVIEW_TIMEOUT_SECONDS)
            reviews = [m.payload for m in replies.values()]
            # Fail closed: a veto-power reviewer that timed out, crashed, or
            # abstained cannot be counted as consent. Its silence is a veto —
            # the desk would rather miss a trade than skip a safety gate.
            for name in self.REVIEWERS:
                if name not in VETO_POWERS:
                    continue
                reply = replies.get(name)
                if reply is None or reply.payload.get("stance") == STANCE_ABSTAIN:
                    reviews = [r for r in reviews if r.get("reviewer") != name]
                    reviews.append(make_review(
                        name, STANCE_VETO,
                        [f"{name} gave no answer — failing closed"]))
            verdict = merge_reviews(current, reviews, weights=weights,
                                    min_confidence=MIN_CONFIDENCE,
                                    rounds_left=total_rounds - rnd)
            if verdict["decision"] != "revise":
                break
            # Objections on the table — the analyst gets to answer them.
            reply = await self.bus.request(
                self.name, proposal.get("proposed_by", "analyst"), TOPIC_REVISION_REQ,
                {"proposal": verdict["proposal"], "objections": verdict["objections"],
                 "round": rnd},
                correlation_id=corr, timeout=REVIEW_TIMEOUT_SECONDS)
            defense = reply.payload if reply else make_defense(
                DEFENSE_CONCEDE, ["analyst silent — counters accepted"])
            action = defense.get("action", DEFENSE_CONCEDE)
            if action == DEFENSE_WITHDRAW:
                verdict = {**verdict, "decision": "rejected",
                           "reasons": ["analyst withdrew: "
                                       + "; ".join(defense.get("reasons", []))[:200]]}
                break
            if action == DEFENSE_DEFEND:
                boosted = dict(verdict["proposal"])
                boosted["confidence"] = round(
                    min(0.95, boosted.get("confidence", 0)
                        + defense.get("confidence_boost", 0.0)), 4)
                boosted["defense"] = defense.get("reasons", [])
                current = boosted
            else:
                current = defense.get("proposal") or verdict["proposal"]

        await self._verdict(corr, proposal, verdict, rounds_used=rnd)
        if verdict["decision"] == "approved":
            await self._execute(corr, verdict["proposal"])

    async def _verdict(self, corr, original, verdict, rounds_used):
        payload = {
            "proposal_id": corr,
            "symbol": original.get("symbol"),
            "action": original.get("action"),
            "decision": verdict["decision"],
            "proposal": verdict["proposal"],
            "reasons": verdict.get("reasons", []),
            "score": verdict.get("score", 0.0),
            "tally": verdict.get("tally", {}),
            "vetoes": [v["reviewer"] for v in verdict.get("vetoes", [])],
            "rounds": rounds_used,
        }
        await self.publish(TOPIC_VERDICT, payload, correlation_id=corr)
        self.state["deliberations"] = self.state.get("deliberations", 0) + 1
        self.state[verdict["decision"]] = self.state.get(verdict["decision"], 0) + 1
        await self.save_state()
        await self.log(
            f"{payload['symbol']} {payload['action']}: {verdict['decision'].upper()} "
            f"after {rounds_used} round(s), vote {payload['score']:+.2f}"
            + (f", vetoed by {', '.join(payload['vetoes'])}" if payload["vetoes"] else ""))
        # Rolling verdict feed for the dashboard.
        def _write_feed():
            feed = (self.memory.read("decisions", "desk_verdicts") or {}).get("verdicts", [])
            feed.append({k: payload[k] for k in
                         ("proposal_id", "symbol", "action", "decision", "score",
                          "reasons", "rounds", "vetoes")})
            self.memory.write("decisions", "desk_verdicts", {"verdicts": feed[-30:]})
        await self.work(_write_feed)

    async def _execute(self, corr, proposal):
        prep = await self.bus.request(self.name, "execution", TOPIC_EXECUTE,
                                      {"proposal": proposal},
                                      correlation_id=corr, timeout=30)
        if not prep or not prep.payload.get("ok"):
            reasons = (prep.payload.get("reasons") if prep else ["execution timed out"])
            await self.publish(TOPIC_EXECUTED, {
                "proposal_id": corr, "symbol": proposal.get("symbol"),
                "status": "rejected_at_execution", "reasons": reasons,
            }, correlation_id=corr)
            return
        placed = await self.bus.request(self.name, "trader", TOPIC_EXECUTE,
                                        {"order": prep.payload["order"]},
                                        correlation_id=corr, timeout=60)
        result = placed.payload if placed else {"status": "trader_timeout"}
        await self.publish(TOPIC_EXECUTED, {
            "proposal_id": corr, "symbol": proposal.get("symbol"),
            "action": proposal.get("action"), **result,
        }, correlation_id=corr)


# ────────────────────────── the analyst ──────────────────────────
class AsyncAnalyst(AsyncAgent):
    """Scans on its own cadence, proposes with conviction, and answers
    objections — concede, defend, or withdraw. Remembers rejections as
    per-symbol cooldowns so it doesn't re-litigate a lost argument."""

    name = "analyst"
    subscriptions = (TOPIC_VERDICT,)
    tick_interval = _TICK
    tick_delay = 5.0   # let regime/sentiment publish one context first

    def __init__(self, bus, services=None):
        super().__init__(bus, services)
        self._legacy = ResearchAnalyst()
        self.state.setdefault("cooldowns", {})
        self.state.setdefault("conviction", {})

    async def tick(self):
        # The legacy scan already parallelizes symbol analysis and writes
        # market_scan/pricing for the dashboard — reuse it wholesale.
        await self.work(self._legacy.run)
        scan = await self.work(self.memory.read, "analyses", "market_scan") or {}
        opportunities = scan.get("opportunities") or []
        if not opportunities:
            return
        portfolio = await self.work(load_portfolio)
        now = time.time()
        cooldowns = self.state["cooldowns"]
        for key in [k for k, until in cooldowns.items() if until <= now]:
            del cooldowns[key]

        proposed = 0
        for opp in opportunities:
            if proposed >= PROPOSALS_PER_SCAN:
                break
            key = f"{opp['symbol']}:{opp['action']}"
            if cooldowns.get(key, 0) > now:
                continue
            if opp.get("confidence", 0) < MIN_CONFIDENCE:
                continue
            price = opp.get("price") or 0
            if price <= 0:
                continue
            qty = self._proposal_qty(opp.get("action", "BUY"), opp["symbol"],
                                     portfolio, price)
            if qty <= 0:
                continue
            proposal = {
                "proposal_id": _pid(),
                "proposed_by": self.name,
                "symbol": opp["symbol"],
                "action": opp.get("action", "BUY"),
                "price": price,
                "entry_price": opp.get("entry_price") or price,
                "qty": qty,
                "confidence": opp.get("confidence", 0),
                "reasons": opp.get("reasons", [])[:5],
                "strategies": opp.get("strategies", []),
                "regime": opp.get("regime"),
                "multi_timeframe": opp.get("multi_timeframe", False),
                "indicators": opp.get("indicators", {}),
                "sl": opp.get("stop_loss"), "tp": opp.get("take_profit"),
                "sl_pct": opp.get("sl_pct"), "tp_pct": opp.get("tp_pct"),
                "ts": now,
            }
            self.state["conviction"][key] = proposal["confidence"]
            await self.publish(TOPIC_PROPOSAL, proposal,
                               correlation_id=proposal["proposal_id"])
            proposed += 1
        if proposed:
            await self.log(f"Proposed {proposed} trade(s) to the desk")
        await self.save_state()

    @staticmethod
    def _proposal_qty(action, symbol, portfolio, price):
        """BUYs are sized from available cash; SELLs are spot-only closes,
        so they are sized by what we actually hold — never by cash."""
        if action == "SELL":
            held = portfolio.positions.get(symbol)
            if not held or held.quantity <= 0:
                return 0.0
            return round(held.quantity, 8)
        return round(portfolio.cash * (MAX_POSITION_SIZE_PCT / 100) / price, 8)

    async def on_message(self, msg):
        if msg.topic == TOPIC_REVISION_REQ:
            defense = self.decide_defense(msg.payload.get("proposal") or {},
                                          msg.payload.get("objections") or [])
            await self.respond(msg, defense)
            return
        if msg.topic == TOPIC_VERDICT:
            payload = msg.payload
            symbol, action = payload.get("symbol"), payload.get("action")
            if not symbol:
                return
            proposer = (payload.get("proposal") or {}).get("proposed_by")
            if proposer not in (None, self.name):
                return
            key = f"{symbol}:{action}"
            if payload.get("decision") == "rejected":
                reasons = " ".join(payload.get("reasons") or []).lower()
                if "desk halted" in reasons:
                    return   # no argument was lost — re-propose once the halt lifts
                # Don't re-argue a lost case immediately; conviction decays too.
                self.state["cooldowns"][key] = time.time() + PROPOSAL_COOLDOWN_TICKS * _TICK
                self.state["conviction"][key] = max(
                    0.0, self.state["conviction"].get(key, 0) - 0.05)
            await self.save_state()

    def decide_defense(self, proposal, objections):
        """The analyst's side of the argument. Structural objections
        (limits, halts, compliance) are conceded — arguing with a circuit
        breaker is how accounts die. Judgment calls get defended when
        conviction is high, withdrawn when the desk is broadly against."""
        reasons = [r.lower() for o in objections for r in o.get("reasons", [])]
        structural = any(w in r for r in reasons for w in
                         ("halt", "exposure", "spot-only", "cap", "breach",
                          "market closed", "already"))
        rejects = [o for o in objections if o.get("stance") == STANCE_REJECT]
        # Argue from original conviction, not the counters-deflated number:
        # the analyst stakes its own credibility, not the desk's discount.
        key = f"{proposal.get('symbol')}:{proposal.get('action', 'BUY')}"
        conf = max(proposal.get("confidence", 0),
                   self.state.get("conviction", {}).get(key, 0.0))
        if structural:
            return make_defense(DEFENSE_CONCEDE,
                                ["structural objections — accepting the desk's terms"],
                                proposal=proposal)
        if len(rejects) >= 2 and conf < 0.65:
            return make_defense(DEFENSE_WITHDRAW,
                                [f"{len(rejects)} reviewers against at {conf:.2f} "
                                 "conviction — not worth the desk's capital"])
        if conf >= 0.70 and proposal.get("multi_timeframe"):
            return make_defense(DEFENSE_DEFEND,
                                ["multi-timeframe agreement backs this entry",
                                 f"conviction {conf:.2f} from "
                                 + ",".join(proposal.get("strategies", [])[:3])],
                                confidence_boost=0.05)
        return make_defense(DEFENSE_CONCEDE, ["accepting desk adjustments"],
                            proposal=proposal)


# ────────────────────────── reviewers ──────────────────────────
class AsyncRiskManager(ReviewerMixin, AsyncAgent):
    name = "risk_manager"
    tick_interval = _TICK
    tick_delay = 15.0

    def __init__(self, bus, services=None):
        super().__init__(bus, services)
        self._legacy = RiskManager()

    async def tick(self):
        # Keeps the dashboard's risk_assessment report fresh.
        await self.work(self._legacy.run)

    def review(self, proposal, rnd):
        portfolio = load_portfolio()
        pnl = portfolio.total_pnl_pct
        if pnl < -MAX_PORTFOLIO_RISK_PCT:
            self.state["vetoes"] = self.state.get("vetoes", 0) + 1
            return make_review(self.name, STANCE_VETO,
                               [f"portfolio down {pnl:.1f}% — breaches "
                                f"{MAX_PORTFOLIO_RISK_PCT}% risk limit"])
        symbol = proposal.get("symbol")
        price = proposal.get("price") or 0
        qty = proposal.get("qty") or 0
        exposure = portfolio.exposure_pct
        pos = portfolio.positions.get(symbol)
        current_exposure = (pos.current_price * pos.quantity / portfolio.equity * 100
                            ) if pos and portfolio.equity > 0 else 0
        if current_exposure + MAX_POSITION_SIZE_PCT > 100:
            return make_review(self.name, STANCE_REJECT,
                               [f"{symbol} already at {current_exposure:.0f}% — "
                                "no exposure headroom"])
        reasons = []
        mult = 1.0
        if price > 0 and qty > 0:
            headroom = portfolio.equity * max(
                0.0, (MAX_POSITION_SIZE_PCT - current_exposure) / 100)
            max_cost = min(portfolio.cash * (MAX_POSITION_SIZE_PCT / 100), headroom)
            cap_qty = max_cost / price
            if cap_qty < qty:
                mult *= (cap_qty / qty) if qty else 0
                reasons.append(f"size exceeds risk budget — capped at {cap_qty:.6f}")
        if exposure > 80:
            mult *= 0.5
            reasons.append(f"portfolio exposure {exposure:.0f}% critical — halving size")
        elif exposure > 60:
            mult *= 0.75
            reasons.append(f"portfolio exposure {exposure:.0f}% elevated — trimming size")
        if reasons:
            return make_review(self.name, STANCE_COUNTER, reasons, qty_mult=mult)
        return make_review(self.name, STANCE_APPROVE,
                           [f"within risk budget at {exposure:.0f}% exposure"])


class AsyncPositionSizer(ReviewerMixin, AsyncAgent):
    name = "position_sizer"

    def review(self, proposal, rnd):
        kelly = PositionSizer._kelly_fraction()
        # kelly == 0 means "no measurable edge" — floor the size, don't
        # fall through to full size on a falsy value.
        mult = max(0.25, min(kelly / 25.0, 1.0))
        reasons = [f"Kelly fraction {kelly:.1f}% → {mult:.0%} size"]
        vol = (proposal.get("indicators") or {}).get("volatility", 0) or 0
        if vol > 6:
            mult *= 0.50
            reasons.append(f"volatility {vol:.1f}% — halving again")
        elif vol > 4:
            mult *= 0.75
            reasons.append(f"volatility {vol:.1f}% — trimming")
        self.state["last_kelly"] = kelly
        if mult >= 0.999:
            return make_review(self.name, STANCE_APPROVE, ["full Kelly size justified"])
        return make_review(self.name, STANCE_COUNTER, reasons, qty_mult=round(mult, 4))


class AsyncPortfolioManager(ReviewerMixin, AsyncAgent):
    name = "portfolio_manager"

    def review(self, proposal, rnd):
        portfolio = load_portfolio()
        symbol = proposal.get("symbol")
        if proposal.get("action", "BUY") == "BUY" and symbol in portfolio.positions:
            return make_review(self.name, STANCE_REJECT,
                               [f"already holding {symbol} — no doubling up"])
        reasons = []
        mult, conf_delta = 1.0, 0.0
        if portfolio.exposure_pct > 60:
            mult *= 0.5
            reasons.append(f"book {portfolio.exposure_pct:.0f}% deployed — half size only")
        weights = {}
        try:
            from agents.portfolio_manager import PortfolioManagerAgent
            weights = PortfolioManagerAgent._load_strategy_weights()
        except Exception:
            pass
        for strat in proposal.get("strategies", []):
            w = weights.get(strat, 1.0)
            if w < 1.0:
                conf_delta -= (1.0 - w) * 0.2
                reasons.append(f"strategy {strat} has been losing (weight {w:.2f})")
        if reasons:
            return make_review(self.name, STANCE_COUNTER, reasons,
                               qty_mult=mult, confidence_delta=round(conf_delta, 4))
        return make_review(self.name, STANCE_APPROVE, ["fits the current book"])


class AsyncCompliance(ReviewerMixin, AsyncAgent):
    """The desk's hard line: circuit breakers and structural rules.
    Publishes halt state on its own clock; vetoes are non-negotiable."""

    name = "compliance"
    tick_interval = 60
    tick_delay = 1.0

    def __init__(self, bus, services=None):
        super().__init__(bus, services)
        self._pos_mgr = PositionManager()
        self._last_halted = None

    def _halt_check(self):
        blockers = []
        if LEVERAGE_ENABLED:
            blockers.append("leverage enabled — desk is spot-only")
        day_pnl = daily_loss_pct()
        if day_pnl < -DAILY_LOSS_LIMIT_PCT:
            blockers.append(f"daily loss {day_pnl:.2f}% tripped the "
                            f"{DAILY_LOSS_LIMIT_PCT}% circuit breaker")
        portfolio = load_portfolio()
        if portfolio.total_pnl_pct < -MAX_PORTFOLIO_RISK_PCT:
            blockers.append(f"portfolio drawdown {portfolio.total_pnl_pct:.2f}% "
                            "exceeds risk limit")
        recent = fetchall("SELECT pnl FROM trades ORDER BY closed_at DESC LIMIT ?",
                          [MAX_CONSECUTIVE_LOSSES])
        streak = 0
        for row in recent:
            if row["pnl"] < 0:
                streak += 1
            else:
                break
        if streak >= MAX_CONSECUTIVE_LOSSES:
            blockers.append(f"{streak} consecutive losses — cooling off")
        if BROKER_TYPE not in {"paper", "binance", "mt5", "alpaca", "dxtrade"}:
            blockers.append(f"unknown broker type {BROKER_TYPE}")
        return bool(blockers), blockers

    async def tick(self):
        halted, blockers = await self.work(self._halt_check)
        if halted != self._last_halted:
            self._last_halted = halted
            await self.publish(TOPIC_HALT, {"halted": halted, "blockers": blockers,
                                            "source": self.name})
            if halted:
                await self.log("HALT: " + "; ".join(blockers))
        await self.work(self.memory.write, "decisions", "compliance_gate",
                        {"halted": halted, "blockers": blockers,
                         "warnings": [], "approved_opportunities": []})

    def review(self, proposal, rnd):
        halted, blockers = self._halt_check()
        if halted:
            return make_review(self.name, STANCE_VETO, blockers)
        symbol = proposal.get("symbol", "")
        action = proposal.get("action", "BUY")
        if action == "SELL":
            portfolio = load_portfolio()
            held = portfolio.positions.get(symbol)
            if not held or held.quantity <= 0:
                return make_review(self.name, STANCE_VETO,
                                   ["spot-only: SELL without holdings would open a short"])
        if not is_market_open(symbol):
            return make_review(self.name, STANCE_VETO, [f"market closed for {symbol}"])
        opened_today = fetchone(
            "SELECT COUNT(*) AS c FROM positions WHERE opened_at >= date('now')")
        opened_today = opened_today["c"] if opened_today else 0
        if opened_today >= MAX_TRADES_PER_DAY:
            return make_review(self.name, STANCE_VETO,
                               [f"daily trade cap {opened_today}/{MAX_TRADES_PER_DAY} reached"])
        if action == "BUY" and self._pos_mgr.has_position(symbol):
            return make_review(self.name, STANCE_REJECT,
                               [f"position already open for {symbol}"])
        if proposal.get("confidence", 0) < MIN_CONFIDENCE:
            return make_review(self.name, STANCE_REJECT,
                               [f"confidence {proposal.get('confidence', 0):.2f} "
                                f"below the {MIN_CONFIDENCE} floor"])
        if (proposal.get("price") or 0) <= 0 or (proposal.get("qty") or 0) <= 0:
            return make_review(self.name, STANCE_REJECT, ["invalid price or quantity"])
        return make_review(self.name, STANCE_APPROVE, ["clears every compliance gate"])


class AsyncSentiment(ReviewerMixin, AsyncAgent):
    name = "sentiment"
    tick_interval = _TICK
    tick_delay = 10.0

    def __init__(self, bus, services=None):
        super().__init__(bus, services)
        self._legacy = SentimentAgent()

    async def tick(self):
        report = await self.work(self._legacy.run)
        if report:
            await self.publish(TOPIC_CONTEXT, {
                "kind": "sentiment", "market_mood": report.get("market_mood"),
            }, persist=False)

    def review(self, proposal, rnd):
        scan = self.memory.read("analyses", "sentiment_scan") or {}
        sym = (scan.get("symbols") or {}).get(proposal.get("symbol"), {})
        action = proposal.get("action", "BUY")
        if not sym:
            return make_review(self.name, STANCE_APPROVE, ["no sentiment read — abstaining"])
        label, score = sym.get("label", "neutral"), sym.get("score", 50)
        if sym.get("block_buy") and action == "BUY":
            return make_review(self.name, STANCE_REJECT,
                               [f"sharp selloff underway (score {score}) — "
                                "catching knives is not a strategy"])
        aligned = (label == "bullish" and action == "BUY") or \
                  (label == "bearish" and action == "SELL")
        against = (label == "bearish" and action == "BUY") or \
                  (label == "bullish" and action == "SELL")
        if aligned:
            return make_review(self.name, STANCE_APPROVE,
                               [f"sentiment {label} ({score}) agrees with {action}"])
        if against:
            return make_review(self.name, STANCE_COUNTER,
                               [f"sentiment {label} ({score}) argues against {action}"],
                               qty_mult=0.65, confidence_delta=-0.08)
        return make_review(self.name, STANCE_APPROVE, [f"sentiment neutral ({score})"])


class AsyncRegime(ReviewerMixin, AsyncAgent):
    name = "regime"
    tick_interval = _TICK * 2   # regimes move slower than ticks
    tick_delay = 0.0

    def __init__(self, bus, services=None):
        super().__init__(bus, services)
        self._legacy = RegimeAgent()

    async def tick(self):
        report = await self.work(self._legacy.run)
        if report:
            await self.publish(TOPIC_CONTEXT, {
                "kind": "regime", "summary": report.get("summary", {}),
            }, persist=False)

    def review(self, proposal, rnd):
        scan = self.memory.read("analyses", "regime_scan") or {}
        sym = (scan.get("symbols") or {}).get(proposal.get("symbol"), {})
        if not sym:
            return make_review(self.name, STANCE_APPROVE, ["no regime read — abstaining"])
        regime = sym.get("regime", "unknown")
        favored = sym.get("favored_action")
        action = proposal.get("action", "BUY")
        if regime == "volatile":
            return make_review(self.name, STANCE_COUNTER,
                               ["volatile regime — half size or nothing"],
                               qty_mult=0.5, confidence_delta=-0.05)
        if favored and favored != action:
            return make_review(self.name, STANCE_COUNTER,
                               [f"regime {regime} favors {favored}, not {action}"],
                               qty_mult=0.7, confidence_delta=-0.05)
        if favored == action:
            return make_review(self.name, STANCE_APPROVE,
                               [f"regime {regime} favors {action}"],
                               confidence_delta=+0.03)
        return make_review(self.name, STANCE_APPROVE, [f"regime {regime} is workable"])


class AsyncExecution(ReviewerMixin, AsyncAgent):
    """Feasibility reviewer during the argument; order-builder after the
    verdict. Rejecting an approved trade here (spread blew out between
    rounds) is its independent authority."""

    name = "execution"

    def __init__(self, bus, services=None):
        super().__init__(bus, services)
        self._pos_mgr = PositionManager()

    def _market_data(self, symbol):
        scan = self.memory.read("analyses", "market_scan") or {}
        return (scan.get("all_analyses") or {}).get(symbol, {}) or {}

    def _feasibility(self, proposal):
        symbol = proposal.get("symbol")
        data = self._market_data(symbol)
        price = proposal.get("price") or data.get("price") or 0
        bid, ask = data.get("bid") or price, data.get("ask") or price
        spread_pct = ((ask - bid) / price * 100) if price and ask >= bid else 0
        problems = []
        if spread_pct > MAX_SPREAD_PCT:
            problems.append(f"spread {spread_pct:.2f}% exceeds {MAX_SPREAD_PCT}% — "
                            "we'd pay the market maker's rent")
        tp_pct = proposal.get("tp_pct") or 0
        round_trip = 2 * TRADE_FEE_PCT + spread_pct
        # 3x round trip, matching the legacy execution gate: thinner targets
        # feed the fee grinder (36% of gross eaten in live post-mortems).
        min_viable = max(MIN_TP_PCT, round_trip * 3.0)
        if tp_pct and tp_pct < min_viable:
            problems.append(f"TP {tp_pct:.2f}% can't clear costs "
                            f"({round_trip:.2f}% round trip)")
        return problems, spread_pct

    def review(self, proposal, rnd):
        problems, spread = self._feasibility(proposal)
        if problems:
            return make_review(self.name, STANCE_REJECT, problems)
        return make_review(self.name, STANCE_APPROVE,
                           [f"executable at {spread:.3f}% spread"])

    async def on_other_message(self, msg):
        if msg.topic != TOPIC_EXECUTE:
            return
        proposal = msg.payload.get("proposal") or {}
        result = await self.work(self._prepare, proposal)
        await self.respond(msg, result)

    def _held_qty(self, symbol):
        row = fetchone(
            "SELECT quantity FROM positions WHERE symbol=? AND status='open'", [symbol])
        return float(row["quantity"]) if row else 0.0

    def _prepare(self, proposal):
        problems, spread = self._feasibility(proposal)
        if problems:
            return {"ok": False, "reasons": problems}
        symbol = proposal.get("symbol")
        action = proposal.get("action", "BUY")
        qty = proposal.get("qty") or 0
        if action == "SELL":
            # Spot-only backstop, re-checked at the last gate because the
            # position can change mid-deliberation: a SELL may only close an
            # existing long, and it closes all of it — never more, never a
            # fresh short.
            held = self._held_qty(symbol)
            if held <= 0:
                return {"ok": False,
                        "reasons": ["spot-only: no holdings to sell — a short is not allowed"]}
            qty = round(held, 8)
        elif self._pos_mgr.has_position(symbol):
            return {"ok": False, "reasons": ["position opened mid-deliberation"]}
        # Plan geometry uses the pricing engine's entry target — the SL/TP
        # were computed off it, not off the last trade print.
        price = proposal.get("entry_price") or proposal.get("price") or 0
        sl, tp = proposal.get("sl") or 0, proposal.get("tp") or 0
        # Risk-cap the negotiated size of opening trades: RISK_PER_TRADE_PCT
        # of equity at stake. Closing a position always reduces risk.
        if action == "BUY" and sl and price:
            risk_per_unit = abs(price - sl)
            if risk_per_unit > 0:
                risk_qty = (load_portfolio().equity * RISK_PER_TRADE_PCT / 100) / risk_per_unit
                qty = min(qty, round(risk_qty, 8))
        # GARCH vol throttle (BUYs only — a SELL is a full close): size down in
        # high-vol regimes, never up. Off by default; fails safe to 1.0x.
        if action == "BUY" and VOL_THROTTLE_ENABLED and qty > 0:
            from core.vol_forecast import vol_throttle
            thr = vol_throttle(symbol, VOL_THROTTLE_TARGET_VOL, VOL_THROTTLE_FLOOR)
            if thr < 1.0:
                qty = round(qty * thr, 8)
        if qty <= 0:
            return {"ok": False, "reasons": ["risk cap reduced size to zero"]}
        from datetime import datetime, timezone
        plan_id = f"plan_{symbol}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S%f')}"
        sl_pct, tp_pct = proposal.get("sl_pct") or 0, proposal.get("tp_pct") or 0
        save_plan({
            "plan_id": plan_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "symbol": symbol, "direction": proposal.get("action", "BUY"),
            "entry_price": price, "stop_loss": sl, "take_profit": tp,
            "position_size_usd": round(price * qty, 2), "position_size_units": qty,
            "confidence": proposal.get("confidence", 0),
            "strategy": (proposal.get("strategies") or [""])[0],
            "regime": proposal.get("regime", "") or "",
            "rationale": ", ".join(proposal.get("reasons", [])[:3]),
            "risk_reward_ratio": round(tp_pct / sl_pct, 2) if sl_pct else 0,
            "status": "created",
        })
        return {"ok": True, "order": {
            "plan_id": plan_id, "symbol": symbol,
            "action": proposal.get("action", "BUY"),
            "qty": qty, "price": price, "sl": sl, "tp": tp,
            "strategy": (proposal.get("strategies") or [""])[0],
        }}


class AsyncTrader(AsyncAgent):
    """Places what the desk approved — nothing else reaches it."""

    name = "trader"
    MAX_PRICE_DRIFT_PCT = 1.5

    def __init__(self, bus, services=None):
        super().__init__(bus, services)
        self._pos_mgr = PositionManager()
        self._broker = None   # built lazily; broker ctors can hit the network

    def _get_broker(self):
        if self._broker is None:
            from agents.trader import Trader
            self._broker = Trader().broker
        return self._broker

    async def on_message(self, msg):
        if msg.topic != TOPIC_EXECUTE:
            return
        order = msg.payload.get("order") or {}
        result = await self.work(self._place, order)
        self.state["orders_placed"] = self.state.get("orders_placed", 0) + (
            1 if result.get("status") == "filled" else 0)
        await self.respond(msg, result)

    def _place(self, order):
        symbol, action = order.get("symbol"), order.get("action", "BUY")
        qty, price = order.get("qty", 0), order.get("price", 0)
        sl, tp = order.get("sl", 0), order.get("tp", 0)
        if qty <= 0 or price <= 0:
            return {"status": "rejected", "reasons": ["invalid order"]}
        open_row = fetchone(
            "SELECT id, quantity FROM positions WHERE symbol=? AND status='open'", [symbol])
        if action == "SELL":
            # Spot-only, enforced at the very last gate: a SELL with nothing
            # to close would be a short — dead on arrival regardless of what
            # the deliberation concluded. A SELL that does close, closes the
            # whole holding.
            if not open_row or open_row["quantity"] <= 0:
                return {"status": "rejected",
                        "reasons": ["spot-only: no holdings to sell — a short is not allowed"]}
            qty = float(open_row["quantity"])
        elif open_row:
            return {"status": "rejected", "reasons": ["position already open"]}
        scan = self.memory.read("analyses", "market_scan") or {}
        market_price = ((scan.get("all_analyses") or {}).get(symbol) or {}).get("price") or price
        drift = abs(market_price - price) / price * 100
        if drift > self.MAX_PRICE_DRIFT_PCT:
            return {"status": "rejected",
                    "reasons": [f"market moved {drift:.2f}% since the desk approved"]}
        broker = self._get_broker()
        placed = broker.place_order(symbol, action, qty, market_price, sl=sl, tp=tp)
        if placed.get("status") == "filled":
            fill_price = placed.get("price") or market_price
            fill_qty = placed.get("quantity") or qty
            if order.get("plan_id"):
                update_plan_status(order["plan_id"], "executed")
            if action == "SELL":
                self._pos_mgr.close_position(open_row["id"], fill_price, reason="desk_exit")
            else:
                self._pos_mgr.open_position(symbol, action, fill_qty, fill_price,
                                            sl=sl, tp=tp, strategy=order.get("strategy", ""))
            self.notifier.on_trade({
                "symbol": symbol, "side": action, "qty": fill_qty,
                "price": fill_price, "stop_loss": sl, "take_profit": tp,
                "status": "filled",
            })
            self.memory.log(self.name,
                            f"{action} {fill_qty} {symbol} @ ${fill_price:.5f} (desk-approved)")
            return {"status": "filled", "price": fill_price, "qty": fill_qty}
        return {"status": placed.get("status", "rejected"),
                "reasons": [placed.get("reason", "broker rejected")]}


class AsyncAuditor(AsyncAgent):
    """The desk's memory. Archives every deliberation, then scores the
    reviewers against what the market actually did: oppose a loser or back
    a winner and your vote counts for more next time."""

    name = "auditor"
    subscriptions = (TOPIC_VERDICT, TOPIC_EXECUTED)
    tick_interval = _TICK
    tick_delay = 30.0

    WEIGHT_STEP = 0.05
    WEIGHT_MIN, WEIGHT_MAX = 0.5, 1.5
    DECISION_TTL = 7 * 24 * 3600

    def __init__(self, bus, services=None):
        super().__init__(bus, services)
        self._legacy = Auditor()
        self.state.setdefault("reviewer_weights", {})
        self.state.setdefault("decisions", {})
        self.state.setdefault("last_attribution_id", 0)

    async def on_message(self, msg):
        pid = msg.payload.get("proposal_id")
        if not pid:
            return
        if msg.topic == TOPIC_VERDICT:
            self.state["decisions"][pid] = {
                "symbol": msg.payload.get("symbol"),
                "action": msg.payload.get("action"),
                "decision": msg.payload.get("decision"),
                "stances": {k: v.get("stance") for k, v in
                            (msg.payload.get("tally") or {}).items()},
                "ts": time.time(),
                "executed": False,
            }
        elif msg.topic == TOPIC_EXECUTED:
            d = self.state["decisions"].get(pid)
            if d is not None:
                d["executed"] = msg.payload.get("status") == "filled"
        await self.save_state()

    async def tick(self):
        await self.work(self._legacy.run)   # audit report + strategy stats
        await self.work(self._attribute_outcomes)
        await self.save_state()

    def _attribute_outcomes(self):
        # Cursor by rowid, not timestamp: closed_at has second resolution,
        # so two trades closing in the same second would otherwise leave
        # one of them permanently unattributed behind a strict '>' comparison.
        last_id = int(self.state.get("last_attribution_id") or 0)
        closed = fetchall(
            "SELECT id, symbol, side, pnl, opened_at, closed_at FROM trades "
            "WHERE id > ? ORDER BY id ASC LIMIT 100", [last_id])
        weights = self.state["reviewer_weights"]
        now = time.time()
        for row in closed:
            trade = dict(row)
            self.state["last_attribution_id"] = trade["id"]
            won = (trade["pnl"] or 0) > 0
            match = None
            for pid, d in self.state["decisions"].items():
                if (d.get("executed") and not d.get("attributed")
                        and d.get("symbol") == trade["symbol"]
                        and d.get("action") == trade["side"]):
                    match = d
                    break
            if not match:
                continue
            match["attributed"] = True
            match["won"] = won
            for reviewer, stance in (match.get("stances") or {}).items():
                backed = stance in (STANCE_APPROVE, STANCE_COUNTER)
                right = (backed and won) or (not backed and not won)
                w = weights.get(reviewer, 1.0) + (self.WEIGHT_STEP if right
                                                  else -self.WEIGHT_STEP)
                weights[reviewer] = round(min(self.WEIGHT_MAX,
                                              max(self.WEIGHT_MIN, w)), 3)
        # Old arguments stop mattering.
        self.state["decisions"] = {
            pid: d for pid, d in self.state["decisions"].items()
            if now - d.get("ts", 0) < self.DECISION_TTL}


class AsyncHealthMonitor(AsyncAgent):
    """Watchdog over real heartbeats — not log archaeology. Can halt the
    desk, and the halt lifts the moment the desk is healthy again."""

    name = "health"
    tick_interval = 60
    tick_delay = 45.0

    def __init__(self, bus, services=None):
        super().__init__(bus, services)
        self._last_halted = None

    async def tick(self):
        issues, warnings = [], []
        runtime = self.services.get("runtime")
        stale_after = max(_TICK * 3, 180.0)
        if runtime is not None:
            now = time.time()
            for name in runtime.dead_agents():
                issues.append(f"agent {name} actor task has died")
            for name, beat in runtime.heartbeats().items():
                agent = runtime.agent(name)
                interval = getattr(agent, "tick_interval", None) if agent else None
                if interval is None:
                    # Message-driven agents (chair, sizer, execution, trader…)
                    # legitimately idle between deliberations — silence is
                    # not sickness, only a dead task is.
                    continue
                # Each agent is judged against its own cadence: the optimizer
                # ticking every 2h must not read as "silent" to a 3-tick rule.
                threshold = max(3 * interval, stale_after) + getattr(agent, "tick_delay", 0.0)
                if now - beat > threshold:
                    issues.append(f"agent {name} silent for {int(now - beat)}s")
        errors = await self.work(self.memory.get_recent_errors, 10)
        recent = [e for e in errors if time.time() - e.get("time", 0) < stale_after]
        if len(recent) > 5:
            issues.append(f"{len(recent)} errors in the last {stale_after}s")
        elif len(recent) > 2:
            warnings.append(f"{len(recent)} recent errors")
        try:
            from core import websocket_prices
            if not websocket_prices.get_all_prices():
                warnings.append("websocket price feed empty")
        except Exception:
            pass
        halted = bool(issues)
        if halted != self._last_halted:
            self._last_halted = halted
            await self.publish(TOPIC_HALT, {"halted": halted, "blockers": issues,
                                            "source": self.name})
        status = "halted" if halted else ("warning" if warnings else "ok")
        await self.work(self.memory.write, "reports", "health", {
            "status": status, "halted": halted, "issues": issues,
            "warnings": warnings, "errors_last_cycle": len(recent),
            "agents_active": len(runtime.heartbeats()) if runtime else 0,
            "price_feed_alive": "websocket price feed empty" not in warnings,
        })
        if status != "ok":
            await self.log(f"Health: {status} — {'; '.join(issues + warnings)}")


class AsyncOptimizer(AsyncAgent):
    name = "optimizer"
    tick_interval = 7200
    tick_delay = 600.0   # let the desk trade before tuning it

    def __init__(self, bus, services=None):
        super().__init__(bus, services)
        self._legacy = OptimizerAgent()

    async def tick(self):
        from config import OPTIMIZER_ENABLED
        if not OPTIMIZER_ENABLED:
            return   # same rationale as main: don't tune live risk on noise
        await self.work(self._legacy.run)
        await self.publish(TOPIC_TUNING, {"checked_at": time.time()}, persist=False)


DESK_AGENTS = (
    DeskChair,
    AsyncAnalyst,
    AsyncRiskManager,
    AsyncPositionSizer,
    AsyncPortfolioManager,
    AsyncCompliance,
    AsyncSentiment,
    AsyncRegime,
    AsyncExecution,
    AsyncTrader,
    AsyncAuditor,
    AsyncHealthMonitor,
    AsyncOptimizer,
)

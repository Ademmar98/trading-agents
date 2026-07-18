import json
import re
import time

import requests

from config import (
    HERMES_API_KEY, HERMES_API_URL, HERMES_MODEL, HERMES_FALLBACK_MODEL,
    DEBATE_ENABLED, DEBATE_TOP_N, DEBATE_DOWNGRADE_MULT, DEBATE_TIMEOUT_SEC,
    DEBATE_SKIP_WHEN_CAPPED,
    TRADE_FEE_PCT,
)
from agents.base_agent import BaseAgent

BULL_SYSTEM = """You are the BULL analyst in an adversarial trade debate at a crypto spot trading firm.
Argue the strongest possible case FOR taking the candidate trade below.

Rules:
- Use ONLY the evidence pack provided. 4-6 lines maximum. No preamble.
- Cite specifics: the strategy rationale, regime alignment, sentiment,
  per-strategy track record (trades / win rate / net PnL), and R:R geometry.
- You must argue FOR the trade even when the evidence is mixed — the bear
  and the arbiter handle the other side.
"""

BEAR_SYSTEM = """You are the BEAR analyst in an adversarial trade debate at a crypto spot trading firm.
Argue the strongest possible case AGAINST taking the candidate trade below.

Rules:
- Use ONLY the evidence pack provided. 4-6 lines maximum. No preamble.
- Attack the weak points: round-trip fee burden vs the TP distance, tiny or
  negative per-strategy samples, regime mismatch, correlation/beta against
  the open book, crowded signals, and any number that contradicts another.
- You must argue AGAINST the trade even when the evidence is mixed — the
  bull and the arbiter handle the other side.
"""

ARBITER_SYSTEM = """You are the ARBITER in an adversarial trade debate at a crypto spot trading firm.
A bull and a bear argued over one candidate trade. Judge which case is stronger.

Rules:
- Your power is strictly bounded: you may NEVER create a trade, NEVER raise
  confidence, and NEVER widen size. APPROVE passes the trade through
  unchanged; DOWNGRADE multiplies its confidence by 0.85; REJECT removes it
  from the plan.
- Bias conservative: a balanced debate resolves DOWNGRADE; a decisive bear
  case resolves REJECT; APPROVE only when the bull case clearly dominates.
- Reply with 1-3 lines of judgment, then EXACTLY one line of JSON:
  {"verdict": "APPROVE"|"DOWNGRADE"|"REJECT", "rationale": "one line"}
"""

VERDICTS = ("APPROVE", "DOWNGRADE", "REJECT")


class DebateAgent(BaseAgent):
    """Adversarial bull/bear debate over the top portfolio candidates.

    Runs after PortfolioManager and before Compliance. Each cycle the top
    DEBATE_TOP_N candidates by confidence are debated: one LLM call argues
    FOR (bull), one argues AGAINST (bear), and an arbiter call returns a
    bounded verdict — APPROVE (pass through unchanged), DOWNGRADE
    (confidence x DEBATE_DOWNGRADE_MULT), REJECT (removed from the plan).

    The arbiter can never create a trade, raise confidence, or widen size,
    and every survivor still faces the unchanged downstream compliance and
    execution gates. Any LLM failure, parse failure, or timeout fails OPEN:
    the candidate passes through untouched. DEBATE_ENABLED=false makes the
    agent a pure no-op pass-through.
    """

    name = "debate"

    def run(self):
        plan = self.memory.read("decisions", "portfolio_plan") or {}
        opportunities = plan.get("approved_opportunities") or []

        # No-op pass-throughs — zero LLM calls in every case.
        if not DEBATE_ENABLED:
            return plan
        if not opportunities:
            return plan
        if not HERMES_API_KEY:
            self.log("Debate skipped — no HERMES_API_KEY; plan passes through unchanged")
            return plan

        # Zero-deployment cap: when the SMA200 dial has the firm at 0% (full
        # cash, scout mode off), compliance will reject every candidate —
        # debating them is wasted LLM spend and a full timeout stall.
        if DEBATE_SKIP_WHEN_CAPPED:
            regime_scan = self.memory.read("analyses", "regime_scan") or {}
            if (regime_scan.get("deployment_target") is not None
                    and regime_scan.get("deployment_target") <= 0):
                self.log("Debate skipped — firm at 0% deployment (risk_off), "
                         "compliance will reject all candidates anyway")
                return plan

        # Debate only candidates that could actually trade (risk_ok) — blocked
        # ones are compliance's job, not worth an argument.
        candidates = [o for o in opportunities
                      if o.get("risk_ok") and (o.get("confidence") or 0) > 0]
        candidates.sort(key=lambda o: o.get("confidence", 0), reverse=True)
        top = candidates[:max(0, DEBATE_TOP_N)]
        if not top:
            return plan

        started = time.time()
        deadline = started + DEBATE_TIMEOUT_SEC
        records = [self._debate_candidate(c, deadline) for c in top]
        plan = self._apply_verdicts(plan, opportunities, records)

        report = {
            "debates": records,
            "candidates_debated": len(records),
            "verdicts": {v: sum(1 for r in records if r["verdict"] == v)
                         for v in VERDICTS},
            "duration_sec": round(time.time() - started, 2),
            "timestamp": time.time(),
        }
        self.memory.write("reports", "debate", report)
        self._append_jsonl(records)
        counts = ", ".join(f"{v}:{report['verdicts'][v]}" for v in VERDICTS)
        self.log(f"Debate round complete: {len(records)} debated ({counts}) "
                 f"in {report['duration_sec']}s")
        return plan

    # ── One candidate, three rounds ──

    def _debate_candidate(self, cand, deadline):
        symbol = cand.get("symbol", "?")
        orig_conf = cand.get("confidence", 0) or 0
        record = {
            "symbol": symbol,
            "action": cand.get("action", "BUY"),
            "strategies": cand.get("strategies") or [],
            "confidence_before": orig_conf,
            "confidence_after": orig_conf,
            "bull_argument": "",
            "bear_argument": "",
            "verdict": "APPROVE",
            "arbiter_rationale": "",
            "model": None,
            "timestamp": time.time(),
        }
        if time.time() >= deadline:
            record["arbiter_rationale"] = (
                "debate time budget exhausted — fail-open pass-through")
            self._journal(record)
            return record

        ev = self._evidence_pack(cand)
        evidence_json = json.dumps(ev, default=str)
        try:
            bull, model = self._llm(
                BULL_SYSTEM,
                "Candidate trade and evidence pack (JSON):\n" + evidence_json
                + "\n\nMake the strongest case FOR this trade now.",
                deadline)
            record["bull_argument"] = bull.strip()
            record["model"] = model

            bear, model = self._llm(
                BEAR_SYSTEM,
                "Candidate trade and evidence pack (JSON):\n" + evidence_json
                + "\n\nMake the strongest case AGAINST this trade now.",
                deadline)
            record["bear_argument"] = bear.strip()
            record["model"] = model

            arbiter_msg = (
                f"Candidate: {record['action']} {symbol} | "
                f"confidence {orig_conf:.2f} | "
                f"strategies: {', '.join(record['strategies']) or 'n/a'}\n"
                f"Evidence pack (JSON):\n{evidence_json}\n\n"
                f"BULL CASE:\n{record['bull_argument']}\n\n"
                f"BEAR CASE:\n{record['bear_argument']}\n\n"
                "Judge now: 1-3 lines, then the single-line verdict JSON."
            )
            raw, model = self._llm(ARBITER_SYSTEM, arbiter_msg, deadline)
            record["model"] = model
            verdict, rationale = self._parse_verdict(raw)
            record["verdict"] = verdict
            record["arbiter_rationale"] = rationale
        except Exception as e:
            # LLM debate unavailable → fall back to the deterministic engine
            # so the firm still has a real "brain" judging every candidate
            # instead of a blind fail-open. Only if the engine itself breaks
            # do we pass through untouched.
            self.log(f"LLM debate unavailable for {symbol} "
                     f"({type(e).__name__}: {e}) — deterministic engine")
            try:
                self._deterministic_debate(cand, ev, record)
            except Exception as e2:
                record["verdict"] = "APPROVE"
                record["arbiter_rationale"] = (
                    f"debate unavailable ({type(e2).__name__}: {e2}) "
                    "— fail-open pass-through")

        if record["verdict"] == "DOWNGRADE":
            # The fixed multiplier is the ONLY confidence move the arbiter
            # gets, and the min() clamp means confidence can never rise —
            # even against a misconfigured DEBATE_DOWNGRADE_MULT > 1 or a
            # malicious arbiter payload demanding a pump.
            record["confidence_after"] = round(
                min(orig_conf * DEBATE_DOWNGRADE_MULT, orig_conf), 4)
        self._journal(record)
        return record

    # ── Verdict application (bounded power) ──

    def _apply_verdicts(self, plan, opportunities, records):
        by_symbol = {}
        for r in records:
            by_symbol.setdefault(r["symbol"], r)
        new_opps = []
        changed = False
        for opp in opportunities:
            rec = by_symbol.get(opp.get("symbol"))
            if rec is None:
                new_opps.append(opp)
                continue
            if rec["verdict"] == "REJECT":
                changed = True
                continue  # removed from the plan
            if rec["verdict"] == "DOWNGRADE":
                orig = opp.get("confidence", 0) or 0
                # Never-raise clamp: confidence can only move DOWN, size and
                # every other field stay untouched.
                opp = {**opp, "confidence": round(
                    min(orig * DEBATE_DOWNGRADE_MULT, orig), 4)}
                changed = True
            new_opps.append(opp)
        if changed:
            plan = {**plan, "approved_opportunities": new_opps}
            self.memory.write("decisions", "portfolio_plan", plan)
        return plan

    # ── LLM call (same Hermes/OpenRouter pattern as HeadTrader) ──

    # Process-level flag: once the primary model answers with a credits /
    # balance error it will keep failing all cycle (and every cycle). Stop
    # spending timeout budget on it and go straight to the fallback model.
    _primary_credits_dead = False

    def _llm(self, system_prompt, user_msg, deadline):
        errors = []
        models = (HERMES_MODEL, HERMES_FALLBACK_MODEL)
        for model in models:
            if not model:
                continue
            if (model == HERMES_MODEL and type(self)._primary_credits_dead):
                continue
            remaining = deadline - time.time()
            if remaining <= 0:
                raise TimeoutError("debate time budget exhausted")
            r = requests.post(
                HERMES_API_URL,
                headers={"Authorization": f"Bearer {HERMES_API_KEY}",
                         "Content-Type": "application/json"},
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_msg},
                    ],
                    "max_tokens": 2000,
                    "temperature": 0.3,
                    # The free fallback (stepfun) is a reasoning model: without
                    # this it burns the whole token budget on hidden reasoning
                    # and returns content=null. Excluding reasoning makes it
                    # fast, cheap, and reliable for structured debate calls.
                    "reasoning": {"exclude": True},
                },
                # Per-request timeout shrinks to fit the cycle's total
                # DEBATE_TIMEOUT_SEC wall-clock budget.
                timeout=max(1.0, min(30.0, remaining)),
            )
            data = r.json()
            content = (data.get("choices") or [{}])[0].get("message", {}).get("content")
            if content:
                return content, model
            err_msg = str(data.get("message") or data)[:150]
            errors.append(f"{model}: {err_msg}")
            if (model == HERMES_MODEL
                    and ("credit" in err_msg.lower() or "balance" in err_msg.lower())):
                type(self)._primary_credits_dead = True
                self.log(f"Primary model {model} out of credits — "
                         "skipping it for the rest of this process")
        raise RuntimeError("; ".join(errors) or "no model produced a response")

    @staticmethod
    def _parse_verdict(raw):
        """Extract {"verdict": ..., "rationale": ...} from the arbiter reply.
        Strict: anything unreadable resolves to APPROVE (fail-open)."""
        for block in reversed(re.findall(r"\{[^{}]*\}", raw or "")):
            try:
                parsed = json.loads(block)
            except (ValueError, TypeError):
                continue
            if not isinstance(parsed, dict):
                continue
            verdict = str(parsed.get("verdict", "")).strip().upper()
            if verdict in VERDICTS:
                rationale = str(parsed.get("rationale", "")).strip()[:200]
                return verdict, rationale or "(no rationale given)"
        return "APPROVE", "arbiter reply unparseable — fail-open pass-through"

    # ── Deterministic debate engine (zero-LLM fallback brain) ──
    #
    # Judges a candidate from the same evidence pack the LLM sees: price
    # geometry vs. fees, live per-strategy track record, regime and
    # sentiment. Produces real bull/bear text and a verdict so the journal
    # shows an actual decision process even with no LLM access at all.

    def _deterministic_debate(self, cand, ev, record):
        record["model"] = "deterministic-engine"
        action = record["action"]
        symbol = record["symbol"]

        entry = ev.get("price") or 0
        sl = ev.get("stop_loss") or 0
        tp = ev.get("take_profit") or 0
        sl_pct = tp_pct = None
        if entry and sl:
            sl_pct = abs(entry - sl) / entry * 100
        if entry and tp:
            tp_pct = abs(tp - entry) / entry * 100
        rr = ev.get("risk_reward")
        if rr is None and sl_pct:
            rr = round((tp_pct or 0) / sl_pct, 2)

        stats = ev.get("strategy_stats") or {}
        n_trades = sum(s.get("trades") or 0 for s in stats.values())
        pnl = sum(s.get("pnl") or 0 for s in stats.values())
        wins = sum((s.get("win_rate") or 0) * (s.get("trades") or 0)
                   for s in stats.values())
        win_rate = wins / n_trades if n_trades else None

        cost_rt = (ev.get("round_trip_fee_pct") or 0) + 0.2  # fees + slippage
        fee_burden = (cost_rt / tp_pct) if tp_pct else None

        regime = (ev.get("regime") or {}).get("regime")
        sent = ev.get("sentiment") or {}
        conf = record["confidence_before"] or 0

        strats = ", ".join(record["strategies"]) or "n/a"
        stats_txt = (f"{n_trades} live trades, win-rate {win_rate:.0%}, "
                     f"PnL ${pnl:+.2f}" if n_trades
                     else "no live track record yet")
        rr_txt = f"R:R {rr:.2f}" if rr is not None else "R:R unknown"

        record["bull_argument"] = (
            f"{strats} signal(s) aligned on {symbol} ({rr_txt}; {stats_txt}; "
            f"regime {regime or 'unknown'}); entry geometry "
            + (f"targets {tp_pct:.2f}% vs ~{cost_rt:.2f}% round-trip cost"
               if tp_pct else "not measurable"))
        record["bear_argument"] = (
            (f"Round-trip cost ~{cost_rt:.2f}% eats "
             f"{fee_burden:.0%} of the {tp_pct:.2f}% target; " if fee_burden
             else "Fee impact unmeasurable; ")
            + f"live evidence thin ({stats_txt}); "
            + (f"regime {regime} " if regime else "regime unknown ")
            + ("and sentiment blocks longs" if sent.get("block_buy")
               else f"sentiment {sent.get('label', 'neutral')}"))

        if rr is None and tp_pct is None:
            record["verdict"] = "APPROVE"
            record["arbiter_rationale"] = (
                "no geometry to judge — deterministic pass-through")
            return
        if ((rr is not None and rr < 1.2)
                or (fee_burden is not None and fee_burden > 0.5)
                or (n_trades >= 10 and pnl < 0)):
            record["verdict"] = "REJECT"
            record["arbiter_rationale"] = (
                f"deterministic reject: {rr_txt}"
                + (f", fees eat {fee_burden:.0%} of target" if fee_burden
                   is not None else "")
                + (f", live PnL ${pnl:+.2f} over {n_trades} trades"
                   if n_trades else ""))
            return
        if (n_trades < 10
                or (fee_burden is not None and fee_burden > 1 / 3)
                or regime == "volatile"
                or (action == "BUY" and (sent.get("block_buy")
                                         or sent.get("label") == "risk_off"))
                or conf < 0.65):
            record["verdict"] = "DOWNGRADE"
            record["arbiter_rationale"] = (
                f"deterministic downgrade: thin/adverse evidence "
                f"({stats_txt}; regime {regime or 'unknown'}; {rr_txt})")
            return
        record["verdict"] = "APPROVE"
        record["arbiter_rationale"] = (
            f"deterministic approve: {rr_txt}, {stats_txt}, "
            f"fee burden {(fee_burden or 0):.0%} acceptable")



    def _evidence_pack(self, cand):
        symbol = cand.get("symbol", "")
        sentiment = self.memory.read("analyses", "sentiment_scan") or {}
        regimes = self.memory.read("analyses", "regime_scan") or {}
        sent = (sentiment.get("symbols", {}) or {}).get(symbol, {})
        reg = (regimes.get("symbols", {}) or {}).get(symbol, {})

        entry = cand.get("entry_price") or cand.get("price") or 0
        sl = cand.get("stop_loss") or 0
        tp = cand.get("take_profit") or 0
        rr = None
        if entry and sl and tp and entry != sl:
            rr = round(abs(tp - entry) / abs(entry - sl), 2)
        elif cand.get("sl_pct"):
            rr = (round((cand.get("tp_pct") or 0) / cand["sl_pct"], 2)
                  if cand["sl_pct"] else None)

        # Recent per-strategy live stats from the DB — enrichment only; the
        # debate must work (and fail open) even with an empty stats table.
        stats = {}
        try:
            from core.database import fetchall
            wanted = set(cand.get("strategies") or [])
            for row in fetchall(
                    "SELECT strategy, trades, win_rate, pnl, avg_pnl "
                    "FROM strategy_stats"):
                if row["strategy"] in wanted:
                    stats[row["strategy"]] = {
                        "trades": row["trades"], "win_rate": row["win_rate"],
                        "pnl": row["pnl"], "avg_pnl": row["avg_pnl"],
                    }
        except Exception:
            pass

        return {
            "symbol": symbol,
            "action": cand.get("action", "BUY"),
            "confidence": cand.get("confidence", 0),
            "price": entry,
            "max_qty": cand.get("max_qty", 0),
            "stop_loss": sl or None,
            "take_profit": tp or None,
            "risk_reward": rr,
            "strategies": cand.get("strategies") or [],
            "signal_reasons": (cand.get("reasons") or [])[:5],
            "portfolio_notes": cand.get("portfolio_notes") or [],
            "regime": {k: reg.get(k) for k in (
                "regime", "favored_action",
                "confidence_multiplier", "size_multiplier") if k in reg},
            "sentiment": {k: sent.get(k) for k in (
                "score", "label", "block_buy",
                "confidence_multiplier", "size_multiplier") if k in sent},
            "strategy_stats": stats,
            "round_trip_fee_pct": round(2 * TRADE_FEE_PCT, 4),
        }

    # ── Visibility ──

    def _journal(self, record):
        self.log(
            f"Debate {record['action']} {record['symbol']}: {record['verdict']} "
            f"(conf {record['confidence_before']:.2f}→"
            f"{record['confidence_after']:.2f}) — "
            f"{record['arbiter_rationale'][:90]}")

    def _append_jsonl(self, records):
        try:
            log_file = self.memory.dirs["logs"] / "debate_log.jsonl"
            with open(log_file, "a") as f:
                for r in records:
                    f.write(json.dumps(r, default=str) + "\n")
        except Exception as e:
            self.log(f"debate_log append failed: {e}")

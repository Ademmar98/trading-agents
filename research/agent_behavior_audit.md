# Agent Behavior Audit — Multi-Agent Interaction Model

Repo: `C:/Users/DELL/OneDrive/1m` — crypto spot paper-trading "firm" of 14 agents.
Method: static code reading only (no web research). Every claim cites `file:line`.

---

## 0. How the cycle actually runs (the ground truth)

`main.py` runs a **strictly sequential, synchronous pipeline**, not a living agent fabric:

- `CYCLE_AGENTS` is a fixed tuple of 14 classes in hardcoded order: Orchestrator → HealthMonitor → SentimentAgent → NewsAgent → RegimeAgent → ResearchAnalyst → RiskManager → PositionSizer → PortfolioManagerAgent → ComplianceAgent → ExecutionAgent → Trader → Auditor → HeadTrader (`main.py:190-205`).
- Each cycle does `for agent_cls in CYCLE_AGENTS: agent_cls().run()` — a **fresh instance per agent per cycle**, called synchronously, one after another (`main.py:260-261`).
- `run_cycle()` is invoked from a plain `threading.Thread` loop with `time.sleep` between cycles (`main.py:449-455`). No `asyncio` loop, no `bus.register_agent()`, no agent tasks exist at runtime.

The async machinery in `core/agent_bus.py` (pub/sub, request/reply, agent lifecycle, `register_agent` at `core/agent_bus.py:136-140`) and the async `_run_loop`/`run_cycle` in `agents/base_agent.py:40-111` is **dead code in production**: every concrete agent overrides the synchronous `run()` (e.g. `agents/orchestrator.py:14`, `agents/analyst.py:79`), which shadows `BaseAgent.run()` entirely. A repo-wide grep confirms no production agent ever calls `bus.subscribe`, `bus.request`, `bus.publish` (outside `base_agent.py`'s unused lifecycle hooks at `agents/base_agent.py:64-88`), or `register_reply_handler`. The bus docstring's "living, concurrent agent fabric" (`core/agent_bus.py:1-12`) describes an architecture that is not wired in.

---

## 1. Do agents communicate peer-to-peer or only via shared JSON state?

**Only via shared JSON state.** There is zero peer-to-peer messaging at runtime.

- All inter-agent data flows through `SharedMemory`, which writes/reads flat JSON files under `DATA_DIR/{analyses,decisions,orders,reports,logs}` (`core/memory.py:11-17`, write at `core/memory.py:21-26`, read at `core/memory.py:28-32`).
- The handoff chain is filename-based and one-directional:
  - Analyst writes `analyses/market_scan` + `decisions/pricing` (`agents/analyst.py:345-352`)
  - RiskManager reads `analyses/market_scan`, writes `decisions/risk_assessment` (`agents/risk_manager.py:16, 83`)
  - PositionSizer reads `decisions/risk_assessment`, writes `decisions/position_sizing` (`agents/position_sizer.py:12, 51`)
  - PortfolioManager reads `decisions/position_sizing`, writes `decisions/portfolio_plan` (`agents/portfolio_manager.py:15, 84`)
  - Compliance reads `decisions/portfolio_plan`, writes `decisions/compliance_gate` (`agents/compliance_agent.py:44, 227`)
  - Execution reads `decisions/compliance_gate`, writes `orders/execution_plan` (`agents/execution_agent.py:29, 241`)
  - Trader reads `orders/execution_plan`, writes `orders/trade_log` (`agents/trader.py:30, 123`)
- The Orchestrator's "instructions" (`decisions/instructions`, `agents/orchestrator.py:51-54`) are **read by no agent** (repo-wide grep finds only tests referencing them). Its `orchestrator_plan` (`agents/orchestrator.py:35`) is likewise write-only. The Orchestrator is a figurehead: it cannot skip, reorder, or parameterize any agent — `main.py:260-261` runs everyone unconditionally.
- The only non-file side channel is Telegram notifications (`agents/base_agent.py:27`, `Notifier`), which is human-facing, not agent-facing.

## 2. Is there any negotiation, argument, debate, or voting between agents?

**No.** There is no mechanism anywhere for one agent to contest, annotate, reply to, or vote on another's output.

- No agent reads another agent's "reasons"/"warnings" and responds; downstream gates append their own rejection reasons independently (e.g. `agents/compliance_agent.py:163-216`).
- The closest things to aggregation are deterministic merges inside the Analyst, and they are max/sort operations, not votes:
  - `confidence = max(confidence, mtf_signal["confidence"])` — a strategy's confidence is raised to the multiframe signal's confidence when they agree on direction (`agents/analyst.py:218-219`).
  - Scalp signals across timeframes are sorted by `win_prob` and the strongest wins (`agents/analyst.py:158`).
  - Final opportunity list is sorted by confidence; strongest per symbol kept via `setdefault` (`agents/analyst.py:326-332`).
- The "bearish_votes" in the trade steward (`agents/analyst.py:57-61`) is a 3-condition tally within one agent, not inter-agent voting.
- Where two agents could "disagree" (e.g. Sentiment says bearish while Analyst says BUY), the resolution is a silent numeric dampening, not an argument: sentiment/regime multipliers can only *reduce* confidence, capped at 1.0 (`agents/portfolio_manager.py:38-45`), or set `risk_ok=False` (`agents/portfolio_manager.py:47-49`). The signal's originator never sees the objection.

## 3. Which agents make independent decisions vs. pass-through transformations?

**Independent decision-makers** (produce new judgments or hard binary outcomes from their own inputs):

| Agent | Decision | Evidence |
|---|---|---|
| ResearchAnalyst | Generates the opportunity set from market data; also autonomously tightens SL / extends TP on open trades (steward) | `agents/analyst.py:105-349`; steward `agents/analyst.py:37-77` |
| RegimeAgent | Sets firm-wide `deployment_target` (risk_on / risk_off → 0.0) from the SMA200 bellwether rule | `agents/regime_agent.py:12-26, 71-81` |
| SentimentAgent | Sets `block_buy` during risk-off selloffs | `agents/sentiment_agent.py:78` |
| HealthMonitor | Sets `halted=True` on stale data / error bursts | `agents/health_monitor.py:19-31, 52-63` |
| RiskManager | Per-candidate `risk_ok` flag, size caps, correlation halving; portfolio verdict | `agents/risk_manager.py:42-84` |
| PortfolioManagerAgent | Blocks candidates (`risk_ok=False`), re-ranks by adjusted confidence | `agents/portfolio_manager.py:47-75` |
| ComplianceAgent | Global halt + per-candidate approve/reject across ~15 rules | `agents/compliance_agent.py:47-218` |
| ExecutionAgent | Rejects on spread/geometry/fee-viability; final sizing; creates order plans | `agents/execution_agent.py:43-233` |
| Auditor | Sets `needs_rebalance`, auto-persists per-strategy stats | `agents/auditor.py:50-71, 101` |
| OptimizerAgent | Autonomously mutates live config params from backtests (blocked from widening risk limits; disabled by default) | `agents/optimizer_agent.py:61-83`; `main.py:466-475` |

**Pass-through / near-pass-through transformations** (transform or forward upstream output with narrow discretion):

| Agent | Why pass-through |
|---|---|
| Orchestrator | Reads state, writes a plan + instructions nobody consumes; changes nothing (`agents/orchestrator.py:14-54`) |
| NewsAgent | Enrichment only: "news enriches decisions, it never blocks the pipeline" (`agents/news_agent.py:11-12`); writes scores others may read |
| PositionSizer | Only multiplies `max_qty` by a Kelly/vol scalar ≤ 1.0; never approves or rejects (`agents/position_sizer.py:26-44`) |
| Trader | Executes the execution plan verbatim except three mechanical skips: position already open (`agents/trader.py:68-70`), price drift > 1.5% (`agents/trader.py:76-79`), and VWAP-extended entries converted to resting limits (`agents/trader.py:92-99`) |
| HeadTrader | Advisory memo only (see §5) |

## 4. Where can one agent veto another's output?

The system is a **cascade of vetoes** — but every veto is a silent filter on shared JSON, never a communicated objection:

1. **HealthMonitor → everything**: `halted` in `reports/health` forces Compliance into global halt (`agents/health_monitor.py:52-63` → `agents/compliance_agent.py:50-53`).
2. **RegimeAgent → all new entries**: `deployment_target` reached (risk_off ⇒ 0.0) blocks every candidate at Compliance (`agents/regime_agent.py:26` → `agents/compliance_agent.py:152-159`).
3. **SentimentAgent → BUY candidates**: `block_buy` sets `risk_ok=False` in PortfolioManager (`agents/sentiment_agent.py:78` → `agents/portfolio_manager.py:47-49`).
4. **RiskManager → Analyst's candidates**: `risk_ok=False` and `max_qty=0` per candidate (`agents/risk_manager.py:50-53`); `verdict == "critical"` halts all trading at Compliance (`agents/risk_manager.py:69-71` → `agents/compliance_agent.py:57-59`).
5. **PortfolioManager → candidates**: `risk_ok=False` (sentiment guard, existing position) and confidence re-ranking (`agents/portfolio_manager.py:47-75`) → Compliance rejects any `risk_ok=False` (`agents/compliance_agent.py:167-168`).
6. **Compliance → everything downstream**: global `halted` makes Execution emit an empty halted plan (`agents/compliance_agent.py:47-96` → `agents/execution_agent.py:37-41`), and Trader skips entirely (`agents/trader.py:36-43`). Per-candidate rejections at `agents/compliance_agent.py:163-216`.
7. **ExecutionAgent → Compliance's approvals**: rejects on spread > 0.35%, zero qty, scalp win-prob gate, broken SL/TP geometry, TP below fee floor, min dollar profit (`agents/execution_agent.py:50-197`). Rejected orders never reach the Trader.
8. **Trader → Execution's orders**: final skip on drift/VWAP (`agents/trader.py:76-99`).
9. **Auditor → open positions**: `needs_rebalance` triggers `main._rebalance_positions()`, which **closes the worst underwater position directly through the broker, deliberately bypassing the entire RiskManager→Compliance→Execution gate chain** (`agents/auditor.py:66-71` → `main.py:143-183`; bypass rationale documented at `main.py:143-159`).
10. **OptimizerAgent → all agents' parameters**: rewrites live config values (`agents/optimizer_agent.py:73-83`); the only check is a self-imposed block on widening risk limits (`agents/optimizer_agent.py:66-71`), and the whole agent is off by default (`main.py:469-475`).

No veto carries a reply channel: the vetoed agent is never notified and cannot appeal.

## 5. Is the LLM HeadTrader able to override anything, or advisory-only?

**Strictly advisory-only. It can override nothing.**

- Its system prompt states: "You have NO execution power. Your only lever is per-strategy confidence" (`agents/head_trader.py:25`).
- Its only structured output — strategy confidence multipliers — is hard-clamped to [0.8, 1.1] (`agents/head_trader.py:152`) with the comment "a strategy can only be nudged, never disabled or supercharged" (`agents/head_trader.py:139-141`).
- **Even that nudge is now disconnected**: PortfolioManager used to consume it but this was removed — "The HeadTrader LLM memo is READ-ONLY (dashboard/Telegram)… nothing an LLM writes may touch sizing, ranking, or routing" (`agents/portfolio_manager.py:20-22`). A repo-wide grep shows `reports/head_trader` is read only by the web dashboard API (`core/webserver.py:292-293`) and tests.
- Class docstring: "Output is advisory only… Every trade still passes risk and compliance gates, and any failure here degrades to a no-op — the pipeline never blocks on an LLM" (`agents/head_trader.py:34-42`).
- It no-ops without `HERMES_API_KEY` (`agents/head_trader.py:47-48`), self-throttles (`agents/head_trader.py:49-54`), and any API failure returns `None` (`agents/head_trader.py:59-61`).

## 6. What would need to change for agents to actually argue/debate?

Concretely, to get e.g. a bull analyst vs. bear analyst with challenge rounds before execution:

1. **Wire the bus (or drop it honestly).** Today `main.py:260-261` runs fresh instances synchronously and the async fabric (`core/agent_bus.py`, `agents/base_agent.py:40-111`) is unused. Debate needs agents that persist, subscribe, and reply — i.e. actually call `bus.register_agent()` / `subscribe()` / `request()` (`core/agent_bus.py:61, 97, 119`), which nothing does today.
2. **Replace one-way file handoffs with addressed messages.** The JSON-file chain (§1) is write-once/read-once with fixed filenames; a rebuttal would silently overwrite the original (`core/memory.py:21-26` replaces whole files). Debate needs message identity, threading, and multiple named artifacts per topic (e.g. `thesis/<symbol>/<agent>.json`), or bus topics per symbol.
3. **Create a dissent channel that survives to a decision point.** Today objections are destructive filters: PortfolioManager flips `risk_ok=False` (`agents/portfolio_manager.py:47-49`), Compliance appends rejection reasons (`agents/compliance_agent.py:210-215`), and the Analyst never learns its signal was killed. A debate loop needs rejected/vetoed items routed *back* to their originator with the objector's rationale attached (a new `dissents` field or topic), plus a rule for how many challenge rounds run before the gate's verdict is final.
4. **Add a bull/bear analyst pair and an arbiter.** The Analyst is monolithic (`agents/analyst.py:109-197`) and merges its own signals by `max()` (`agents/analyst.py:218-219`). Splitting into `BullAnalyst`/`BearAnalyst` that independently score the same symbol, then a `ThesisReviewer` (deterministic or LLM) that must adjudicate *before* the opportunity enters `market_scan`, is the minimal debate topology. The HeadTrader memo pattern (`agents/head_trader.py:103-135`) is a working template for an LLM arbiter call.
5. **Give the arbiter bounded, explicit power.** The firm already enforces the right safety pattern for LLM output: clamp to a narrow band and degrade to no-op (`agents/head_trader.py:152`, `agents/head_trader.py:34-42`; LLM-must-not-touch-routing rule at `agents/portfolio_manager.py:20-22`). A debate arbiter should emit a bounded verdict (e.g. confidence adjustment in [0.8, 1.1] or a require-human flag) that still passes through Compliance/Execution unchanged — debate must inform, never bypass, the deterministic gates (`agents/compliance_agent.py`, `agents/execution_agent.py`).
6. **Fix the Orchestrator or remove it.** If debate is orchestrated (challenge rounds, turn order, stopping condition), the Orchestrator must gain real control: the ability to skip/reorder/parameterize agents — currently impossible since `main.py:260-261` ignores its output and nobody reads its instructions (`agents/orchestrator.py:51-54`).
7. **Budget the latency.** The cycle is interval-driven (`main.py:449-452`); synchronous multi-round debate per candidate would stretch cycle time. A pragmatic design: debate only the top-N opportunities by confidence (already sorted at `agents/analyst.py:326`), run challenge rounds between Analyst and RiskManager in the pipeline order, and cap rounds at 1–2 before defaulting to the conservative verdict (the current system's bias — vetoes always favor doing nothing — should be kept).

---

*Audit completed. 14 agents inspected: orchestrator, analyst, sentiment, news, regime, risk_manager, position_sizer, portfolio_manager, compliance, execution, trader, auditor, head_trader, health_monitor (+ optimizer, off-cycle).*

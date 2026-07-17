# Test Cycle Launch Report — 2026-07-17

## 0. What happened today (summary)

| Step | Result |
|---|---|
| Reset | Balance reset to $10,000, trade history wiped. 0 open positions needed closing. Full backup: `data_backups/full_data_pre_reset_20260717_084723` |
| Deep research | 6 catalogs in `research/` — **~145 strategies cataloged** across trend/momentum (29), mean-reversion (29), breakout/volatility (29), volume/order-flow (28), ICT/SMC/price-action (27), quant/forex-specific (30) |
| Measurement fixes | 4 fix packs landed (data integrity, execution realism, sizing/audit truth, config/risk gates) |
| New code | **146 new strategy functions** in `core/strats/` (6 modules), each with per-tag attribution and unit tests |
| Integration | **174 strategies** now registered in `core/strategies.py::ALL_STRATEGIES`; 6 broken legacy indicators fixed; full suite **785 passed / 0 failed** |
| Test-cycle flags | `CLASSIC_STRATEGIES_ENABLED` and `SCALP_15M_ENABLED` default to `true` for the cycle (`config.py`) |
| Monitoring | Day-3 mid-check (2026-07-20 09:17) and day-8 final review (2026-07-25 09:23) scheduled as local automations |

## 1. Agent behavior — do they argue, negotiate, or decide independently?

(full audit: `research/agent_behavior_audit.md`)

- **No argument, no negotiation, no voting.** Agents never talk to each other. They run sequentially in a fixed pipeline and communicate **only by overwriting shared JSON files** (`core/memory.py:21-32`): market_scan → risk_assessment → position_sizing → portfolio_plan → compliance_gate → execution_plan → trade_log.
- **The async pub/sub bus (`core/agent_bus.py`) is dead code in production** — main.py runs each agent synchronously, fresh instance per cycle.
- **Independent decision-makers:** Analyst, RegimeAgent, RiskManager, PortfolioManager, Compliance, Execution, Auditor, HealthMonitor. **Pass-through:** Orchestrator (writes instructions nobody reads), NewsAgent, PositionSizer, Trader (mechanical + 3 skip rules).
- **Disagreement resolution = silent numeric dampening or silent veto.** ~10 veto points exist (health halt, regime dial, sentiment, risk, compliance global halt, execution geometry/fees, trader skips). **No veto ever notifies the vetoed agent.** The Auditor's rebalance path (`main.py:143-183`) deliberately bypasses the whole gate chain.
- **HeadTrader LLM is advisory-only and currently not even consumed** — confidence nudges clamped to [0.8, 1.1] and read only by the dashboard.
- To make agents actually argue (optional upgrade): wire the bus, add addressable messages so rebuttals don't destroy the original thesis, route vetoes back with rationale, split the Analyst into a bull/bear pair with a bounded LLM arbiter, debate only top-N candidates with the deterministic gates kept downstream.

## 2. FIXED today (was blocking a meaningful test cycle)

| # | Fix | Where |
|---|---|---|
| F1 | **Forming/repainting bar dropped at fetch level** — all strategies now see closed bars only | `core/data_provider.py`, `agents/regime_agent.py` |
| F2 | **Kline pagination** past Binance 1000 / Crypto.com 300 caps; bar-gap validation; `ts` (unix UTC) on every candle | `core/data_provider.py` |
| F3 | **Slippage + spread on paper fills** (0.05%/side default); TP fills at exact TP only; SL fills at stop-or-worse; no exits < 1 bar after entry; limit orders need trade-through | `core/broker.py`, `core/positions.py`, `core/pending_orders.py` |
| F4 | **Kelly zero→full-size bug** fixed; Kelly now needs n≥30 position-grouped R-multiples | `agents/position_sizer.py` |
| F5 | **Auditor 0% win-rate bug** (wrong pnl key) fixed | `agents/auditor.py` |
| F6 | **Drawdown halt is now peak-relative** (−10% from peak, was from initial balance); correlation gate blocks at 0.7 (was halve at 0.9); `MAX_TRADES_PER_DAY` 150→20; `MIN_SL_PCT` 0.3→1.0 (fee trap); REGIME_PRICING risk multipliers neutralized to 1.0; new keys `MAX_DAILY_LOSS_USD`, `MAX_WEEKLY_LOSS_PCT`, `PER_STRATEGY_MAX_OPEN` | `config.py`, `agents/compliance_agent.py`, `agents/risk_manager.py`, `core/pricing.py` |
| F7 | **6 broken legacy indicators**: Donchian (could never fire), Heikin-Ashi (frozen open), ADX (constant — worst offender), RSI divergence (overlapping windows), Ichimoku (no displacement), VWAP (wrong price) | `core/strategies.py` |
| F8 | **Per-strategy attribution** — pipe-joined contributor tags flow to plans/positions; auditor + analytics split and credit each contributor | `agents/execution_agent.py`, `agents/trader.py`, `agents/auditor.py`, `core/analytics.py` |
| F9 | Regime filter **fails open** for unlisted strategies (new names were silently dropped before) | `core/strategies.py` |
| F10 | `base_agent.py` async rewrite swallowing `NotImplementedError` — contract restored | `agents/base_agent.py` |

## 3. ADDED today

- `core/strats/` — 6 modules, 146 per-tag strategies + `research/` catalogs + 6 test files (232 new tests).
- Strategy counts: trend_momentum 29, mean_reversion 26, breakout_volatility 29, volume_orderflow 21, ict_smc_priceaction 26, quant_forex_specific 15. With legacy: **174 live-registered**.
- Expectancy harness (`analysis/strategy_expectancy.py`) verified dynamic — covers all 174 automatically (smoke-tested).

## 4. SKIPPED strategies (cataloged but not implementable here) — the "missing" list

| Blocker | Strategies | What's needed to unblock |
|---|---|---|
| Needs funding-rate feed | Funding fade, funding arb, liquidation cascade | Binance futures funding API |
| Needs open interest | OI divergence | Futures OI feed |
| Needs order book | Book imbalance, iceberg | L2 depth websocket |
| Needs perp/futures sim | Spot-perp basis, cash & carry | Perp leg in paper broker |
| Needs multi-symbol frames | Pairs/cointegration, SMT divergence, cross-sectional momentum, FX carry | Multi-symbol scan contract |
| Needs rates/calendar feeds | FX carry, event straddle | Rates + macro calendar feed |
| Dangerous by design | **Martingale/DCA doubling — permanently excluded** | — |
| Concept-only | Triangular arbitrage (needs tick-level multi-pair) | Different infra |
| Forex untestable here | Pure-forex session plays | System is crypto-only spot (`MARKET_TYPE="crypto"`, `BUY_ONLY=true`) |

## 5. STILL MISSING / TO IMPROVE (post-cycle backlog, ranked)

1. **Run the full historical harness on all 174** (`py -3 analysis/strategy_expectancy.py`, ~hours) — the week-long forward test cannot statistically validate 174 strategies (need n≥200/strategy; a week gives far less). The harness is the real filter; the week validates plumbing + forward behavior. Run it overnight during the cycle.
2. **Double-slippage on stop exits** (positions.py slips, then broker slips the same exit again) — conservative but inconsistent; pick one layer to own exit slippage.
3. **No remote kill switch** — Telegram is read-only; add `/halt`. Current kill path: stop the process (instance lock prevents duplicates; open positions resume on restart).
4. **Auto-cull threshold** — `get_unprofitable_strategies(min_trades=3)` will cull aggressively among 174 strategies; deliberate choice, but review at day 3.
5. **Log rotation** — `journal.jsonl`/`errors.jsonl` grow unbounded.
6. **Startup backtest cost scales linearly** with 174 strategies — expect slow first boot.
7. **Debate layer** (optional, see §1) — bull/bear analyst pair + arbiter.
8. `.env.example` is stale (mentions removed stocks/metals/MT5) — cosmetic.

## 6. How to run the cycle

```powershell
cd C:\Users\DELL\OneDrive\1m
py -3 main.py            # dashboard on http://localhost:8000
# or headless:  py -3 main.py --headless
```

- Fresh $10,000 paper account, 174 strategies, per-tag attribution on.
- **Day-3 auto-check:** 2026-07-20 09:17 (local conversation, observation only).
- **Day-8 auto-review:** 2026-07-25 09:23 → writes `research/test_cycle_final_report.md` with keep / cull / inconclusive lists.
- Kill switch: stop the process. State persists; restarting resumes open positions.

## 7. Honest caveats

- The previous negative-expectancy verdicts were measured **with broken indicators and a 0.16% cost assumption**; the cycle re-tests everything with fixed math at true cost (~0.3% RT). Expect most of the pool to still fail — that's the filter working, not a bug.
- One week of forward data is a smoke test, not proof. Keep decisions at "candidate" level until n≥200 per strategy.
- Correlated strategies (catalogs flag ~13 pairs at r≥0.8) count as ONE bet when judging winners.

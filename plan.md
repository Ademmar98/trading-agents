# Plan — Strategy Universe Expansion + 1-Week Test Cycle

## Goal
1. Reset balance/history (DONE — backup in `data_backups/full_data_pre_reset_20260717_084723`).
2. Deep-research the full crypto + forex strategy universe and add the implementable set to the codebase.
3. Fix the measurement stack FIRST (a test cycle on broken measurement produces garbage).
4. Run a 1-week forward paper test cycle, then keep only strategies that pass the expectancy gate.
5. Report on agent behavior (argue / negotiate / independent).
6. Deliver a full fix / improve / delete / add list for the test cycle.

## Stage 1 — Research (explore agents, parallel)
Six research workers, one per family, each writes a catalog file to `research/`:
- `research/trend_momentum.md` — MA systems, MACD, ADX/DMI, Donchian/Turtle, Ichimoku, Supertrend, PSAR, Keltner, Hull/TEMA, ROC/momentum, Dual Thrust
- `research/mean_reversion.md` — RSI(2)/Connors, Bollinger %b, Stochastic, z-score, VWAP reversion, pairs/cointegration fade
- `research/breakout_volatility.md` — range/opening-range breakout, BB squeeze, ATR expansion, NR7, inside bar, London/NY session breakouts
- `research/volume_orderflow.md` — OBV, VWAP, volume profile, CVD/delta, book imbalance, funding rate, open interest, liquidation cascades
- `research/ict_smc_priceaction.md` — FVG, order blocks, liquidity sweeps, BOS/CHoCH, OTE, killzones, breaker blocks, premium/discount, candlestick pattern library
- `research/quant_forex_specific.md` — carry, cross-sectional momentum, cointegration/pairs, triangular arb, funding arb, grid, DCA (flagged dangerous), time-of-day/week seasonality, session strategies
Each entry: name, family, markets, timeframes, precise entry/exit rules, parameters. Machine-readable enough to implement from.
Two more parallel workers:
- Agent-behavior audit: read `agents/`, `core/agent_bus.py`, `core/memory.py` — do agents argue, negotiate, or decide independently?
- Test-cycle readiness: verify current config/data state, enumerate exact changes needed for a safe 1-week full-pool forward test.

## Stage 2 — Measurement fixes (coder agents, parallel, disjoint files)
Before ANY new strategy work (per prior audit):
- Fix A: `core/data_provider.py` + `core/market.py` — drop forming bar, kline pagination, staleness guard
- Fix B: `core/broker.py` + `core/positions.py` + `core/pending_orders.py` — slippage, spread, realistic SL/TP fills, no sub-1-bar exits
- Fix C: `agents/position_sizer.py` (Kelly zero→full-size bug) + `agents/auditor.py` (win-rate bug) + `core/analytics.py` (partial-exit counting)
- Fix D: `config.py` cost unification + `agents/compliance_agent.py` peak-relative drawdown + `agents/risk_manager.py` correlation cap

## Stage 3 — Strategy implementation (coder agents, parallel, one new file per family)
- New package `core/strategies/` with one module per family from Stage 1 catalogs
- Consistent signal contract matching existing `scan_symbol` interface
- Per-family unit tests

## Stage 4 — Integration (single coder agent, after Stage 3)
- Wire families into `core/strategies.py` registry + `analysis/strategy_expectancy.py` harness
- Run full pytest suite; fix failures

## Stage 5 — Validation + test cycle setup
- Batch-run fixed expectancy harness over full pool (historical screening, cost = 0.3% RT)
- Re-enable pool for forward paper cycle; document how to start the 1-week run
- Schedule monitoring (daily check-in cron)

## Stage 6 — Final report
- Strategy catalog summary + what was added
- Agent behavior answer (argue/negotiate/independent)
- Complete fix / improve / delete / add list
- Honest caveat: 1 week of forward data cannot statistically validate ~100 strategies (need n>=200); historical harness does the heavy filtering, the week validates plumbing + forward behavior

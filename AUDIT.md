# AUDIT.md — Phase 0 read-only audit

Date: 2026-07-13. No behavior was changed for this audit; it is a map of what exists,
with exact file:line evidence. Live state at time of writing: VPS firm at commit f625fc5,
equity $10,127 on a $10,000 start, 474 tests passing.

**Method note.** The multi-agent audit workflow was launched twice and both times every
subagent was rejected by the platform session limit before reading a single file, so this
audit was performed inline by one reader. Files fully read this pass: config.py, main.py,
conftest.py, core/{scalp15, pricing, backtester, optimizer, indicators, strategies,
scalping_signals, multiframe, regime, market, analytics, broker, data_provider,
database(schema)}.py, agents/{analyst, position_sizer, portfolio_manager, risk_manager,
compliance_agent, execution_agent, trader, auditor, head_trader, sentiment_agent,
regime_agent, optimizer_agent}.py. Files known in detail from recent work on them and
described from that knowledge: core/{positions, equity, pending_orders, microstructure,
memory, notifier, webserver, dashboard, websocket_prices, reconcile}.py,
agents/{news_agent, orchestrator, health_monitor, base_agent}.py, broker adapters.

---

## 0. Executive verdict

The framing brief is correct on every specific defect it names, and the audit found the
holes to be deeper than described:

1. **No proof of edge exists anywhere in the repo** (§2). The one walk-forward gate that
   looks like proof runs **cost-free** under the production fee profile, on a window that
   is silently **≤1000 bars (~10 days)** — or **300 bars (~3 days)** on the VPS — and its
   output table is consumed by nothing but tests.
2. The **win_prob loop is exactly the censored self-selection loop described**, and its
   indirect reach is wider than the three named uses: through `confidence` it also decides
   compliance passage, correlation-group overrides, macro-dip overrides, order-book probe
   allocation, and the rebalance trigger (§3).
3. Look-ahead in the classic sense (full-series statistics leaking backward) was **not
   found** — indicators are suffix-window computed. What was found instead: the **live
   path trades on the current forming candle** (repaint risk), and the backtester resolves
   intrabar partial-TP/breakeven in the optimistic order (§4).
4. The deterministic suite pins `TRADING_TIMEFRAME=5m` and `TRADE_FEE_PCT=0.1` while
   production runs 15m and fee 0 — the suite validates a sibling of production (§8).
5. A pile of machinery is provably dead or broken at runtime: an entire scoring module
   consumes indicator keys that are never produced, the regime module's volatility
   trigger contains an arithmetic bug, and the per-symbol optimizer's results table has
   no live consumer (§9).

An honest summary: **the risk plumbing is real; the alpha layer is unproven constants.**
Every confidence number in the system is either a hard-coded constant (0.5–0.7 per
strategy), a heuristic win-rate estimate on a censored sample, or an arithmetic blend of
the two by hand-tuned multipliers.

---

## 1. Repo map — modules, state flow, decision authority

State flows through **SharedMemory** (JSON files under `data/{analyses,decisions,orders,reports}/`,
core/memory.py) and **SQLite** `data/trading.db` (core/database.py:50-185: positions,
pending_orders, trades, analytics, strategy_stats, backtest_results, equity_history, meta,
trade_plans, optimization_results).

Pipeline order (main.py:196-211), one pass every `TRADING_INTERVAL_MINUTES`:

| # | Agent | Reads | Writes | Decision authority |
|---|-------|-------|--------|-------------------|
| 1 | Orchestrator | — | reports/plan | none (narration) |
| 2 | HealthMonitor | logs, feeds | reports/health | can halt (compliance reads `halted`) |
| 3 | SentimentAgent | market_scan (prev cycle) | analyses/sentiment_scan | confidence ×0.85–1.10, size ×0.65–1.0, `block_buy` (sentiment_agent.py:58-79) |
| 4 | NewsAgent | RSS (throttled) | reports/news_scan | ±0.05 confidence via analyst boost |
| 5 | RegimeAgent | daily OHLC | analyses/regime_scan | confidence ×0.85–1.10, size ×0.5–1.0, `favored_action` (regime_agent.py:28-37) |
| 6 | ResearchAnalyst | prices, OHLC (all TFs), news | analyses/market_scan, decisions/pricing | creates opportunities; sets confidence; SL/TP via compute_pricing OR scalp's own; steward adjusts open SL/TP; priority boosts (analyst.py:29-69, 71-107, 294-301) |
| 7 | RiskManager | market_scan | decisions/risk_assessment | max_qty from notional caps; correlation halving; `risk_ok` (risk_manager.py:42-66) |
| 8 | PositionSizer | risk_assessment, regime_scan | decisions/position_sizing | Kelly×vol multiplier 0.25–1.0 on max_qty (position_sizer.py:26-44) |
| 9 | PortfolioManagerAgent | sizing, sentiment, regimes, strategy_stats, head_trader | decisions/portfolio_plan | multiplies confidence by sentiment/regime/strategy-weight/LLM-nudge; sorts by confidence (portfolio_manager.py:41-80) |
| 10 | ComplianceAgent | portfolio_plan, health, market_scan | decisions/compliance_gate | hard gates: halts, daily-loss breaker, spot-only, MIN_CONFIDENCE 0.55, market hours, heat cap, cluster & correlation-group caps, macro-dip interlock, no-leverage notional (compliance_agent.py:135-184) |
| 11 | ExecutionAgent | compliance_gate, pricing, market_scan | orders/execution_plan | spread gate, win-prob gate, ATR skill sizing, session-mult risk sizing, TP cost floor (execution_agent.py:47-136) |
| 12 | Trader | execution_plan | broker + positions | fills at ask/bid, drift guard 1.5%, VWAP limit resting (trader.py:81-107) |
| 13 | Auditor | trades, portfolio | reports/audit, **strategy_stats** | writes the stats every estimate feeds on (auditor.py:23-40) |
| 14 | HeadTrader | analytics, regime, news | reports/head_trader | LLM memo + per-strategy confidence nudges (§7) |

Background: optimizer thread every 2h (main.py:485-495); monitor loop every 2-30s runs
`process_price_triggers` (SL/TP/trailing closes + pending-limit fills, main.py:218-259);
`_rebalance_positions` closes the worst >5%-underwater position when a >0.75-confidence
opportunity exists, **bypassing all gates by design** (main.py:149-191).

Signal modules: core/strategies.py (28 detectors, §6), core/scalp15.py (EMA/MACD/RSI stack
× 6 timeframes), core/scalping_signals.py (score-blend "scalping_mtf", partially broken —
§9), core/multiframe.py (weighted 5-TF consensus over the same 28 strategies),
core/microstructure.py (VWAP, order-book imbalance, funding).

Money modules: core/broker.py (paper fills, fee per side broker.py:38-43; **note: the
SELL-without-holdings branch opens a SHORT at broker level**, broker.py:88-98 — only the
compliance gate above it prevents shorts), core/positions.py (scaled exits; net PnL
subtracts round-trip fee, positions.py:35-46), core/pricing.py (ATR-first stop,
regime R:R TP), core/equity.py (snapshots, daily summaries), core/analytics.py
(position-grouped stats, corrected `_rolling_drawdown` analytics.py:77-102).

Non-crypto surface (scope-creep per the brief — see §10 Contradictions before removing):
stocks/Yahoo (market.py:244-282, data_provider.py:211-242), metals via COMEX proxies
`GC=F`/`SI=F` (data_provider.py:78-90), TwelveData (data_provider.py:110-149), Massive
(182-208), Alpaca (245-288), MT5 (market.py:8-13, 114-134), NYSE hours (market.py:28-62).
It is config-gated by `MARKET_TYPE` (config.py:239-251) and cleanly severable.

---

## 2. The edge question — the most important section

**Verdict: no artifact in this repo demonstrates positive net expectancy after costs for
any single strategy on data it was not fitted to.** Four candidate artifacts examined:

**(a) The optimizer's walk-forward gate** — the closest thing to proof, and it is not one:

- core/optimizer.py:159-167:
  ```python
  # Walk-forward gate: the winning params must survive candles the grid
  # search never saw, or they are noise fit to the training window.
  validation = _backtest_with_params(..., bar_range=(TRAIN_FRACTION, 1.0))
  pf = (validation or {}).get("profit_factor")
  adopted = bool(validation and validation["total_return"] > 0
                 and (pf is None or pf >= 1.0))
  ```
  This validates **parameters**, pooled across whichever of the 28 strategies fired
  (`scan_symbol`, optimizer.py:60), never a single strategy.
- **It is cost-free in production.** optimizer.py:7 `FEE_RATIO = TRADE_FEE_PCT / 100.0` —
  no `BACKTEST_SPREAD_PCT` term anywhere in `_backtest_with_params` (entry fee line 69,
  exit fee line 54 are the only costs). The production env sets `TRADE_FEE_PCT=0`
  (GFT profile), making `FEE_RATIO = 0`: the validation gate then proves profitability
  **at zero cost**, which on a 15m scalcadence is the whole question.
- **Its per-trade stats ignore costs even when fees are nonzero**: optimizer.py:53-56
  appends `{"pnl": round(pnl, 2)}` computed *before* fees (fees only hit `cash`), so
  `win_rate`/`profit_factor` — one of the two adoption criteria — are gross numbers.
- **The window is a fraction of what the code believes.** `BACKTEST_BARS=2500` (config.py:113)
  → `fetch_klines(limit=2700)` → data_provider.py:96 passes `limit` straight to Binance's
  klines API, which caps at **1000 bars** (~10.4 days of 15m); the Crypto.com fallback caps
  at **300 bars** (data_provider.py:166 `"count": min(limit, 300)`) — ~3.1 days, and that
  is the path the geo-blocked VPS uses. Train = first 70% (≈7 days / ≈2.2 days), the 225-combo
  grid (PARAM_GRID, optimizer.py:9-14: 5×5×3×3) picks a winner there, and one validation
  pass on ≈3 days / ≈0.9 days of candles is the entire out-of-sample test.
- **Nothing consumes the result.** `get_optimized_params` (optimizer.py:190-199) is called
  only from tests/test_optimizer.py. The `optimization_results` table gates no live decision.
  The only live effect of this machinery is OptimizerAgent's single-param nudge
  (optimizer_agent.py:46-83), which searches on `WATCHED_SYMBOLS[0]` — **BTC only**
  (optimizer.py:210) — with the same cost-free backtest, then mutates live config at
  runtime (optimizer_agent.py:77-80).

**(b) The backtester's benchmark** — honest but toothless. backtester.py:208-211 computes
buy-and-hold `benchmark_return` and a `beats_benchmark` flag; nothing reads it. Startup
backtests are explicitly informational: main.py:473 "# Backtests are informational — never
let them kill the bot". Also only `WATCHED_SYMBOLS[:10]` crypto at startup (main.py:464),
`[:5]` by default (backtester.py:286).

**(c) `strategy_stats` / live win rates** — a censored sample by construction, see §3.

**(d) Strategy exclusion** — database.py:309-316 drops strategies with `trades >= 3 AND
(win_rate <= 40 OR pnl < 0)`. Three trades is a coin-flip sample; this both excludes
unlucky strategies and, worse, **retains lucky ones as if validated**. It is a noise
filter, not evidence.

**Where win rates are born:** auditor.py:23-40 aggregates the `trades` table
(position-grouped — that part is correct) into `strategy_stats` every cycle. The `trades`
table only ever contains executed trades (positions.py close paths). Nothing anywhere
records the outcome of a setup that was gated away. **The unconditional win rate of any
strategy is unobservable in the current system** — precisely the brief's censoring claim.

---

## 3. Every consumer of win_prob

Born: core/scalp15.py:44-59 — `estimate_win_probability()`, Laplace-smoothed win rate from
`strategy_stats` (censored, §2) + 0.10 regime-alignment bonus, clamped [0.05, 0.95]. The
in-code caveat (scalp15.py:49-50: "an honest heuristic, NOT a calibrated probability") is
accurate and then ignored by every consumer below.

Direct consumers:

| # | Site | What the number does |
|---|------|---------------------|
| 1 | scalp15.py:117 `rr = rr_for_win_prob(wp)` → :122 `take_profit = price + sl_dist * rr` | **Sets TP distance** (matrix :69-75: wp≥0.85→1.0R … <0.60→2.0R). Optimism ⇒ closer TP. |
| 2 | analyst.py:170 `scalp_sigs.sort(key=lambda s: s["win_prob"], reverse=True)` | Picks which timeframe's setup represents the symbol. |
| 3 | analyst.py:272 `"confidence": min(ssig["win_prob"], 0.95)` | **Becomes ranking confidence** for the entire downstream pipeline. |
| 4 | execution_agent.py:68-71 `if wp < SCALP_MIN_WIN_PROB: … abort` | **Probability gate** (0.60). This is the censor: only passing setups ever produce outcomes, which then feed (born). |
| 5 | analyst.py:281 / execution_agent plan `confidence` | Persisted into trade_plans (database.py:159) — display/history. |

Indirect consumers — everything that treats scalp `confidence` (= win_prob) as meaning:

| Site | Effect |
|------|--------|
| portfolio_manager.py:44,52,65,70 | multiplied by sentiment/regime/strategy-weight/LLM nudges |
| portfolio_manager.py:80 `adjusted.sort(key=…confidence)` | ranking again |
| analyst.py:295 `opportunities.sort(key=…confidence)` + :299-301 pricing_map keeps strongest per symbol | ranking + which pricing wins |
| analyst.py:40 top-10-by-confidence get order-book probes | who gets microstructure evidence |
| compliance_agent.py:145 `confidence < MIN_CONFIDENCE (0.55)` | pass/fail |
| compliance_agent.py:156-161 `GROUP_OVERRIDE_CONF (0.85)` | may **override the correlation-group cap** |
| compliance_agent.py:162-167 `MACRO_DIP_OVERRIDE_CONF (0.9)` | may **override the macro-dip interlock** |
| main.py:179 `[o for o in opps if o.get("confidence",0) > 0.75]` | triggers gate-bypassing rebalance |

So the brief's "three places" is the floor. If the estimate runs optimistic, the system
simultaneously: takes the setup (4), ranks it first (3, PM:80), gives it the nearest TP (1),
lets it pierce the correlated-selloff defenses (group/dip overrides), and can trigger a
rebalance to fund it. All failures point the same way.

Note the bootstrap asymmetry: `SCALP_MIN_WIN_PROB=0.60` (config.py:84) vs a fresh-strategy
prior of exactly `(0+1)/(0+2)=0.5`, or 0.6 only when regime-aligned (scalp15.py:57-58) —
so unaligned scalp strategies can never start trading, and aligned ones start at exactly
the bar. The gate's binding edge sits precisely on the prior — maximum sensitivity to the
0.10 synergy bonus, which is itself a made-up constant.

---

## 4. Look-ahead / future-leakage hunt

Full-series-statistics leakage (the classic kind): **none found.** core/indicators.py,
core/strategies.py, core/regime.py, core/scalping_signals.py all compute on suffix windows
of the series passed to them; the backtester feeds causal slices `ohlc[:i+1]`
(backtester.py:70, optimizer.py:42). Swing detection (strategies.py:9-24) needs `window`
future bars to confirm a pivot, but is only consumed at indices `< len-3` — causal.

What *was* found, in severity order:

1. **Live path trades the forming candle** — live, medium-high. Binance klines and
   Crypto.com candlesticks include the current in-progress bar as the last element;
   data_provider.py:93-107/158-177 pass it through untrimmed. scalp15.py:89 `price =
   closes[-1]` and :101-102 fresh-cross detection on `hist[-1]` therefore run on an
   **unclosed candle**: a MACD cross that exists mid-bar can un-cross by the close
   (repaint). Same for analyst indicators, `bellwether_moves` (analyst.py:307-310 uses
   `bars[-1]` vs `bars[-3]`), and every strategy's `ohlc[-1]`. Note the backtest evaluates
   only closed bars — so the backtest tests a *cleaner* signal than the live bot trades.
2. **Backtester intrabar-ordering optimism** — backtest, medium. backtester.py:85-115
   banks partial-TP if `high >= partial_px`, then :117-127 moves the stop to breakeven if
   `high >= entry + 1R`, then :129-136 checks the (possibly moved) stop against the same
   bar's `low`. On a bar that touched both extremes this assumes the favorable extreme
   came first — partial profit is banked and the runner exits at breakeven+buffer instead
   of the original (losing) stop. Live, sequence decides; backtest always credits the
   optimistic sequence. (SL-before-TP at :129-136 is, correctly, pessimistic — good.)
3. **Entry at signal-bar close** — backtest, low. backtester.py:170/180 and
   optimizer.py:66/83 fill entries at `current["close"]`, the same bar whose close
   completed the signal. Live pays next-tick ask (trader.py:90-91). Half-spread is charged
   in backtester.py:60 but not in the optimizer (§2a). ~One 15m tick of drift per entry,
   systematically favorable.
4. **Silent window truncation** — both, low-medium (realism §5). Callers believe they
   analyze/backtest 2500 bars; providers return ≤1000/≤300 (data_provider.py:96,166).
   Also fetch_yahoo_ohlc pins 15m to `range=1mo` regardless of request (data_provider.py:213).
5. **Optimizer validation reuse** — process-level leakage, structural. The 2-hourly
   OptimizerAgent re-runs `test_single_param` against essentially the same most-recent
   window (optimizer.py:213 fetches fresh but overlapping candles); the "unseen" 30%
   is unseen only within one call, not across the repeated calls that decide live params.
6. Not leakage but a correctness bug distorting regime labels: core/regime.py:121
   `avg_width = sum(all_bb[-20:]) / min(len(all_bb[-20:]), 1)` — `min(…, 1)` is always 1,
   so `bb_avg_width` is a **sum**, ~20× too large, and the `bb_width > bb_avg_width * 1.5`
   volatile trigger (regime.py:50) can never fire; "volatile" only arises via `atr_pct > 4`
   (:52). Every consumer of regime labels (pricing multipliers, strategy eligibility,
   PM multipliers, wp synergy bonus) inherits this skew.

---

## 5. Backtest realism

| Question | Answer | Evidence |
|---|---|---|
| Timeframe matches live? | Yes by construction (`TRADING_TIMEFRAME` used by both) — but the *test suite* pins 5m (§8), and analyst scalp signals additionally trade 1m–4h which are never backtested at all. | backtester.py:54, conftest.py:19 |
| Window | Believed 2500 bars; actually ≤1000 (Binance cap) ≈ 10.4 days at 15m; 300 (~3.1 days) on Crypto.com fallback (VPS). Far below the brief's "much longer than 90 days". | data_provider.py:96,166 |
| Costs | backtester: fee + constant half-spread per leg (0.05%), **no slippage model** beyond it, no depth/impact. optimizer: **fee only, no spread — $0 total under production fee=0**. | backtester.py:58-60, optimizer.py:7 |
| Intrabar resolution | SL before TP (conservative, good); partial-TP/breakeven before SL (optimistic, §4.2). | backtester.py:85-136 |
| Sizing model | Backtest risks `MAX_POSITION_SIZE_PCT` (15%) of cash as notional per trade; live sizes by `risk$ ÷ stop distance` with session multipliers. The backtest does not test the live sizing. | backtester.py:170 vs execution_agent.py:128-136 |
| Exit model | Backtest: partial TP + breakeven, **no R-trailing, no steward adjustments**. Optimizer: bare SL/TP only. Live runs all of it. Three different exit engines. | backtester.py:82-127, optimizer.py:45-57, positions.py |
| Regime gating | Live `scan_symbol(ohlc, regime=regime, …)` (analyst.py:171); backtest `scan_symbol(slice_data, exclude_strategies=bad_strats)` — **no regime arg**, all 28 strategies always eligible. | backtester.py:160 |
| Universe / survivorship | Today's WATCHED_SYMBOLS (config defaults = today's top-liquidity names) applied to the past; startup backtests `[:10]`, default `[:5]` crypto only. Classic survivorship + tiny cross-section. | backtester.py:286, main.py:464 |
| Who consumes results | Console + dashboard only. `beats_benchmark` and `backtest_results` gate nothing. | main.py:462-475 |

Verdict: **unrealistic for the question that matters** (net edge at 15m under GFT costs),
independent of the window-length problem.

---

## 6. Multiple-testing exposure

Counted exactly:

- **28** strategies in `ALL_STRATEGIES` (strategies.py:669-698), all with **hard-coded
  confidence constants** 0.5–0.7 combined per action by `max()` capped 0.95 (:769-778).
- **6** scalp tags (`scalp_1m…scalp_4h`, config.py:71-72) + **1** scalping_mtf blend +
  **1** multiframe consensus = **36 signal streams**.
- **29 symbols** under `MARKET_TYPE=both` (20 crypto + 7 stocks + 2 metals, config.py:225-235).
- Per cycle that is ~**1,044 strategy×symbol hypothesis streams** (28×29 classic, plus
  174 scalp-TF streams, plus the two blends), each writing into a shared `strategy_stats`
  namespace keyed by strategy name only (symbol-blind).
- Optimizer: **225 parameter combos** per symbol (optimizer.py:9-14) selected on ~7 days,
  validated once on ~3 days (§2a).

Corrections that exist: exclusion at `trades≥3 AND (win_rate≤40 OR pnl<0)`
(database.py:309-316); PM weight 0.5 for negative-PnL strategies (portfolio_manager.py:113-115);
auditor divergence warning at n≥10 (auditor.py:60-65).

Corrections that do not exist: any multiple-comparison control (no deflated Sharpe, no
White reality check, no Bonferroni/FDR, no minimum-n before a strategy may be *promoted*
rather than excluded). With ~1,000 streams and 3-trade thresholds, several strategies will
look good by luck at any moment, get max-confidence ranking, and win the per-symbol
`pricing_map` slot. **Uncorrected.**

---

## 7. The LLM layer (HeadTrader)

- Writes `reports/head_trader` with `strategy_confidence` nudges (head_trader.py:63-69),
  clamped to [0.8, 1.1] at parse time (:152).
- **Consumed inside the decision path**: portfolio_manager.py:67-71 multiplies candidate
  confidence by the nudge (6-hour TTL, :22-25). Since confidence then decides compliance
  passage (0.55 bar), ranking, and the group/dip overrides (§3), the LLM does touch
  routing — bounded, but in-path. The docstring's "advisory only" (head_trader.py:38) is
  aspirational, not factual.
- Failure behavior is correct: no key / API error → `return None`, throttle stamped before
  the call (head_trader.py:47-61); pipeline never blocks.

---

## 8. Test reality

- conftest.py:19 `os.environ["TRADING_TIMEFRAME"] = "5m"` and :22 `os.environ["TRADE_FEE_PCT"] = "0.1"`
  — production runs **15m** and **fee 0**. The deterministic suite therefore exercises a
  different data path and a different cost regime than production. (The pins are
  deliberate — determinism and fee-math coverage — but the divergence stands.)
- The suite is 474 passing (the brief's "429" is a stale count). Content: overwhelmingly
  plumbing and unit-level — agents wire, DB writes, exits fire, gates reject. There are
  scalp-specific tests (tests/test_scalp15.py: signal geometry, wp gate, ATR sizing;
  tests/test_buy_only_scalp.py: multi-TF tags, BUY-only), so defect #5's "no 15m tests" is
  *partially* stale — but no end-to-end test runs the pipeline at 15m, and **nothing in
  the suite measures or asserts edge**. Green tests prove the machine turns, not that it
  makes money — the suite has been green for weeks while the book lost money.

---

## 9. Kill list (candidates — final verdicts belong to Phase 1 evidence)

Provably dead or broken today (no Phase 1 needed):

| Component | Evidence |
|---|---|
| core/scalping_signals.py gradient inputs | Reads `williams_r` (:239), `volume_ratio` (:242), `macd_histogram_prev`/`macd_histogram` (:247) — **none of these keys is ever produced by** `compute_all` (indicators.py:184-223). Effects: williams term frozen at -50→0.5 grad; `(vol_ratio >= 1.0) * 20` always +20; `macd_grad` always 0. Divergence detection stubbed dead (:298-302). The "scalping_mtf" confidence is arithmetic on defaults. |
| Per-symbol optimizer output | `optimization_results`/`get_optimized_params` consumed only by tests (§2a). 225-combo grid burning CPU for a table nobody reads. |
| core/regime.py `_bb` | sum-not-mean bug (:121) disables the BB volatile trigger (§4.6). |
| Backtest `beats_benchmark` | computed (backtester.py:210,280), consumed nowhere. |

High complexity, no demonstrated contribution (Phase 1 must measure before removal):

- The 28-strategy battery — constants-as-confidence, max-combined; expect most to fail
  net-cost OOS testing (the brief predicts this; the audit found nothing to contradict it).
- multiframe.py + scalping_signals.py — two additional consensus layers re-blending the
  same underlying signals with hand-tuned weights (×1.3 at multiframe.py:118, 90/100
  thresholds at scalping_signals.py:322-329).
- SentimentAgent multipliers / NewsAgent keyword scores / microstructure boosts /
  HeadTrader nudges — four separate ad-hoc multiplier layers on an uncalibrated base.
- OptimizerAgent live param mutation (BTC-only, cost-free scoring, optimizer_agent.py:46-83).
- `_rebalance_positions` gate bypass (main.py:149-191) — triggered by `confidence > 0.75`,
  which §3 shows can be win_prob in disguise.
- Kelly layer (position_sizer.py) — already effectively a 0.25–1.0 downward multiplier
  capped at 25% Kelly (:26-27, :78), but computed on **raw trade rows** (`SELECT pnl FROM
  trades`, :59 — not position-grouped, so scaled exits double-count) from the censored
  sample. The brief's "replace with flat fractional risk" loses little.
- Stocks/metals legs — severable; **see contradiction #1 below before acting.**

Keep — verified working, do not touch in any subtraction:

- Fee-honest accounting: broker.py:38-43 (fee per side), positions.py:35-46 (net PnL),
  trader.py:87-93 (ask/bid fills), backtester.py:58-60 (fee+half-spread).
- Circuit breakers: daily loss (compliance_agent.py:63-66), consecutive losses (:67-76),
  drawdown halt (:60-62), portfolio heat (:101-110), cluster cap (:151-155),
  correlation-group cap + macro-dip interlock (:156-167), no-leverage notional gate
  (:119-174), spot-only SELL block (:141-144).
- Session-aware sizing + ATR-first stops (core/risk.py, execution_agent.py:128-136,
  pricing.py:40-50).
- `_rolling_drawdown` (analytics.py:77-102), position-grouped stats (auditor.py:23-26,
  analytics.py:10-20), instance lock (main.py:90-102), `process_price_triggers`
  always-run pattern (main.py:269-272), Telegram auth gate, health monitor.

---

## 10. Contradictions with the brief (told, not silently complied with)

1. **"Remove stocks/metals as scope creep" contradicts the owner's standing instructions.**
   The stocks+metals legs, MT5 demo integration, and the hunt for a halal metals funded
   account were explicit requests in this project's history, and the halal constraint
   memory says metals matter to the operator. Removal is a product decision, not a code
   cleanup. Recommendation: **hard-disable via `MARKET_TYPE=crypto` in Phase 2** (one env
   var, zero deletion) and defer deletion until you confirm.
2. "429 passing tests" → 474 at audit time. "conftest.py" lives at repo root, not tests/.
3. Defect #5's "add explicit 15m scalp tests" — partially exists already
   (tests/test_scalp15.py, tests/test_buy_only_scalp.py); the real gap is a
   pipeline-level 15m test and the fee-0 profile.
4. "Kelly sizing … over-bets toward ruin" — today's Kelly is a bounded *downward*
   multiplier (≤1.0, quarter-ish cap), so ruin-by-Kelly is not the live failure mode;
   the live failure mode is that it's noise steering size at all. Replacement is still
   the right call, just for a different reason than stated.
5. "0.1%/side fees" — production runs the GFT commission-free profile (`TRADE_FEE_PCT=0`);
   real costs live in the spread. Any cost floor built on `TRADE_FEE_PCT` alone (the
   optimizer; partially execution's `round_trip_cost = 2*TRADE_FEE_PCT + spread_pct`,
   execution_agent.py:121) must be spread-based to bind in production.
6. "The scalp win-probability … can never converge" — confirmed, with the sharper note
   that the gate's 0.60 bar sits exactly on the fresh-strategy prior (§3), so which
   strategies ever get a sample is decided by the fabricated 0.10 regime bonus.
7. The brief assumes an "existing TypeScript paper-trading bot" — none existed in this
   repo (the firm is pure Python). The specified TS bot was built fresh as a standalone
   sidecar (src/*.ts + data/ledger.csv + data/learnings.md); it shares nothing with the
   Python pipeline.

---

## 11. What Phase 1 needs that today's code cannot give it

Recorded here because the audit exposed prerequisites (no code written, per instructions):

1. **Shadow logging (brief's required fix #1)** — a `shadow_setups` table logging every
   scalp setup at the gate with its would-have SL/TP, resolved by later price data, to
   recover the *unconditional* win rate. Until it exists, every win_prob-derived number is
   untrustworthy — including the Phase 1 expectancy study's per-strategy priors.
2. **Deep history** — Binance klines paginate via `startTime`/`endTime` (1000/call);
   `fetch_ohlc` needs a paginated variant before any "much longer than 90 days" backtest
   is possible. Without it, Phase 1's OOS study physically cannot run on adequate data.
3. **A spread/slippage model with teeth** — per-symbol live spread is already measured
   (execution_agent.py:47); Phase 1 should persist observed spreads and use per-symbol
   medians, not a constant 0.05%.
4. `analysis/strategy_expectancy.py` per the brief: per strategy × regime, net expectancy
   after fee+spread+slippage, CI, n-flag (<~300 trades = noise), correlation clustering,
   multiple-comparison control. Expect the 28 constants-confidence strategies to mostly
   fail; that is the finding, not a bug in the study.

**Stopped here per instructions. Phase 1 will not start until you review this audit.**

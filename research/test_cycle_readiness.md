# Readiness Audit — 1-Week Forward Paper-Trading Cycle with Expanded Strategy Pool

Auditor: READINESS AUDIT WORKER (code-only review, no web research)
Scope: `config.py`, `.env.example`, `main.py`, `agents/compliance_agent.py` + traced dependencies
(`core/strategies.py`, `core/database.py`, `core/memory.py`, `core/notifier.py`,
`core/backtester.py`, `core/analytics.py`, `core/webserver.py`, `agents/analyst.py`,
`agents/trader.py`, `agents/execution_agent.py`, `agents/health_monitor.py`, `agents/auditor.py`).
Date of audit: current workspace state (data/ freshly wiped).

---

## 0. Verified current state

- `data/` exists with only empty subdirs (`analyses/`, `candles/`, `decisions/`, `logs/`, `orders/`, `reports/`)
  plus one leftover file `data/reports/portfolio.json`. **No `trading.db`** — it is recreated by
  `init_db()` on startup (`main.py:384`, schema in `core/database.py:50-186`).
- `.env` exists (1,525 bytes) — NOT read per assignment rules. Its contents must be verified by the owner.
- `.env.example` is **stale**: it still documents stocks/metals/MT5/DXtrade (`.env.example:6-30`), but the
  firm is crypto-only (`config.py:25` — `MARKET_TYPE = "crypto"` hardcoded). Update it before the cycle.
- `prod_run.py:24` honors `RESET_ON_START` env; **`main.py` does not** — plain `python main.py` only resets
  via the `--reset` CLI flag (`main.py:15`). The `.env.example:43` comment is misleading for direct runs.

---

## 1. Configuration checklist (flags to set / verify before launch)

| # | Item | Where | Action for the cycle |
|---|------|-------|----------------------|
| 1.1 | `BROKER_TYPE=paper` | `config.py:151`, `.env.example:2` | Confirm `paper`. Default is paper — safe. |
| 1.2 | `TRADING_CAPITAL` | `config.py:22` | Set the paper bankroll (`.env.example:3` shows 20000; default 10000). |
| 1.3 | `TRADING_INTERVAL_MINUTES` | `config.py:283` | Default **1** (`.env.example:4` shows 60). 1-min cycles × 75 strategies × 20 symbols is heavy; pick deliberately. |
| 1.4 | `CLASSIC_STRATEGIES_ENABLED` | `config.py:80` | Currently `false` — the 28 classic strategies are proven net-negative (`config.py:73-80`). **Decision needed:** the new 50+ strategies need their own registration/enable path; do NOT blanket-enable the old battery. |
| 1.5 | `SCALP_15M_ENABLED` / `SWING_ENABLED` | `config.py:105`, `config.py:132` | scalp off (proven negative), swing on. These are the only live sources today. |
| 1.6 | `BUY_ONLY` | `config.py:46` | `true` — all SELL-side signals are filtered out (`agents/analyst.py:169-171`). **Every short strategy in the expansion catalog will never trade.** Either accept long-only testing or flip policy. |
| 1.7 | `OPTIMIZER_ENABLED=false` | `config.py:54`, `main.py:469-475` | Keep off — it mutates live risk params from a cost-free backtest. |
| 1.8 | Trade-frequency caps | `config.py:234-235` | `MAX_TRADES_PER_DAY=150`, `MAX_TRADES_PER_HOUR=0` (unlimited). With 50+ strategies × 20 symbols, set an hourly cap to pace entries. |
| 1.9 | Risk caps | `config.py:239-274` | `MAX_OPEN_RISK_PCT=10`, `MAX_POSITIONS_PER_CLUSTER=8`, `MAX_GROUP_POSITIONS=2`, `MAX_GROSS_LEVERAGE=1.0` (hard no-leverage). These bound total concurrent exposure — verify they're acceptable for the test's statistical goals. |
| 1.10 | Circuit breakers | `config.py:175-177` | `STREAK_LOSS_HALT_PCT=1.2`, `DAILY_LOSS_LIMIT_PCT=3` — both halt new entries in `compliance_agent.py:63-92`. |
| 1.11 | `BINANCE_ALL_SYMBOLS` | `config.py:316-330` | **Do NOT set** — it expands the watch universe to all Binance USDT pairs even in paper mode, blowing up scan time and `market_scan.json` size. |
| 1.12 | `HEADLESS` | `main.py:12`, `.env.example:40` | Set for unattended server runs; terminal Rich dashboard otherwise (`main.py:484-496`). |
| 1.13 | `TRADING_DATA_DIR` | `config.py:8` | Optional: point the cycle at a dedicated data dir for isolation. Note `LOCK_PORT` derives from it (`config.py:277-281`). |
| 1.14 | Telegram creds | `config.py:157-158` | Verify in `.env` (not audited). Without them there is no remote monitoring at all. |
| 1.15 | Fresh start | `main.py:321-369` | data/ is already wiped; on first start `init_db()` + fresh portfolio from `INITIAL_BALANCE` (`main.py:403-410`). If another wipe is needed use `--reset` (backs up DB to `data_backups/`, `main.py:331-342`) or `RESET_ON_START` **only via `prod_run.py`**. |
| 1.16 | Test suite | `tests/` | Run `pytest` before launch — schema migrations and exit logic are covered (`tests/test_analytics.py`, `tests/test_exits.py`). |

---

## 2. What breaks (or silently misbehaves) when 50+ strategies are registered

These are ordered by severity. **Items 2.1–2.5 mean a naive "add to `ALL_STRATEGIES`" expansion
cannot produce per-strategy forward results** — the stated goal of the cycle.

- **2.1 — Regime filter silently drops every new strategy.**
  `strategies_for_regime()` selects by exact name from hardcoded `REGIME_STRATEGIES` lists
  (`core/strategies.py:700-750`). When the RegimeAgent labels a symbol, any strategy not listed
  for that regime **never runs**. Fix: add every new name to all five regime lists, or change the
  filter to default-include unknown names.

- **2.2 — Signal aggregation destroys per-strategy attribution.**
  `scan_symbol()` merges all agreeing signals into ONE combined signal per action
  (`core/strategies.py:769-778`): confidence = max, reasons concatenated. Fifty strategies voting
  BUY produce a single "BUY" blob.

- **2.3 — One opportunity per symbol+action reaches the pipeline.**
  The analyst dedups with `seen_actions` (`agents/analyst.py:210-215`); only the
  highest-confidence source per symbol+side becomes an opportunity. Weaker strategies never get
  positions, so they can never build a track record.

- **2.4 — Positions are tagged with only the FIRST strategy name.**
  `strategy = strategies[0]` in `agents/trader.py:105` and `agents/execution_agent.py:213`.
  Per-strategy stats (`strategy_stats` table, `core/database.py:109-116`) then credit/blame one
  strategy for a consensus trade. The patterns that DO preserve attribution are the dedicated
  tags `scalp_<tf>` and `swing_*` (`agents/analyst.py:285,315`) — new strategies must follow
  that per-tag opportunity pattern, not the classic combined path.

- **2.5 — One open position per symbol, hard cap.**
  `compliance_agent.py:208-209` rejects any candidate for a symbol already held. 75 strategies ×
  20 symbols still means ≤ 20 concurrent positions, further throttled by
  `MAX_POSITIONS_PER_CLUSTER=8` (`config.py:242`) and `MAX_GROUP_POSITIONS=2` (`config.py:257`).
  Throughput math for statistical significance in 7 days must account for this.

- **2.6 — Auto-exclusion of "unprofitable" strategies kicks in at 3 trades.**
  `get_unprofitable_strategies(min_trades=3, max_win_rate=40)` (`core/database.py:313-320`)
  excludes any strategy with `trades>=3 AND (win_rate<=40 OR pnl<0)` from both live scans
  (`agents/analyst.py:93,168`) AND backtests (`core/backtester.py:67`). For a judged 1-week cycle
  this is roughly the intended "keep only winners" mechanism — but note it triggers on tiny
  samples and also contaminates the startup backtests. Decide the thresholds deliberately.

- **2.7 — SL/TP geometry clamps will rewrite new strategies' exits.**
  Non-swing stops are clamped to `MAX_SL_PCT=3%` / `MAX_TP_PCT=6%` (`config.py:208-209`), and a
  boot-time DB migration force-rewrites any open non-`swing%` position beyond those caps
  (`core/database.py:218-234`). Strategies whose edge needs wider stops must either be tagged
  `swing*` (exempt; `execution_agent.py:132-134` uses `SWING_MAX_SL_PCT=25%`) or accept clamped
  geometry that changes their expectancy.

- **2.8 — The win-probability gate matches one literal tag.**
  `execution_agent.py:68`: `is_scalp = "scalp_15m" in strategies`. New strategy tags bypass the
  `SCALP_MIN_WIN_PROB` gate entirely (gate exists only for the old scalp stack). Fine if
  unintended tags shouldn't be gated — but be aware nothing else is probability-gated.

- **2.9 — Startup backtest cost scales linearly with strategy count.**
  `main.py:434-447` runs `run_all_backtests()` on 10 symbols × `BACKTEST_BARS=2500`
  (`config.py:168`) bars, calling `scan_symbol()` per bar (`core/backtester.py:160`).
  Going 28 → 75+ strategies roughly triples boot time before the first cycle. Consider gating
  startup backtests off for the forward cycle.

- **2.10 — Hardcoded compliance confidence floor.**
  `MIN_CONFIDENCE = 0.55` (`agents/compliance_agent.py:29`) is not env-configurable. Any new
  strategy emitting confidence < 0.55 is dead on arrival.

- **2.11 — Forex strategies cannot trade in this repo at all.**
  `MARKET_TYPE = "crypto"` hardcoded (`config.py:25`); `classify_symbol`/clusters are crypto-only
  (`config.py:250-256`, `267`). Forex-cataloged strategies are research-only here — flag them
  "not deployable in this cycle."

- **2.12 — BUY_ONLY suppresses all short entries** (`config.py:46`, `agents/analyst.py:169-171`,
  `core/backtester.py:162`). Short-side catalog entries are untestable without a policy change.

- **2.13 — `.env.example` drift**: documents removed markets/brokers (`.env.example:6-30`) and a
  `RESET_ON_START` that `main.py` ignores (`.env.example:43` vs `main.py:15`). Rewrite alongside
  the expansion so the next operator isn't misled.

### DB / analytics / dashboard impact

- `strategy_stats` upsert is per-name with a unique index (`core/database.py:254-261, 294-305`) —
  **schema scales fine to 100+ strategy names**, provided tagging (2.4) is fixed.
- `trades.strategy` / `positions.strategy` columns already exist (`core/database.py:248-253`).
- Analytics recomputed each cycle by the Auditor (`agents/auditor.py:18,40`) —
  `_compute_strategy_stats` groups by `trades.strategy` (`core/analytics.py:137-165`). Cost is
  trivial; correctness depends entirely on tagging.
- Web dashboard: `/api/strategy-stats` (`core/webserver.py:237-239`) and a per-strategy scorecard
  page (`core/webserver.py:313-316`) — both render lists; fine with 100 rows.
- `market_scan.json` is fully rewritten every cycle with all per-symbol analyses
  (`agents/analyst.py:345-349`). Signal `reasons` are capped at 5 (`agents/analyst.py:224`) so
  size grows modestly; it balloons only if `BINANCE_ALL_SYMBOLS` is set (1.11).
- **Log files are unrotated**: `journal.jsonl` and `errors.jsonl` append forever
  (`core/memory.py:44-54`). At 1-min cycles a week is ~10k cycles — monitor `data/logs/`; add
  rotation if the cycle runs hot with errors.
- `data/reports/portfolio.json` leftover from before the wipe — harmless (rewritten by
  `SharedMemory.write_portfolio`, `core/memory.py:85-86`), but delete it for a truly clean start.

---

## 3. Monitoring that exists (and its gaps)

| Channel | What you get | Where |
|---------|--------------|-------|
| Telegram | Trade opens/closes, SL/TP hits, daily summary, errors, compliance HALT alerts; quiet mode on | `config.py:230`, `core/notifier.py:343-439` |
| Telegram commands | **Only** `/start /help /positions /pnl /status /stats` | `core/notifier.py:52-60` |
| Web dashboard | Portfolio, trades, plans, `/api/strategy-stats`, strategy scorecard | `core/webserver.py:116,237-239,313-316`; port from `PORT` env, default 8000 (`webserver.py:356-357`) |
| Terminal dashboard | Rich live layout (skipped in HEADLESS) | `main.py:484-496` |
| Health monitor | Writes `reports/health`; `halted=true` propagates into compliance gate | `agents/health_monitor.py:52-56` → `agents/compliance_agent.py:50-53` |
| Equity/daily reporting | Snapshot every cycle; daily summary to Telegram; portfolio snapshot every 30 cycles | `main.py:269-278` |

**Gaps:** no Telegram `/halt`, `/pause`, or `/flatten` command; no remote way to stop trading.
No log rotation (2.13 above). No per-strategy forward P&L alert — you must poll the scorecard API.

---

## 4. Kill-switch path (if the cycle goes wrong)

1. **Immediate stop:** Ctrl+C / terminate the process (`main.py:497-500`). The instance lock
   (`main.py:88-100, 314-319`; port derived at `config.py:277-281`) prevents a second instance
   from double-trading while you restart.
2. **Entries-only halts (automatic, exits always run):**
   - Daily loss > `DAILY_LOSS_LIMIT_PCT` (`compliance_agent.py:63-66`)
   - Losing-streak money breaker (`compliance_agent.py:72-92`)
   - Portfolio drawdown > `MAX_PORTFOLIO_RISK_PCT` (`compliance_agent.py:60-62`)
   - Health-monitor halt (`compliance_agent.py:50-53`)
   - Heat / regime / cluster / macro-dip gates (`compliance_agent.py:122-198`)
3. **Open positions on shutdown are NOT closed** — they persist in SQLite and SL/TP triggers
   resume on restart (`main.py:421-425`; trigger loop `main.py:212-253`). Paper broker, so no
   real-money exposure, but the book survives restarts.
4. **Full reset:** stop process → `python main.py --reset` (backs up DB to `data_backups/`,
   wipes, fresh capital; `main.py:321-369`).
5. **Recommended addition before launch:** a halt mechanism that doesn't require shell access —
   e.g. a Telegram `/halt` command or a `data/HALT` sentinel file checked in `run_cycle()`
   (`main.py:256`). Today the only remote surface is read-only (`notifier.py:52-60`).

---

## 5. Pre-flight checklist (ordered)

- [ ] Verify `.env` contents: `BROKER_TYPE=paper`, `TRADING_CAPITAL`, Telegram token/chat ID, `HEADLESS` (owner task — `.env` not audited).
- [ ] Decide strategy registration architecture: per-tag opportunities (like `scalp_*`/`swing_*`) NOT the combined `scan_symbol` path — fixes 2.1–2.4.
- [ ] Add new strategy names to `REGIME_STRATEGIES` or change the filter (`core/strategies.py:700-750`).
- [ ] Fix strategy tagging to record the true source strategy (`agents/trader.py:105`, `agents/execution_agent.py:213`).
- [ ] Decide `get_unprofitable_strategies` thresholds for the cycle (`core/database.py:313`) — it is the de-facto "keep only winners" mechanism.
- [ ] Tag wide-stop strategies `swing*` or raise caps, else geometry is rewritten (2.7).
- [ ] Set `MAX_TRADES_PER_HOUR` (currently unlimited, `config.py:235`).
- [ ] Keep `OPTIMIZER_ENABLED=false`, `CLASSIC_STRATEGIES_ENABLED=false`, `SCALP_15M_ENABLED=false` unless deliberately testing those.
- [ ] Do NOT set `BINANCE_ALL_SYMBOLS`.
- [ ] Accept or change BUY_ONLY (2.12) and crypto-only scope (2.11) — forex/short catalog entries are otherwise untestable.
- [ ] Add a remote kill switch (Telegram `/halt` or halt-file) — section 4, item 5.
- [ ] Consider disabling or scoping startup backtests (`main.py:434-447`) to keep boot time sane with 75+ strategies.
- [ ] Update stale `.env.example` (markets, brokers, RESET_ON_START).
- [ ] Delete leftover `data/reports/portfolio.json`; run `pytest`; then launch with `python main.py --headless` (or `prod_run.py`).
- [ ] During the cycle: watch `data/logs/errors.jsonl` growth, `/api/strategy-stats`, and the Telegram daily summary.

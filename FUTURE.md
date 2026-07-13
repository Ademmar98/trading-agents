# FUTURE.md — additions parked with evidence (nothing here goes into code without review)

Per the working rules: no new strategies, markets, indicators, or LLM layers. Everything
below is deferred and justified by audit evidence (see AUDIT.md sections cited).

1. **Shadow logging of gate-rejected scalp setups** (Phase 1 prerequisite, AUDIT §3/§11).
   Log every setup the SCALP_MIN_WIN_PROB gate rejects, resolve its would-have outcome from
   later candles, no capital. Evidence: strategy_stats is a censored sample (auditor.py:23-40
   only sees executed trades); the unconditional win rate is currently unobservable.

2. **Paginated deep-history fetch** (Phase 1 prerequisite, AUDIT §5). Binance klines cap at
   1000 bars/call (data_provider.py:96), Crypto.com at 300 (:166); backtests silently run on
   ~10 (or ~3) days. Pagination via startTime/endTime unlocks the >90-day windows Phase 3
   requires. Not a new indicator — a data-plumbing fix.

3. **Per-symbol observed-spread ledger** (AUDIT §11.3). Execution already measures live
   spread per candidate (execution_agent.py:47); persisting the observations gives the
   Phase 1 expectancy study and the Phase 3 tradeability gate real per-symbol cost floors
   instead of the constant BACKTEST_SPREAD_PCT=0.05%.

4. **Prediction-vs-realized R tracker** (brief's Phase 4; AUDIT §5 exit-model mismatch).
   trade_plans already stores predicted risk_reward_ratio (database.py:163); joining it to
   realized R per position, bucketed by regime, closes the loop the brief calls "worth more
   than any new indicator".

5. **Two-file memory for the Python firm** (port of the TS sidecar's mechanism). The TS bot
   (src/, data/ledger.csv, data/learnings.md) demonstrated skip-on-real-prior-loss on real
   candles. A Python equivalent keyed by (strategy, regime, session) could gate re-entry into
   setups with repeated realized losses — but ONLY after shadow logging exists, or it inherits
   the same censoring it is meant to fight. Evidence: 2026-07-12 post-mortem (six correlated
   losses repeating one setup shape).

6. **Forming-candle policy** (AUDIT §4.1). Decide once: drop the in-progress bar at the
   data-provider boundary (live signals then match the closed-bar semantics the backtester
   already tests), or timestamp-gate signal evaluation to bar close. One-line data fix, but
   it changes live behavior — needs the Phase 2 review, not a drive-by patch.

7. **Regime `_bb` fix** (core/regime.py:121, min→max). One character; deferred only because
   Phase 0 changes nothing. Should be the first Phase 2 commit, with a regression test.

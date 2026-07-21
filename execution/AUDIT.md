# Limit-execution audit & staging protocol

Two layers ship here:

- **Live now (paper/testnet):** the integrated maker-limit path in
  `core/pending_orders.py` + `core/fill_monitor.py`, wired into the trader and
  the maintenance loop. It measures fill rate, time-to-fill (seconds/cycle),
  post-fill adverse selection (1m/5m), and net spread saved on the firm's actual
  resting limits. Surfaced at `GET /api/fill-diagnostics` and in the daily
  report. This is what to watch today.
- **Go-live template (not running):** `execution/limit_execution_monitor.py` —
  a `ccxt.pro` `post_only` engine for a real spot exchange. Real order books,
  millisecond time-to-fill, 10s adverse scoring. Audit it fully before capital.

## What's honestly measurable on each layer

| Metric | Paper/REST (live now) | ccxt.pro (go-live) |
|---|---|---|
| Fill rate | ✅ trade-through model | ✅ real maker fills |
| Time-to-fill | seconds / 1-min cycle | milliseconds (real ticks) |
| Adverse selection | 1m, 5m | 10s, 1m, 5m |
| Spread saved | maker-fee + price improvement | + real bid/ask capture |
| `post_only` maker | simulated | real (`{"postOnly": True}`) |

Do **not** report microsecond latency or 10s adverse scores from the paper
firm — its cadence can't measure them.

## Staging protocol for the go-live engine (before any real capital)

1. **Testnet first.** `ex.set_sandbox_mode(True)` (uncomment in `main()`), use
   exchange *testnet* API keys. Never live keys in staging.
2. **Dry-run placement.** Confirm every order is created with
   `{"postOnly": True}` and is rejected (not silently crossed to taker) when the
   quote would cross the spread — that rejection IS the maker guarantee.
3. **Cancel/replace loop.** Verify quotes cancel on `>1.5xATR` drift and on the
   `QUOTE_TTL_S` staleness timeout, and re-place at a fresh distance. Watch for
   orphaned orders (cancel confirmations lost) — reconcile open orders each loop.
4. **Kill-switch.** Force a wide spread (thin testnet book) and confirm
   `_spread_ok` halts placement.
5. **Adverse throttle.** Simulate 3 consecutive knife fills; confirm `k` widens
   50% for 15 min, then relaxes.
6. **Diagnostics parity.** Run `fill_dashboard.py` against the testnet run for
   ≥48h; confirm fill rate, time-to-fill, adverse selection, and spread-saved
   numbers are sane and match manual exchange-statement spot checks.
7. **Rate limits & reconnects.** Kill the WebSocket mid-run; confirm it
   reconnects and re-syncs open orders without duplicate placement.
8. **Capital ramp.** Only after 1–2 weeks of clean testnet: go live at minimum
   size, one symbol, with a hard daily-loss halt, and compare live fill rate to
   the testnet baseline before scaling.

## Rollback

- Paper path: `LIMIT_ENTRY_ATR_MULT=0` in `.env` + restart → back to market
  entries; diagnostics stop accruing (no data loss).
- Go-live engine: it's a standalone process — stop it; the firm falls back to
  its existing execution.

## Reality check baked into the research

The 2026-07 study (`research/limit_exec_2026_07`) already quantified the ceiling:
maker/limit entries cut per-decision execution drag from −16.5 bps to
~breakeven, but in genuine crashes limits fill anyway with ~zero protection —
so this engine is a **cost optimizer, not a risk shield**. Stops and the vol
throttle carry crash risk; this carries execution cost. Judge it on
spread-saved and adverse-selection, not on P&L.

# passive_maker_engine — staging & deployment guide

The go-live passive-maker spot engine (`passive_maker_engine.py`). Runs against
a real `ccxt.pro` exchange — the firm currently uses a paper broker, so this is
staged separately and audited on testnet before any real capital.

## What it is (and is not)

- **IS:** a passive maker execution engine — cross-sectional daily rebalance
  (top-3 momentum, long-only spot), passive `post_only` ATR-offset quotes with a
  45s/1×ATR cancel-replace lifecycle, fill diagnostics, adverse-selection
  feedback, and two hard risk controls (daily 1.5% kill switch, 2.5× spread
  filter).
- **IS NOT:** an alpha engine. The cross-sectional tilt failed our own OOS test
  (`research/cross_sectional_2026_07`: no selection skill vs random). Deploy it
  to cut execution cost and enforce risk discipline — not to beat the market.
  Success = spread-saved and neutral adverse-selection, not returns.

## Prerequisites

```bash
pip install "ccxt>=4.4"        # includes ccxt.pro (async/websocket) in modern builds
export EXCHANGE_API_KEY=...    # TESTNET keys for staging
export EXCHANGE_API_SECRET=...
```

## Staging protocol (do every step before real capital)

1. **Testnet only.** Uncomment `ex.set_sandbox_mode(True)` in `main()` and use
   *testnet* keys. Confirm `fetch_balance()` shows testnet funds, not mainnet.
2. **post_only proof.** Force a quote that would cross (temporarily set
   `ENTRY_K = -0.001`); confirm the engine's own guard rejects it AND, if it
   reaches the exchange, that `postOnly` bounces it. A passive engine must never
   pay taker — this is the single most important check.
3. **Lifecycle.** Watch a quote for one cycle: it must cancel at 45s if unfilled
   and re-quote, and cancel early if price runs `>1×ATR` away. Confirm no
   orphaned orders (kill the process mid-quote; on restart, `cancel_all()` and a
   fresh `fetch_open_orders` reconcile should clear leftovers — add that
   reconcile before mainnet).
4. **Spread filter.** On a thin testnet book, confirm placement pauses when the
   spread exceeds 2.5× its rolling average.
5. **Adverse feedback.** Simulate a knife fill (or wait for one); confirm the
   symbol's `k` widens 25% and the dashboard shows it under "Widened offsets".
6. **Kill switch.** Temporarily set `DAILY_KILL_PCT = -0.1`; confirm a small
   drawdown halts placement and cancels all open quotes.
7. **Dashboard parity.** Run `python passive_maker_dashboard.py` alongside for
   ≥48h; spot-check fill rate / spread-saved against the exchange statement.
8. **Reconnect.** Drop the network mid-run; confirm websocket reconnect and no
   duplicate placement.

## Go-live ramp

Only after 1–2 clean testnet weeks:
1. Real keys, `set_sandbox_mode` OFF, **`capital_usd` at the minimum tradable
   size**, top-3 only.
2. Run for a week; compare live fill rate + adverse-selection to the testnet
   baseline. If they match and the kill switch never mis-fires, scale capital
   gradually.
3. Keep the dashboard and an independent equity alert running at all times.

## Known gaps to close before mainnet (deliberately left for the operator)

- **Open-order reconciliation** on startup (fetch_open_orders vs internal state)
  — the template cancels-all on exit but doesn't yet re-sync orphans on boot.
- **Partial fills**: `_on_fill` treats fills as complete; add partial-fill
  accounting for the diagnostics and remaining-qty re-quote.
- **Per-exchange fee tiers / rebates**: `MAKER_FEE`/`TAKER_FEE` are placeholders;
  wire to the account's actual schedule.
- **Rate-limit budgeting** across many symbols under `fetch_order` polling —
  prefer `watch_orders` (order-update stream) over polling at scale.

## Rollback

It's a standalone process — stop it (`Ctrl-C` triggers `cancel_all()`), or kill
it and run one manual `cancel_all` reconcile. The paper firm is unaffected;
nothing here touches the running test cycle.
```

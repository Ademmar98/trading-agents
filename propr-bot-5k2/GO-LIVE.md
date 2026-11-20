# Propr 5K demo #2 — go-live checklist

Everything here is prepared. The only missing pieces are the two credentials
for the new demo account. Nothing is running yet, and nothing will run until
they are filled in.

## What this deployment is

An identical copy of the live 5K strategy, on its own account, with its own
process, state, lock and logs. It is isolated on purpose: a bug in one account
cannot touch the other two.

| Setting | Value | Why |
|---|---|---|
| Universe / signal | top-5 by trailing 336h funding | measured edge: funding-continuation |
| Rebalance | every 168h (weekly) | 72h and 336h both tested worse |
| Gross exposure | 20% of equity (~4%/slot) | worst 30d drawdown ~-7.1% vs ~-10.6% at 30% |
| Stop | -20% per name (auto-fitted to the 6% gate) | tighter stops crushed the backtest |
| Take-profit | +30% (1.5x stop) | 1.0x/2.0x/3.0x all tested: no reliable gain |
| Trailing | arms at +8%, trails 12% of peak | removing it cost -5.6pp in October |
| Chase filter | skip if 24h > +8% or >5% above 4H EMA20 | removing it cost -10pp over 7 months |
| Entry | limit at best bid +1bp, 45s, then market | see below |
| Walls | -3% daily / -6% drawdown | challenge rules |
| Halts | -2% daily / -5% from start (flatten) | stops the bleeding before the wall |

Expected behaviour, honestly: **median +1.6%/month at 20% gross, ~1 month in 5
is red, worst observed month -5.8%.** Roughly a 31% chance of hitting +10%
before -6%, 2% chance of losing the account, and 67% chance of neither within
120 days. This is a small real edge, not a lottery ticket.

## Entry execution (upgraded for this deployment)

The old rule placed limits at **mid - 3bps**. Measured top-of-book spreads on
the traded names run **0.10-4.8bps**, so that price sat *below the best bid* on
every single one: it only filled if price ticked down, and the 12s timeout meant
most entries paid the full spread on the market fallback anyway.

New rule:
1. Read the live book (`l2Book`) for bid/ask/mid.
2. Skip the entry (retry next 5-minute cycle) if the spread is wider than 12bps.
3. Otherwise post a limit at **best bid + 1bp**, never above mid + 6bps and
   never crossing the ask.
4. Wait 45s. If unfilled, **cancel and confirm the cancel**, then market-buy.
5. Immediately place the exchange-side stop and take-profit, and log what was
   actually paid vs mid to `entry_quality.csv`.

The cancel-and-confirm step matters: without it, a fill that lands at the
timeout gets joined by the fallback order and the position ends up **2x sized**.
That bug caused most of the earlier bleed.

## Go-live steps

1. Fill in `propr-bot-5k2\.env`:
   - `PROPR_API_KEY` — the new account's key (`pk_live_...`)
   - `PROPR_ACCOUNT_ID` — `urn:prp-account:...` for the new demo
   - leave `CHALLENGE_START=5000`
2. Run the preflight (read-only, places no orders):

   ```
   cd C:\Users\DELL\OneDrive\1m\propr-bot-5k2
   python preflight.py
   ```

   It checks credentials, that the API resolves the *same* account id that is in
   `.env`, that the account is clean, that the funding feed works, and then
   prints the exact first-cycle plan: the 5 pairs, dollars per slot, entry limit,
   stop and take-profit. It exits non-zero on any blocking problem.
3. Start it:

   ```
   powershell -ExecutionPolicy Bypass -File C:\Users\DELL\OneDrive\1m\start_5k2.ps1
   ```

   (or just `python watchdog.py` in that folder)

4. Verify, ~2 minutes after start:

   ```
   Get-Content propr-bot-5k2\live_run.log -Tail 20
   ```

   You want: `FUNDING runner LIVE account urn:prp-account:<new id>`, then one
   `REBAL open LONG` line per name, then 5 positions each with a stop and a TP.
   Ask me and I'll verify stops/TPs directly from the API.
5. It is already in `start_bots.ps1`, so it comes back after a reboot. Until
   `.env` is filled, the watchdog logs `not starting: ... placeholder` and exits
   quietly instead of crash-looping.

## Safety rails now in place

- **Single-instance lock**: each deployment binds a port derived from its folder
  path. A second bot on the same folder exits with code 3 and the watchdog stops
  rather than fighting it. Two bots on one account is how a book silently
  doubles.
- **Stray-process cleanup**: the watchdog now matches `python.exe` *and*
  `pythonw.exe`. The old filter only matched `python.exe`, so a windowless
  orphan could survive a watchdog restart and trade alongside the new bot.
- **Crash-loop backoff**: 4 exits in 5 minutes pauses restarts for 5 minutes and
  sends one Telegram message instead of spamming.
- **Account guard**: a `pk_live_` key paired with the beta account id refuses to
  start. `.env` is loaded at import time now, so a direct run cannot silently
  fall back to the wrong account.
- **Missing-leg repair every cycle**: if a stop or TP is ever missing, the bot
  re-places both at the live quantity.

## What NOT to change

Every one of these was tested and rejected on the 7-month panel (it helped
October and hurt the record — curve-fitting):

- gross 30%, q=3, rebalance 72h, K=168h, stop 35%, tp 3.0x, market-only entries
- a BTC-trend overlay that scales exposure down in downtrends: return halved
  (+10.8% -> +4.6%) for a 1pp drawdown improvement
- a volatility-target overlay: +10.8% -> +8.8%, Sharpe 1.89 -> 1.60
- adding the 7-day reversal leg live: correlation 0.61 with funding, and it
  loses -0.72% on funding's 10 worst days, so it does not hedge what hurts

## Files

| File | Purpose |
|---|---|
| `fund_bot.py` | the strategy (identical to the other deployments) |
| `watchdog.py` | keeps the bot alive; scoped to this folder only |
| `preflight.py` | read-only readiness check + first-cycle plan |
| `.env` | credentials (placeholders until you paste them) |
| `cache/` | state + funding history, empty at first start |
| `entry_quality.csv` | created on the first fill: paid vs mid, in bps |

Cross-account health check from the workspace root:
`python reconcile_all.py 7`

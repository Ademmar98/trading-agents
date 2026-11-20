# Propr Challenge — Live Bot Performance Report

Date: 2026-08-30
Account: `urn:prp-account:iqw6EgAYpQ4Z`
Bot: `/opt/propr-bot/bot.py --live` on VPS `69.48.202.100`
Run window: 2026-07-29 -> 2026-08-29 (~31 days)

## Strategy
- Signal: cross-sectional momentum (`mom lb96 k3/long`) over 49 symbols, long-only
- Risk: `risk_pct=0.005` (CFG_PATIENT, selected by DEADLINE_DAYS=90), SL = 2 x ATR, TP = 2 x SL distance, 1 concurrent, 72h max hold
- Challenge: $5k CLASSIC_1STEP, target +10% ($500), daily wall $150 (3%), DD floor $4,700 (6%)

## Results (live, ~31 days)
| Metric | Value |
|---|---|
| Equity (Aug 29) | $5,081.39 |
| Net P/L | +$81 (~1.63% of 10% target) |
| Day-1 low | $4,830 (-3.4%, near daily wall) |
| Floor breached | No |
| Target reached | No |
| Entries opened | 37 |
| Closed trades | 15 |
| Current open | 1 (FARTCOIN, +uPnL) |
| Entries refused (risk gate) | 43 |

## Realized closed-trade outcomes (recent 20 records)
Almost all stop-outs, TPs cancelled:
- INJ: -$9.13 -0.35 -9.42 (stop filled, TP cancelled)
- ENA: -$23.45 (stop filled), -$0.11
- TRUMP: -$16.74, -$5.84, -$6.73, -$3.20 (all stops; TPs cancelled)
- Net realized on closes ~ -$81; offset by unrealized on the open FARTCOIN

## Red flags / engineering issues
1. **2,082 `order_create_failed` [400] errors** - dominated by an ADA retry loop (Aug 1), then intermittent.
2. **43 risk-gate refusals** (daily-loss-breach blocks) - bot fires but can't trade after losses.
3. **`DEADLINE_DAYS=90`** set on operator instruction -> selects CFG_PATIENT. Bot's own boot log:
   "measured: 0 breaches at every horizon; target in 52% of 90d windows. NOT a 30-day plan."
   The 30-day deadline terms have NOT been confirmed against Propr's written rules.
4. **Cloudflare 502 errors** in the log - API instability.
5. Equity effectively flat after ~31 days (+1.6%), consistent with the "drawdown control, not alpha" admission (expected ~+0.5%/mo OOS).

## Verdict
- Risk control works: never hit the walls, self-halted near the daily floor and recovered.
- Live edge is UNPROVEN: realized closes are net-negative; the account is carried by one open position.
- 2,082 order errors and the unfilled deadline ambiguity are the two things to fix/confirm before trusting it.

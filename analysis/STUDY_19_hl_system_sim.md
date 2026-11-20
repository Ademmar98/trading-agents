# Study 19 — 30-day portfolio simulation of `hl_system`

**Run:** `analysis/hl_system_backtest.py --slide 5`
**Universe:** 169 Hyperliquid perp assets (232 listed − 55 delisted − 8 short history)
**Windows:** 5 × 30 days, sliding 3 days each, ending 2026-07-16 → 2026-07-28
**Costs:** Hyperliquid base perp **maker** fee 0.015%/side, both legs. No taker anywhere.
**Capital:** one shared $10,000 book (not N independent accounts — study #16 lesson)

## Coverage: 2 of 3 modules

Module A (CVD divergence at liquidity walls) is **not simulated**. Hyperliquid
serves no historical trade tape and no historical L2 — probed and confirmed
(`trades`, `tradeHistory`, `l2BookHistory` all return nothing). Rebuilding CVD
from kline volume is exactly the error study #10 made, so A is excluded and
said so rather than proxied. B and C run through the **shipped classes**
imported from `hl_system`, not reimplementations.

## Headline — and why it is wrong

| window | return | buy & hold | "excess" | max DD | trades | WR | PF |
|---|---|---|---|---|---|---|---|
| 06-28 → 07-28 | **+0.01%** | −2.44% | +2.45% | 0.60% | 169 | 62.1% | 1.01 |
| 06-25 → 07-25 | **+0.21%** | −0.69% | +0.90% | 0.55% | 146 | 62.3% | 1.12 |
| 06-22 → 07-22 | **−0.44%** | −4.61% | +4.17% | 1.14% | 170 | 60.0% | 0.84 |
| 06-19 → 07-19 | **−0.63%** | −6.26% | +5.63% | 1.14% | 147 | 59.2% | 0.76 |
| 06-16 → 07-16 | **−0.57%** | −6.09% | +5.52% | 1.14% | 138 | 59.9% | 0.77 |

Read naively this is a triumph: **+3.74% mean excess over buy & hold, positive
in 5/5 windows**, max drawdown 0.95% against a 5% wall never approached.

It is not a triumph. **Average gross exposure is 6.0% of equity.** The book is
94% in cash. Beating a market that fell 4% while holding almost nothing is
arithmetic, not skill — and the giveaway is visible in the table without any
further work: the strategy return is pinned near zero in every window
(+0.01 … −0.63%) while "excess" tracks −1× buy & hold almost exactly.

## The decisive test: exposure-matched null

Scale buy & hold to the same average exposure the strategy actually carried,
then ask what is left.

| window | avg exposure | B&H at that exposure | strategy | **skill** |
|---|---|---|---|---|
| 06-28 → 07-28 | 6.4% | −0.16% | +0.01% | **+0.17%** |
| 06-25 → 07-25 | 6.2% | −0.04% | +0.21% | **+0.25%** |
| 06-22 → 07-22 | 6.5% | −0.30% | −0.44% | **−0.14%** |
| 06-19 → 07-19 | 5.8% | −0.36% | −0.63% | **−0.27%** |
| 06-16 → 07-16 | 5.2% | −0.32% | −0.57% | **−0.25%** |

**Skill: mean −0.05%, median −0.14%, positive in 2/5 windows.** Zero, to
within noise. The entire apparent edge is the cash weighting.

## Supporting evidence

- **Profit factor 1.01 / 1.12 / 0.84 / 0.76 / 0.77** — median **0.84**, only
  2/5 at or above 1.0.
- **Win rate ~60% but negative expectancy.** Median net/trade is +0.37% to
  +0.66% while *mean* net/trade is −0.22% to +0.26%. A high hit rate with a
  fat left tail: many small winners, few large losers. Consistent with the
  no-stop design (see below).
- **Concentration check (study #10):** net excluding the best two pairs is
  *worse* than total net in **5/5 windows** (e.g. −$63.45 → −$99.85). No
  outlier is masking anything — the loss is broad, across 92–104 pairs traded
  with only ~55–64 profitable. This is the opposite of the study #10 failure
  mode, and it means the result is a genuine flat, not a hidden lottery.
- **By module:** Module B is +$8.98 / +$2.77 / −$14.57 / −$28.39 across
  windows; Module C is −$9.39 / +$3.62 / −$30.09 / −$33.13. Both drift
  negative as the window moves back. The 12–19 trades where **both** modules
  agreed had the best win rate (66–85%) but the sample is far too small to
  mean anything.
- **Circuit breakers never fired.** 0/5 halts, worst drawdown 1.14% against
  the 4.0% / 4.8% / 5.0% tiers. The risk layer was never tested by this data
  because the strategy never risked enough to test it.

## Two design defects the simulation exposed

**1. The system is radically under-deployed against its own caps.**
`conviction = Σ(strengths) / 3 modules`, and with `min_module_agreement = 1`
most signals are single-module, so conviction lands near 0.17 and multiplies
the size down by ~6×. Result: 8 concurrent positions (the gate was blocking at
max-positions for ~500 of 720 hours) totalling **6% of equity**, against a
theoretical 8 × 3% = 24% cap. The binding constraint is the conviction
divisor, not any risk limit. Fixing it would scale a ~0 edge up — amplifying
the drawdown without creating expectancy.

**2. There is no per-position stop in the shipped system.**
`RiskManager.position_size` uses ATR to *size*, but no stop order is ever
placed; exits come only from module EXIT signals, the VWAP target, or the
account-level flatten. The simulation added an ATR stop and it fired just
2–12 times per window against 105–149 signal exits — but it is charged at the
stop price, which is **optimistic**: a stop is a taker action and a maker-only
system has no guaranteed stop at all. The fat left tail in the trade
distribution is the cost of that gap.

## Verdict

Consistent with studies #1–#17. Maker-only execution genuinely removes the
taker leg — round-trip cost here is 0.03% versus the 0.14% that killed earlier
work — and the system is well-behaved, never breaching a risk tier. But
**removing a cost does not create an edge**: after correcting for exposure,
expectancy is −0.05%, and PF sits below 1.0 in 3 of 5 windows.

The one thing this does *not* replicate from study #10 is the concentration
failure: this result is broadly flat rather than two lucky pairs. That makes
it a more trustworthy negative.

**Not deployable.** Module A remains untested and untestable without forward
tape capture — that is the only unexplored surface left in this design, and it
would need a live recorder running for weeks before any claim could be made.

## Files

- `analysis/hl_system_backtest.py` — simulation (re-runnable; caches to scratchpad)
- `analysis/hl_system_backtest_results.json` — per-window + per-pair results
- `hl_system/` — the system under test

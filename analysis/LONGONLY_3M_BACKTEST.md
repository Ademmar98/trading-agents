# Long-Only, Unleveraged — Last-3-Month Backtest

**Date:** 2026-11-20
**Constraint from user:** long only, no leverage. This rules out the funding carry
(which needs a short perp) and every short signal in the earlier batteries.

## 1. Direct answer — last 3 months (2026-07-20 → 2026-10-18)

The last 3 months were an exceptional alt-coin bull market. True long-only unleveraged
results (equal-weight basket of 50 perps, 7.5bp/side fees):

| Strategy | Return | Max DD | K5 (3%/6%) | B-Book (10%/20%) |
|----------|--------|--------|-----------|------------------|
| Buy & hold, no rebalance | +29.8% | — | — | — |
| Buy & hold, daily rebalanced | +53.7% | −15.6% | **BREACH** (daily-loss wall) | **PASS** |
| 30d mom, top-10, weekly rebal | +49.6% | 1.8% | **PASS** | PASS |
| 14d mom, top-10, weekly rebal | +65.9% | 8.1% | BREACH | PASS |

So in that specific window, plain long-only B&H passed the B-Book (+10% target, 20% DD
wall) with room to spare, and a 30-day momentum rotation even passed the tight K5 walls.
**But this is one bull-market window — it is not evidence of an edge.**

## 2. Robustness — the "edge" evaporates

I stress-tested the apparent winner on every overlapping 90-day window in the 200-day
hourly panel (12 windows), across 21 parameter combinations:

- B&H control: K5 pass 3/12, B-Book 9/12
- Best momentum configs: K5 3–4/12, B-Book 9–10/12 (essentially identical to B&H)
- Results are parameter-unstable (K5 passes range 1/12 to 4/12 across neighbours)

## 3. Proper out-of-sample test — 2 years of daily data, 25 coins

142 overlapping 30-day windows, train (older) / OOS (newer) split:

| Strategy | K5 train | K5 OOS | BB train | BB OOS |
|----------|----------|--------|----------|--------|
| Buy & hold | 21/68 | 19/68 | 42/68 | 34/68 |
| 7d mom, top-5, daily rebal | 20/68 | 27/68 | 34/68 | 43/68 |

The 7-day rotation's OOS K5 improvement (27 vs 19) is suggestive but **not statistically
significant** (two-proportion z ≈ 1.35, p ≈ 0.18) and appears only in OOS, not train — a
hallmark of regime dependence rather than a real edge.

## 4. 6-year BTC daily — decisive falsification

440 overlapping 30-day windows, train/OOS split, long-only unleveraged timing rules:

| Rule | K5 train | K5 OOS | BB train | BB OOS |
|------|----------|--------|----------|--------|
| **Buy & hold** | 58/217 | **64/217** | 100/217 | **94/217** |
| mom14 | 67/217 | 53/217 | 81/217 | 65/217 |
| mom7 | 58/217 | 48/217 | 77/217 | 60/217 |
| sma200 | 26/217 | 53/217 | 40/217 | 72/217 |
| random50 (control) | 45/217 | 44/217 | 71/217 | 57/217 |

**No timing rule beats buy-and-hold out-of-sample.** The momentum rotation's apparent edge
does not replicate on long history. The random control sits below B&H, as expected.

## 5. Bottom line

- On the last 3 months, long-only unleveraged **buy-and-hold made +30–54%** and passed the
  B-Book easily — because the market was in a strong bull phase, not because of any signal.
- There is **no long-only unleveraged rule that reliably beats buy-and-hold** out-of-sample.
  The 30d-mom K5 pass in the last window is a bull-market artifact, not a repeatable edge.
- The funding-carry result from the earlier report is **not available** under this
  constraint (it requires shorting a perp).
- To make money with this constraint, the honest path is simply to hold a diversified
  long-only basket through bull regimes and accept the drawdowns — not to pay for a prop
  challenge (still −EV) or to chase signals that don't survive OOS.

## Files
- `longonly_3m.py`, `longonly_robust.py`, `longonly_rotation.py`, `final_longonly.py`
- `mom_robust.py`, `daily_mom_test.py`, `fetch_daily_universe.py`, `btc_timing_test.py`
- Results: `%TEMP%\opencode\longonly_3m.json`, `mom_robust.json`,
  `daily_mom_results.json`, `btc_timing_results.json`

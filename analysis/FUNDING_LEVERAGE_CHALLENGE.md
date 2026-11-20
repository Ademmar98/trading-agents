# Leveraged Funding Carry for Challenge Passing — Test

**Date:** 2026-11-20
**Question:** can the funding-carry edge be levered to clear a 30-day +10% challenge?

## 1. Setup

Real Binance data, 6+ years (2020-06 → 2026-10), spot + perp daily closes + 8h funding.
Carry return = long spot − short perp + funding received. Leverage L applied, entry+exit
fees charged once on gross notional. 227 OOS 30-day windows. K5 (+10/−3/−6), B-Book
(+10/−10/−20).

Base carry (unlevered): BTC +11.4%/yr, ETH +12.8%/yr, daily vol 0.06–0.08%, worst day
−0.3% (BTC) / −0.64% (ETH). This is the real, steady edge found earlier.

## 2. Result

| L | BTC K5 pass | BTC mean 30d | ETH K5 pass | ETH mean 30d | Max DD |
|---|-------------|--------------|-------------|--------------|--------|
| 1 | 0/227 | +0.4% | 0/227 | +0.5% | 0.1% |
| 3 | 0/227 | +1.4% | 0/227 | +1.4% | 0.2% |
| 5 | 7/227 | +2.3% | 8/227 | +2.3% | 0.4% |
| 10 | 30/227 | +4.7% | 33/227 | +4.8% | 0.7% |
| 20 | **84/227 (37%)** | **+10.0%** | 84/227 (37%) | +10.3% | 1.3% |

At **20× leverage the carry averages +10% per 30 days** — exactly the target — and passes
~37% of windows with only 1.3% max drawdown. Compare to the driftless K5 ceiling (~33%) and
B-Book ceiling (~46%): that 37% is barely above random on K5 and **below random on B-Book**.

## 3. Why this is not a usable edge

1. **20× leverage is not permitted on prop challenges.** Propr caps leverage (typically
   1:10–1:20 on majors, lower on alts) and deliberately maxing it to hit a target fast is
   the "gambling" pattern that gets funded accounts revoked.
2. **One adverse day kills it.** At 20×, the worst historical carry day is −6% (BTC) and
   −12.8% (ETH) — both breach the 3% K5 daily wall; ETH breaches the 10% B-Book wall too.
   A basis blowout (perp–spot dislocation) is exactly such a day and it has happened.
3. **No edge over the barrier ceiling.** 37% pass ≈ the 33% driftless K5 ceiling and < the
   46% B-Book ceiling. You are not beating the game — leverage just compresses the carry
   into a 30-day sprint while the walls stay the same.
4. **Cash-and-carry needs spot.** A prop account is a perp account: you cannot hold spot
   against the short perp, so the true neutral carry is not even executable there. What you
   *can* do (short perp only) is a naked directional short with none of the funding hedge.

## 4. Bottom line

- The funding carry is real at 1× (+11–13%/yr, <2% DD) — a genuine way to make money in a
  **spot + perp account with modest leverage**, but it is slow (~1%/month).
- Levering it to 20× to hit +10%/30d **does raise the pass rate to ~37%**, but that is at or
  below the random-walk ceiling, requires prohibited leverage, is one bad basis day from a
  wall breach, and isn't executable on a perp-only prop account anyway.
- So even the strongest honest edge in this whole study **cannot be turned into a +EV
  challenge pass**. The challenge remains negative-EV; the carry is only worth running on a
  real spot/perp account where you keep the spread, not inside a prop evaluation.

## Files
- `funding_leverage.py`; earlier `funding_carry.py`, `funding_report.py`

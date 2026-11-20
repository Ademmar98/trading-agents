# Shorts + Leverage Only During Challenge Phase — Does It Help?

**Date:** 2026-11-20
**Question:** user allows short + leverage during the challenge-passing phase only.

## 1. What leverage actually does

Leverage raises the **raw pass rate** — but it raises the **driftless ceiling by the same
amount**. It amplifies noise as much as signal.

BTC daily OOS, 217 overlapping 30d windows, K5 (+10/-3/-6) and B-Book (+10/-10/-20):

| Leverage | Driftless K5 | B&H K5 | Driftless BB | B&H BB |
|----------|--------------|--------|--------------|--------|
| 1× | 33/217 (15%) | 64/217 (29%) | 44/217 (20%) | 94/217 (43%) |
| 2× | 50/217 (23%) | 80/217 (37%) | 85/217 (39%) | 139/217 (64%) |
| 3× | 60/217 (28%) | 83/217 (38%) | 97/217 (45%) | 141/217 (65%) |
| 5× | 71/217 (33%) | 92/217 (42%) | 101/217 (46%) | 129/217 (59%) |

The **excess over driftless** — the only thing that is actual edge — stays roughly flat or
*shrinks* as leverage rises:

- K5 excess: +31 (1×) → +30 (2×) → +23 (3×) → +21 (5×)
- BB excess: +50 (1×) → +54 (2×) → +44 (3×) → +28 (5×)

At 5× the B-Book B&H excess collapses from +50 to +28 while the driftless ceiling doubles.
Leverage does not create edge; it compresses the time to *either* barrier.

## 2. Shorts add nothing here

Hourly 50-asset panel, 35 windows:

| Config | L | K5 | B-Book |
|--------|---|----|--------|
| Mom30d Top10 long-only | 1 | 10/35 | 24/35 |
| Mom30d Top10 long/short | 1 | 3/35 | 13/35 |
| Mom30d Top10 long-only | 5 | 15/35 | 20/35 |
| Mom30d Top10 long/short | 5 | 17/35 | 23/35 |
| Random L/S (driftless) | 5 | 6.6/35 | 17.8/35 |

Market-neutral long/short is *worse* than long-only at matched low leverage and only
matches it at 5× — where the driftless control also rises. The one genuine edge (funding
carry) requires a **spot long + perp short held for weeks** — it cannot be compressed into a
30-day +10% sprint without 10× leverage, which breaches K5's 6% wall.

## 3. The source of the "edge" is BTC drift, not leverage

B&H beats driftless at every leverage only because BTC rose historically in the OOS window.
That drift is not guaranteed forward. The 6-year full-history study already showed B&H at or
below the driftless control once you account for the bear regimes. Leverage does not make
that drift more reliable — it just makes the outcome arrive sooner.

## 4. Practical answer

- Allowing short + leverage **does raise raw pass frequency** (e.g. B&H B-Book 43% → 59–65%
  at 2–3×). So if the only goal is "pass the challenge more often," leverage mechanically helps.
- But it does **not** create a repeatable edge: the driftless ceiling rises with it, and the
  excess over random shrinks. You are not trading better — you are hitting barriers faster.
- **Shorts add no edge** in this market; they only help the funding-carry structure, which
  cannot pass a 30-day +10% sprint within the drawdown walls.
- **Warning:** deliberately maxing leverage to hit a target fast is exactly the "gambling /
  consistency" pattern prop firms flag. Even if the rules permit shorts and leverage, doing
  it *only* to clear the evaluation can get a funded account revoked. Check Propr's specific
  leverage caps and consistency rule before attempting this.

## 5. Bottom line

Shorts + leverage in the challenge phase raise the pass *rate* but not the *edge*; at 5× the
edge over a random walk is smaller than at 1×. There is no configuration here that turns a
negative-EV challenge into a positive-EV one. The honest conclusion is unchanged: do not buy
the challenge expecting a repeatable profit, whether or not leverage and shorts are allowed.

## Files
- `leverage_sweep.py`, `lev_control.py`
- Results: `%TEMP%\opencode\leverage_sweep.json`

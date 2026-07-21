# Cross-sectional selection — 2026-07 quick pass (verdict: no selection skill)

**Question:** the timing studies all failed. Cross-sectional asks a different
question — *which* coins to hold vs the others, not *when* — and has more
academic support in crypto. Does ranking a universe and holding the top-K beat
holding everything, or random selection?

**Answer: no**, even as a survivorship-biased upper bound. No factor's top-K
beats equal-weight-all; none beats random-K selection; the long-short signal is
weak and flips sign out of sample. Per the pre-agreed research economics (if the
biased upper bound shows nothing, stop), this closes the line without buying
survivorship-free data.

## Method

- 22 Alpaca US majors, daily 2021–2026. Long-only, spot, equal-weight, fully
  invested top-K, weekly rebalance, turnover costs 0.14% round trip. Everything
  100% invested, so top-K vs benchmarks isolates **selection** from timing.
- Factors: cross-sectional momentum (30/60/90d, skip last week), low-vol,
  short-term reversal, relative strength. K ∈ {3, 5}.
- Benchmarks: equal-weight-all, BTC-hold. 70/30 IS/OOS.
- **Null (decider):** random-K Monte Carlo at matched K (200 draws) — does
  factor ranking beat a dartboard?
- Diagnostic: long-short spread (top-K − bottom-K), the pure signal even though
  spot-only can't trade the short leg.

## Results

| | ALL Sharpe | ALL CAGR | vs random-K null | LS spread (ALL / OOS) |
|---|---:|---:|---|---:|
| **equal-weight-all** | **0.67** | +23% | — | — |
| BTC-hold | 0.53 | +15% | — | — |
| xmom-30 top3 (best) | 0.57 | +12% | 60.5th pct | +0.37 / −0.29 |
| xmom-60 top5 (worst) | 0.03 | −27% | 0.0th pct | −0.50 / −1.36 |
| reversal-7 top3 | 0.53 | +8% | 51.5th pct | +0.17 / −0.69 |
| lowvol-30 top5 | 0.29 | −4% | 2.5th pct | −0.40 / +0.50 |

Three clean negatives: (1) **no factor top-K beats equal-weight-all** (0.67) —
the best manages 0.57; (2) **no factor beats random-K** — every combo ≤61st
percentile of the null, most *below* 25th, none near the 95th skill bar; (3) the
**long-short spread** is at best weakly positive in-sample (momentum +0.37) and
negative out-of-sample (−0.29). Ranking by these factors is no better than
random.

## Caveats (why this is a quick pass, not the last word)

- **Survivor universe, only 22 coins.** Bias should *flatter* selection, yet it
  still fails — but a 22-coin cross-section has limited power to detect a *weak*
  effect, and real cross-sectional momentum studies use 50–100+ coins.
- **OOS window (2024-11 → 2026-07) is a single bear/chop regime** — everything
  is negative OOS, so the null test (full-period, period-agnostic) is the
  cleaner read, and it says no skill.
- **Spot-only can't trade the short leg**, where the cross-sectional premium
  mostly lives. The weak long-short spread means even the untradeable form is
  thin on this universe.

## Verdict & what a real re-run would need

For the firm's purposes (spot, long-only, ~20 liquid coins) there is **no
deployable cross-sectional selection edge**. We stop here per the agreed logic.
If ever revisited, the honest version needs: a **survivorship-free universe of
50–100+ coins** with point-in-time constituents (CoinGecko/Kaiko), and it would
measure the long-short spread as the primary signal — accepting that the
long-only spot form the firm can trade captures little of it.

## Reproduce

```
python fetch_universe.py   # daily bars for the universe -> daily_bars/ (gitignored)
python cross_study.py      # factors + null -> cross_RESULTS.json
```

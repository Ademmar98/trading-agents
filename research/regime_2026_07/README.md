# Regime-participation follow-up — 2026-07 (verdict: NO timing skill; the edge was a mirage)

**Question:** the swing study's control ("long above SMA50, cash below") beat
buy-and-hold on all 6 tested symbols. Is that a real, deployable edge — or an
artifact of cherry-picking and total-return framing? Walk-forward the SMA
period, add hysteresis, build the real multi-symbol portfolio, and test it
against honest nulls.

**Answer: it is a mirage.** Across a broad 12-symbol universe, with Sharpe
(not total return) and a cost-fair null, the SMA-crossover timing has **no
skill** — and a mindless constant cash allocation beats it on both
risk-adjusted return and drawdown.

## Method

- 12 Alpaca US majors (BTC ETH SOL LINK DOGE AVAX LTC BCH UNI AAVE XRP DOT),
  daily bars 2021–2026. Look-ahead-free: state at close[i] vs SMA±band,
  position held day i+1, close-to-close returns, 0.14% per-flip cost,
  mark-to-market throughout.
- **Stage 1**: walk-forward SMA ∈ {50,100,150,200} (train 2y / test 6mo).
- **Stage 2**: hysteresis band sweep {0,1,2,3,5}%.
- **Stage 3**: equal-weight portfolio, each sleeve on/off by its own regime.
- **Stage 4 (the decider)**: circular-shift null — roll each sleeve's own
  position series by a random offset. Same flips, same exposure, same block
  structure, same costs; only the alignment with price is destroyed. If regime
  beats this, the timing is predictive. Plus a constant-fractional-exposure
  benchmark (de-risking without any timing).

## Results

**Stage 1 — SMA period is unstable.** Pick distribution 50→47, 150→23, 200→16,
100→9 (no dominant period). WFO OOS Sharpe swings wildly by symbol: BTC +1.11,
ETH +1.02, but DOT −3.81, XRP −1.44. Dispersion this large at n~130 windows is
noise, not a robust rule.

**Stage 2 — the band helps flips, but return is negative.** Pooled over 12
symbols at SMA100: band 0→5% cuts flips 81→29 and nudges Sharpe 0.18→0.24, but
mean CAGR stays **negative (−3 to −6%)** with −76% drawdown. The whipsaw-cutting
works; the underlying return doesn't exist.

**Stage 3+4 — the null kills it.**

| SMA100, band 2% | Sharpe | CAGR | maxDD | vs cost-fair null |
|---|---:|---:|---:|---|
| regime portfolio | 0.31 | +4.2% | −73% | **5.8th percentile** (worse than random) |
| buy & hold | 0.81 | +40% | −82% | — |

| SMA50, band 2% | Sharpe | CAGR | maxDD | vs cost-fair null |
|---|---:|---:|---:|---|
| regime portfolio | 0.71 | +26% | −55% | **54.6th percentile** (= random, no skill) |
| buy & hold | 0.81 | +40% | −82% | — |
| **constant 43% cash** | **0.81** | +26% | **−49%** | (dominates regime) |

The SMA50 timing performs at the **median of its own randomly-shifted versions**
— indistinguishable from shuffling the same in/out pattern to random dates.
SMA100 is *worse* than random. And a constant 43%-in-cash allocation — never
trading — beats the SMA50 regime timing on **both** Sharpe (0.81 vs 0.71) and
drawdown (−49% vs −55%) at the same total return. The regime overlay adds
negative value: you'd be strictly better off holding a fixed cash fraction.

## Why the swing-study Stage B looked good (and was wrong)

Three artifacts, all removed here: (1) it cherry-picked BTC/ETH/SOL — the
window's biggest winners; (2) it used total return, which rewards ~43% exposure
riding a bull; (3) it had no null and no de-risking benchmark. Under Sharpe +
broad universe + cost-fair null, the "edge" is exactly random.

## Implications for the firm

1. **There is no timing/signal edge at any horizon tested** — 1H trend, 15m
   scalp, daily swing entries, and now daily regime participation all fail
   under honest testing. Stop searching for one in public-indicator crossovers.
2. **If lower drawdown than buy-and-hold is the goal, hold a constant cash
   fraction** — it beats SMA regime timing and costs nothing to run. This is a
   real, evidence-backed allocation choice (and it's the halal "cash is a
   position" posture, done correctly).
3. The firm's durable value is what the studies *couldn't* refute: hard risk
   caps, fee discipline, spot-only safety, and the daily measurement/post-mortem
   loop that produced these findings. Direction over the book (how much crypto
   vs cash) is a risk-appetite decision, not an alpha engine.

## Reproduce

```
python fetch_daily_vps.py   # daily bars for 12 majors -> daily_bars/ (gitignored)
python regime_study.py      # 4 stages -> regime_RESULTS.json
```

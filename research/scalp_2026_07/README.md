# Scalp study — 2026-07 (verdict: scalp_15m is provably negative-EV; retire it)

**Question:** does the firm's 15m scalp stack (`core/scalp15.scalp_signal` and
the `core/scalping_signals.evaluate_tf` composite) have positive expectancy
after realistic costs?

**Answer: no, and decisively.** 8,490 bracket-simulated trades across 6 liquid
majors over 2 years (2024-07 → 2026-07, 15m Alpaca bars, the firm's own feed).
The signal stack wins at the same rate as random entry; every trade bleeds ~0.12%
in costs. The `evaluate_tf` composite carries a genuine but tiny forward edge
(+0.04–0.08% at 4h) that is ~4–7× too small to survive as a scalp trigger.

## Method

- Replayed the **deployed VPS code** bar-by-bar (not a proxy). Signals on bar
  `t`, entry at `t+1` open, SL-priority intrabar bracket walked ≤96 bars (24h),
  costs 0.05% taker + 0.02% slippage per side (0.14% round trip).
- **Stage 0** (cost feasibility): for the stack's own geometry (SL = 1.5×ATR,
  R:R ∈ {1.0, 1.5, 2.0}), the win rate a *random* entry achieves vs the win
  rate costs *require*.
- **Stage 1a**: every historical `scalp_signal` BUY, its actual SL/TP bracket
  simulated → realized WR, net expectancy, and `win_prob` calibration.
- **Stage 1b**: every `evaluate_tf` BUY event's forward return at 1h/4h vs an
  unconditional baseline, sliced by tier / component / regime.

## Results

**Stage 0 — the cost bar.** At R:R 1.5, random WR ≈ 39%, breakeven WR ≈ 47–53%.
Any 15m signal must add **7–13 points of WR over random** just to break even.
Random expectancy is −0.11 to −0.16% per trade on every symbol.

**Stage 1a — the live scalp stack clears none of it.**

| sym | signals | realized WR | random WR | net exp/trade |
|-----|--------:|------------:|----------:|--------------:|
| BTC | 1466 | 39.5% | 39.6% | −0.112% |
| ETH | 1453 | 39.6% | 39.2% | −0.132% |
| SOL | 1278 | 37.3% | 38.7% | −0.143% |
| LINK| 1475 | 37.0% | 39.7% | −0.149% |
| DOGE| 1408 | 37.6% | 39.1% | −0.067% |
| AVAX| 1410 | 37.6% | 38.5% | −0.143% |
| **pooled** | **8490** | **≈38%** | **≈39%** | **−0.124%** |

Realized WR is **at or below random** on all six. With n≈1,400/symbol the ±2pt
CI makes this a solid zero, not noise. `win_prob` is badly miscalibrated: trades
labeled "60%" win 37–43%; "50%" win 32–37%. The gate the executor trusts is
worse than a coin flip.

**Stage 1b — evaluate_tf has real but untradeable drift.** BUY events beat
baseline by +0.04 to +0.08% at 4h (consistent, all 6 symbols). Strongest with
volume confirmation (SOL +0.15%) and Ichimoku agreement (AVAX +0.17%). But the
kill gate is 2× costs = 0.28%; the edge is ~4–7× too small to trigger a scalp.
The 7-tier ladder is degenerate — 99% of BUYs are STRONG_BUY, so confidence
tiers carry no discrimination.

## Recommendations for the firm

1. **Disable `scalp_15m` in the live scanner.** It is provably −EV and generates
   pure fee bleed plus deliberation/CPU load. (BUY_ONLY spot; the SELL branch
   is already inert.)
2. **Fix or retire `estimate_win_probability`.** It mislabels win rates by
   10–20 points; do not gate execution on it until recalibrated on real
   outcomes.
3. Keep `evaluate_tf`'s +5bp drift **only as a confirmation input** for
   longer-horizon (swing/daily) entries — never as a standalone trigger.
4. Flatten the 7-tier signal or recalibrate its thresholds; as-is it is a
   constant.

## Reproduce

```
# runs against deployed code on the VPS (Binance/Alpaca connectivity there)
scp scalp_study.py root@<vps>:/tmp/ && ssh root@<vps> 'cd /tmp && python3 scalp_study.py'
python analyze_scalp.py RESULTS.json
```

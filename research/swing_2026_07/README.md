# Daily-bar swing study — 2026-07 (split verdict: entries fail, regime participation wins)

**Question:** at the daily horizon — where costs go negligible and the big trend
runs are catchable — does the firm's swing stack work, and does the regime idea
that churned to death on 1H finally pay?

**Answer, two parts:**
1. The firm's deployed `swing_signal` (breakout/pullback/momentum) is **NOT
   validated** — pooled −0.32%/trade over 697 signals, and every exit geometry
   flips positive in-sample → negative out-of-sample (textbook overfit).
2. The naive **daily regime-participation** control (long above SMA50, cash
   below) **is validated** — it beats buy-and-hold on all 6 symbols with ~40
   flips (vs the 1H control's 500–700), roughly half the drawdown, and only
   ~50% time in the market. The edge is the regime filter, not the entries.

## Method

- 6 Alpaca US majors (BTC/ETH/SOL/LINK/DOGE/AVAX), daily bars 2021–2026 (~2000
  each), 4h bars for the signal's alignment filter. Ran against the **deployed
  VPS code** (`core/swing.swing_signal`), not a proxy.
- **Stage A** (event study): every historical swing BUY, its actual SL/TP
  bracket walked ≤60 daily bars, SL-priority intrabar, costs 0.14% round trip.
- **Stage B** (control): a-priori "long above SMA50, flat below," next-open
  fills, same costs. True mark-to-market Sharpe/drawdown computed separately on
  local 2022–2026 daily data.
- **Stage C** (WFO): exit-geometry grid (SL mult × RR) on the same entries,
  70/30 IS/OOS split by bar index.

## Results

**Stage A — deployed swing_signal is not robust.**

| sym | signals | realized WR | net/trade | avg R | net_sum% | B&H% |
|-----|--------:|------------:|----------:|------:|---------:|-----:|
| BTC | 136 | 14.3% | −2.48% | −0.27 | −337 | +30 |
| ETH | 135 | 30.7% | +1.91% | +0.20 | +258 | +19 |
| SOL | 107 | 23.0% | +0.73% | −0.05 | +79 | +410 |
| LINK| 131 | 17.5% | −3.29% | −0.26 | −431 | −70 |
| DOGE| 109 | 24.8% | +0.95% | −0.01 | +104 | +43 |
| AVAX| 79 | 27.0% | +1.34% | +0.04 | +106 | −93 |
| **pooled** | **697** | — | **−0.32%** | **−0.07** | — | — |

Four of six positive per-trade, but the two big losers (BTC, LINK) drag the
pool negative, and the symbol dispersion (ETH +1.9 vs LINK −3.3 at n~130) reads
as noise, not signal. **Stage C settles it:** every SL-mult × RR combination is
positive IS and negative OOS (IS +0.07 to +7.1%, OOS −1.2 to −7.8%) — the
entries carry no edge that survives out of sample, at any geometry.

**Stage B — daily regime participation works, honestly.** (true mark-to-market,
2022–2026 local data)

| sym | strategy | buy&hold | Sharpe | true maxDD | B&H maxDD | flips | time-in |
|-----|---------:|---------:|-------:|-----------:|----------:|------:|--------:|
| BTC | +127% | +69% | 0.72 | −48% | −67% | 48 | 51% |
| ETH | +169% | −29% | 0.73 | −42% | −72% | 40 | 47% |
| SOL | +477% | −17% | 0.56 | −58% | −93% | 35 | 43% |

On the 6-symbol VPS run it beat B&H on **all six** (SOL +4036% vs +410%; AVAX
+3% vs −93%). ~40 flips over 4.5y (the 1H control needed 500–700 and still
lost), drawdown roughly half of B&H, and it sits in cash ~50% of the time —
structurally the halal "cash is a position" posture. Not spectacular (Sharpe
~0.6–0.7), but positive and robust where every intraday family was negative.

## Recommendations for the firm

1. **Do not promote the swing entry signals.** Keep at most as minor
   confirmation (like `evaluate_tf`); the breakout/pullback/momentum logic has
   no out-of-sample edge. Consider demoting `SWING_ENABLED` to confirmation-only
   or off, pending the follow-up below.
2. **Lean into per-symbol daily regime participation.** The firm already gates
   deployment on SMA200 at the portfolio level; this study says make it the core
   per-symbol allocation driver — be long the trend on daily, hold cash below
   it — rather than trading complex entries. That is where the measurable,
   halal edge is.
3. **Follow-up before deploying regime participation:** add position sizing,
   walk-forward the SMA period (50 vs 100 vs 200) and add hysteresis/band to cut
   flips further, and confirm the ~0.6 Sharpe holds per-window. The SMA50 here
   is a-priori (a strength — no overfit) but its robustness across periods is
   untested.

## Reproduce

```
scp swing_study.py root@<vps>:/tmp/ && ssh root@<vps> 'cd /tmp && python3 swing_study.py'
python analyze_swing.py RESULTS.json
```

# Multi-strategy sweep — 2026-07 (verdict: 0 of 168 configs survive)

**Mandate:** find the Top 30 scalping and Top 30 swing strategies across a
large pair universe, passing a PRI ranking and hard exclusion criteria.

**Result: zero strategies survive.** 56 scalp configs + 112 swing configs
(7 strategies × parameter grids × 8–16 majors), 70/30 IS/OOS, exact friction,
exact exclusion rules. **Every** strategy has *negative* mean OOS Sharpe in
both categories. The Top-30 tables are empty; padding them would be fabrication.

## Method

- Long-only spot (halal firm — no shorts/funding; "order-flow/liquidity-sweep"
  mechanics need L2 data, proxied by VWAP-dev / vol-burst / micro-breakout).
- Data: Alpaca US majors. Scalp: 8 pairs × 15m (~124k bars each, 2023–2026).
  Swing: 16 pairs × 1D. Real pairs, not the nominal top-50; survivorship-aware.
- Friction: scalp 0.05% taker + 0.03% slip = 0.08%/side; swing 0.04% + 0.015%
  = 0.055%/side. Charged per flip.
- Per (strategy, pair): best params by IS Sharpe, single-pass OOS. PRI =
  0.35·Sharpe + 0.25·Calmar + 0.20·PF + 0.20·(OOS/IS consistency), all OOS.
- Exclusion (verbatim): OOS decay >25% vs IS; MaxDD >12% (scalp)/22% (swing);
  <100 trades/asset; (WR<40% AND R:R<1.5).

## Results

**Scalp — 0/56 pass.** All 56 fail the 12% drawdown cap; 55 also fail OOS
decay. Mean OOS Sharpe per strategy (all negative — friction is decisive):

| strategy | mean OOS Sharpe | mean CAGR | mean MaxDD | trades |
|---|---:|---:|---:|---:|
| RSI mean-rev | −1.04 | −34% | −48% | 246 |
| Bollinger mean-rev | −1.22 | −39% | −52% | 376 |
| VWAP deviation | −1.22 | −47% | −63% | 277 |
| EMA cross | −2.79 | −73% | −78% | 679 |
| Vol burst | −2.95 | −31% | −33% | 119 |
| Donchian break | −5.24 | −47% | −49% | 428 |
| MACD momentum | −5.63 | −94% | −94% | 1422 |

The 0.16% round-trip friction at 15m frequency buries every edge — the more a
strategy trades, the worse it does (MACD: 1422 trades, −94% CAGR).

**Swing — 0/112 pass.** All 112 fail "≥100 trades/asset" — a **structural
tension in the criteria**: daily-timeframe strategies over 3.5 years make
3–14 trades, so 100/asset is unreachable on daily bars. Even setting that
aside, every strategy's mean OOS Sharpe is negative (−0.29 to −0.91), and 94
also breach the 22% drawdown cap. Five "near-misses" fail *only* on trade
count (Donchian breakout on DOGE/SOL/ETH, Bollinger on GRT: OOS Sharpe
0.8–1.0, PF 2.8–7) — but on **3–8 trades**, i.e. noise, not evidence. That is
precisely why the ≥100 rule exists.

## Verdict

This is the sixth study to converge on the same conclusion, now at the largest
scale (168 configs × real friction × OOS): **no price-based strategy survives
honest validation** for this firm. Public-indicator strategies on majors do
not beat costs at any timeframe. The firm's value remains what the studies
could not refute — risk control, cheap execution (limit/maker entries now live),
and honest measurement — not signal generation.

Two criteria caveats worth noting to whoever wrote the brief: (1) "≥100
trades/asset" is incompatible with the "1D" swing timeframe; (2) shorts/funding
and L2-liquidity mechanics don't apply to a halal spot firm.

## Reproduce

```
python sweep_study.py   # fetch + sweep on the VPS -> RESULTS.json
```

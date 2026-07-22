# HTF Swing (1H/4H) study — 2026-07 (verdict: no deployable edge; a lookahead bug nearly faked one)

**Mandate:** test the pivot away from scalping — do 1H/4H swing holds (3–8% moves,
where 0.30% friction is a rounding error) provide a deployable gross edge on
halal spot? Three strategies, long-only, fixed $1,000/trade, min 1:2.5 R:R,
both frictions (0.14% maker / 0.30% taker), 22 pairs, 2022–2026.

- **A** — 1H/4H MTF trend + VWAP/EMA pullback (4H EMA50/200 bull filter, 1H dip-reclaim, RSI>40).
- **B** — 4H MSS + 1H FVG/OB retest (HTF smart-money).
- **C** — 4H Donchian(20) breakout + volume filter + ATR/SuperTrend trailing stop.

## ⚠️ The headline lesson: a lookahead bug

The first run reported **B at 68.5% WR, PF 3.56, +$48,934** — a "deployable"
result. It was a **lookahead artifact**: the 4H→1H mapping used the *currently-
forming* 4H bar, whose OHLC contains the next 1–4h of price. Fix: map only the
last **closed** 4H bar, and start each trade the bar **after** entry.

| | Buggy (lookahead) | Corrected |
|---|---:|---:|
| B win rate | 68.5% | **31.2%** |
| B profit factor | 3.56 | **0.89** |
| B net P&L (maker) | +$48,934 | **−$9,765** |

31% WR at ~2R is the random-walk expectation → no signal. See
`map_4h_to_1h()` and `simulate()` (`s0=e_idx+1`) for the guards.

## Results (lookahead-corrected)

| Strategy (TF) | Fric | Trades | WR% | Net P&L | Avg %/tr | Avg hold | PF |
|---|---|---:|---:|---:|---:|---:|---:|
| A trend pullback (1H) | maker | 1,874 | 26.0 | +$1,318 | +0.07% | 18.6h | 1.05 |
| A (1H) | taker | 1,874 | 26.0 | −$1,680 | −0.09% | 18.6h | 0.94 |
| B MSS+FVG (1H) | maker | 2,624 | 31.2 | −$9,765 | −0.37% | 81.5h | 0.89 |
| C Donchian (4H) | maker | 1,748 | 30.8 | −$15,253 | −0.87% | 58.9h | 0.71 |

**A** is break-even at best (maker) and negative on taker — the maker/taker
0.16%/trade gap *is* the entire margin. **B, C lose at both fee tiers.** Per-pair
scatter for A ranges PF 0.24→4.07 (noise, not edge); total +$1,318 is smaller
than the friction spread — i.e. inside the error bar around zero.

**Buy-and-hold benchmark:** 20/22 pairs lost over 2022–2026 (median −66%; only
BTC +38%, XRP +79%). A staying ~flat is *defensive non-participation*, not alpha.

## Verdict

The pivot's hypothesis (friction, not signal, killed the scalps) was the right
experiment — and it's **falsified as a fix**: with friction nearly neutralised
at swing frequency, the strategies *still* don't predict direction. 9th
convergent study. The only untested lever left is genuinely **non-price** data
(on-chain / flow / fundamentals), not another price-pattern timeframe. See
[[firm-strategy-research-findings]].

## Reproduce

```
python swing_study.py   # fetch (cached) + backtest on the VPS -> RESULTS.json
```

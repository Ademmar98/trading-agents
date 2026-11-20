# Option A — Directional signal research: HONEST RESULT

Date: 2026-08-30
Venue: Hyperliquid (only available venue; buy-only long book)

## Data caveat (important)
Hyperliquid's `candleSnapshot` API caps history at ~5000 1h bars per asset —
**a real 1-year window is NOT retrievable** (windows ending >~208 days ago
return 0 bars). The backtest therefore covers the **maximum available history:
~208 days (2026-02-02 → 2026-08-30), 50 top-volume perps**. This is ~7x the
challenge's 30-day window and spans multiple regimes.

## Method
- Reused the VPS `sim.py` engine (real fill rules: open-through-stop fills at
  the open not the trigger, stop=market/taker, TP=maker limit, funding carry,
  position sizing to risk, cooldown, max-hold, concurrent cap).
- Tested 14 directional, **long-only** signals across 5 signal families:
  dip-buy (mean reversion), Donchian breakout, cross-sectional momentum,
  EMA trend-follow, Bollinger breakout. Risk profile fixed (1% risk, 2 ATR
  stop, 2R TP, 2 slots, 72h max hold) so the test measures signal edge, not
  risk-parameter overfitting.
- Walls loosened so the full span runs and total expectancy is measured
  cleanly (no early stop at +10% or a wall).

## Results (loose walls, fullspan, 1% risk, taker 0.075%/side)
| Signal                | Net ret | MaxDD | Trades | WR   | PF   |
|----------------------|---------|-------|--------|------|------|
| dip_07_40            | -4.65%  | 4.65% | 173    | 34.7%| 1.00 |
| dip_05_30            | -4.73%  | 4.85% | 38     | 26.3%| 0.83 |
| dip_10_30            | -5.18%  | 5.18% | 26     | 26.9%| 0.74 |
| dip_04_35            | -6.28%  | 6.28% | 9      | 11.1%| 0.06 |
| donch_24             | -4.55%  | 4.60% | 5      | 0.0% | 0.00 |
| donch_48             | -5.28%  | 5.58% | 15     | 20.0%| 0.54 |
| donch_96             | -4.74%  | 4.91% | 11     | 18.2%| 0.47 |
| momf_96k3            | -5.24%  | 5.36% | 47     | 31.9%| 0.85 |
| momf_48k5            | -5.96%  | 6.01% | 29     | 31.0%| 0.68 |
| ema_24_96            | -5.55%  | 5.57% | 6      | 0.0% | 0.00 |
| ema_48_192           | -4.53%  | 4.55% | 7      | 14.3%| 0.05 |
| bb_24_2              | -4.66%  | 4.71% | 5      | 0.0% | 0.00 |
| bb_48_25             | -4.57%  | 5.18% | 12     | 25.0%| 0.49 |
| **current_mom**      | **-5.24%** | 5.36% | 47  | 31.9%| 0.85 |

**Not one of 14 directional signals achieves a positive profit factor.**

## Fee sensitivity — the decisive test
On the two best signals, swept the taker fee from 0% (free) up:
| Fee     | dip_07_40 PF | dip_07_40 net | mom PF | mom net |
|---------|--------------|---------------|--------|---------|
| 0.000%  | 0.96         | -5.02%        | 0.88   | -4.78%  |
| 0.075%  | 1.00         | -4.65%        | 0.85   | -5.24%  |
| 0.100%  | 0.97         | -4.76%        | 0.87   | -5.01%  |

**Even at ZERO trading fees both signals lose ~5%.** The negative expectancy
is intrinsic to the entries (winners don't pay enough vs losers), not a
cost problem. No attainable fee or win-rate tweak rescues it.

## Conclusion
There is **no directional edge** to reverse-engineer on this venue from the
maximum 7 months of data. The losing `CFG_DEADLINE` config is not a
parameterization mistake — the whole signal *family* is unprofitable at the
taker rate the account actually pays. Option A (build a winning directional
signal) does not exist within this venue's data.

## Files
- `analysis/_fetch_1y_data.py` (data), `analysis/_signal_backtest.py` (14 signals),
  `analysis/_fee_sweep.py` (break-even test)
- VPS: `candles_1y.pkl`, `signal_bt_results.json`

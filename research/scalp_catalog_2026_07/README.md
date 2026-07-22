# Scalping catalog + expanded backtest — 2026-07 (verdict: 0/36 configs profitable)

**Mandate:** exhaustive halal-filtered catalog of crypto scalping strategies, plus
a 4-year backtest across major spot pairs at 1m/3m/5m/15m. Fixed $1,000/trade,
0.10% fee/leg + 0.05% slip/side = **0.30% round-trip**, long-only spot, canonical
fixed params (no per-pair optimization → honest realized performance).

## Catalog (see the session report for the full ~54-strategy halal/feasibility matrix)

Four categories — Order-Flow & Liquidity, Volatility & Breakout, Trend & Momentum,
Mean Reversion. Halal filter: nearly all have a long-only spot form; the real cull
is **feasibility** — true order-flow (OBI, CVD, footprint, iceberg, microprice)
needs L2/tape the firm doesn't ingest, so it can only be *proxied* (VWAP-dev,
volume-burst, micro-breakout). Backtested pool = 9 OHLCV strategies spanning all
four categories.

## Data honesty

Alpaca US crypto offers ~29 USD pairs, **not 50** (many top-volume names are
Binance USDT pairs Alpaca lacks; several 2026-listed = no history). Ran 24 clean
majors. 15m/5m on the full set (2022+); 1m/3m on 6 majors (2024+, data volume).

## ⚠️ Data artifact caught

Raw run showed +$7.3M "profit" for 5m/15m mean-reversion. 100% of it came from
**two corrupted pairs (SHIB micro-price, XTZ glitch prints)** booking +100–185%
*per trade* (cf. the `round_sig` "+13,716% artifact" warning). Excluded. After
cleanup: **0 of 36 strategy×timeframe combos net positive; every clean pair×strategy
has PF < 1.0.**

## Results (clean, SHIB/XTZ removed)

Every strategy loses at every timeframe. Avg $/trade ≈ −$1.6 to −$3.6 ≈ the 0.30%
friction. Even 66–71% win-rate reversion strategies lose (wins < losses after cost).
1m is worst (friction paid most often). This is the 7th convergent study.

## Reproduce

```
python scalp_sweep.py   # runs on the VPS (Alpaca) -> RESULTS.json
```
See [[firm-strategy-research-findings]].

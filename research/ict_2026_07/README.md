# ICT Silver-Bullet / Liquidity-Sweep study — 2026-07 (verdict: no edge at any TF/killzone)

**Mandate:** backtest the most-hyped retail framework — ICT kill-zone-timed SSL
sweep → bullish MSS → FVG/OB entry, VWAP/absorption confluence — as a halal spot
LONG-ONLY system. Bearish setups → NO TRADE. Fixed $1,000/trade, 0.30% round-trip,
min 1:2 R:R, 2-consecutive-loss daily circuit breaker.

## Method (faithful Stage 1–3)

- Kill zones (America/New_York, DST-aware): London 03–04, NY-AM 10–11, NY-PM 14–15.
  A setup must *initiate* in a kill zone.
- SSL sweep: bar wicks below prior 20-bar swing low then reclaims. MSS: within 8
  bars a close breaks the immediate 6-bar structural high. Entry: first unmitigated
  bullish FVG (else OB), limit-filled on retrace.
- SL = structural low below the sweep; TP1 = +1R (bank 50%, stop→BE); TP2 = real
  overhead BSL pool (≥2R, cap 5R). Reject if no pool offers 1:2.
- Canonical fixed params. Fee model: one leg per fill.

## Results (15m, 22 pairs, 2022–2026 — the primary, least-bad TF)

- Overall: **756 trades, WR 27.6%, −$2,454.73, avg −$3.25/trade, PF 0.33.**
- Kill zones: London (297tr, WR20.9%, −$836, PF0.33); NY-AM (263tr, WR28.9%,
  −$1,024, PF0.21); **NY-PM (196tr, WR36.2%, −$594, PF0.47)** — least-bad, still loses.
- Timeframe (BTC): 5m −$246 (WR15.2%, PF0.16) worse than 15m −$106 (WR24.4%, PF0.23).
- **All 22 pairs negative; every PF < 0.7.**
- **Circuit breaker impact: 0 triggers in 4 years** — kill-zone gating makes the
  strategy so selective (~8.6 trades/pair/yr) that 2 losses rarely land in one NY day.

## Why it fails

Mean R is only ~0.24% (5m) / 0.42% (15m) — the FVG entry sits just above the swept
low, so the stop is tight. Against 0.30% round-trip friction, even a full 2R win
barely clears cost while losses are −1R−friction. WR 27.6% << the ~43% break-even.
Even maximum-confluence (FVG+VWAP−2σ+absorption) trades net ≈ 0. The tight
structural stops that give ICT its "clean R:R" are exactly what make it fragile to
spot friction. 8th convergent study. See [[firm-strategy-research-findings]].

## Reproduce

```
python ict_study.py 15Min,5Min,1Min   # runs on the VPS (Alpaca) -> RESULTS.json
```

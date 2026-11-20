# Public-Hypothesis Mining — Final Report

**Date:** 2026-11-20
**Mandate:** Mine trading claims from Reddit / X / YouTube / quant platforms, turn them into
deterministic rules, and test them until something makes money or passes a Propr challenge.
No credentials accepted; public data only.

---

## 1. What was mined

Three parallel research sweeps returned **106 distinct hypotheses**:

| Source | Count | File |
|--------|-------|------|
| Reddit (r/Daytrading, r/Trading, r/algotrading, r/InnerCircleTraders) | 35 | `%TEMP%\opencode\hyp_reddit.md` |
| X / YouTube / SMC / price-action | 36 | `%TEMP%\opencode\hyp_social.md` |
| Retail quant / academic blogs | 38 | `%TEMP%\opencode\hyp_quant.md` |

Top repeated claims: Fair Value Gap (FVG), Order Block / mean threshold, liquidity sweep /
stop hunt, equal highs/lows, BOS / CHoCH / MSS, premium–discount / OTE Fib, ICT Silver
Bullet / Judas Swing / killzones, 9/21 EMA + VWAP, Connors RSI(2), Double-7, 50/200 EMA,
Donchian/Turtle, Bollinger reversion, TTM squeeze, IBS, ORB, VWAP deviation, cash-and-carry
funding, cross-sectional momentum, pairs/cointegration, vol targeting.

## 2. What was coded

`%TEMP%\opencode\hyp_signals.py` — **34 deterministic next-bar-fillable signals** covering
all high-frequency claims: FVG/ifvg, order block, sweep, BOS, MSS, Fib, ICT session, Asian
sweep, momentum, Donchian, dip-buy, EMA cross, MACD, RSI reversal, Bollinger reversion,
BB squeeze, Supertrend, Ichimoku, VWAP reversion, candlestick patterns, volume spike, CVD
divergence, ORB, time-of-day seasonality, ATR break, IBS, RSI(2), Double-7, EMA+VWAP,
50/200 filter, pin bar, inside bar.

## 3. Test batteries (all adversarial)

| Battery | Rules | Entry | Windows | Result |
|---------|-------|-------|---------|--------|
| `hyp_battery.py` | K5 (3%/6%) + 3 risk profiles | market next-open | 33 sliding + 6 non-overlap + train/OOS + LOO + random halves + param neighbours | **All 34 fail.** Best non-overlap score ≤ 3/6; all negative. |
| `hyp_bbook.py` | Pass Fast B Book (10%/10%/20%) | market next-open | 33 sliding + 6 blocks + D1-EMA200 overlay + confluence | **All fail.** Best 16/33 = 48.5% < 0.671 driftless ceiling. |
| `hyp_daily.py` | K5 + B-book, 3 risk profiles | market next-open | 6.7y BTC daily, 390×30d + 126×90d + 65 blocks | **No edge.** Only "passing" signal (`mom`) = B&H (4/0 both). Most signals 1–46 trades in 6y. |
| `hyp_maker.py` | K5 + B-book | **passive limit** (offset × patience) | 33 sliding + 6 blocks | **Improved but still fails.** Best 15/33 = 45.5% < 0.671 ceiling and ≤ B&H 0.477. |

Controls applied throughout: driftless-martingale ceiling, buy-and-hold, non-overlapping
blocks, train/OOS split, leave-one-asset-out, 20 random half-universes, adjacent-parameter
neighbourhoods, and B-book rule geometry. Next-open fills, 7.5bp/side fees, funding drag,
and intrabar equity walls are all enforced by `sim.py`.

## 4. Verdict on directional trading

**No mined directional hypothesis survives.** Passive entries (the "wait for the retest"
that SMC claims require) lift the best B-book pass rate from ~12/33 to 15/33, but that is
still **below both the driftless ceiling (0.671 ≈ 22/33) and buy-and-hold (0.477)**.
In other words, on these challenge geometries, *trading these signals is worse than doing
nothing*. The fee+funding drag on 500–700 fills per sweep overwhelms any claimed edge.

Buying challenges stays **−EV**: $99 fee, break-even funded value ~$207, EV −$99 at zero
funded drift; population data averages −$334 per buyer. Pass Fast B Book is the best product
shape (highest ceiling) but still negative EV. **Do not buy a challenge on this evidence.**

## 5. The one thing that actually makes money: funding carry

Public Binance funding-rate history (`fapi/v1/fundingRate`), 2020-01-01 → 2026-09-21,
7,367 payments. Strategy: **long spot BTC/ETH + short equal-notional 1× perp**, collect the
8h funding, fully collateralised (no directional exposure). Net of 0.28% round-trip taker fee.

| Asset | CAGR (always-on) | Max DD | Total return | Positive payments | Gated(21) CAGR | Gated Max DD |
|-------|------------------|--------|--------------|-------------------|----------------|--------------|
| BTC | **+12.42%** | −1.51% | +119.8% | 86% | +12.77% | −0.37% |
| ETH | **+14.82%** | −1.78% | +153.4% | 86% | +15.22% | −0.36% |

Year-by-year annualised carry (gross) was positive in **all 6 years** for both BTC and ETH:

| Year | BTC | ETH |
|------|-----|-----|
| 2020 | +17.2% | +27.4% |
| 2021 | +30.6% | +37.5% |
| 2022 | +4.2% | +0.8% |
| 2023 | +7.9% | +8.3% |
| 2024 | +11.9% | +13.0% |
| 2025 | +5.1% | +5.0% |

Worst rolling 30-day (~90 payment) carry: **−1.31% BTC / −1.78% ETH**. Best: +7.7% / +9.8%.
The 21-payment gate (only collect funding when the trailing mean is positive) keeps ~90% of
the return while cutting max DD to ~0.4%.

**This is real, persistent, and low-drawdown — but it is NOT a challenge strategy.**
It yields roughly 1%/month, so it cannot hit +10% in 30 days. It requires capital on a
spot+perp exchange (Binance), actual execution, and carries exchange/counterparty risk,
basis risk, and the risk that funding compresses. It is a structural carry, not alpha.

## 6. Practical paths

- **To make money:** cash-and-carry on BTC/ETH at 1× with the 21-payment gate is the only
  public-hypothesis result with a positive, persistent, low-DD track record. Expected
  12–15%/yr on deployed capital. (Leveraging it to challenge-level returns scales drawdown
  past the 6% wall — do not.)
- **To pass a Propr challenge:** no strategy found; the challenge is negative-EV by
  construction. Do not buy.

## 7. Still open

- Code hygiene fixes (slippage guard, DD latch, stop-loss delegation, closed-bar signal,
  early `.env`) are prepared but **uncommitted**; bots remain **halted**. No orders placed.
- Keep-vs-revert decision on those fixes is still pending user instruction.

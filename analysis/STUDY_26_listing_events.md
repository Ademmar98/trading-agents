# STUDY 26 — The last non-price source: 135 Hyperliquid perp listings

**Date:** 2026-11-14
**Question.** STUDY_25 closed every price-derived family (momentum, reversal,
carry, trend, relative value, selection) and left one branch open: event data —
listing / unlock / delisting timestamps. Hyperliquid listing times and prices are
public and reconstructible, so that branch is now testable end to end. This is
the result.

**Short answer.** The listing event is real: the typical new perp loses **19%
absolute / 13% vs peers** over the 13 days after its first full day. It is also
untradeable, for three independent reasons, each sufficient on its own:
the alpha is t = 0.24–0.65, the payoff is a short-squeeze lottery (5 of 135
listings more than doubled in 13 days), and no position size that captures the
drift survives the 3% daily / 6% static rules. **No edge.**

---

## 1. Pipeline built (reusable)

| file | contents |
|---|---|
| `temp/opencode/listings_meta.parquet` | 235 HL perps: true listing time, candle/funding counts |
| `temp/opencode/listings_1h.parquet` | 1h candles, first 21d per listing (71 coins, retention limited) |
| `temp/opencode/listings_4h.parquet` | 4h candles, first 20d (141 coins) |
| `temp/opencode/listings_1d.parquet` | 1d candles, listing −2d → +30d (**all 235**) |
| `temp/opencode/listings_funding.parquet` | first 500 hourly funding prints per coin (**235 coins, 117k prints**) |
| `temp/opencode/hl_daily_panel.parquet` | 1,414 days × 235 coins daily OHLCV — the cross-sectional benchmark panel |
| `temp/opencode/btc_1d.parquet`, `btc_1h.parquet` | benchmark series |
| `temp/opencode/hl_listings2.py`, `hl_listings_daily.py`, `hl_daily_panel.py` | fetch layer |
| `temp/opencode/listing_xs_study.py`, `listing_stop.py`, `listing_split.py` | analysis layer |

### Three data facts that cost time and will matter again

1. **`candleSnapshot` is not a history API.** It stores ~5,000 bars per interval
   per coin: 1h ≈ 208 days, 4h ≈ 2.3 years, 1d = full history. A request whose
   window reaches further back than the retention silently returns `[]`, not an
   error. `startTime=0` returns the *last* 5,000 bars, so the earliest bar it
   hands back looks like a listing date and is not one (it is `now − 5000h`).
   Anyone who builds a listing study on `candleSnapshot(startTime=0)` will
   "discover" 171 coins listing on the same day.
2. **The true listing time is the first funding print.** `fundingHistory` with
   `startTime=0` returns the *earliest* 500 prints; print #1 is the moment the
   perp went live. This gives listing timestamps for the full 235-coin universe
   in 235 calls.
3. **Span caps are real and per-interval:** at 1h a single request may span at
   most **240h** (250h already returns `[]`); 1d accepted a 400-day window
   (401 bars) and 4h a 60-day one (361 bars). Chunk the window and verify the
   reply is non-empty — an empty array is the failure mode, never an error.

### Event sample

135 listings from 2024-01-03 to 2026-10-27 (66 in 2024, 58 in 2025, 11 in 2026).
The 100 coins with a 2023-05-12 timestamp are the Hyperliquid mainnet launch
batch — one event, excluded from inference. Survivorship: only perps alive today
are in `meta`, so delisted listings (the worst performers) are missing. That bias
runs *against* the short side, i.e. the results below are, if anything, generous
to the strategy.

---

## 2. The result that looked like an edge

Entry = open of the first full day after listing. Exit = close of day H. Costs
10bp/side, funding priced from the hourly prints.

Benchmark = BTC over the same calendar window.

| H (days) | n | short % | BTC-excess short % | hit | t | median short % |
|---:|---:|---:|---:|---:|---:|---:|
| 3 | 135 | +2.75 | +2.66 | 63% | 1.15 | +7.82 |
| 7 | 135 | +6.70 | +6.73 | 67% | 2.04 | +13.47 |
| 10 | 135 | +7.38 | +7.96 | 69% | 2.23 | +16.98 |
| 14 | 135 | +7.86 | **+8.71** | 70% | 2.21 | **+18.41** |

Raw vs BTC it is better still: 14-day short **+7.86%**, hit rate 70%, t = 1.94;
BTC-excess **+8.71%**, t = 2.21, block-bootstrap p = **0.019**
(CI [+0.5, +16.1]). By year: 2024 **−1.75%**, 2025 **+18.56%**
(t = 2.81), 2026 **+9.10%**. Two of three eras positive, one t-stat above 2.

That is exactly the shape that has seduced every previous family in this file.
It does not survive contact with a correct benchmark.

---

## 3. The benchmark that kills it

New listings are small, high-beta assets. Comparing them to BTC leaves the
alt-vs-BTC drift in the "edge": over the 135 event windows the equal-weight alt
market returned **−3.4%** (median −4.9%) while BTC returned **+0.9%**. So compare
each listing to the equal-weight return of **all other coins over the same
calendar days** — same dates, same regime, same beta if beta = 1.

| benchmark | 14-day short edge | t | median |
|---|---:|---:|---:|
| BTC | +8.71% | 2.21 | — |
| equal-weight alt market (β = 1) | +2.74% | 0.65 | +12.9% |
| β = 1.25 | +1.89% | 0.45 | — |
| β = 1.50 | +1.04% | 0.24 | — |
| β = 2.00 | −0.66% | −0.15 | — |

The panel's own median beta against the EW factor is 1.00, and fresh listings
are structurally above 1 (smaller, more volatile, no options market). At any
plausible beta the alpha is **indistinguishable from zero**.

Two independent confirmations:

* **Placebo.** Random coin, random 13-day window: mean short excess −0.28%,
  sd 20.6%, n = 2,445. The event mean (+2.33%) is **0.13σ** above that.
* **Era split.** vs the EW benchmark: 2024 **−5.85%** (t −0.99), 2025 **+10.70%**
  (t 1.58), 2026 **+7.32%** (t 0.72). One cell out of 18 (2025, H = 10, t = 2.78)
  clears 2 — the number of false positives 18 tests at 5% are expected to throw.

---

## 4. The payoff shape is the real finding

Distribution of 14-day short excess, deciles:

```
  p0     p10     p25     p50     p75     p90    p100
-199.3  -50.9   -11.9   +12.9   +32.7   +47.3   +71.2     (% of notional)
```

* median **+12.9%**, mean **+2.3%** — a fat left tail, not a fat right one.
* the **top 10 events produce 195% of the total gross P&L** (614pp of 315pp):
  remove them and the strategy loses money.
* **5 of 135 listings (3.7%) more than doubled** in 13 days: IP +187%,
  FOGO +188%, GRASS +200%, NOT +141%, HYPE +115%. A short at 1× notional on any
  of these is instant ruin.
* trimming the tails *raises* the mean to +7.1% (5%) / +9.5% (10%, t = 4.04) —
  i.e. the body of the distribution is genuinely negative for listings, and the
  mean is destroyed by squeezes.

This is **selling squeeze insurance on newly listed microcaps**. The median
trade wins and the average trade is zero. Nothing in a 3%/6% rulebook can hold
that.

---

## 5. Stops do not rescue it

Exit at the stop price when the daily high breaches entry × (1 + s):

| stop | short % | xshort % | hit | median | t | worst |
|---:|---:|---:|---:|---:|---:|---:|
| none | +5.66 | +2.25 | 67% | +13.0 | 0.53 | −210.7 |
| 5% | +3.59 | +0.17 | 44% | +2.1 | 0.06 | −236.1 |
| 10% | +4.42 | +1.01 | 45% | +2.5 | 0.35 | −236.1 |
| 15% | +5.17 | +1.75 | 49% | +2.9 | 0.58 | −236.1 |
| 20% | +5.51 | +2.10 | 53% | +5.3 | 0.67 | −236.1 |
| 30% | +5.21 | +1.79 | 58% | +6.0 | 0.53 | −236.1 |

The stop removes exactly the squeezes it is supposed to remove *and* the drift.
Best t anywhere is 0.67, era signs flip (2026 negative at every stop ≥ 5%,
2025 at 5%), and the worst case is unchanged because squeezes gap through the
stop.

---

## 6. Challenge arithmetic — no size works

14-day short, per-event edge +2.33%, sd 49.5% (event-to-event dispersion).

| notional / equity | expected | daily σ | P(1-day loss > 3%) | months to +10% |
|---:|---:|---:|---:|---:|
| 0.25× | +2.3%/month | 3.3% | 18% | 4.3 (before any drawdown) |
| 0.50× | +4.6%/month | 6.6% | 33% | 2.2 |
| 1.00× | +9.3%/month | 13.2% | 41% | 1.1 |

Every row is a fail: at 0.25× you are already at a coin-flip of breaching the 3%
daily rule; the first 100% squeeze (−3% at 0.03× notional, −25% at 0.25×) ends
the attempt. Sizing to survive the observed worst case (~200% adverse) inside
the 6% wall means ≤ 0.03× notional ⇒ **+0.07% per event ⇒ 0.28%/month ⇒ ~3
years of flawless execution for one 10% target.**

And the frequency is not there anyway: 4 listings/month in 2024–25, **0.6/month
in 2026** (11 listings in 11 months), with a 13-day hold.

---

## 7. Verdict

**Event data is closed too.** The pipeline works, the data is now local and
reusable, and what it says is:

1. The listing effect is a **beta-and-tail** phenomenon, not an alpha. Measured
   honestly it is +1.0% to +2.7% per trade at t ≤ 0.65.
2. Its **payoff geometry is incompatible with the constraint set**: a real
   negative median funded by occasional 100–200% squeeze losses. Prop rules
   monetise the tail against you.
3. It is **too rare** (0.6 events/month now) and too slow (13-day hold).

This is the same conclusion as the 40 constructions in STUDY_25, arrived at from
the only source that was not price data. The generalisable lesson, and the reason
this study was worth running: **a real, large, repeated median effect (here
−13% vs peers over two weeks) can still be worthless** when its dispersion is
3–4× its mean and its loss tail is the width of the account.

### What would have to be true for a listing strategy to work

* an ex-ante classifier that separates the 5 squeezes from the 130 bleeds —
  with n = 135 and 3.7% positive-class prevalence, nothing here can be
  estimated; it needs a feature set we do not have (FDV, float, holder
  concentration, pre-listing futures OI, launchpad provenance);
* or an instrument that caps the squeeze (options — not offered);
* or a rulebook without a 6% static wall.

None of the three exists in this account.

### Assets to keep

`hl_daily_panel.parquet` (1,414 × 235 daily OHLCV, full history, no Survivorship
in the price series itself) and `listings_meta.parquet` are the two things worth
keeping from this branch — the panel is a better benchmark than BTC for any
future cross-sectional test, and the listing timestamps are reusable for
"attention" controls.

---

## 8. Recommendation, unchanged and now fully evidenced

Do not buy another challenge. There is no constructible edge in this venue's
price data (STUDY 25) or in its event data (this study), and the fee is −$119
per purchase at zero drift. Both halts stay on.

# Study 22 — the carry retest: the last control-beating construct fails out of sample

**Question.** `pop/quant/carry_live.py` was the only construct in any of the
three trees that beat its own random-leg control on a holdout it was not
discovered on (k=5 24h: holdout return −0.79% vs control, return edge +3.98 pts,
carry edge +7.74 pts; k=8 24h: +2.89%, edge +3.91 pts). Everything else had
already failed. Does it reproduce?

**Shorts are not implemented.** `ProprRiskClient` has `open_long` and no
`open_short`, so the long/short book the study measured cannot be placed. The
only deployable half is long the most-negative-funding names. Both halves are
reported below.

## Method

- Panel: `cw.pkl` / `fw.pkl`, **149 Hyperliquid perps, 2026-08-07 → 2026-11-11
  (2316 hourly bars)** — a window the carry study never saw (it used
  2026-01-31 → 2026-07-30, 160 assets).
- Simulator: a line-for-line port of `pop/quant/neutral.py::simulate_neutral`,
  with the leg-selection switch generalised to `both | long | short`. Verified
  **bit-identical** to `neutral.compare` on this panel (return, carry, price,
  cost, max DD, turnover all diff 0.00e+00) before any variant was run.
- Control: the identical book rebuilt on **random legs**, 6 seeds, every other
  mechanic untouched.
- Costs: 4.5bp taker/side + square-root impact (`coef 1.0`, cap 2%), ADV =
  trailing 24h dollar volume. Funding charged hourly, signed, per asset.
- `t_edge` = t-stat of the daily return of (live − mean control).

## 1. Under the challenge's own drawdown wall, every cell halts

`dd_halt = 4.5%` (the shipped bot's halt, inside the 6% wall), 24h rebalance:

| book | k | span | return | carry | price | cost | maxDD | ret edge | t_edge |
|---|---|---|---|---|---|---|---|---|---|
| L/S | 5 | train | −4.79% | +0.48% | −4.80% | 0.46% | 4.79% | +0.10 | +0.10 |
| L/S | 5 | **holdout** | −4.62% | +0.35% | −4.20% | 0.77% | 4.67% | −1.90 | −0.61 |
| L/S | 8 | **holdout** | −4.80% | +0.31% | −4.52% | 0.60% | 5.05% | −3.26 | −1.61 |
| long | 5 | **holdout** | −4.77% | +0.17% | −4.65% | 0.29% | 4.79% | +0.30 | +1.43 |
| short | 5 | **holdout** | +4.04% | +1.08% | +4.87% | 1.90% | 9.40% | −10.09 | −1.83 |

Every configuration that trades hits the halt inside the window. The one
positive row is the short book, and its control made **+15.34%** — the carry
signal *lost* to random legs by 10 pts.

The recorded holdout was +7.74% of carry. Reproduced, the same k=5 24h book
produces **+0.35%** of carry on the new panel.

## 2. With the halt removed, the return is price dispersion, not carry

109 names with ≥$5M/day ADV, full window, **no halt**:

| book | k | rebal | return | carry | price | cost | maxDD | turnover | control | t_edge |
|---|---|---|---|---|---|---|---|---|---|---|
| L/S | 5 | 48h | **+19.26%** | +5.94% | +17.14% | 3.81% | 11.44% | 67× | −14.89% | +1.81 |
| L/S | 8 | 48h | **+23.77%** | +4.09% | +22.98% | 3.30% | 8.04% | 60× | −2.18% | +1.80 |
| L/S | 8 | 24h | +5.32% | +4.52% | +6.11% | 5.32% | 10.48% | 98× | −5.34% | +0.93 |
| long | 5 | 24h | −1.24% | +5.34% | −0.28% | 6.29% | 34.92% | 109× | −1.84% | +0.00 |
| long | 8 | 48h | +15.77% | +2.56% | +17.68% | 4.48% | 34.14% | 76× | +0.68% | +0.77 |
| long | 5 | 168h | +0.28% | −1.51% | +2.73% | 0.93% | 25.32% | 16× | −3.33% | +0.27 |

Two facts kill it:

1. **The drawdowns are larger than the challenge wall.** The L/S book's best
   return (+19 to +24%) carries 8–11% of drawdown; long-only carries 25–35%.
   Against a 6% static wall, none of these survive to collect. That is why
   section 1 halts everywhere.
2. **The carry is real but the price leg is not hedgeable on perps.** Carry is
   ~+6.7% per 95 days at gross 1.0 on liquid names ≈ **26%/yr**; the same book's
   price P&L is a ±20% random walk over the same window. Equal-notional long and
   short baskets do not cancel beta — they differ in beta, and the dispersion of
   the *difference* is the risk. `neutral.py` says this in its own docstring:
   the real trade is long spot + short perp, and Propr routes no spot.

## 3. The turnover arithmetic on the deployable half

Long the most-negative-funding names, liquid subset, no halt:

- 24h rebalance: carry **+5.34%**, costs **6.29%** → carry does not pay for its
  own turnover.
- 48h: carry +4.45%, costs 3.25% → funding nets +1.2% over 95 days, but price
  P&L is −7.25%.
- 168h: carry **−1.51%** → the ranking has no persistence at a weekly horizon.

The long leg is a short-momentum book: names with deeply negative funding
underperformed the market by 7–18% over the window. That is the mirror image of
the old "funding-implied momentum" finding, and it means the long-only half is
structurally on the wrong side.

## 4. The one place funding is large: single-hour spikes

The K=336h smoothing in the 1m funding arm erases the only prints that are big.
Event study on the same 149-coin panel, entry at close[t+1], costs 0.19% RT.
Every qualifying hour is one event, so rows overlap and the t-stats below are
inflated; the deduplicated numbers are in the text under the table:

| event | hold | N | gross | net | win | t_net | control | edge |
|---|---|---|---|---|---|---|---|---|
| F ≤ −2e-4/h (long) | 6h | 1748 | −0.38% | −0.57% | 40.6% | −1.16 | −0.12% | −0.45 |
| F ≤ −1e-3/h (long) | 24h | 343 | −1.87% | −2.06% | 40.8% | −1.96 | −0.02% | −2.04 |
| F ≤ −1e-3/h (long) | 48h | 334 | −5.68% | −5.87% | 31.4% | −2.68 | +0.17% | −6.03 |
| F ≥ +5e-4/h (short) | 24h | 90 | +4.36% | +4.17% | 65.6% | +2.28 | −0.36% | +4.53 |

Deeply **negative** funding predicts further decline, decisively (t = −2.7 at
48h). It is a dump-in-progress marker, not a subsidy worth holding.

Extremely **positive** funding predicts a 24h drop: the only positive number in
this study. It is not established:

- 90 prints, but only **9 coins** (SAGA 29 events, CASHCAT 21, PURR 16, APEX 10,
  HMSTR 5, XMR 5, PONS 2, GOAT 1, W 1), i.e. 73% of the sample is three names.
- Many are the same coin on consecutive hours. Deduplicated to **one entry per
  coin per day** the sample is **N=35: mean +4.36%, median +2.67%, win 74.3%,
  day-clustered t = +1.24.** Dropping the three big clusters (N=14) gives +3.23%,
  t = +1.08. Sep–Nov only (N=11) gives +4.95%, t = +1.51. Suggestive, not
  significant.
- Liquidity is better than feared but still thin: median $1.2M/day, 38 of 90
  under $1M/day, p90 $36M. A $5k account can size here; a funded account cannot
  scale it.
- It needs a **short**, which `ProprRiskClient` does not implement, and the same
  short book on liquid, continuously-ranked names lost 10 pts to its control
  (section 1). The signal is a one-hour over-leverage print, not a carry.
- The distribution is hostile: median favourable, but the 95th percentile of the
  price move is +10% against the short and CASHCAT's +45% 24h move is in the
  sample. A $50k/day book does not fill at 5bp.

## 5. What the challenge actually demands

Barriers +10% target / −6% wall (5k Classic), 3% daily, no edge, Monte Carlo:

| σ per month | 30d pass | 30d breach | 90d pass | 365d pass |
|---|---|---|---|---|
| 3% | 0.1% | 3.2% | 5.6% | 30.3% |
| 5% | 4.2% | 18.7% | 22.8% | 37.8% |
| 10% | 26.6% | 48.0% | 38.0% | 38.7% |
| 20% | 39.1% | 60.1% | 39.4% | 39.4% |

With zero edge the pass rate converges to the barrier ratio 6/16 = **37.5%** at
long horizons and is **invariant to size**. A 30-day deadline caps it at 26–39%
depending on volatility.

The best out-of-sample number anywhere in the corpus is `deep_hunt_v5` core12
at **+0.50%/month, 4/5 positive, worst window −3.51%, max DD 4.06%**. That drift
is worth almost nothing against the target: at σ=5%/mo, +0.5%/mo gives a 5.1%
30-day pass rate and 45.9% over a year.

To make the 30-day challenge a coin flip you need **≈10%/month at σ=5%/month** —
a monthly Sharpe near 2.0. Nothing in the corpus is within an order of magnitude
of that (best ≈0.2).

## Verdict

1. The carry construct does **not** reproduce. Its discovery-window holdout was
   a 51-day funding regime that carried +7.74%; the same book on the next 95
   days carries +0.35% and loses to its own control.
2. The deployable long-only half is negative in 10 of 12 configurations, and
   its funding income is smaller than its turnover cost at every rebalance
   frequency that is also profitable.
3. The long/short construction has positive carry (~26%/yr gross on liquid
   names) but 8–11% drawdowns with no halt — larger than the 6% wall. It cannot
   be harvested because Propr routes no spot leg to hedge the dispersion.
4. The only statistically suggestive funding signal (short extreme
   positive-funding spikes, +4.2%/24h on the raw sample, but **t = +1.24** once
   deduplicated to one entry per coin-day, N=35) is concentrated in three
   names, is not significant, and requires a short the client does not
   implement.
5. Therefore: **no construct in the three trees both survives a control and is
   deployable long-only.** The correct action remains the latched halt.

## What would change this

- **A spot leg on the same venue.** Long spot + short perp collapses the
  dispersion and leaves the carry. That is the actual trade; it is unavailable.
- **A strategy with ~10%/month of measured drift at ≤5% monthly vol.** That is
  the bar. It does not exist in the corpus, and two independent audits (the 36
  JSON results and the pop 480-config sweep) both say selection carries no
  information out of sample.
- **If Propr's challenge has no time limit**, the honest statement changes from
  "you cannot pass" to "you are buying a 37.5%-odds barrier lottery and the only
  way to maximise it is to stop paying costs". **Resolved in Study 23**: there is
  no time limit — a 64-day-old attempt is still `active` and no product schema
  contains a time field — and at the pass-optimal risk the lottery is 34–50%,
  not 37.5% at a fixed σ. The barrier is not the binding constraint; the
  configuration is.

## Reproduce

```
python C:\Users\DELL\AppData\Local\Temp\opencode\carry_retest.py        # sections 1
python C:\Users\DELL\AppData\Local\Temp\opencode\carry_clean.py         # section 2
python C:\Users\DELL\AppData\Local\Temp\opencode\carry_grid.py          # section 3
python C:\Users\DELL\AppData\Local\Temp\opencode\event_funding.py       # section 4
python C:\Users\DELL\AppData\Local\Temp\opencode\barrier_math.py        # section 5
python C:\Users\DELL\AppData\Local\Temp\opencode\crosscheck_carry.py    # bit-identity check
```

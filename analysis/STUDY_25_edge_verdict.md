# STUDY 25 — The edge hunt is over. Here is what the data says.

Date: 2026-11-14
Panels: A = 149 coins, 1h, 2026-08-07 → 2026-11-11 (97d, BTC +15.3%)
        B = 157 coins, 1h, 2026-01-31 → 2026-07-30 (181d, BTC −17.7%)
Costs: 10bp/side (5bp fee + 5bp slip) unless stated. Execution: signal at close t,
       trade at **open t+1**, exit at **open t_end+1** (no same-close fill).

---

## Part 1 — The counterparty is real. That question is closed.

Read from Propr's live transparency API (`/api/transparency/api-payouts`,
`/api/transparency/revenue`, `/gateway/v1/stats/funded-traders/history`).

| Metric | Value |
|---|---|
| Gross revenue | **$6,194,394** |
| Purchases | 52,165 (avg fee $119) |
| Payout records | 9,854 (9,769 processed, 56 rejected) |
| Paid out to users | **$4,022,575** |
| Platform share deducted | $1,052,127 |
| Unique users ever paid | **1,133** |
| Payout ratio | **64.9% of revenue** |
| On-chain | 9,490 transfers on chain 1 (Ethereum L1 — real gas, real tx) |
| Funded traders today | 1,969 (555 on 2026-07-17, +10/day) |

Monthly user payouts: May $63k → Jun $222k → Jul $279k → Aug $1,293k →
Sep $1,490k → Oct $1,728k → Nov (14d) $705k. Monthly unique payees: 61 → 201 →
252 → 375 → 469 → 447 → 253.

**This is not a scam.** It is a solvent business paying out roughly two-thirds of
fees it collects. The paper-account and discretionary-A-booking language in the
terms is a legal shield, not evidence of non-payment.

### But the payout distribution is a lottery

| Cut | Lifetime paid to a user |
|---|---|
| median payee | **$415** |
| mean payee | $3,550 |
| 361 users | ≥ $1,000 |
| 89 users | ≥ $10,000 |
| 5 users | ≥ $100,000 |
| largest single payout | $48,675 |
| single-payout median | $142 |

Concentration: **top 10 users = 28.7%** of all money paid, top 50 = 59.3%,
**top 100 = 74.1%**, top 500 = 96.8%. 334 users were paid exactly once.

Estimated ~6,500 paying accounts lifetime (revenue by country gives ~4,800
traders across the top 20 countries = $4.69M of $6.19M). So:

- P(ever paid) ≈ 1,133 / 6,500 ≈ **17%**
- lifetime spend ≈ $952, expected receipt ≈ $618 → **average paying trader loses ~$330**
- to reach the top 1.4% ($10k+) you must out-earn 98.6% of paying customers

The economics are the economics of a prop firm: 65% payout ratio, power-law
distribution, median buyer is the product.

---

## Part 2 — Five new test families, all with controls. All failed.

Prior work killed funding-as-signal, long-only selection, B&H, baskets, trend
filters, and the 14-signal trade-engine sweep. This session tested the
constructions that sweep could not: **portfolio-level, always-in, no stops,
next-open execution, funding priced.**

### 2.1 Time-series and cross-sectional momentum (`mom_portfolio.py`)

Weekly and daily rebalance, lookbacks 7/14/28d, long-only and dollar-neutral,
against a 200-draw random long-only null with identical schedule.

| book | panel A | panel B | verdict |
|---|---|---|---|
| TS mom lb14 LS, weekly | +47.5%, Sharpe 3.36 | +10.2%, Sharpe 0.91 | dies at daily rebalance (−21.6%, −15.7%) |
| TS mom lb7 LS, weekly | −26.2% | −16.7% | sign flips vs lb14 |
| CS mom lb28 LS, weekly | −2.7% | +5.9% | noise |
| **long-only, any signal** | −15% to −42% | −20% to −55% | **identical to the random null** |

Random long-only null: panel A ann p5/p50/p95 = −47.8% / −34.3% / −6.9%; every
long-only signal lands inside that band. Nothing selects better than chance.

Momentum only looks alive at one (lookback × frequency) cell and dies when either
is moved. That is the definition of a fitted parameter, not an edge.

### 2.2 Cross-sectional reversal — the one that nearly fooled me (`rev_test.py`)

At **close-to-close** execution, 7-day cross-sectional momentum is reliably
negative in both panels: t = −2.33 (B weekly), −3.03 (B daily), −3.36 (A). That
implies reversal is real and significant.

At **next-open** execution it evaporates:

| execution | A reversal lb7 | B reversal lb7 |
|---|---|---|
| close-to-close | +17.8% (t 0.69) … +63% (t 1.49) | +13.7% (t 0.76) |
| next-open | +1.9% (t 0.11) / +17.8% (t 0.69) | +11.1% (t 0.55) / +13.7% (t 0.76) |
| p(mean ≤ 0) bootstrap | 0.21 – 0.37 | 0.26 – 0.60 |

**It was the bid-ask bounce.** The "signal" is the last trade printing at the ask
and the next at the bid. Untradeable by construction. The liquidity filter makes
it *worse* in panel B (−25.5%) and better in panel A (+82.4%) — sign flips across
panels, which is a third independent confirmation of noise.

Gross-of-cost the reversal book earns +27% (A) / +39% (B); net it loses 39% / 44%
annualized. Even if the effect were real, **it is smaller than its own
transaction cost.** (Annualized figures on 97/181-day windows are unstable; the
sign-consistency and t-stats are what carry the conclusion.)

### 2.3 Carry — funding is genuinely large, and genuinely priced (`study26_book.py`)

| book (short high-funding / long low-funding, dollar-neutral) | funding leg | price leg | net |
|---|---|---|---|
| panel A, hold 7d | **+15.6%/yr** | −14.5%/yr | −1.1%/yr |
| panel B, hold 7d | **+24.9%/yr** | −10.0%/yr | +12.4%/yr (t 0.80) |

Funding statistics (`funding_fact.py`): 87.8% of coin-days have positive funding;
longs pay mean +6.6%/yr, median +10.2%/yr; cross-sectional dispersion
**14.1%/yr**; BTC funding +8.9%/yr. Top payers: CASHCAT +41%, XMR +39%, APEX +35%.
Bottom: KAITO −100%, SKR −114% (shorts pay longs).

**The funding cash flow is the single most reliable thing in the entire dataset**
(vol 0.5–1.0%, Sharpe 20+ in isolation). It is also almost exactly offset by the
price drift of the same ranking, because the market knows: high funding means
crowded longs that are winning. You are being paid to take the other side of a
position that has been right.

### 2.4 Beta-neutral carry — isolating the funding (`beta_carry.py`)

Rolling 20-day beta hedge with BTC removes the beta (−0.08 → +0.01) but the
residual is **idiosyncratic dispersion, not beta**, so it barely helps:

| variant | A ann / Shp | B ann / Shp |
|---|---|---|
| unhedged hold 1d | +19.6% / 1.19 | +3.3% / 0.33 |
| beta-hedged hold 1d | +30.2% / 1.58 | −1.5% / −0.06 |
| funding only | +20.2% / 25.3 | +30.5% / 26.0 |

### 2.5 Book-width sweep — the last nail (`carry_width.py`)

To shrink the idiosyncratic leg, widen the book. The optimum flips between panels:

| q (fraction per side) | A benchmark | B benchmark |
|---|---|---|
| 0.1 | −22.7% (Shp −0.79) | −4.4% (−0.15) |
| 0.2 | +19.6% (1.19) | **+12.4% (0.98)** |
| 0.3 | +16.5% (1.30) | −5.5% (−0.55) |
| 0.4 | +18.1% (1.68) | −10.4% (−1.35) |
| 0.5 | **+28.6% (2.70)** | −6.3% (−0.88) |

Panel A says "go as wide as possible, Sharpe 2.7". Panel B says "wide books lose
money". A parameter that has opposite optima in two halves of the same year is
not an edge — it is a regime bet with a fitted knob.

### 2.6 A correction I owe the record

Earlier in this session I read the alt basket as "−34% while BTC was flat" and
started building a long-BTC/short-alt factor on it. That was an **annualization
artifact**: −34.1% was the annualized rate over the last 62 days of panel A.
The actual cumulative numbers are alt-EW **+14.0%** in panel A and **−24.6%** in
panel B. The long-BTC/short-alt book is t ≈ 0 (−11.3% / −2.6%) with a random
control at −14.2% / −6.2%. **No BTC-vs-alt factor exists.** The trade would have
been a bet on a number that wasn't there.

---

## Part 3 — What is actually invariant

Across both panels, all widths, both frequencies, next-open execution:

1. **Funding paid by longs is positive ~87% of days** and disperses 14.1%/yr.
2. **Shorting the high-funding quintile collects +8% to +50%/yr**, every cell, both panels, always positive.
3. **The price leg of that same book is −10% to −15%/yr with 7–28% vol**, and its
   sign is not forecastable at any width or lookback.
4. **Turnover is the dominant term.** A daily-rebalanced cross-sectional book
   turns ~0.6–1.5 of gross per day = **22%+ per year at 10bp/side.** Every
   high-turnover signal in this corpus, including the ones that are gross-positive,
   is destroyed by this alone.
5. **Long-only selection is indistinguishable from random** in both panels.

---

## Part 4 — Verdict

**There is no forecastable price edge in 9.5 months of Hyperliquid perp data.**
Not momentum, not reversal, not carry, not trend, not relative value, not
selection. I have now run ~40 constructions across two independent panels with
random controls, next-open execution, and priced funding. The null wins every
time. Two apparent winners (7d reversal, carry at wide q) each dissolved under a
single legitimate robustness check.

Combined with STUDY_24: **buy-and-hold's 41% pass rate is not skill, it is the
driftless first-passage value at that asset's volatility** (theoretical 37.5–40%
for the 10/6 barrier, measured 41.1%).

So the money question does not have a strategy answer. It has an arithmetic one:

- A challenge with zero drift has P(pass) ≈ 37–41% and yields **nothing** when it
  passes, because the funded account also has zero drift.
- Expected value = 0.40 × $0 − $119 = **−$119 per purchase.**
- Propr collects that $119 and returns 65% of the aggregate to the top ~17% of
  payees, 74% of it to the top 100 users.

We are not in that top 100 today, and nothing in this corpus suggests a path in.

---

## Part 5 — What would have to be true to try again

Pre-register these *before* touching data, or don't spend the money:

1. **A signal that survives next-open execution after costs at two different
   rebalance frequencies, in both panels, with t > 2 and a bootstrap p < 0.05.**
   Nothing in the corpus has come close.
2. **Turnover below 0.1 of gross per day.** Above that, 22%/yr of cost makes the
   exercise arithmetically impossible.
3. **A data source this venue does not provide:** a catalyst/listing/unlock feed
   (STUDY_18 was a census, never tested as a strategy), or L2 depth. The HL data
   wall (1h capped ~180d, 15m ~52d, no tape) is the binding constraint on
   microstructure work.
4. **A reason the counterparty pays you more than the option is worth.** Right now
   the arithmetic says they don't, for anyone outside the top 1%.

## Recommendation

1. **Keep both accounts halted.** No edge means every dollar of challenge fee is
   a −EV purchase. The halt latch costs nothing and is correct.
2. **Do not buy another challenge** until item 1 in Part 5 is met.
3. If the goal is to make money from these markets, the honest ranking is:
   (a) hold BTC at sub-1x and accept the 37–41% barrier odds if you want the
   option; (b) build the event-data pipeline, which is the only untested source;
   (c) stop paying fees.
4. The funding carry is real and worth ~15–25%/yr — but it requires a spot leg to
   hedge, and Propr offers none. That trade belongs on a venue where you can hold
   spot, not on a long-only paper account with a 6% wall.

## Files
- `payout_audit.py` — payout ledger analysis
- `funding_fact.py` — funding census
- `mom_portfolio.py` — momentum, both panels, controls, barrier MC
- `rev_test.py` — adversarial next-open reversal test
- `study26_book.py` — carry with funding priced
- `beta_carry.py` — beta-neutral carry
- `carry_width.py` — width sweep
- `btc_alt.py` — BTC-vs-alt factor (and the annualization correction)

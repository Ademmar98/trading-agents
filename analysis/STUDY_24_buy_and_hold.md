# Study 24 - buy and hold, measured against the actual barriers

**Question.** Studies 21-23 killed every alpha claim and showed the shipped bot
runs the worst barrier shape at a third of the optimal risk. The obvious
follow-up: does the dumbest possible strategy, buy and hold, do better? And if
so, is it a strategy?

**Short answer.** Yes, it beats the bot by 3-4x - but it is not a strategy. It is
a variance dial. B&H scores exactly what the barrier geometry says any zero-edge
asset with the right volatility must score (~41-50% per 10/10/5 phase), and it
scores that identically whether the market went up or down, whether you use BTC
or a diversified basket, and whether you use a trend filter. The bot lost
because its volatility was 3.4x too low, not because it lacked a signal.
And the variance that passes the challenge is 3-10x more than a funded account
can survive.

All numbers below are barrier tests on 1h panels with funding charged and a
0.1% entry fee: 50 coins 2026-03-25 -> 2026-10-19 (BTC +14.6%) and 157 coins
2026-01-31 -> 2026-07-30 (BTC -16.9%). Every 6h start, no deadline, no lookahead.
A start is scored `pass` if +10% is touched before -10% or a -daily% day.

---

## 1. The result, on the user's actual product (10/10/5)

| asset | bull panel | bear panel | mean |
|---|---|---|---|
| BTC | 49.4% | 32.7% | **41.1%** |
| ETH | 38.5% | 28.2% | **33.4%** |
| SOL | 49.9% | 37.4% | **43.7%** |
| BNB | 50.5% | - | - |
| EW-10-majors basket (real wicks) | 49.3% | 32.2% | **40.8%** |
| all coins, pooled | 37.0% (50) | 32.4% (157) | **34.7%** |
| per-coin median | 36.6% | 31.8% | **34.2%** |
| **the shipped funding bot** | - | - | **11%** |

Two things to notice before anything else.

**First, the basket is BTC.** An earlier version of this test built the basket
from closes only, which deletes every intrabar wick; it reported 62.6% / 34.4%.
With the legs' highs and lows aggregated properly the basket is 49.3% / 32.2%,
i.e. the same as holding BTC. Diversification buys nothing here, because the
basket's monthly sigma is 14.3% versus BTC's 10.9% - the legs are too correlated
for the average to be calmer. The 62.6% was a bug in my own test. Corrected.

**Second, the two regimes bracket the theory.** A zero-drift GBM at each panel's
own measured sigma against the same barriers scores **45.4%** on the bull panel
(10.9%/mo) and **41.0%** on the bear (13.3%/mo). BTC measured 49.4% and 32.7% -
a 4.0pp overshoot in the bull, an 8.3pp undershoot in the bear, mean 41.1% in a
theoretical band of 41-45%. The bull overshoot is the +14.6% drift; the bear
undershoot is the -16.9% drift plus fat tails. The bull and bear panels are not
two different edges; they are two draws around the same geometry. The block
bootstrap confirms there is no way to tell them apart: with ~11 independent
19-day blocks per regime, the standard error on each pass rate is 11-13pp and
individual blocks range from 0% to 100%.

So B&H's 41% is not alpha. It is the fair value of the barrier shape at the
asset's variance. Nothing has to be forecast for this number to appear.

## 2. Why the bot's 11% is not a mystery

The zero-drift barrier surface, no costs, 730 days (no deadline), 10/10/5:

| sigma/month | 2% | 3% | **4%** | **5%** | **6%** | **7%** | **8%** | 10% | 13% |
|---|---|---|---|---|---|---|---|---|---|
| P(pass) | 30.5 | 45.0 | **49.0** | **49.7** | **49.5** | **49.8** | **49.2** | 46.9 | 41.3 |

There is a plateau at 49-50% for 4-8%/month of volatility, a cliff below 3%
(too little movement to reach +10% before the account ages out of usefulness),
and a slow decline above 10% (the 5% daily wall starts clipping).

BTC at 1x is 10.9-13.3%/mo - already past the plateau. The shipped bot is
2.24-5.60%/mo: below the plateau, and at the beta configuration roughly at the
cliff. Its 11% is what 2.9%/mo scores. Nothing about its funding signal enters
this calculation.

**The pass-optimal B&H exposure is 0.5-0.7x BTC, not 1x.** That is the single
most useful number in this study.

## 3. Product shape dominates everything else

Same test, same assets, three barrier shapes:

| product | BTC bull | BTC bear | all coins bull | all coins bear | theory (zero drift, 10.9%/mo) |
|---|---|---|---|---|---|
| 10/10/5 (free trial, Professional) | 49.4% | 32.7% | 37.0% | 32.4% | 45.4% |
| 10/6/3 (legacy K5, 1-Step 9/3/3 family) | 15.7% | 8.0% | 18.3% | 16.7% | 20.8% |
| 10/20/10 (Pass Phase1 phase 2) | 59.9% | 44.2% | 66.0% | 56.6% | 64.9% |

The 3% daily wall is what separates these, and crypto cannot avoid it. Measured
on the 50-coin panel, days where the intrabar low is 3% or more below the prior
close:

- median coin: **45.2%** of all days
- BTC: 12.0%
- 49 of 50 coins exceed 5% of days

A -3% daily limit is not a risk limit on a crypto account; it is a timer. That
is why 10/6/3 never leaves the band 20-26% on the zero-drift surface, at any
sigma from 8% to 25%/mo, while 10/10/5 never drops below 34% over the same
range. The daily wall does the killing. The bot's own 11% on that shape is therefore much
closer to its ceiling than it looks - and that ceiling is the reason the shape
is unplayable.

The mirror image: 10/20/10 is the easiest shape on the menu (65% theory, 60-78%
measured on BTC/ETH/SOL/BNB in the bull). But every product that uses it is
either two-phase (Pass Phase1 Fast, where it is phase 2 only) or reports the
implausible 0.1% phase-1 target. As a two-phase attempt, 0.60^2 = 36% loses to
the single-phase 10/10/5 at 41%.

## 4. Trend filters do not survive both regimes

"With no deadline, sitting in cash is free, so only hold above the trend." That
is the natural idea and it fails the two-regime test. Filter = hold only while
close > SMA(N hours), exit to cash below, re-enter above, pay a round trip each
switch. 10/10/5:

| asset / filter | bull | bear |
|---|---|---|
| BTC buy&hold | **49.5%** | **32.9%** |
| BTC SMA500 | 38.6% | 16.6% |
| BTC SMA200 | 58.8% | 9.8% |
| ETH buy&hold | 38.6% | 28.5% |
| ETH SMA200 | 29.4% | 20.8% |
| SOL buy&hold | 49.9% | 37.4% |
| SOL SMA500 | 53.8% | 20.5% |
| EW basket buy&hold | **49.3%** | **32.2%** |
| EW basket SMA500 | 61.9% | 6.2% |

Every filter that helps in one regime destroys the other. SMA200 on BTC is the
best line in the bull (58.8%) and the worst strategy in the bear (9.8% - it
spends 67% of the sample in cash and misses the recoveries). This is parameter
selection, exactly like the "old edge": two regimes, one filter, and you get to
pick which one you report. Not deployed.

## 5. B&H versus the bot, in money

| window | BTC | ETH | SOL | EW-10 | EW-all | funding bot |
|---|---|---|---|---|---|---|
| 3M to 2026-11-10 | +16.4% | +11.9% | +18.7% | +17.3% | +14.8% | **-2.1%** |
| account life 2026-09-09 -> 11-10 | -4.7% | -14.8% | -16.6% | -10.5% | - | **-7.1%** |

Read the second row carefully, because it is the honest one: over the live
account's own two months, the market **fell**, and the bot lost 7.1% against a
basket that lost 10.5%. The bot did not underperform the market in its own
lifetime; it underperformed by being flat-vol and then paid costs. It lost to
B&H over the 3M window only because that window went up.

So B&H is not a better prediction machine. It is the same zero-edge exposure
with 3-5x more of it, and a barrier that pays for touching +10% before -10%
does not care where the drift came from.

## 6. The part that kills the plan: the funded phase

The challenge phase is an option - you can only lose the fee. The funded phase
is capital. Model: 13%/mo sigma (the bear panel's BTC; the bull's 10.9% is
milder, so this is the pessimistic end of the range), -10% wall, -5% daily, 365
days, breach = the account is gone. Static drawdown (the Professional / Bronze
shape), 80% profit split, profits only.

| leverage | sigma/mo | P(lose account) | P(any payout) | E[profit] | EV after split | median payout |
|---|---|---|---|---|---|---|
| 0.1x | 1.3% | 2.0% | 49.1% | 1.8% | **1.5%** | 0.0% |
| 0.2x | 2.6% | 24.6% | 47.2% | 3.6% | **2.9%** | 0.0% |
| 0.3x | 3.9% | 44.4% | 42.0% | 5.1% | **4.1%** | 0.0% |
| 0.5x | 6.5% | 65.5% | 30.4% | 7.1% | **5.7%** | 0.0% |
| 1.0x | 13.0% | **99.9%** | 0.1% | 0.1% | **0.0%** | 0.0% |

(Zero drift. With +1.5%/mo drift: 0.1x -> 2.3% EV / 0.7% ruin; 0.3x -> 6.7% EV /
31% ruin; 1x -> 0.1% EV / 99.9% ruin. With -1%/mo drift: 0.3x -> 2.8% EV / 54%
ruin.)

**The risk that passes the challenge is the risk that loses the funded account.**
At the pass-optimal 0.5-0.7x, a funded account is gone within a year with
65% probability. At 1x - the exposure the bull panel rewards with a 49% pass
rate - survival probability is **0.1%**. Not "risky": arithmetically certain
death, because a 10% static wall against a 13%/mo random walk is absorbing.

You therefore have to de-lever by 5-10x *after* passing, which means the funded
account earns 1.5-5.7% of notional per year, not the 10%+ the challenge phase
was chasing.

And these ruin numbers are **optimistic**: the model is normal, crypto is not.
Median coin has a -5% day on 22.1% of days; a normal model at 18%/mo sigma
predicts 6.5%. Fat tails mean the daily wall fires 3.4x more often than the sim
believes. Treat 24.6%/44.4%/65.5% as lower bounds.

## 7. What this does to the fee-per-pass arithmetic

p per phase = 0.41 (BTC, both regimes, single 10/10/5 phase):

| product | price | account | phases | P(pass) | cost / expected pass | **cost per $1k funded** |
|---|---|---|---|---|---|---|
| Professional | $1,000 | 1,000,000 | 1 | 41% | $2,439 | **$2.44** |
| Polymarket Bronze (9/6/3) | $49.99 | 50,000 | 1 | ~24% | $208 | $4.17 |
| Bronze Challenge | $99.99 | 100,000 | 2 | 17% | $595 | $5.95 |
| Silver (trailing) | $199.99 | 200,000 | 2 | 17% | $1,190 | $5.95 |

The $1,000 product is the cheapest per funded dollar by 2.4x, because it is
single-phase and 10/10/5. Everything cheaper on the sticker is worse per unit
of account. Silver is trailing, which is strictly worse than static.

Then the EV side. On a $1M funded account run at 0.3x after passing: EV after
split 4.1%/yr = **$41,000/yr** against a $2,439 expected fee. At 0.1x: $15,000/yr
and 2% annual ruin. The multiple is 6-17x.

That multiple is too good, and that is the point. Either the funded account is
not a real dollar obligation (the live account is `type: paper`), or the payout
terms cap what you can extract (profit caps, consistency rules, scaled
allocations, withdrawal minimums), or both. **None of those terms are in the
API.** The entire decision therefore reduces to a counterparty question that no
amount of strategy work can answer: *does Propr actually pay, and under what
caps?*

## 8. Verdict

1. **B&H beats the shipped bot 3-4x** (41% vs 11% per 10/10/5 phase) and it
   does so with zero prediction. The bot's failure was a variance configuration,
   not a missing edge.
2. **B&H is not a strategy and cannot be improved by one.** Measured pass rate
   sits inside the zero-drift barrier band at the asset's sigma (41.1% measured
   vs 41-45% theoretical; the gap is each panel's own drift). Basket = BTC once
   wicks are aggregated (the earlier 62.6% was my bug). Trend filters win one
   regime and lose the other. The only dial that moves the number is variance,
   and the pass-optimal setting is **0.5-0.7x BTC**, which is *below* 1x.
3. **Product shape dominates asset choice.** 10/6/3 caps at ~20% because the
   median altcoin has a -3% day 45% of the time. 10/20/10 is the easiest shape
   but is two-phase for everything except the products that matter. The only
   single-phase 10/10/5 with real size on the menu is the $1,000 Professional.
4. **The challenge and the funded account want opposite risk.** 0.5-0.7x passes
   at 49%; the same 0.5-0.7x loses the funded account with 65% probability
   inside a year, and 1x is 99.9% certain death. De-levering after the pass is
   mandatory, and it cuts the funded income to 1.5-5.7% of notional per year.
5. **So the real answer to "what about buy and hold" is: it is the correct
   baseline, it is not an edge, and it does not settle the decision.** The
   arithmetic says buy the $1,000 Professional, pass it at ~0.6x, then run 0.1-
   0.3x. The arithmetic also says that cannot be right. The tiebreaker is the
   payout terms, which are not published in the API and are the only thing left
   to check.
6. **Do not un-halt either live book for this.** The beta account is `type:
   paper`, the 5k account is FAILED, and resuming a 2.9%/mo strategy against a
   10/10/5 wall is the 11% option described in section 2.

## Reproduce

```
python %TMP%\buyhold.py        # section 1/3, both barrier shapes, 50-coin panel
python %TMP%\buyhold2.py       # section 1/3, the H1-2026 bear panel, 157 coins
python %TMP%\bh_basket.py      # section 1, the basket WITH wicks (the correction)
python %TMP%\barrier_bh.py     # section 2, zero-drift surface + daily-day census
python %TMP%\buyhold4.py       # section 4, SMA200/SMA500 with re-entry, both regimes
python %TMP%\opportunity.py    # section 5, B&H vs bot, 3M and account life
python %TMP%\funded_leverage.py# section 6, ruin vs leverage
python %TMP%\funded_ev.py      # section 6/7, EV after profit split, breach=lost
python %TMP%\ci_bh.py          # section 1, block bootstrap error bars
```

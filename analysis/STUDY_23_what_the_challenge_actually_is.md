# Study 23 — what the challenge actually is (measured, from the live API)

**Question.** Studies 21 and 22 killed every alpha claim in the corpus. But
"there is no edge" and "you cannot pass" are different statements. This study
measures the second one directly: the product rules, whether a clock exists, the
shipped bot's actual volatility, and the pass-optimal amount of risk.

Everything below is read from `api.beta.propr.xyz` on 2026-11-12 and from the
shipped simulator, not from the config tables.

## 1. The deadline question is settled: there is no enforced time limit

The API's challenge object has **95 distinct fields across all 9 products and
not one of them is time-related** — no `durationDays`, `timeLimit`, `expiresAt`,
`deadline`. Phases carry `profitTargetPercent`, `maxDailyLossPercent`,
`maxDrawdownPercent`, `drawdownType` and nothing else.

The attempt record is stronger evidence than the schema:

```
attemptId   urn:prp-challenge-attempt:sBy3N6f4sTYX
startedAt   2026-09-09T23:21:51Z
endedAt     null
status      active            <- 64 days later, still running
```

A 64-day-old attempt that is still `active` cannot be under a 30-day clock.
`analysis/propr_bot_performance_report.md` flagged the deadline as unconfirmed;
it is now confirmed **absent** for this product, and the schema gives no product
any clock. (A human T&C could still say otherwise, but the platform does not
enforce one.)

That changes the objective completely. With a 30-day deadline you need ~10%/month
of drift to make the challenge a coin flip. Without one, the task is only "touch
+10% before −10%", and drift is not required at all.

## 2. The live account is `paper`, and it already had its shot

```
type            paper          (not a funded brokerage account)
balance         929.172426
highWaterMark   1069.149121    ($1,069.15 = +6.9%, target was $1,100)
```

The account peaked **3.1% short of the target** in September and gave it all
back. It now needs +18.4% to pass with 3.15% of room to the $900 floor.

## 3. What the shipped bot's risk actually is

Same panel (`c3m`/`f3m`, 49 coins, 2026-08-07 → 2026-11-10), shipped logic,
8 seeds — fills are stable enough that σ is identical to 2 decimal places:

| config | gross | stop | 3M return | max DD | **monthly σ** | drag/month |
|---|---|---|---|---|---|---|
| beta live | 0.25 | 24% | −2.11% | 4.63% | **2.94%** | −0.70% |
| 5k live | 0.20 | 20% | −1.85% | 3.73% | **2.24%** | −0.62% |
| code default | 0.50 | 20% | −4.68% | 9.12% | **5.60%** | −1.56% |

Cost drag is proportional to risk: **drag ≈ 0.26 × σ per month**. That single
ratio, plus σ, is the whole story.

## 4. Why 0 pass / 0 fail / 100% timeout over 80 windows

Two mechanical facts, neither of them about alpha:

1. **The bot's own halt is tighter than the challenge wall.** `dd_halt` is 4.5%
   and it *latches*; the wall is 6%. A 4.5% drawdown retires the book
   permanently instead of letting it recover. So a window can only end in
   "timeout" — breach is pre-empted, and the pass needs +10% first.
2. **σ is a third of what the barrier wants.** At σ = 2.94%/mo, a barrier MC at
   the account's own barriers gives:

| | 30d pass | 90d pass | 180d | 365d |
|---|---|---|---|---|
| shipped beta, σ 2.94%, μ −0.70%/mo | 0.03% | 2.16% | 6.54% | 10.99% |
| same σ, **zero** edge | 0.08% | 5.16% | 16.28% | 29.55% |
| same σ, +0.5%/mo drift | 0.14% | 8.75% | 27.28% | 48.68% |

At 2.16% per 90-day window, 80 windows predict **1.7 passes**; observing 0 has
probability 0.978^80 = 17%. The 0/80 result is not bad luck and not a new
finding — it is the arithmetic of running a 3%/month book against a 10% target.

## 5. With no deadline, pass odds are a *risk-budget* problem

Zero edge, drag = 0.26σ, 365-day horizon, barriers per product:

| product | target/DD | 2% | 3% | 5% | 7% | 10% | 13% | 16% | 20% | 25% |
|---|---|---|---|---|---|---|---|---|---|---|
| Free Trial (beta) | 10/10 | 3.3 | 11.2 | 24.3 | 30.7 | **33.9** | 32.3 | 30.5 | 29.4 | 29.6 |
| Bronze | 10/10 | 3.3 | 11.2 | 24.4 | 30.9 | **33.9** | 32.4 | 30.5 | 29.3 | 29.6 |
| Silver (trailing) | 10/10 | 3.3 | 10.9 | 20.8 | 25.5 | 29.1 | **29.4** | 28.9 | 28.6 | 29.3 |
| Polymarket Bronze | 9/6 | 4.8 | 13.0 | **22.0** | 22.1 | 19.0 | 18.5 | 19.2 | 20.9 | 23.4 |
| Bronze Turbo | 9/3 | 4.2 | 9.6 | 15.3 | **16.6** | 16.1 | 16.7 | 18.0 | 20.1 | 22.8 |
| **legacy K5 (bot config)** | 10/6 | 3.3 | 10.4 | 19.4 | 19.3 | 16.2 | 15.7 | 16.6 | 18.3 | **20.9** |
| Pass Phase1 Fast | 10/20 | 3.3 | 11.1 | 26.7 | 36.1 | 43.0 | 46.8 | 48.8 | **49.9** | 49.4 |

Three things fall out:

- **Pass odds are not invariant to σ once costs are real.** They peak, then
  decay. For 10/10 products the peak is σ ≈ 10%/month (33.9%); for 20% DD
  products σ ≈ 20%/month (49.9%).
- **The shipped config is 3.4× under-risked.** 2.94%/mo instead of 10%/mo is
  11.2% vs 33.9% — a factor of three of pass probability thrown away for no
  risk reduction that matters.
- **The K5 product is the worst shape on the menu** (10% target, 6% DD, 3%
  daily): its ceiling is 20.9% against 33.9% for any 10/10 product. The bot was
  configured for the worst barrier and then run at a third of the optimal
  volatility.

The daily wall binds above ~15%/mo — that is what caps the high-σ end.

## 6. The live account cannot be saved at any risk setting

Distances are from equity, not from start: floor $900 is −3.15%, target $1,100
is +18.4%. 365 days, 5% daily wall:

| σ/month | pass | fail | timeout |
|---|---|---|---|
| 1.0% | 0.00% | 66.8% | 33.2% |
| 2.94% (now) | 1.10% | 91.8% | 7.1% |
| 5% | 5.05% | 93.9% | 1.0% |
| 10% | 9.67% | 90.3% | 0.0% |
| 20% | **10.8%** | 89.2% | 0.0% |

Best available is **10.8% at σ = 20%/month**, and it fails 89% of the time. At
the current 2.94% it is **1.1%**. There is no setting that makes this account a
good bet; the halt is not costing anything, because the option it protects is
worth about one percent.

A *fresh* attempt on the same product at the same barriers is 33.9% at σ=10%/mo
— 30× better, purely because it starts at 0% instead of −7%.

## 7. If you keep playing: fee per unit of pass probability

Pass probability is what the fee buys. Using the 365-day optimum for each:

| product | fee | phase-1 pass | phases | **pass to funded** | **fee per pass** |
|---|---|---|---|---|---|
| Polymarket Bronze 1 Step | $49.99 | 23.4% | 1 | 23.4% | **$214** |
| Pass Phase1 Fast | $99 | ~50% (0.1% first target) | 2 | ~50% | **$198** |
| Bronze Challenge | $99.99 | 33.9% | 2 | 11.5% | $869 |
| Silver Challenge | $199.99 | 29.4% | 2 | 8.6% | $2,325 |
| Professional | $1,000 | 33.9% | 1 | 33.9% | $2,950 |
| Free Trial | $0 | 33.9% | 1 | 33.9% | — (max 1 purchase, used) |
| Bronze 1-Step Turbo | n/a | 16.6% | 1 | 16.6% | worst barrier shape |
| **legacy K5 (bot config)** | n/a | 20.9% | 1 | 20.9% | worst barrier shape |

Break-even: a product is +EV if the funded account is worth more than
`fee / pass_probability` — about **$200** for the two cheap single/low-phase
products. That is a low bar *if* the funded account pays anything. It is not a
recommendation to buy; it is the arithmetic any purchase has to clear. The
`type: paper` on the live account means the "funded" side is a simulated B-book,
so the payout terms decide whether any of this is real, and they are not in the
API.

**Caveat on `Pass Phase1 Fast` and `Pass Fast B Book`:** both are live, priced
$99, and report a phase-1 target of **0.1%** with a 20% drawdown. Taken
literally that is a 99.5% phase-1 pass. A $99 product that hands out a $100k
account for +0.1% is not economically plausible, so treat the 0.1% as a field
that needs confirming on the product page before any money moves.

## Verdict

1. **There is no deadline.** A 64-day-old attempt is still `active`; no product
   schema contains a time field. The 30-day assumption that made the challenge
   look impossible is wrong.
2. **The alpha verdict stands** (Studies 21–22): nothing in the three trees
   survives a control and is deployable long-only. Zero edge is the right prior.
3. **With zero edge and no deadline, the challenge is a 34% bet, not a 0.03%
   one** — but only at the right product and the right volatility. The shipped
   bot runs the worst barrier shape (10/6/3 → 20.9% ceiling) at a third of the
   optimal risk (2.94%/mo vs 10%/mo → 11% of 34%).
4. **The live account is already dead**: 1.1% at current risk, 10.8% at the
   best possible risk. Halting it costs nothing. Do not re-open it to chase a
   10% lottery with 3.15% of room.
5. The honest statement is no longer "you cannot pass". It is: **"you are
   buying a 34–50% lottery ticket, and the shipped configuration is the worst
   available way to buy it."**

**Follow-up (Study 24).** The natural baseline for this section's "34-50%
lottery" is buy and hold. Measured on two regimes, B&H scores 41.1% on the
10/10/5 shape (vs the bot's 11%), matches the zero-drift barrier value at the
asset's sigma to one decimal, and is not improved by baskets or trend filters.
The catch is section 6 of Study 24: the exposure that passes (0.5-0.7x) loses a
funded account with 65% probability inside a year, and 1x is 99.9% certain
death. See `analysis/STUDY_24_buy_and_hold.md`.

## Reproduce

```
python %TMP%\bot_sigma.py          # section 3, measured sigma
python %TMP%\barrier_measured.py   # section 4, at the measured sigma
python %TMP%\risk_budget.py        # section 5, pass-optimal risk per product
python %TMP%\live_odds.py          # section 6, the account as it stands
python %TMP%\probe6.py             # section 1/7, live product menu
python %TMP%\probe8.py             # section 1/2, the attempt record
```

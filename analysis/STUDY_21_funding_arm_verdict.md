# Study 21 — Funding vs reversal: neither has an edge, so the live books are halted

**Date:** 2026-11-11
**Runs:** `reversal_arm.py`, `which_arm.py`, `bottom_levels.py` (temp), `funding_audit.py`
**Question:** Is the edge in funding, in reversal (buy the losers), or in both together?

## Answer

Neither. Measured against a random-5 control on the same engine, both arms are
statistically indistinguishable from picking coins at random, and the
combination is the arithmetic average of the two — not a synergy.

## What was tested

Same signal (trailing funding, K=336h), same weekly rebalance, same 20% stop /
1.5x TP / trailing exit, same chase filter (skip 24h move >8% or ATR extension
>5%), same costs (4.5bp fee + 5bp slippage per side), funding paid on longs.

| arm | what it holds |
|---|---|
| A | long top-5 by funding (what the bot ships) |
| B | long bottom-5 by funding ("buy losers") |
| C | barbell: long top-5 **and** bottom-5, same gross split 50/50 |
| E | extremes: long top-2 **and** bottom-2 (dispersion) |
| D | long top-5 / short bottom-5, dollar-neutral (the study's spread; not deployable — no short path) |
| R | random 5 names (control) |

## Results

### 3M, 2026-08-21 → 11-10, 49 coins, 81 days

| arm | return | maxDD | Sharpe | trades | 30d pass | 30d fail |
|---|---|---|---|---|---|---|
| A top-5 | −2.9% | −4.17% | −1.84 | 36 | 0.0% | 2.6% |
| B bottom-5 | +1.4% | −6.46% | +0.68 | 29 | 0.2% | 1.5% |
| C barbell | −0.7% | −4.87% | −0.53 | 65 | 0.0% | 0.7% |
| E extremes | −3.4% | −7.14% | −1.58 | 26 | 0.0% | 4.2% |
| D long/short | −7.9% | −11.68% | −2.01 | 53 | 1.0% | 33.2% |
| R random | **+2.0%** | −5.89% | +0.94 | 44 | 0.0% | 1.1% |

### 7M, 2026-04-08 → 10-19, 50 coins, 194 days (bull)

| arm | return | maxDD | Sharpe | trades | 30d pass | 30d fail |
|---|---|---|---|---|---|---|
| A top-5 | +9.3% | −3.97% | +1.89 | 80 | 0.4% | 0.3% |
| B bottom-5 | +6.4% | −4.91% | +1.53 | 55 | 0.0% | 0.2% |
| C barbell | +7.9% | −4.06% | +1.94 | 135 | 0.0% | 0.1% |
| E extremes | +4.9% | −4.29% | +1.23 | 44 | 0.0% | 0.1% |
| D long/short | −5.1% | −10.55% | −0.70 | 116 | 0.3% | 10.6% |
| R random | +5.3% | −6.59% | +1.04 | 119 | 0.4% | 1.4% |

### Wide, 2026-08-21 → 11-11, 149 coins, 82 days

| arm | return | maxDD | Sharpe | trades | 30d pass | 30d fail |
|---|---|---|---|---|---|---|
| A top-5 | −7.5% | −8.07% | −3.48 | 38 | 0.0% | 14.8% |
| B bottom-5 | −1.5% | −7.74% | −1.20 | 37 | 0.0% | 3.4% |
| C barbell | −4.5% | −7.00% | −2.76 | 75 | 0.0% | 4.6% |
| E extremes | −10.4% | −11.03% | −4.81 | 31 | 0.0% | 25.9% |
| D long/short | +1.4% | −8.11% | +0.89 | 58 | 2.0% | 5.4% |
| R random | −2.2% | −9.23% | −1.34 | 45 | 0.0% | 6.1% |

## Statistical test: arm vs random-5

Welch t on daily returns.

| panel | top funding | bottom funding |
|---|---|---|
| 3M | −0.066%/d, t −0.90 | −0.005%/d, t −0.06 |
| 7M bull | +0.019%/d, t +0.34 | +0.005%/d, t +0.10 |
| Wide | −0.057%/d, t −0.70 | +0.007%/d, t +0.08 |

The +9.3% in the bull panel is not the signal: 5 random names made +5.3% over
the same 194 days. The selection premium is 1.9bp/day with a t of 0.34.

## "Both together" is exactly the average

| panel | C actual | (A+B)/2 |
|---|---|---|
| 3M | −0.7% | −0.75% |
| 7M | +7.9% | +7.85% |
| Wide | −4.5% | −4.50% |

The barbell splits the same gross 50/50 across two long baskets, so there is no
interaction term. The sleeves correlate +0.43 / +0.55 / +0.48, so it does not
diversify either — it only trims drawdown slightly (wide panel −7.00% vs
−8.07% / −7.74%) in proportion to lower return.

## The decisive test: the spread itself does not pay

D is the original study's claim expressed as a trade. It returns −7.9%, −5.1%,
+1.4% — negative on two of three panels. A cross-section that cannot pay
top-minus-bottom cannot pay either leg alone. (D carries a structural handicap
here: the short leg has no stop path and pays two books of fees, which is one
more reason it is not deployable.)

E, betting on both extremes, is the worst arm almost everywhere (−3.4%, +4.9%,
−10.4%): extreme funding marks crowding, not direction.

## Why the headline number was misleading

`fund_bot.py` quoted a spread of +0.36 / +1.14 / +2.48 / +3.47% by lookback.
Rebuilt on the 7M panel at K=336:

| h | top | bottom | spread |
|---|---|---|---|
| 24 | +0.33% | +0.24% | +0.10% |
| 72 | +1.03% | +0.73% | +0.30% |
| 168 | +2.80% | +1.88% | +0.92% |
| 336 | +5.88% | +3.69% | +2.20% |

Two errors compounded: the 3.47% is a spread that a long-only bot cannot earn,
and it does not reproduce (2.2% on the rebuild). Both tails are positive, so
"bottom" was a smaller trade, not a short.

## Provenance: what "the old edge" actually was

Read back out of session `ses_03260db06ffeC5ugHtH3kUMd3P` (2026-08-04 → 10-31), the
claim that justified the deployed bot was built from two numbers, both measured
on 2026-10-17:

1. **Top-funding quintile absolute forward return: +4.18% per 2 weeks** at K=336.
2. **Top-minus-bottom quintile spread: +3.47%** at 336h (+0.36 / +1.14 / +2.48 by
   lookback).
3. Long-only top-q backtest at gross 1.0, weekly, 200-day panel:
   `K=336 q=10` → ann +262%, Sharpe 2.07, pass 21/33, breach 3/33;
   `K=336 q=5` → ann +434%, Sharpe 2.31, pass 18/33, breach 1/33.

No random control was ever run against the long-only version in that session
(the only mention of one is a note to add it). That is the hole the verdict
above fills.

The two headline numbers also refute each other. If the top quintile returns
+4.18% and the spread is +3.47%, the **bottom quintile returned ≈ +0.71%** — it
was a long, just a smaller one. Every dollar of the long-only return was market
drift; the selection only "earned" the 3.47pp *difference*, which requires
shorting the bottom, which `propr/client.py` cannot do (`open_long` only, no
`open_short` — recorded in the same session at line 11514).

The number was revised down four times inside that same session:

| when | claim |
|---|---|
| 10-17 | ann +262%, Sharpe 2.07, pass 21/33 |
| 10-19, 200d walk-forward | "183% ann / Sharpe 3.37" — later called a short lucky bull window |
| 10-19, 6-month OOS (Mar 25–Oct 19, 188d) | +19.6% ann, Sharpe ~1–1.5 |
| 10-23, live config | Sharpe 0.75 (beta .25) / 0.81 (5k .30), +10–18% ann |
| 11-11, live realized | beta −7.08%, 5k −6.07% |

q=10 and gross 1.0 — the old edge's actual winning config — were both covered by
the current work: the q sweep tested 3/5/8/10/20/30/50 at constant gross and
found 5 optimal on both panels, and gross only scales a zero, it does not
create one.

## What the reversal side actually is

Ranking from the bottom is not "buy negative funding". On the 3M panel:

- median funding across the book: **+0.0013%/h**
- only **4.8%** of rebalance x coin observations are negative
- **12 of 49** coins were ever negative
- at **5 of 12** rebalances, **0 of 5** bottom names were negative

So the bottom-5 is usually "the least frothy majors" (ADA, DOT, TRUMP, ARB,
JUP). The genuine negative-funding trade only fires in the last three
rebalances (Oct 23 – Nov 6), which is exactly when it cannot be validated.

The July studies tested the absolute-threshold version and lost:

| study | rule | sample | result |
|---|---|---|---|
| `funding_contrarian_2month.py` | long when funding < −0.01%/8h | 338 perps, 1,576 trades | mean −6.90%, median −11.32%, 79/286 positive, median WR 20% |
| `zscore_funding_backtest.py` | long when funding z < −2 | 24 majors, 54 trades | mean +6.54%, median 0.00%, 9/18 positive |
| `funding_rate_backtest_top50.py` | delta-neutral carry | 50 pairs, 365d | mean −4.38%, median −0.38%, 18/38 positive |

## Decision taken

Both live books are halted and the halt is now latched to disk.

- Beta `urn:prp-account:LDA3R3vW74Ym`: $929.17, −7.08%, target +18.4%, floor
  $900 (3.15% of room). Every simulated arm passes with probability ≈0%.
- 5k `urn:prp-account:hkTafyCzQ3cn`: $4,696.59, already failed 2026-11-08.

The account's own risk rule had already fired; the bug was overriding it on
every boot. Trading a zero-edge strategy with 3% of room is a negative-EV bet,
so the correct action is the one the risk rule already chose: stay stopped.

### Code changes

| file | change |
|---|---|
| `propr-bot/fund_bot.py` | halt latched in `cache/HALT`; startup guard exits before seeding/ranking; `--resume-halt` is the only way to clear it; dd and target halts both latch |
| `propr-bot/notify.py` | HTML-escape dynamic text (`cfg_desc`, reasons, titles); added public `esc()` |
| `propr-bot/watchdog.py` | stands down and exits when `cache/HALT` exists (kills stray, logs, one Telegram, return) |
| `propr-bot-5k/watchdog.py` | same stand-down |
| `propr-bot-5k/fund_bot.py` | startup halt guard |
| `propr-bot-5k/notify.py` | replaced with the fixed notifier (was byte-identical) |
| both `cache/HALT` | marker written with the measured reason |

Processes stopped: watchdogs 3856, 11832; bots 11488, 17776. No open positions
on either account (`open 0` in the final cycle of each).

Telegram was failing on **every** message with
`400 can't parse entities: Unsupported start tag "="` because `stop<=24%` was
interpolated into HTML. Fixed and verified end-to-end (HTTP 200 for the exact
string that used to fail). This means no halt, entry, or flatten alert had ever
been delivered.

### Correction to an earlier recommendation

The earlier note to "delete the hourly top-up re-entry" does not apply to the
shipped code: the current `fund_bot.py` has no hourly re-entry. The only
top-up is the under-filled-slot fill inside a rebalance (line ~978), which does
not add to existing positions. The destructive hourly re-entry existed only in
the simulation variant used to measure it, and it was removed from consideration
by not being in the code.

### How to resume

```
del propr-bot\cache\HALT                 # or: python fund_bot.py --resume-halt
python fund_bot.py --once                # dry run, must not print HALTED
python watchdog.py                       # then the supervisor
```

`--resume-halt` on the current equity re-latches immediately (equity is below
the 7% dd line), which is the intended behaviour and was verified.

## Caveats

- The two recent windows are 81 and 82 days; enough to show the sign flip, not
  enough to prove a regime.
- All panels are built from today's survivors, so every arm's absolute return
  is optimistic by the same bias. Comparisons across arms remain fair.
- D's short leg has no stop path and pays two fee books; its negative result is
  partly structural, not purely signal.
- Pass probabilities come from an iid bootstrap of daily returns, which
  understates path dependence (volatility clustering, gap risk).

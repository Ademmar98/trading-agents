# Study 20 — exit-geometry sweep: the hypothesis is refuted

**Run:** `analysis/hl_exit_geometry_sweep.py`
**Universe:** 121 Hyperliquid perps with a full 195-day history
**Windows:** 6 × 30 days, **non-overlapping** · B&H per window: −23.8%, −4.6%, +13.5%, +4.8%, −19.1%, −2.2%
**Grid:** 64 exit configs × 6 windows = 384 portfolio simulations
**Costs:** HL base perp maker 0.015%/side, both legs

## The hypothesis

Study #19 showed the B+C entry signal has positive central tendency (mean
+0.174%, median +0.698% net/trade, WR 60.8%) but lost money because the exit
geometry was mis-scaled: a stop at 2× **daily** ATR against an **intraday**
VWAP target produced R:R of 1:5.6, requiring an 84.9% win rate against an
actual 60.8%. The hypothesis was that this is a specification error — a units
mismatch — and that matching the exit timescale to the signal timescale would
fix it.

**It does not. The hypothesis is refuted, and not marginally.**

## Result

| metric | value |
|---|---|
| Configs positive on all-window skill | **0 / 64** (best −0.05%) |
| Configs positive **in-sample** | **0 / 64** (best −0.25%) |
| Configs with profit factor ≥ 1.0 | **0 / 64** (max 1.00, median 0.92) |
| Configs positive in all 6 windows | **0 / 64** |
| Configs where excluding the best 2 pairs still profits | **0 / 64** |

Skill is measured against an **exposure-matched** buy & hold — B&H scaled to
the average exposure the config actually carried — so a low-exposure config
cannot score by sitting in cash while the market falls. Four of the six
windows were down, one of them −23.8%; without this correction the whole table
would read as a large fake win.

The strongest result in the entire 64-config space is `nostop/vwap/24` at
**−0.05% skill, PF 0.97, positive in 3 of 6 windows**. That is zero, reached
by the config that does the least.

## The study #11 protocol

Study #11 established that an IS/OOS split alone does not validate a parameter
sweep: with N configs the in-sample winner is mostly selection noise. The
decisive tests are (a) where the IS pick *ranks* when every config is run OOS,
and (b) the IS→OOS correlation across the whole space. Both were computed
before looking at results.

- Best IS config: `atr1hx1/vwap/48`, **IS skill −0.25%**
- Its OOS skill: **+0.20%**
- Its **OOS rank: 26 / 64 — 61st percentile.** Needs ≥90th. **FAIL.**
- **IS→OOS skill correlation: −0.206** — mildly *anti*-predictive.

That correlation is worth pausing on. Study #11's SL/TP grid on a completely
different book, timeframe, venue, and fee structure produced **−0.177**. This
one produces **−0.206**. Two independent sweeps, same anti-predictive sign,
nearly the same magnitude. Whatever the in-sample ranking of an exit parameter
measures on this kind of series, it is slightly worse than useless for
predicting the out-of-sample ranking.

The trap is visible directly in the top-OOS table: the five best OOS configs
have IS skills of −1.40, −0.40, −1.35, −1.12, −1.28. **The out-of-sample
winners are the in-sample losers.** 41 of 64 configs show positive OOS skill,
which looks encouraging until you notice that OOS is 2 windows and that 0 of
64 hold up across all 6.

## What this rules out, specifically

The sweep covered the entire space the hypothesis pointed at:

- **Stop source:** 1h ATR(14) at 1.0/1.5/2.0/3.0×, daily ATR at 2× (the #19
  baseline), and no stop at all.
- **Target:** R-multiples of the actual stop distance (1R/1.5R/2R/3R) and the
  VWAP mean.
- **Time stop:** 24h, 48h, none — the honest, fixed-in-advance version of
  study #19's hold-time table.
- **Sizing coupled to the stop:** `size = equity × risk_factor / stop_distance`,
  so dollar risk per trade is constant by construction across all configs.

No corner of it works. The R:R geometry was a real defect and fixing it
changed the *shape* of the loss without changing its sign.

## Two secondary findings

**The exposure defect is independent and was not fixed.** I expected
stop-coupled sizing to lift exposure — a tighter stop buys more size for the
same risk. It did not: exposure stayed at 5.7–7.6% across all 64 configs,
because the conviction divisor (`Σ strengths / 3 modules` ≈ 0.17 for a
single-module signal) caps size long before the stop distance binds. That
prediction was wrong, and it means the 6% exposure problem is a separate
defect in the signal-combination layer, not a consequence of exit geometry.

**Wide stops walk into the drawdown wall.** The `atr1h×3` configs reach 4.64–
4.92% max drawdown against the 4.8% passive-liquidation tier and the 5.0% hard
lock. No config actually halted, but the widest-stop arm was operating inside
the circuit breaker's margin — while still losing.

## Survivorship caveat

Requiring 195 days of history cut the universe from 177 listed perps to 121.
That restriction selects for assets that survived and stayed listed for six
months, which biases **in favour** of the strategy. The negative result holds
despite a universe tilted its way.

## Verdict

Study #19 identified a genuine mechanical defect and this study fixed it
across 64 variants. The result did not move off zero. Combined with the
in-sample failure (0/64 positive even before any OOS test), the honest reading
is that the positive median per-trade in study #19 is not an exploitable edge —
it is what a 60% win rate with a fat left tail looks like when you measure only
its centre.

**The B+C exit-geometry line of inquiry is closed.** Module A (CVD at
liquidity walls) remains the only untested surface in this design, and it
requires forward tape capture — Hyperliquid serves no historical trades or L2.

## Files

- `analysis/hl_exit_geometry_sweep.py` — the sweep (re-runnable, caches to scratchpad)
- `analysis/hl_exit_geometry_results.json` — all 64 configs × 6 windows
- `analysis/STUDY_19_hl_system_sim.md` — the study this tests

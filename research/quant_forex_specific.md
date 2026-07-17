# Strategy Catalog — Quant/Statistical & Forex-Specific Family

- **Worker:** Research Worker — Quant/Statistical & Forex-Specific
- **Date:** 2026-07-17
- **Count:** 30 strategies
- **Target system:** crypto spot paper broker (core/strategies.py signal contract: function receives OHLC data, returns signal dicts with side/confidence/rationale).

## Global adaptation notes for the implementation team

1. **Spot-only constraint:** The current paper broker is crypto spot. Entries specifying shorts of a spot asset are marked; the standard adaptation is "short signal = exit long / stay flat" unless the entry explicitly says the strategy requires margin/futures infra (funding arb, basis trade, triangular). Those three need perp/futures simulation — flagged in `notes`.
2. **Extra data feeds:** Entries list data needs beyond OHLC using the catalog vocabulary (funding / open_interest / orderbook / multi-symbol) plus explicit external feeds (central bank rates, economic calendar) where unavoidable. OHLC-only variants are always preferred when available and are stated in the rule.
3. **Overlays vs. signals:** Entries #29 (vol targeting) and #30 (risk parity) are portfolio/sizing overlays, not standalone signals. Implement as shared modules so any core strategy can be sized through them — this also makes the 1-week paper cycle a fair equal-risk comparison.
4. **Cost sensitivity:** Every entry assumes taker fee 0.1% per side unless noted. High-turnover entries (grid, time-of-day, ORB) must be evaluated with maker/limit fills or they will fail the paper cycle on fees alone.
5. **Vague-but-famous strategies:** Where a famous strategy has no single canonical rule (e.g., "London breakout", "weekend effect"), the most standard published variant is codified and this is stated in `notes`.

---

### STRATEGY: FX Carry Trade — Single Pair Trend-Filtered
- family: Quant/Statistical & Forex-Specific
- markets: forex
- timeframes: 1d
- entry_long: Long pair (base/quote) when policy-rate differential (i_base − i_quote) ≥ +1.0% annualized AND close > SMA(200) on daily. Enter at next daily open.
- entry_short: Mirror — short pair when (i_base − i_quote) ≤ −1.0% AND close < SMA(200). (Spot crypto broker: not applicable.)
- exit_rule: Exit on (a) rate differential sign flip, (b) daily close back across SMA(200), or (c) trailing stop 2.0×ATR(20) from peak. Re-check rates after every central-bank meeting date for either currency.
- params: rate_diff_threshold=1.0%, sma_period=200, atr_period=20, trail_mult=2.0, recheck=weekly
- data_needs: OHLC + central bank policy rates (manual feed acceptable, G10 rates change ~8x/yr); multi-symbol
- notes: Classic crash risk — carry unwinds violently in risk-off episodes (AUD/JPY −40% in 2008); the SMA filter and ATR trail are the drawdown controls, do not remove them. Academic basis: Lustig/Roussanov/Verdelhan carry factor; Burnside et al. crash literature. Forex-native; crypto adaptation would need stablecoin lending-rate differentials (out of scope for paper cycle).

### STRATEGY: FX Carry Basket — Cross-Sectional G10 Rank
- family: Quant/Statistical & Forex-Specific
- markets: forex
- timeframes: 1d
- entry_long: Rank G10 currencies by 3-month implied rate differential vs USD (from forward points or OIS). Long the top-3 currencies vs USD, equal risk weight (each leg vol-scaled to 10% annualized using 60d realized vol). Rebalance weekly.
- entry_short: Short the bottom-3 currencies vs USD, same risk weight. Combined the book is ~dollar-neutral.
- exit_rule: Weekly rebalance replaces any leg that falls out of its tertile; hard risk-off kill-switch: exit all legs if portfolio 20d drawdown > 5% or VIX-equivalent FX vol index rises > 50% in 5 days.
- params: universe=G10, legs_per_side=3, rate_tenor=3m, vol_lookback=60d, target_vol_per_leg=10%, rebalance=weekly
- data_needs: OHLC multi-symbol + rate/forward-point feed
- notes: This is the HML_Carry factor portfolio form — more robust than single-pair carry because idiosyncratic crashes diversify. Same forex-native data caveat as above. Crypto analog = ranking by perp funding rates; see "Funding-Rate Arbitrage".

### STRATEGY: Carry-to-Risk — Vol-Adjusted Carry Ranking
- family: Quant/Statistical & Forex-Specific
- markets: forex
- timeframes: 1d
- entry_long: Compute carry-to-risk ratio = (i_base − i_quote) / σ_60d(annualized) for each pair. Long top tertile when ratio > +0.25.
- entry_short: Short bottom tertile when ratio < −0.25. Mirror of long.
- exit_rule: Rebalance weekly on rank change; per-leg stop = 2.5×ATR(20) trailing; de-risk all legs 50% when portfolio realized vol (20d) exceeds 1.5× its 1-year median.
- params: ratio_threshold=0.25, vol_lookback=60d, tertile=3, trail_mult=2.5, atr_period=20, rebalance=weekly
- data_needs: OHLC multi-symbol + rates feed
- notes: Practitioner's refinement of raw carry ranking — avoids loading on high-yield/high-vol EM currencies that dominate raw rankings and crash hardest. Carry-to-risk is the standard desk metric; no single canonical paper, codify as stated.

### STRATEGY: Time-Series Momentum — Classic 12-Month TSMOM
- family: Quant/Statistical & Forex-Specific
- markets: both
- timeframes: 1d (monthly rebalance)
- entry_long: 252-day total return > 0 → long. Position size = target_vol / σ_EWMA(60d), i.e., signal return scaled as sign(r_252) × (target_vol / σ) per Moskowitz-Ooi-Pedersen.
- entry_short: 252-day total return < 0 → short. (Spot adaptation: flat / hold stablecoin.)
- exit_rule: Monthly rebalance; position flips only on sign change of the 252d return. No separate stop-loss — vol scaling is the risk control. Optional overlay: cut size 50% if σ_20d > 2× σ_252d (vol shock).
- params: lookback=252d, hold=21d, vol_lookback=60d EWMA, target_vol=10% annualized (forex) / 40% (crypto single-asset)
- data_needs: OHLC (single symbol)
- notes: Documented across 58 futures markets (Moskowitz/Ooi/Pedersen 2012, JFE; verified in current research): positive in every asset class, "crisis alpha" in equity crashes. Caveat: Kim/Tse/Wald (2016) show part of the effect is the vol-scaling itself — keep the scaling anyway, it is a feature not a bug. For crypto's short history use the multi-lookback variant below.

### STRATEGY: Crypto TSMOM — Multi-Lookback Ensemble
- family: Quant/Statistical & Forex-Specific
- markets: crypto
- timeframes: 4h / 1d
- entry_long: Compute sign of trailing return at 3 lookbacks: 30d, 90d, 180d. Ensemble score = mean of signs ∈ {−1, −1/3, +1/3, +1}. Long when score ≥ +1/3, size ∝ score × (target_vol / σ_60d).
- entry_short: Score ≤ −1/3 → short (spot: flat). Symmetric.
- exit_rule: Weekly rebalance; exit when score crosses 0 (i.e., to flat when |score| < 1/3 hysteresis band). Trailing disaster stop 3×ATR(20)d.
- params: lookbacks=(30d,90d,180d), vol_lookback=60d, target_vol=40%, hysteresis=1/3, rebalance=weekly, stop_mult=3.0
- data_needs: OHLC
- notes: Hurst/Ooi/Pedersen (2017) blend 3/6/12-month signals — the 30/90/180d set is the crypto-compressed analog, standard in crypto CTA practice. Ensemble reduces whipsaw vs single lookback. Vol scaling essential given crypto's 60-100% vol regime swings.

### STRATEGY: Cross-Sectional Momentum — Crypto Basket
- family: Quant/Statistical & Forex-Specific
- markets: crypto
- timeframes: 1d (weekly rebalance)
- entry_long: Universe = top-20 liquid spot symbols. Rank by trailing 30d return SKIPPING the most recent 7 days (short-term reversal avoidance). Long top quintile (4 symbols), equal risk weight via inverse 20d vol.
- entry_short: Short bottom quintile. (Spot adaptation: simply don't hold them — the short leg's economic value in the paper cycle is the ranking signal; run long-only-vs-basket first.)
- exit_rule: Weekly rebalance; a holding is sold when it drops out of the top half (rank > 10) — buffer zone reduces churn vs strict quintile exit. Per-name disaster stop 2.5×ATR(14)d.
- params: universe_size=20, lookback=30d, skip=7d, legs=4, exit_rank_buffer=10, vol_lookback=20d, rebalance=weekly, stop_mult=2.5
- data_needs: OHLC multi-symbol
- notes: Jegadeesh-Titman relative momentum adapted to crypto horizons (crypto momentum is documented at 1–4 week formation, much faster than equities' 3–12 months). The skip-week is critical: crypto 1-week reversal is strong. Turnover ~weekly → cost sensitive; use close-price fills assumption in paper test.

### STRATEGY: Dual Momentum — Absolute + Relative (Antonacci)
- family: Quant/Statistical & Forex-Specific
- markets: both
- timeframes: 1d (monthly rebalance)
- entry_long: Step 1 (relative): rank basket (e.g., BTC, ETH, SOL, + forex majors if multi-market) by 90d return, pick #1. Step 2 (absolute filter): hold it only if its 90d return > cash benchmark (stablecoin lending rate, or 0% if unavailable); otherwise hold stablecoin/flat.
- entry_short: none (long/cash switch only)
- exit_rule: Monthly re-rank; switch when a different symbol is #1 AND passes the absolute filter; drop to cash when the #1 fails the absolute filter.
- params: lookback=90d, basket=(BTC,ETH,SOL,...), benchmark=stablecoin_yield or 0, rebalance=monthly
- data_needs: OHLC multi-symbol
- notes: Antonacci's GEM model with crypto-compressed lookback (90d vs his 12m). The absolute filter is what sidesteps bear markets — in backtests this is most of the edge. Only 12 decisions/year → cheapest possible cost profile; good paper-cycle citizen.

### STRATEGY: Cointegration Pairs Stat-Arb — Engle-Granger Z-Score
- family: Quant/Statistical & Forex-Specific
- markets: crypto (forex pairs also viable)
- timeframes: 4h / 1d
- entry_long: Formation: on trailing 120d of log prices, OLS-regress log(A) on log(B) → hedge ratio β; ADF-test the residual spread; pair is tradable only if p < 0.05 AND estimated half-life ∈ [5, 60] days. Trade: when spread z-score (20d rolling) < −2.0 → long A, short β×B (dollar-neutral legs).
- entry_short: z-score > +2.0 → short A, long β×B. Mirror of long. (Spot adaptation: trade only the underperforming leg long, or use as rotation signal between A and B.)
- exit_rule: Exit at z crossing 0 (mean). Stop: |z| > 3.5 (spread breakdown) OR holding time > 1.5× half-life. Kill pair permanently if re-test p > 0.10 at weekly re-estimation.
- params: formation_window=120d, z_entry=2.0, z_exit=0.0, z_stop=3.5, adf_pvalue=0.05, halflife_min=5d, halflife_max=60d, z_window=20d, reestimate=weekly
- data_needs: OHLC multi-symbol
- notes: The canonical stat-arb (Gatev/Goetzmann/Rouwenhorst 2006; Vidyamurthy 2004). Candidate crypto pairs: ETH/BTC, SOL/ETH, LTC/BTC, BNB/ETH. Main failure mode: cointegration breaks on idiosyncratic news (hacks, delistings, L1 narrative shifts) — the z-stop and weekly re-test are mandatory. Two legs = 2× fees.

### STRATEGY: Kalman Filter Pairs — Dynamic Hedge Ratio
- family: Quant/Statistical & Forex-Specific
- markets: crypto / both
- timeframes: 1h / 4h
- entry_long: Same tradability screen as Engle-Granger entry (ADF p<0.05 on formation window), but hedge ratio β_t is a latent random-walk state estimated by Kalman filter on log(A) = β_t·log(B) + ε_t (process-noise variance δ=1e-4, observation noise from OLS residual variance). Enter long spread when residual/σ_running < −2.0.
- entry_short: Mirror at > +2.0.
- exit_rule: Exit at residual crossing 0; stop |residual/σ| > 3.5; max hold 1.5× half-life from AR(1) fit on residual. Re-initialize filter if |β_t − β_{t−20d}| > 30% (structural break heuristic).
- params: delta=1e-4, z_entry=2.0, z_exit=0.0, z_stop=3.5, formation_window=120d, beta_shift_kill=30%
- data_needs: OHLC multi-symbol
- notes: Standard upgrade when β drifts (common in crypto where relative narratives shift fast). Slightly more lag in β adjustment is the trade-off vs. rolling OLS. If implementation budget is tight, Engle-Granger + weekly re-estimation captures ~80% of the benefit.

### STRATEGY: Ornstein-Uhlenbeck Mean Reversion — Half-Life Timed
- family: Quant/Statistical & Forex-Specific
- markets: both
- timeframes: 4h / 1d
- entry_long: Fit AR(1) on the series x_t (log spread for pairs, or log price detrended by 200d SMA for single asset): Δx = a + b·x_{t−1} + ε. Require b < 0 with |t-stat| > 2 and half-life = −ln(2)/ln(1+b) ∈ [3, 60] bars. OU equilibrium mean μ = −a/b, equilibrium σ_eq = σ_ε/√(1−(1+b)²). Enter long when x < μ − 1.5·σ_eq.
- entry_short: Enter short when x > μ + 1.5·σ_eq. Mirror.
- exit_rule: TP at x = μ (equilibrium). Time stop at 2× half-life (half the reversion edge decays by then). Hard stop at μ ∓ 3.0·σ_eq.
- params: entry_mult=1.5, stop_mult=3.0, halflife_range=(3,60), max_hold=2×halflife, b_tstat=2.0
- data_needs: OHLC (or multi-symbol for the pair variant)
- notes: Parameterization follows Avellaneda & Lee (2010) stat-arb framework. The half-life gate is the key filter — series with half-life > 60 bars are near-random-walks and will bleed via the time stop. Works well as the mean-reversion engine inside the regime-switching meta-strategies (#26–#28).

### STRATEGY: Funding-Rate Arbitrage — Spot-Perp Cash & Carry
- family: Quant/Statistical & Forex-Specific
- markets: crypto
- timeframes: funding cycles (8h: typically 00:00/08:00/16:00 UTC); monitor on 1h
- entry_long: When predicted/last funding ≥ +0.03% per 8h (≈30%+ annualized): buy spot notional X AND short perp notional X, same exchange, 1× leverage on the perp leg. Delta-neutral; PnL = funding collected ± basis change.
- entry_short: Reverse cash-and-carry (short spot via margin, long perp) when funding ≤ −0.03% — NOT implementable on a spot-only paper broker; skip in paper cycle.
- exit_rule: Exit both legs when funding flips sign or falls below fee break-even, whichever first. Break-even: round-trip fees ~0.2% (0.1% taker × 2 legs × open+close ≈ 0.4% conservative) ÷ funding per period ≈ minimum hold periods — enforce min_hold=5 periods (~40h) unless funding flips negative. Basis stop: close if |perp−spot basis| widens > 1.5% against entry (margin stress).
- params: min_funding=0.0003 (0.03%/8h), min_hold_periods=5, leverage=1.0, fee_per_side=0.001, basis_stop=1.5%
- data_needs: OHLCV + funding + perp mark price (basis); ideally open_interest for crowding check
- notes: Verified current mechanics: funding settles every 8h on most major exchanges (some contracts 4h/1h — confirm per venue); typical +0.01–0.015%/8h ≈ 10–19% APR, bull peaks sustained 0.05%+ (50%+ APR). Risks: funding flips (bear phases go negative), liquidation on the perp leg if leverage > 1×, exchange counterparty, basis blowout (Mar-2020 basis exceeded 10% and wiped under-margined arb books). REQUIRES perp simulation in the paper broker — flag as infra dependency; if unavailable in this cycle, simulate with funding-rate series as the PnL driver.

### STRATEGY: Triangular Arbitrage — Concept
- family: Quant/Statistical & Forex-Specific
- markets: crypto (forex majors also)
- timeframes: tick/seconds (not OHLC)
- entry_long: For loop USDT→A→B→USDT: compute implied cross rates through orderbook. Buy loop when (bid_A/USDT × bid_B/A × bid_USDT/B) − 1 > total fees + buffer, i.e., net edge > ~1.5× the 3-leg taker fee (~0.3%). Execute all 3 legs atomically.
- entry_short: Reverse loop evaluated symmetrically (asks). Both directions checked continuously.
- exit_rule: No holding period — the loop IS the trade; all legs fill within milliseconds or the attempt aborts.
- params: fee_buffer=1.5×, legs=3, loops=(USDT-BTC-ETH, USDT-BTC-SOL, USDT-ETH-SOL, ...)
- data_needs: orderbook (L2), tick data, atomic execution
- notes: CONCEPT ONLY for this stack. Edges are single-digit bps, persist for milliseconds, and are competed away by colocated market makers; it cannot be evaluated on OHLC bars or in a 1-week daily/hourly paper cycle. Recorded for catalog completeness. Revisit only if infra gains orderbook capture + sub-second execution; zero implementation effort recommended now.

### STRATEGY: Spot-Futures Basis Convergence — Calendar Carry
- family: Quant/Statistical & Forex-Specific
- markets: crypto
- timeframes: 1d monitoring, weeks-to-expiry holding
- entry_long: When annualized basis = (F_dated − S)/S × (365/days_to_expiry) > 10%: buy spot X, short dated future X (e.g., quarterly). Hold to expiry — convergence is mechanical at settlement.
- entry_short: Reverse (short spot/long future) on deeply negative basis — not spot-implementable; skip.
- exit_rule: Time-based: hold to expiry, or early-exit if basis compresses to < 2% annualized (capture early and redeploy). Basis stop: if basis widens > 2× entry level, review margin but convergence thesis is intact — do not panic-close; real risk is funding/margin, not direction.
- params: entry_basis_apr=10%, early_exit_apr=2%, tenor=quarterly
- data_needs: OHLC + dated-futures prices (multi-symbol)
- notes: Distinct from funding arb: no funding payments, PnL locked at entry modulo margin flows. CME/Deribit crypto basis has ranged ~5–40% APR historically. Requires dated-futures simulation in paper broker — same infra flag as funding arb. If neither is available this cycle, catalog both and defer.

### STRATEGY: Classic Range Grid
- family: Quant/Statistical & Forex-Specific
- markets: both (best on range-bound crypto majors)
- timeframes: 5m / 15m / 1h
- entry_long: Define range [P_low, P_high] = 30d Donchian channel (skip setup if channel width > 1.5× its 90d average — range too wide = trending). Place N=20 equally spaced limit buys from mid down to P_low (each size = risk_budget/N). Each filled buy gets a paired limit sell one grid step up.
- entry_short: Symmetric limit sells above mid with paired buys one step down — spot-only broker: disable upper half (long-only grid below current price).
- exit_rule: Per-fill TP = one grid step (the pairing). GLOBAL stop: close all resting orders and positions if price exits the range by > 2×ATR(14) — grids accumulate inventory into trends and must be killed on breakout. Re-arm only after a new 30d channel forms (price back inside for 3 consecutive bars).
- params: channel_lookback=30d, grids=20, width_filter=1.5×avg90, breakout_stop=2×ATR(14), rearm_bars=3
- data_needs: OHLCV (volume optional, for fill realism)
- notes: Profit source = oscillation count within the range; loss mode = one-way trend with stacked inventory. Extremely fee-sensitive (hundreds of fills): assume maker/limit fills only — with 0.1% taker both ways and a 1% grid step, fees eat ~20% of gross step profit. In the paper cycle, count fills conservatively (touch = fill is optimistic; require trade-through).

### STRATEGY: Infinity Grid — Long-Biased Volatility Harvesting
- family: Quant/Statistical & Forex-Specific
- markets: crypto
- timeframes: 1h / 4h
- entry_long: Start with base position (50% of allocated capital in asset). Grid in PERCENT steps: every +1.0% from last anchor, sell fixed quote amount Q; every −1.0%, buy Q. No upper bound ("infinity") — in a long uptrend the position shrinks but never fully exits; re-anchor on each fill.
- entry_short: none (long-biased accumulator by design)
- exit_rule: No stop-loss by design — drawdown tracks the asset's drawdown (this is a holding strategy with harvesting, not a trading strategy). Optional circuit breaker: pause buying if 20d realized vol > 150% annualized (crash regime — catch-falling-knife protection), resume when vol < 100%.
- params: step_pct=1.0%, base_position=50%, order_quote=fixed, vol_pause=150%, vol_resume=100%
- data_needs: OHLC
- notes: Pionex-style infinity grid — the standard published variant codified here. Outperforms buy-and-hold in choppy uptrends (harvests round trips), underperforms in strong bull (keeps selling into strength), and matches buy-and-hold drawdown in bears. Suitable only for assets the owner wants structural long exposure to (BTC/ETH). Fee sensitivity high but lower than classic grid (wider steps).

### STRATEGY: DCA — Periodic Accumulation (Benchmark Only)
- family: Quant/Statistical & Forex-Specific
- markets: both
- timeframes: 1d / 1w
- entry_long: Buy fixed quote amount Q of the asset every period (daily at 00:00 UTC, or weekly Monday), regardless of price. Optional value-averaging variant: define target portfolio-value path V_t = V_0 + g·t; buy (or sell) the difference V_t − current_value each period.
- entry_short: none
- exit_rule: No exit — terminal liquidation at horizon end, or never (accumulation mandate).
- params: amount_Q=fixed, period=daily/weekly, variant=simple/value_averaging
- data_needs: OHLC
- notes: NOT A TRADING EDGE. DCA is an allocation/savings scheme whose expected return equals the asset's drift minus fees; it does not beat lump-sum on average when drift is positive (documented: lump-sum beats DCA ~2/3 of the time in equities). Include in the paper cycle ONLY as the buy-and-hold-adjacent benchmark that real strategies must beat. Value-averaging adds mild timing improvement at the cost of occasional forced sells.

### STRATEGY: Martingale Sizing — DANGEROUS, DO NOT IMPLEMENT
- family: Quant/Statistical & Forex-Specific
- markets: both (market-agnostic staking scheme)
- timeframes: any
- entry_long: (Definition for catalog completeness) Any entry signal; initial risk 1 unit. After each losing trade, double the position size (2, 4, 8, ... units) until a winning trade recovers all prior losses + 1 unit, then reset to 1 unit.
- entry_short: Symmetric (scheme is direction-agnostic).
- exit_rule: Exit each sequence at first win; no stop-loss by construction (that is the fatal flaw).
- params: base_unit=1, multiplier=2.0, max_steps=unbounded
- data_needs: OHLC
- notes: ⚠️ DANGEROUS — DO NOT IMPLEMENT, DO NOT PAPER TEST. Capital requirement grows as 2^n while the win target stays 1 unit; with any per-trade loss probability p > 0, a ruinous streak is a certainty over long horizons (gambler's ruin). Crypto's fat tails and gap moves make blow-up faster than textbook coin-flip math. Cataloged solely because "every strategy that exists" was requested; exclude from implementation backlog. The SAFE mirror image — anti-martingale/pyramiding (add size only after wins, within a vol target) — is captured inside the Volatility Targeting overlay (#29) and is the only acceptable sizing-growth scheme.

### STRATEGY: Day-of-Week / Weekend Effect — Crypto
- family: Quant/Statistical & Forex-Specific
- markets: crypto
- timeframes: 1d / 4h
- entry_long: Standard codified variant: long BTC (or basket) Friday 20:00 UTC, exit Sunday 20:00 UTC — the "weekend drift" hypothesis (thin weekend liquidity, retail-driven flows). Alternative variant if estimation shows it stronger on owner's data: flat/short Mondays.
- entry_short: Mirror variant: short Monday 00:00 UTC → Tuesday 00:00 UTC if trailing 180d estimation shows Monday mean return < 0 with |t| > 2.
- exit_rule: Purely time-based exits. Optional disaster stop 1.5×ATR(14)d. No TP (window is the edge).
- params: entry=Fri 20:00 UTC, exit=Sun 20:00 UTC, estimation_window=180d, tstat_gate=2.0, stop_mult=1.5
- data_needs: OHLC
- notes: Academic day-of-week findings in crypto are UNSTABLE across sample periods (positive weekend effects pre-2017, weak/mixed after institutionalization; several studies report Monday/Thursday anomalies instead). Mandate: before paper trading, re-estimate mean return per weekday on trailing 180d with t-stats and multiple-testing control (Bonferroni over 7 buckets); trade only significant buckets. Low liquidity weekends → spread/slippage up; cost-sensitive.

### STRATEGY: Turn-of-Month Effect
- family: Quant/Statistical & Forex-Specific
- markets: both
- timeframes: 1d
- entry_long: Enter at the close of the last trading day of the month (crypto 24/7: enter last calendar day 00:00 UTC). Hold through the first 3 trading days of the new month (crypto: exit day-3 00:00 UTC).
- entry_short: none (documented effect is a long-side return concentration)
- exit_rule: Time-based exit at end of day 3. Optional 2×ATR(14)d stop.
- params: entry=last_day_of_month, hold_days=3, stop_mult=2.0
- data_needs: OHLC
- notes: Equity literature: Lakonishok & Smidt (1988); McConnell & Xu (2008) — the 4-day turn-of-month window has historically captured the bulk of the equity premium; mechanism = month-end salary inflows, fund flows, and rebalancing. Crypto evidence is thinner but the flow logic (month-end fiat inflows, payroll buys) plausibly transfers. Only ~2 round trips/month → cheapest strategy in the catalog to test; ideal paper-cycle candidate.

### STRATEGY: Intraday Time-of-Day Seasonality — Session Return Concentration
- family: Quant/Statistical & Forex-Specific
- markets: both
- timeframes: 1h / 30m
- entry_long: Codified variant from documented pattern (BTC gains concentrated in North-American hours, per market-structure research): long at 13:00 UTC (US pre-open), flat at 21:00 UTC (NY close). Mandatory pre-step on owner's data: compute mean return per hour-of-day over trailing 180d; trade only the top-quartile contiguous hour block with t-stat > 2; substitute that block if it differs.
- entry_short: Mirror: short the bottom-quartile hour block if its mean is significantly negative (spot: avoid holding during it).
- exit_rule: Time-based flat at session end; intraday stop 1.0×ATR(14) on the trading timeframe.
- params: default_window=13:00–21:00 UTC, estimation_window=180d, quartile=top 25%, tstat_gate=2.0, stop_mult=1.0
- data_needs: OHLC (intraday bars)
- notes: Classic multiple-comparisons trap: with 24 buckets, spurious significance is guaranteed without correction — use Bonferroni/FDR and require the block to be economically sensible (aligns with a real session). Forex analog: long EUR/USD during the London–NY overlap has the same flow rationale. Hourly turnover → fee sensitivity high; prefer 4–8h blocks over 1h blocks.

### STRATEGY: Tokyo Range / London Breakout
- family: Quant/Statistical & Forex-Specific
- markets: forex (majors); adaptable to crypto
- timeframes: 15m / 1h
- entry_long: Define Asian range = high/low of 00:00–07:00 UTC. Filters: skip day if range height > 1.2× its 20d average (already moved) or if prior-day ADX(14) > 30 (trending — breakout continuation less reliable from range). At/after 07:00 UTC, enter long on 15m close above range high + 0.1×ATR(14) buffer. First trigger only, one trade per day.
- entry_short: Mirror — short on 15m close below range low − buffer. (Crypto spot: long side only.)
- exit_rule: SL at opposite side of the Asian range. TP = 1.5× range height (aggressive: 2.0×, trail after 1.5×). Time exit: flat by 16:00 UTC if neither TP nor SL hit (momentum window over).
- params: asian_window=00:00–07:00 UTC, buffer=0.1×ATR(14), range_filter=1.2×avg20, adx_max=30, tp_mult=1.5, sl=opposite_range, flat_time=16:00 UTC
- data_needs: OHLC (intraday)
- notes: Most standard published London-breakout variant codified (many retail versions are vague; this is the canonical filter set). Edge source = European desks' opening flow expanding Asian compression. False-breakout rate is the weakness — the range-width and ADX filters are what keep it viable; do not strip them. Spread widens around 07:00–08:00 UTC: use stop-entry orders, not market orders, in live; paper test should model +0.5 pip / +2 bps extra slippage on entry.

### STRATEGY: London–NY Overlap Opening-Range Breakout
- family: Quant/Statistical & Forex-Specific
- markets: forex / both
- timeframes: 15m
- entry_long: Trading window restricted to the 12:00–16:00 UTC overlap (highest global FX liquidity). Opening range (OR) = high/low of 12:00–12:30 UTC. Enter long on 15m close above OR high + 0.1×ATR(14).
- entry_short: Mirror below OR low. First trigger only.
- exit_rule: SL = opposite OR boundary. TP = 1.5× OR height. Hard flat 15:45 UTC (before overlap ends and liquidity fades).
- params: session=12:00–16:00 UTC, or_minutes=30, buffer=0.1×ATR(14), tp_mult=1.5, flat_time=15:45 UTC
- data_needs: OHLC (intraday)
- notes: ORB family documented since Crabel; the overlap restriction is the refinement — breakout reliability scales with session volume. Caution: on days with 12:30 UTC US releases (CPI/NFP/FOMC-minutes cadence), either skip the day or use the event straddle (#24) instead — the OR gets destroyed by the news spike. Cross-reference an economic calendar before enabling.

### STRATEGY: NY Close / Fix Mean Reversion
- family: Quant/Statistical & Forex-Specific
- markets: forex
- timeframes: 15m / 1h
- entry_long: Codified fade variant: at 21:00 UTC (NY close/5pm ET), if the pair's intraday move (London open → 21:00 UTC) < −1.2× its 20d average daily true range, enter long for overnight reversion.
- entry_short: Mirror: intraday move > +1.2× ATR(20)d → short at 21:00 UTC.
- exit_rule: TP = 50% retracement of the intraday move, valid until next-day 12:00 UTC (time exit otherwise). SL = 0.5×ATR(14) beyond the day's extreme.
- params: trigger=1.2×ATR(20)d, entry_time=21:00 UTC, tp_retrace=50%, time_exit=next day 12:00 UTC, sl=0.5×ATR(14) beyond extreme
- data_needs: OHLC (intraday, session-anchored)
- notes: Rationale: end-of-day fixing/benchmark flows (WM/R 4pm London fix flows are documented in BIS and academic fix literature) push prices beyond fair value into the close; the overnight drift partially unwinds it. AVOID month-end days — month-end fix flows are large, pre-announced by bank estimates, and directional (do not fade them). Thin post-NY liquidity → slippage risk; use limits.

### STRATEGY: Event-Driven Volatility Straddle — Concept
- family: Quant/Statistical & Forex-Specific
- markets: both
- timeframes: 5m / 15m around scheduled events
- entry_long: 30–60 min before a scheduled high-impact event (forex: FOMC, CPI, NFP; crypto: ETF rulings, halvings, major token unlocks), place a buy-stop above the pre-event range high (range = trailing 4h high/low).
- entry_short: Simultaneous sell-stop below the range low — straddle the release. Cancel the untriggered leg 15 min after the event.
- exit_rule: TP = 1.5× pre-event range height; SL = 0.75× range; hard time exit 4h post-event.
- params: pre_event_window=30–60min, range_lookback=4h, tp_mult=1.5, sl_mult=0.75, cancel_untriggered=+15min, time_exit=+4h
- data_needs: OHLC + economic/event calendar feed (external dependency)
- notes: CONCEPT ONLY at current infra. Two blockers: (1) needs a reliable event-calendar data source with timestamps and importance grades — not present in the stack; (2) backtest validity is poor because spreads widen 5–20× and slippage explodes at the release — fill assumptions dominate results. Catalog for the roadmap; enable only after a calendar feed + event-window slippage model exist. Do not include in this paper cycle.

### STRATEGY: Post-Event Drift — Concept (with OHLC-only proxy)
- family: Quant/Statistical & Forex-Specific
- markets: both
- timeframes: 1d
- entry_long: Full version: after a scheduled event with positive surprise (actual vs consensus), enter long next session, hold 1–5 days (post-event-announcement drift). OHLC-only proxy (implementable now): if a day's true range > 4×ATR(20) AND close is in the top decile of the day's range, treat as "positive news day" → enter long at next open.
- entry_short: Mirror: range > 4×ATR(20) and close in bottom decile → short (spot: exit longs / stay flat 3 days).
- exit_rule: Time-based: exit after 3 days. Stop 1.5×ATR(14)d. No TP (let drift run the window).
- params: surprise_range_mult=4×ATR(20), close_decile=10%, hold_days=3, stop_mult=1.5
- data_needs: OHLC (proxy version); + consensus/surprise feed (full version)
- notes: PEAD analog; central-bank decision drift and post-news continuation are documented in equities/FX literature. The 4×ATR + close-location proxy is the standard codification when no consensus data exists (state this in implementation comments). Event clustering (CPI week, unlock calendars) creates correlated exposure across symbols — cap concurrent event trades at 3.

### STRATEGY: ADX Regime Switching — Trend vs Mean-Reversion Meta-Strategy
- family: Quant/Statistical & Forex-Specific
- markets: both
- timeframes: 4h / 1d
- entry_long: Regime gate on ADX(14) with hysteresis: ADX rises above 26 → TREND mode: enter long on 20/50 EMA bullish cross or Donchian(20) upside breakout. ADX falls below 20 → MEAN-REVERSION mode: enter long when RSI(14) < 30 or close z-score vs SMA(20) < −2. Between 20–26 (or mode transition < 3 bars old): flat.
- entry_short: Mirror in both modes: trend mode shorts on EMA bearish cross / Donchian downside break; MR mode shorts RSI > 70 / z > +2 (spot: exits/flats).
- exit_rule: Mode-specific — trend: trail 2×ATR(14); MR: exit at SMA(20)/z=0, SL 1×ATR(14) beyond entry. Regime re-evaluated every bar close; open positions inherit their mode's exit rules even if regime flips (avoid mid-trade rule change), but no NEW entries against the new regime.
- params: adx_period=14, trend_enter=26, trend_exit=23, mr_thresh=20, ema=(20,50), donchian=20, rsi_period=14, z_window=20, trail_mult=2.0
- data_needs: OHLC
- notes: Classic Wilder-ADX regime filter; hysteresis band (26 in / 23 out) is mandatory — boundary whipsaw is the #1 failure mode. Implementation note: this is a META-strategy toggling two modules that already exist in core/strategies.py (trend-following and RSI/Bollinger MR) → cheapest high-value build in this catalog; strong paper-cycle candidate.

### STRATEGY: Volatility Percentile Regime Switching
- family: Quant/Statistical & Forex-Specific
- markets: both
- timeframes: 1d
- entry_long: Compute 20d realized vol and its percentile vs trailing 252d. vol_pct < 20 → COMPRESSION mode: favor breakouts — long on Donchian(20) upside break with wide targets (vol expansion expected). 20–80 → NORMAL: run default strategy set unmodified. (Long entries per active mode's module.)
- entry_short: Mirror for downside Donchian breaks in compression mode. vol_pct > 80 → CRISIS mode: no new entries (or trend-mode only at 50% size); let existing positions hit their stops.
- exit_rule: Compression-mode trades: TP = 2.5×ATR(20), trail after 1.5×. Crisis mode: exit or tighten all stops to 1.5×ATR. Mode re-evaluated daily.
- params: vol_lookback=20d, pct_window=252d, compression_max=20th pct, crisis_min=80th pct, donchian=20, tp_mult=2.5, crisis_size_mult=0.5
- data_needs: OHLC
- notes: Volatility clustering is the most robust stylized fact in all of finance; compression-precedes-expansion is its tradable corollary (same insight as Bollinger BandWidth squeeze, stated in percentile form for objectivity). Pairs naturally with #26 (ADX gate): ADX answers "directional or not", vol-percentile answers "expand or chop". Combining both into one regime engine is acceptable if budget-constrained.

### STRATEGY: Hurst Exponent Regime Filter
- family: Quant/Statistical & Forex-Specific
- markets: both
- timeframes: 1h / 4h / 1d
- entry_long: Rolling Hurst exponent H over 100 bars (DFA or wavelet estimator preferred over classic R/S). H > 0.55 → persistent/trending: enable trend module (MA cross / Donchian entries). H < 0.45 → anti-persistent: enable mean-reversion module (z-score/RSI fades back to mean).
- entry_short: Mirror per active module. 0.45 ≤ H ≤ 0.55 → near-random-walk: flat, no entries.
- exit_rule: Inherit active module's exits. Re-estimate H no more than once per day (weekly at 1d timeframe) to prevent flip-flopping; require H to stay in the new band for 2 consecutive estimates before switching modules.
- params: hurst_window=100 bars, trend_min=0.55, mr_max=0.45, estimator=DFA, confirmation=2 estimates
- data_needs: OHLC
- notes: Research-grade filter. H estimation is statistically noisy at short windows — 100 bars is the floor, and DFA/wavelet estimators are materially more stable than rescaled-range. Treat as an experiment in the paper cycle, not a core allocation. Documented use: regime classification in quant equity/FX literature; crypto application is less validated — honest status: promising heuristic.

### STRATEGY: Volatility Targeting Overlay — Constant-Risk Sizing
- family: Quant/Statistical & Forex-Specific
- markets: both
- timeframes: any (sizing overlay, re-evaluated per bar/day)
- entry_long: Not a signal — a sizing transform applied to any strategy's raw signal: position_size = base_size × (target_vol / σ_realized(20d)), capped to [0.25×, 2.0×] base size. Suggested targets: 10–15% annualized for forex majors, 30–40% for crypto single assets.
- entry_short: Same transform (symmetric).
- exit_rule: Inherits the underlying strategy's exits; adds a portfolio-level vol brake: if realized portfolio vol (20d) > 1.5× target, scale ALL positions down by factor target/realized until back under.
- params: target_vol=30% (crypto) / 10% (forex), vol_lookback=20d, cap_mult=(0.25, 2.0), portfolio_brake=1.5×
- data_needs: OHLC
- notes: OVERLAY, not standalone alpha. Evidence: Moreira & Muir (2017) — vol-managed portfolios earn higher Sharpe; Kim/Tse/Wald (2016) — much of TSMOM's published edge IS vol scaling. Deploy as a SHARED sizing module across all paper-cycle strategies so results are comparable on an equal-risk basis; without it, high-vol strategies win the cycle on leverage, not skill. Anti-martingale pyramiding (adding after wins) is only acceptable inside this vol cap.

### STRATEGY: Risk Parity / Equal Risk Contribution — Portfolio Allocation Overlay
- family: Quant/Statistical & Forex-Specific
- markets: both
- timeframes: 1d / weekly-monthly rebalance
- entry_long: Not a signal — capital allocation across N strategies or assets. Naive RP: weight w_i = (1/σ_i) / Σ(1/σ_j), σ from 60d EWMA. Full ERC: solve for weights where each component's risk contribution w_i·(Σw)_i is equal (iterative solver). Rebalance monthly, or when any weight drifts > 25% from target.
- entry_short: n/a
- exit_rule: Rebalance rule is the only action. Covariance estimated with Ledoit-Wolf shrinkage when N > 5 (sample covariance is too noisy).
- params: vol_lookback=60d EWMA, rebalance=monthly, drift_band=25%, shrinkage=Ledoit-Wolf, mode=naiveRP/ERC
- data_needs: OHLC multi-symbol (or per-strategy return series)
- notes: OVERLAY for the "keep the winners" phase: after the 1-week paper cycle, allocate surviving-strategy capital by inverse-vol/ERC instead of equal dollars — prevents one high-vol winner from dominating firm risk. Caveats: RP underperforms cap-weight in strong bull trends (by design); ERC solution is sensitive to correlation estimates — shrinkage is not optional. One week of paper data is too short for stable correlations; seed with prior 90d price-based vols.

---

## Implementation priority for the paper cycle (worker recommendation)

**OHLC-only, spot-compatible, build immediately (best ROI):**
#4/#5 TSMOM variants, #6 Cross-sectional momentum, #7 Dual momentum, #8/#9 pairs (single-leg spot adaptation), #10 OU mean reversion, #14/#15 grids, #18/#19/#20 seasonality trio, #21/#22/#23 session trio, #26/#27 regime switching, #29 vol targeting (as shared sizing module).

**Needs extra data/infra (flag before building):**
#11 funding arb (perp sim + funding feed), #13 basis trade (dated futures), #1/#2/#3 carry (rates feed — forex only), #24/#25 events (calendar feed), #28 Hurst (estimator library).

**Catalog-only, do not build:** #12 triangular (infra mismatch), #16 DCA (benchmark only), #17 martingale (DANGEROUS — excluded).

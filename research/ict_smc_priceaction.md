# ICT / Smart-Money-Concepts & Price-Action Strategy Catalog

Research worker: ICT/SMC & Price-Action family.
Target implementation contract: each strategy receives a list of OHLC(V) candle dicts (`{"open","high","low","close","volume", "timestamp"}`) and returns `None` or `{"action": "BUY"/"SELL", "confidence": 0..1, "reasons": [str]}`. Exits listed below are advisory for the positions/risk layer; SL/TP are stated in structure or ATR terms so the risk layer can compute size.

NOTE: several of these already exist in `core/strategies.py` in simplified form (`detect_fvg`, `detect_order_block`, `detect_liquidity_sweep`, `detect_bos_choch`, `detect_ote`, `detect_market_structure`, `detect_engulfing`). Entries below are the FULL published variants with filters; implementation should upgrade the existing detectors to this spec rather than duplicate them.

## Conventions used below (shared primitives — implement once, reuse)

- **Swing point (fractal)**: bar `i` is a swing high of order `k` (default k=3) if `high[i]` is strictly greater than the `k` bars on either side. Swing low mirrors. Use only CONFIRMED swings (bar i+k has closed) to avoid lookahead.
- **Displacement candle**: a candle whose body `|close-open|` >= 1.5 * SMA(|close-open|, 20) AND whose range `high-low` >= 1.2 * ATR(14). Displacement "through" a level means the candle CLOSES beyond the level (not just wicks).
- **FVG (fair value gap)**: 3-candle imbalance. Bullish FVG at bar i: `low[i] > high[i-2]`, zone = `[high[i-2], low[i]]`. Bearish FVG: `high[i] < low[i-2]`, zone = `[high[i], low[i-2]]`. A gap is "filled/invalidated" when price trades through its far edge (close beyond for conservative mode).
- **Order block (OB)**: bullish OB = the LAST bearish candle (`close < open`) within `ob_lookback` bars immediately preceding a bullish displacement that breaks a swing high; zone = `[low, high]` of that candle (conservative; ICT purists use `[low, open]`, see notes). Bearish OB mirrors.
- **Dealing range**: the most recent confirmed swing low → swing high (bullish leg) or swing high → swing low (bearish leg) with range >= `min_range_atr` * ATR(14).
- **Equilibrium / premium / discount**: 50% of dealing range = equilibrium; longs only valid below it (discount), shorts only above it (premium).
- **OTE zone**: fib retracement 0.62–0.79 of the impulse leg (entry reference 0.705).
- **Sessions (all times US/Eastern, as ICT publishes them)**: midnight open 00:00 ET; London killzone 02:00–05:00 ET; NY killzone 07:00–10:00 ET; Silver Bullet windows 03:00–04:00, 10:00–11:00, 14:00–15:00 ET. Crypto trades 24/7 so the ET clock is applied directly — do NOT skip weekends programmatically, but note weekend liquidity in backtests.
- **ATR** = Wilder ATR(14) on last closed bars unless stated. **R** = |entry − stop|.
- "sweep" of a level = wick trades beyond the level but the candle CLOSES back on the original side (no body close beyond).

---

### STRATEGY: Fair Value Gap Retracement Entry (FVG)
- family: ict_smc_priceaction
- markets: both
- timeframes: 15m, 1h, 4h
- entry_long: A bullish FVG forms at bar i (see conventions) AND the middle candle (i-1) is a displacement candle AND price retraces into the gap zone (last close <= FVG top) without any candle having closed below FVG bottom. Enter at first touch of the FVG 50% midpoint (consequent encroachment) or at first close inside the zone.
- entry_short: mirror of long (bearish FVG after bearish displacement, retrace into zone, midpoint entry).
- exit_rule: SL = FVG bottom (long) minus 0.25*ATR buffer; TP1 = origin swing high (the high that created the gap), TP2 = 2R. Cancel unfilled entry if price closes through the far edge of the gap first.
- params: displacement_body_mult=1.5, min_gap_atr=0.15 (ignore micro-gaps), fill_mode="midpoint", sl_buffer_atr=0.25, tp_r=2.0, max_wait_bars=20
- data_needs: OHLC
- notes: Core ICT entry model. The displacement requirement and the min-gap size filter are the two documented edge preservers — unfiltered "every 3-bar gap" versions (like the current `detect_fvg`) over-trade badly. Confluence with a higher-timeframe bias roughly doubles documented win rate vs counter-trend FVGs. High-frequency strategy on 15m — fee-sensitive; require min_gap_atr such that TP >= 15x taker fee.

### STRATEGY: Inversion FVG (IFVG)
- family: ict_smc_priceaction
- markets: both
- timeframes: 15m, 1h
- entry_long: A bearish FVG exists and price then CLOSES above the gap's top edge (gap violated = "inverted"). Enter long on the first subsequent retracement back into the inverted zone that holds (close back above zone top, or bullish rejection wick into zone with close in upper 50% of that candle).
- entry_short: mirror of long (bullish FVG closed through to the downside, retest holds as resistance).
- exit_rule: SL = far edge of inverted zone; TP = 2R or opposing swing extreme.
- params: min_gap_atr=0.15, retest_max_bars=15, tp_r=2.0
- data_needs: OHLC
- notes: Published ICT concept (2022+ mentorship content): a failed gap flips polarity, so the invalidation of other traders' entries becomes your entry. Fewer signals than plain FVG but documented higher reliability because it confirms with a close, not an assumption. Codable without discretion — the "close through the gap" is the binary trigger.

### STRATEGY: Order Block Retest
- family: ict_smc_priceaction
- markets: both
- timeframes: 15m, 1h, 4h
- entry_long: Identify bullish OB (conventions). Wait for price to retrace into the OB zone; enter at first touch of OB midpoint, or on a rejection candle (close in top 50% of its range) inside the zone. OB must be UNMITIGATED (price has not traded below OB low since formation).
- entry_short: mirror of long (bearish OB = last bullish candle before bearish displacement breaking a swing low).
- exit_rule: SL = OB low (long) − 0.25*ATR; TP1 = breakaway swing high, TP2 = 2.5R. If price closes below the OB low before fill, delete the zone.
- params: ob_lookback=8, sl_buffer_atr=0.25, tp_r=2.5, max_zone_age_bars=100, retests_allowed=1
- data_needs: OHLC
- notes: The single most-traded SMC concept. Documented failure mode: OBs that formed WITHOUT displacement are no better than random S/R — the displacement gate is mandatory. Freshness matters: published SMC practice uses each zone once (first retest only); repeated retests degrade the zone. Use full `[low, high]` zone for backtesting; the wick-only `[low, open]` variant is ICT-canonical but halves fill rate.

### STRATEGY: Breaker Block
- family: ict_smc_priceaction
- markets: both
- timeframes: 1h, 4h
- entry_long: A bearish OB forms and price breaks DOWN through it (OB fails), then that same down-leg sweeps a sell-side liquidity level (old swing low) and reverses with a bullish displacement that closes back ABOVE the failed OB. The failed bearish OB becomes a bullish breaker — enter long on first retest of the breaker zone from above.
- entry_short: mirror of long (failed bullish OB broken up, buy-side swept, bearish displacement back below, retest from below).
- exit_rule: SL = breaker zone far edge; TP = 2.5R or opposing liquidity pool.
- params: sweep_window=10, displacement_body_mult=1.5, tp_r=2.5, max_retest_bars=30
- data_needs: OHLC
- notes: ICT breaker = "failed order block + liquidity sweep + reclaim". Three-condition sequence is fully codable as a state machine (OB broken → sweep → displacement close back through zone). Signals are rare (a few/month/pair on 1h) but this is one of the highest-documented-R:R SMC setups. Implementation pitfall: require the sweep leg to trade through the OB, not just approach it.

### STRATEGY: Mitigation Block
- family: ict_smc_priceaction
- markets: both
- timeframes: 1h, 4h
- entry_long: An up-move fails to take out the prior swing high (lower high forms), price then sells off, and the origin of that failed up-move (last bullish candle cluster before the sell-off) becomes a mitigation zone. When price later rallies back into that zone AND the higher-timeframe bias is bearish, treat as SHORT entry at zone touch... (for the long side: mirror — a down-move fails to make a lower low, origin of the failed down-move mitigates on the next dip; enter long at zone touch with bullish HTF bias).
- entry_short: bearish mitigation: failed rally that did not break the prior high; origin candle zone of that rally is retested from below; enter short at zone midpoint with bearish bias.
- exit_rule: SL = zone far edge; TP = the swing extreme that the failed move was targeting (the unbroken high/low), min 2R.
- params: zone_lookback=30, tp_min_r=2.0
- data_needs: OHLC
- notes: Mitigation = smart money returning to close residual positions at breakeven; zone = origin of a FAILED move (vs breaker = origin of a move that was itself broken). The distinction is codable: breaker requires price to trade THROUGH the zone; mitigation requires the move OUT of the zone to fail at structure. Documented as weaker standalone than breaker/OB — use with HTF bias gate; in backtest expect ~45–55% win rate at 2R.

### STRATEGY: Liquidity Sweep Reversal (Swing Stop Hunt)
- family: ict_smc_priceaction
- markets: both
- timeframes: 15m, 1h, 4h
- entry_long: Price sweeps a confirmed swing low (low < swing low, close back above it) AND the sweep candle closes in its top 50% AND the NEXT candle closes above the sweep candle's high. Enter on that confirmation close. (Upgrade of existing `detect_liquidity_sweep`.)
- entry_short: mirror of long (sweep of swing high, close back below in bottom 50%, next candle closes below sweep candle's low).
- exit_rule: SL = sweep candle extreme; TP1 = nearest opposing swing (1.5–2R typical), TP2 = 3R with trail after TP1.
- params: swing_k=3, close_position=0.5, confirm_bars=1, tp_r=3.0
- data_needs: OHLC
- notes: The confirmation candle requirement is the published fix for the classic failure mode (sweep becomes genuine breakout). Expect slightly worse price but far fewer dead signals than entering on the sweep candle itself. Works on both markets; in crypto the sweep-then-continue pattern is more common on weekends/low-liquidity — consider a volume filter (sweep candle volume >= 1.5 * SMA(vol,20) documented to improve quality).

### STRATEGY: Equal Highs/Lows Liquidity Raid (Turtle Soup)
- family: ict_smc_priceaction
- markets: both
- timeframes: 15m, 1h
- entry_long: Detect equal lows: two confirmed swing lows within `eq_tol_atr` * ATR of each other, separated by >= 5 bars. When price trades below the lower of the two by less than `max_penetration_atr` * ATR and closes back above the equal-lows level within `raid_bars` candles, enter long on that reclaim close.
- entry_short: mirror of long (equal highs raided, close back below).
- exit_rule: SL = raid extreme; TP = nearest internal swing high or 2R, whichever first.
- params: eq_tol_atr=0.1, min_separation=5, max_penetration_atr=0.5, raid_bars=3, tp_r=2.0
- data_needs: OHLC
- notes: Linda Raschke's "Turtle Soup" (published 1995 in *Street Smarts*) is the same setup ICT calls "equal highs/lows liquidity" — the oldest formally backtested version of the concept. The penetration cap is critical: deep penetration statistically converts the raid into a breakout; the published turtle-soup rule requires the false break to be shallow and fast. Well-documented positive edge in forex intraday; works on crypto majors.

### STRATEGY: Session High/Low Sweep + Reclaim (Judas Swing)
- family: ict_smc_priceaction
- markets: both (best-documented on forex majors and BTC/ETH)
- timeframes: 5m, 15m
- entry_long: During the London or NY killzone, price sweeps the Asian-session low (Asian range = 20:00 ET previous day → 00:00 ET, or 00:00–02:00 ET pre-London range — parametrize) and closes back inside the Asian range on a 5m/15m candle within the killzone. Enter long on the reclaim close.
- entry_short: mirror of long (killzone sweep of Asian/session high, close back inside).
- exit_rule: SL = sweep extreme; TP1 = opposite side of Asian range, TP2 = 1.0 * Asian range height projected from the sweep (measured move). Hard time stop: exit by end of killzone + 2h if neither hit.
- params: asia_start_et="20:00", asia_end_et="00:00", killzone="london", sweep_max_bars=12, tp_mode="range_projection", time_stop_h=2
- data_needs: OHLC + timestamps (session-aware)
- notes: ICT "Judas swing" = the false move at session open that runs stops before the real move. Fully codable with session timestamps. Documented best on EURUSD/GBPUSD and BTC. Pitfall: needs intraday data with correct timezone handling; on crypto weekends the "sessions" are weak — gate on weekday for backtest comparability. Time-stop is essential — Judas moves that don't resolve within ~2h typically were real breakouts.

### STRATEGY: Break of Structure Continuation (BOS Pullback)
- family: ict_smc_priceaction
- markets: both
- timeframes: 1h, 4h
- entry_long: In an established uptrend (>= 2 consecutive higher highs and higher lows by confirmed swings), price closes ABOVE the most recent swing high with a displacement candle (BOS). Enter on the first pullback into the BOS candle's 50% range or the FVG it created, whichever is touched first, provided price has not closed below the last higher low.
- entry_short: mirror of long (downtrend, displacement close below last swing low, pullback into BOS candle/FVG).
- exit_rule: SL = pullback origin (last HL) − 0.25*ATR; TP = 2R or next external liquidity (measured 1:1 projection of the prior leg).
- params: swing_k=3, displacement_body_mult=1.5, entry_zone="bos_50pct_or_fvg", tp_r=2.0, max_wait_bars=15
- data_needs: OHLC
- notes: Trend-continuation backbone of SMC. Key codable distinction from CHoCH: BOS requires the trend to already be established (structure count >= 2) — do not fire on the first break. The pullback entry (vs break-entry) is the documented SMC variant and roughly halves the stop distance vs entering the break close. Cancel entry if structure breaks before fill.

### STRATEGY: CHoCH / Market Structure Shift Reversal
- family: ict_smc_priceaction
- markets: both
- timeframes: 1h, 4h
- entry_long: Downtrend in place (>= 2 lower lows/lower highs). Price sweeps the last lower low (liquidity grab), then rallies and CLOSES above the most recent lower high with displacement (Change of Character). Enter long on the first retracement that holds above the broken level (close does not return below it).
- entry_short: mirror of long (uptrend, sweep of last higher high, displacement close below last higher low, failed retest).
- exit_rule: SL = reversal leg extreme; TP1 = origin of the prior trend leg (2R typical); trail below new higher lows once structure flips.
- params: swing_k=3, displacement_body_mult=1.5, retest_max_bars=15, tp_r=2.0
- data_needs: OHLC
- notes: The sweep-before-CHoCH requirement is the single biggest documented quality filter — a CHoCH without a preceding liquidity grab has materially worse follow-through in published SMC backtests. This is a REVERSAL strategy: size down vs continuation plays and demand the retest-hold confirmation. The existing `detect_bos_choch` in the codebase fires on raw structure breaks without sweep/displacement filters; upgrade to this spec.

### STRATEGY: Optimal Trade Entry (OTE)
- family: ict_smc_priceaction
- markets: both
- timeframes: 15m, 1h, 4h
- entry_long: Identify a bullish impulse leg (swing low → swing high, range >= 1.5*ATR, with displacement). Fib the leg; enter long when price retraces into the 0.62–0.79 zone (place limit at 0.705 or enter on first bullish rejection close inside the zone). Leg high must be UNBROKEN since formation and the 0.0 (leg low) must not be violated.
- entry_short: mirror of long (bearish impulse, retrace up into 0.62–0.79).
- exit_rule: SL = leg low (long); TP1 = leg high (≈1.5–2.5R), TP2 = 1.272 fib extension of the leg.
- params: fib_low=0.62, fib_high=0.79, entry_fib=0.705, min_leg_atr=1.5, max_wait_bars=25, tp2_ext=1.272
- data_needs: OHLC
- notes: ICT's signature entry model; the 0.705 "sweet spot" is the published reference. Existing `detect_ote` uses 61.8–79% without leg-quality gates — add min_leg_atr and displacement requirements. Documented edge is entirely in the stop placement (tight invalidation at leg origin); without the leg-origin SL the system has no edge. Reject legs whose retrace exceeds 0.79 before entry — deep retraces statistically fail.

### STRATEGY: Premium/Discount Zone Filter (Equilibrium Model)
- family: ict_smc_priceaction
- markets: both
- timeframes: 4h, 1d (zone definition) with 15m/1h execution
- entry_long: Compute dealing range from higher timeframe. LONG setups (from any companion entry model — FVG, OB, sweep) are only taken when the entry price is in DISCOUNT (below 50% of range); additionally fire a standalone signal when price enters the deepest discount quintile (<= 20% of range) AND prints a bullish rejection candle (close in top 40%).
- entry_short: mirror of long (premium zone >= 80% of range with bearish rejection).
- exit_rule: SL = range extreme or 1.5*ATR beyond entry; TP = equilibrium (50%) at minimum, range opposite extreme as stretch target.
- params: htf="4h", deep_zone=0.2, min_range_atr=2.0, tp_mode="equilibrium"
- data_needs: OHLC (two timeframes, or resample)
- notes: This is published by ICT as a FILTER, not a standalone system — codify it as both: (a) a gate the strategy-runner can apply to any other entry (recommended), and (b) the standalone extreme-zone rejection version above. The gate version is the more robust deliverable: buying premium/selling discount is the most common documented cause of SMC retail losses.

### STRATEGY: Unicorn Setup (Breaker + FVG Overlap)
- family: ict_smc_priceaction
- markets: both
- timeframes: 15m, 1h
- entry_long: A bullish breaker block forms (per Breaker Block spec) AND the displacement leg that reclaimed the OB also left a bullish FVG that OVERLAPS the breaker zone. Entry = first retest of the overlap zone (intersection of breaker zone and FVG zone).
- entry_short: mirror of long.
- exit_rule: SL = overlap zone far edge; TP = 3R (documented typical achieved R:R is high; 3R keeps expectancy realistic).
- params: tp_r=3.0, max_retest_bars=20
- data_needs: OHLC
- notes: ICT "unicorn" = the confluence of a breaker and an FVG in the same price zone; the overlap intersection is the entry. Rare (1–3/month on a major pair 1h) but it is the highest-confluence two-pattern stack in the ICT canon that is still objectively codable. Implementation: run breaker and FVG detectors, intersect zones, require non-empty overlap >= 0.1*ATR wide.

### STRATEGY: Power of Three / AMD (Accumulation–Manipulation–Distribution)
- family: ict_smc_priceaction
- markets: both
- timeframes: 1h, 4h (daily open as anchor)
- entry_long: Anchor = daily open at 00:00 ET. Accumulation = price trades within a range of <= `acc_range_atr` * ATR around the open for the first `acc_hours`. Manipulation = price then breaks BELOW the accumulation range by a shallow amount (<= `manip_max_atr` * ATR) sweeping its low, and closes back inside the range. Distribution = enter long on the first displacement close above the manipulation leg's high; TP = range expansion opposite the manipulation.
- entry_short: mirror of long (manipulation = shallow break above, close back inside, displacement close below manipulation low).
- exit_rule: SL = manipulation extreme; TP = 1.0–2.0 * accumulation range projected in the distribution direction; time stop at 20:00 ET (before next cycle).
- params: anchor_et="00:00", acc_hours=6, acc_range_atr=1.0, manip_max_atr=0.75, tp_range_mult=1.5, time_stop_et="20:00"
- data_needs: OHLC + timestamps
- notes: ICT "Power of Three" = open → manipulation against the true daily direction → expansion. The daily-candle version (weekly AMD on 1d) also exists: accumulate Mon–Tue, manipulate, distribute Thu–Fri — codable identically with weekly anchor. Pitfall: the accumulation range cap must adapt to volatility (use ATR multiples, not fixed pips). Documented strongest on instruments with a real daily open (forex, CME); for crypto use the 00:00 UTC or ET open consistently and do not mix.

### STRATEGY: ICT Silver Bullet (Time-Window FVG)
- family: ict_smc_priceaction
- markets: both (published for indices/forex; crypto applicable)
- timeframes: 1m, 3m, 5m
- entry_long: During a Silver Bullet window (03:00–04:00, 10:00–11:00, or 14:00–15:00 ET), with higher-timeframe draw on liquidity established (e.g., 1h bias bullish / price below a targeted old high), wait for the FIRST bullish FVG to form on 1–5m inside the window. Enter at the FVG midpoint on the retrace.
- entry_short: mirror of long in bearish bias.
- exit_rule: SL = FVG far edge or window low/high extreme; TP = the drawn liquidity level (target fixed before entry), min 2R. Hard exit at window end + 60 min.
- params: windows=["03:00-04:00","10:00-11:00","14:00-15:00"], exec_tf="3m", bias_tf="1h", min_tp_r=2.0
- data_needs: OHLC + timestamps (1–5m granularity)
- notes: One of ICT's most explicitly codified models: fixed time window + first FVG + predefined liquidity target. Requires tick/1m-quality data to backtest honestly — on 15m bars it degenerates to "buy the session move" and loses its edge. High trade frequency and small targets make it the MOST fee/slippage-sensitive entry in this catalog; on a paper broker model it will look better than reality. Flag for cost modeling before promotion.

### STRATEGY: Killzone Open-Range Breakout (London/NY)
- family: ict_smc_priceaction
- markets: both
- timeframes: 5m, 15m
- entry_long: Define the pre-killzone range (last 60 min before killzone start). During the killzone, enter long on the first 15m close above the pre-range high with range >= 0.5*ATR, provided no close beyond the opposite side happened first (one-directional day filter).
- entry_short: mirror of long.
- exit_rule: SL = opposite side of pre-range (or midpoint for tighter variant); TP = 1.0 * pre-range height projected; time stop at killzone end + 2h.
- params: killzone="london" (alt "ny"), pre_range_min=60, min_range_atr=0.5, tp_range_mult=1.0, time_stop_h=2
- data_needs: OHLC + timestamps
- notes: The mechanical sibling of the Judas Swing — trade the expansion instead of the fake-out. Classic session-open momentum literature (opening range breakout) shows it works when pre-range is NARROW relative to ATR; add a max-range filter (pre-range <= 1.2*ATR) to skip already-expanded days. Mutually exclusive with Judas Swing on the same session — run both in backtest and keep the winner per instrument.

### STRATEGY: SMT Divergence (Correlated-Instrument Divergence)
- family: ict_smc_priceaction
- markets: both (pairs: BTC/ETH, EURUSD/DXY or EURUSD/GBPUSD, ES/NQ)
- timeframes: 15m, 1h, 4h
- entry_long: Two correlated instruments (e.g., BTC and ETH). Instrument A makes a LOWER low at a swing point while instrument B makes a HIGHER low (or equal low within 0.1*ATR) at the same timestamp ± 2 bars — the "crack" in correlation. Enter long on the instrument that made the higher low (the relatively stronger one) after its confirmation close above the divergence swing's midpoint.
- entry_short: mirror of long (A makes higher high, B fails to — short the weaker one that made the lower high).
- exit_rule: SL = divergence swing extreme; TP = 2R or the prior swing that aligns with the stronger instrument's direction.
- params: corr_pair=["BTC","ETH"], swing_k=3, time_slack_bars=2, eq_tol_atr=0.1, tp_r=2.0
- data_needs: OHLC multi-symbol (two synchronized series)
- notes: ICT's divergence variant — one leg of a correlated pair sweeping liquidity while the other refuses = signature of engineered liquidity. Objectively codable but needs the data layer to serve synchronized multi-symbol frames — flag to the implementation team as a data prerequisite. Documented reliability is high on tightly correlated pairs (corr > 0.8 trailing 90d); add that correlation gate.

### STRATEGY: Bullish/Bearish Engulfing at Structure
- family: ict_smc_priceaction
- markets: both
- timeframes: 1h, 4h, 1d
- entry_long: Prior trend down (close < SMA50 or last two swings = LH/LL). Bullish engulfing: current candle opens <= prior close and closes >= prior open, with current body >= 1.2 * prior body, AND the pattern prints at a demand zone / swing low / discount zone (see Premium/Discount). Enter on close of the engulfing candle.
- entry_short: mirror of long (uptrend, bearish engulfing at supply/premium).
- exit_rule: SL = engulfing candle low − 0.25*ATR; TP = 2R.
- params: body_mult=1.2, trend_sma=50, require_zone=true, sl_buffer_atr=0.25, tp_r=2.0
- data_needs: OHLC
- notes: Bulkowski's encyclopedia (published stats): raw engulfing is barely better than coin-flip (~51–55%), but engulfing AT a level after a pullback in the higher trend direction is the documented profitable variant — hence the zone gate, which the current codebase's `detect_engulfing` lacks. On 1d forex majors the pattern is rare but respectable; on 15m crypto it's noise without the zone filter.

### STRATEGY: Pin Bar / Hammer / Shooting Star Rejection
- family: ict_smc_priceaction
- markets: both
- timeframes: 1h, 4h, 1d
- entry_long: Hammer/pin bar at support: lower wick >= 2.0 * body, upper wick <= 0.3 * body, candle range >= 1.0*ATR, and the low of the candle is at/below a support level or swing low (test). Enter on break of the pin bar's high (next candle trades above) or on close if close is in top 25%.
- entry_short: mirror of long (shooting star: upper wick >= 2*body at resistance; enter on break of its low).
- exit_rule: SL = pin bar extreme; TP = 2R or nearest opposing structure.
- params: wick_body_ratio=2.0, nose_max=0.3, min_range_atr=1.0, entry_mode="break_of_extreme", tp_r=2.0
- data_needs: OHLC
- notes: The wick-ratio quantification above is the standard published codification (Nison/Pring variants all converge on ~2:1). Entry on break-of-extreme (vs immediate) is the documented higher-quality variant — it filters pins that immediately continue. Reliability documented highest on 4h/1d; on 15m the pattern is too common to mean anything without a level behind it. The level requirement is NOT optional.

### STRATEGY: Doji Reversal at Extremes (Dragonfly/Gravestone)
- family: ict_smc_priceaction
- markets: both
- timeframes: 4h, 1d
- entry_long: Dragonfly doji at support/discount: body <= 0.1 * candle range, lower wick >= 60% of range, upper wick <= 15% of range, after a decline of >= 3 consecutive bearish closes or >= 1.5*ATR down-move. Enter on next candle breaking the doji high.
- entry_short: mirror (gravestone at resistance/premium: upper wick >= 60% of range after a rally; enter on break of doji low).
- exit_rule: SL = doji extreme; TP = 2R or mean reversion to SMA20.
- params: body_pct=0.1, wick_pct=0.6, prior_leg_bars=3, tp_mode="2R_or_sma20"
- data_needs: OHLC
- notes: Plain doji = indecision, no edge (documented ~50%). Only the long-shadow variants (dragonfly/gravestone) AT extremes with a preceding extended leg show published reversal edge; the quantified shadow percentages above are the standard textbook codification. Standard doji (cross) excluded deliberately. Needs the "extended prior leg" gate — a dragonfly in mid-range means nothing.

### STRATEGY: Morning Star / Evening Star
- family: ict_smc_priceaction
- markets: both
- timeframes: 4h, 1d
- entry_long: Morning star (3 candles): candle1 bearish body >= 1.0*ATR body avg; candle2 small body (<= 0.4 * candle1 body) gapping/stalling below candle1 close (in crypto: close within lower 25% of candle1's range — gaps are rare); candle3 bullish close >= 50% into candle1's body. Enter on candle3 close.
- entry_short: mirror (evening star at top).
- exit_rule: SL = candle2 low (long); TP = full retrace of candle1 (≈2R) then trail.
- params: star_body_pct=0.4, third_close_pct=0.5, gap_mode="crypto_no_gap", tp_mode="candle1_retrace"
- data_needs: OHLC
- notes: Bulkowski ranks morning/evening star among the better-performing candle reversals on daily data (published ~60%+ bullish reversal rate on 1d equities/forex samples). Crypto codification must relax the gap requirement (param above) — 24/7 markets rarely gap; without the relaxation the pattern almost never fires. Low frequency: expect a handful of signals per pair per year on 1d.

### STRATEGY: Three White Soldiers / Three Black Crows
- family: ict_smc_priceaction
- markets: both
- timeframes: 4h, 1d
- entry_long: Three consecutive bullish candles, each closing in its top 30%, each opening within the prior candle's body, combined range of the three >= 1.5*ATR, appearing after a decline (prior 5-bar return < 0). Enter on close of candle 3 (momentum variant) or on a 38.2% retrace of the 3-candle range (pullback variant, documented better entry).
- entry_short: mirror (three black crows after a rally).
- exit_rule: SL = candle1 low (momentum) or 61.8% of pattern range (pullback); TP = 2R.
- params: close_pct=0.3, min_pattern_atr=1.5, entry_mode="pullback_382", tp_r=2.0
- data_needs: OHLC
- notes: Documented as a CONTINUATION pattern as often as a reversal — the edge is in the pullback entry, buying the first dip after the soldiers print. Buying candle-3 close chase-style has published negative expectancy in extended conditions. Add an extension filter: skip if candle3 close is > 2.0*ATR above SMA20 (over-extended).

### STRATEGY: Harami (Inside-Body Reversal)
- family: ict_smc_priceaction
- markets: both
- timeframes: 4h, 1d
- entry_long: After a down-leg (>= 3 bearish closes), candle1 is a large bearish body (>= 1.2*avg body); candle2's entire body sits inside candle1's body and candle2 is bullish (or small-bodied). Enter on next candle breaking above candle2's high, only if price is at support/discount.
- entry_short: mirror (bearish harami after up-leg at resistance/premium).
- exit_rule: SL = candle2 low; TP = 1.5–2R (harami targets are documented modest — treat as early-warning, not home-run).
- params: mom_body_mult=1.2, prior_leg_bars=3, tp_r=1.75
- data_needs: OHLC
- notes: Harami = momentum stall; published stats show it as a moderate-reliability reversal that needs trend context and a break-of-extreme trigger. The harami CROSS (candle2 is a doji) variant: same rules with body <= 0.1*range — slightly rarer, similar performance; implement as the same function with a doji flag. Without the level gate it underperforms.

### STRATEGY: Tweezer Tops/Bottoms (Double Rejection)
- family: ict_smc_priceaction
- markets: both
- timeframes: 1h, 4h
- entry_long: Tweezer bottom: two consecutive (or within 3 bars) candles whose lows match within 0.1*ATR, at a support level, where candle1 is bearish and candle2 is bullish. The double-tap IS a mini equal-lows event: enter on close of candle2 if it closes in its top 50%, else on break of the two-candle high.
- entry_short: mirror (tweezer top: matching highs, bearish second candle).
- exit_rule: SL = tweezer extreme − 0.25*ATR; TP = 2R.
- params: match_tol_atr=0.1, window=3, require_level=true, tp_r=2.0
- data_needs: OHLC
- notes: Bridges the candlestick library and the liquidity framework — a tweezer at equal lows is the 2-bar version of the liquidity-raid setup. Documented edge requires the matching extremes to sit at a visible level; mid-air tweezers are random. Simple to implement on top of the swing/level primitives.

### STRATEGY: Inside Bar Breakout (Volatility Compression)
- family: ict_smc_priceaction
- markets: both
- timeframes: 4h, 1d
- entry_long: Inside bar: candle2 high < candle1 high AND candle2 low > candle1 low, with candle2 range <= 0.6 * candle1 range and candle1 range >= 1.0*ATR (meaningful mother bar). In an uptrend (close > SMA50), enter on break of candle1 high.
- entry_short: mirror in downtrend (break of candle1 low).
- exit_rule: SL = candle1 opposite extreme (conservative) or candle2 opposite extreme (tight, documented better R:R); TP = 2R; cancel if not triggered within 3 bars.
- params: compress_ratio=0.6, min_mother_atr=1.0, trend_sma=50, cancel_bars=3, tp_r=2.0
- data_needs: OHLC
- notes: The trend-side-only requirement is the documented fix — counter-trend inside-bar breaks have ~random outcomes. Mother-bar size gate separates real compression from noise. Closely related to NR4/NR7 (narrowest range of last 4/7 bars): if implementation time allows, an `nr_window` param generalizes this entry to the Toby Crabel NR family; noted as an optional extension.

### STRATEGY: S/R Flip (Polarity Principle)
- family: ict_smc_priceaction
- markets: both
- timeframes: 1h, 4h, 1d
- entry_long: A horizontal resistance level (tested >= 2 times as swing highs within 0.3*ATR tolerance) is broken by a displacement close. Enter long on the FIRST retest of the level from above that holds (rejection close or close back above level after intrabar dip).
- entry_short: mirror (support broken, retest from below holds as resistance).
- exit_rule: SL = level ∓ 0.5*ATR (beyond the flip zone); TP = measured move = level ± (last leg height), min 2R.
- params: min_touches=2, touch_tol_atr=0.3, displacement_body_mult=1.5, sl_atr=0.5, tp_min_r=2.0
- data_needs: OHLC
- notes: The oldest price-action principle in the catalog (polarity, Edwards & Magee era) and the conceptual parent of the ICT breaker. Codable with a level-clustering primitive: cluster swing points within tolerance, count touches. Documented failure mode: retests that arrive > 30 bars after the break lose polarity meaning — add max_retest_bars=30.

### STRATEGY: Supply/Demand Zone First Retest (RBR/DBD)
- family: ict_smc_priceaction
- markets: both
- timeframes: 1h, 4h
- entry_long: Demand zone = base → explosive rally: identify a tight base (>= 2 consecutive candles with combined range <= 0.8*ATR) followed by a rally leg of >= 1.5*ATR with displacement (rally-base-rally). Zone = base candle range. Enter long at first untested return to the zone top (limit) with SL below zone.
- entry_short: mirror (drop-base-drop supply zone; enter short at first return to zone bottom).
- exit_rule: SL = zone far edge − 0.25*ATR; TP = 2R or opposing zone. Zone deleted after first touch (fresh-zone rule).
- params: base_max_atr=0.8, leg_min_atr=1.5, displacement_body_mult=1.5, tp_r=2.0, max_zone_age_bars=100
- data_needs: OHLC
- notes: Sam Seiden's supply/demand framework — mechanically the non-ICT-branded sibling of the order block; the difference is codable (OB = single origin candle; S/D zone = multi-candle base). Published edge claims rest entirely on FRESHNESS (first retest only) and DEPARTURE STRENGTH (displacement leg) — both parametrized above. Avoid zones whose base sits mid-range of a larger consolidation (nested bases fail).

---

## Coverage notes for the implementation team

- **Deliberately excluded / merged**: "2022 ICT mentorship model" (= sweep + displacement + FVG, already the composition of Liquidity Sweep + FVG entries above); "NWOG" (New Week Opening Gap — futures-specific, crypto has no weekly close; can be approximated with the Monday 00:00 UTC open if wanted later); "ICT 2022 FVG scalping" (covered by Silver Bullet); "crab/harmonic patterns" (that's the Harmonics family's job, not price action — assign to another worker if unclaimed).
- **Data prerequisites beyond plain OHLC**: SMT Divergence needs synchronized multi-symbol frames; all session/killzone strategies need reliable timestamps with a single agreed timezone (recommend storing UTC, converting to ET in the detector).
- **Highest expected value per implementation effort** (my ranking from published evidence): 1) Liquidity Sweep Reversal, 2) FVG with displacement filter, 3) BOS Pullback, 4) Judas Swing, 5) Turtle Soup. Candlestick singles (doji, harami) are the weakest standalone — include for completeness and as ensemble votes, not solo signals.

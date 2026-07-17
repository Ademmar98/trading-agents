# Breakout & Volatility Strategy Catalog

Research worker: Breakout & Volatility family.
Target implementation contract: each strategy receives a list of OHLC(V) candle dicts (`{"open","high","low","close","volume"}`) and returns `None` or `{"action": "BUY"/"SELL", "confidence": 0..1, "reasons": [str]}`. Exits listed below are advisory for the positions/risk layer; ATR-based exits are the common default because that is what the codebase already supports.

Conventions used below (same as sibling catalogs):
- "cross above / close above X" = confirmed on the last CLOSED bar: previous closed bar's value <= X AND last closed bar's value > X. No lookahead.
- All indicator values are computed on the last CLOSED bar unless stated.
- ATR = Average True Range, Wilder smoothing, period 14 unless stated. "N" in Turtle entries = ATR(20).
- R = initial risk unit (entry − stop distance).
- Donchian channel rule: highest high / lowest low of the PRIOR n bars (excluding the current bar) — the standard, lookahead-free definition.
- Session windows are defined on bar timestamps. Crypto sessions use UTC (day open 00:00 UTC). Forex: London open 08:00 local (07:00 UTC summer / 08:00 UTC winter — implementer must handle DST), New York equity open 13:30 UTC summer / 14:30 UTC winter, Asian session 00:00–07:00 UTC. On 24/7 crypto, "session" strategies use these UTC windows anyway.
- Buffers on breakout triggers are expressed as fractions of ATR to avoid tick-size guessing; default buffer = 0.1*ATR.

---

### STRATEGY: Donchian 20-Bar Breakout (Turtle System 1)
- family: breakout_volatility
- markets: both (crypto 1d/4h works best; forex majors 1d)
- timeframes: 4h, 1d
- entry_long: close > highest high of prior 20 bars. Turtle original used an intraday touch of the level; close-confirmed is the standard codable variant (noted). Original filter: skip the signal if the previous 20-bar breakout would have been a winner.
- entry_short: close < lowest low of prior 20 bars, same skip rule.
- exit_rule: SL = entry − 2*N (N = ATR(20)); exit long on touch/close below the 10-bar Donchian low (Turtle exit), whichever first. Original pyramids every +0.5N up to 4 units — optional, off by default.
- params: entry_len=20, exit_len=10, n_atr=20, sl_n_mult=2.0, skip_winner_filter=false
- data_needs: OHLC (>= 25 bars)
- notes: Original Turtle rules are fully public (Covel, "Trend Following"; Curtis Faith's PDF). The skip-winner filter is part of the authentic system but is stateful — leave off by default and note it. Low trade frequency, high cost tolerance. Long flat stretches in chop are normal; edge is in tail trends.

### STRATEGY: Donchian 55-Bar Breakout (Turtle System 2)
- family: breakout_volatility
- markets: both
- timeframes: 4h, 1d
- entry_long: close > highest high of prior 55 bars. No skip filter (System 2 takes every signal).
- entry_short: close < lowest low of prior 55 bars.
- exit_rule: SL = entry − 2*N; exit on close beyond the 20-bar Donchian opposite extreme. No TP — pure trend ride.
- params: entry_len=55, exit_len=20, n_atr=20, sl_n_mult=2.0
- data_needs: OHLC (>= 60 bars)
- notes: The slower Turtle system; fewer, larger winners. Pairs naturally with System 1 as a portfolio. On crypto 4h a 55-bar channel ≈ 9 days — reasonable breakout horizon. Whipsaw losses cluster around 1–2R; position sizing must assume long losing streaks (documented 10+ consecutive losses historically).

### STRATEGY: N-Day High Breakout with Trend Filter
- family: breakout_volatility
- markets: both
- timeframes: 1h, 4h, 1d
- entry_long: close > highest high of prior N bars (N=20) AND close > EMA(close,200) AND EMA200 slope over 10 bars > 0 (rising). Trigger buffer: require close > donchian_high + 0.1*ATR.
- entry_short: mirror of long (close < prior N-bar low − 0.1*ATR, close < EMA200, EMA200 falling).
- exit_rule: SL = entry − 2*ATR; TP = 3R, or trail chandelier = highest close since entry − 3*ATR once trade > +1.5R.
- params: n=20, trend_ema=200, slope_lookback=10, buffer_atr=0.1, sl_atr=2.0, tp_r=3.0
- data_needs: OHLC (>= 210 bars)
- notes: The generic "breakout + regime filter" workhorse; the EMA200/slope gate is the standard published fix for Donchian chop (cut trade count ~half in sideways regimes). 52-week-high effects documented in equities; in crypto the 20–60 bar channel on 4h/1d is the practical analogue. Good candidate for the paper-test baseline.

### STRATEGY: Previous Day High/Low Breakout (PDH/PDL)
- family: breakout_volatility
- markets: both (crypto: UTC day boundaries; forex: broker day)
- timeframes: 5m, 15m, 1h
- entry_long: intraday close (current timeframe) above previous day's high + 0.1*ATR(14, 1d), while no position open today. One trade per level per day max.
- entry_short: intraday close below previous day's low − 0.1*ATR.
- exit_rule: SL = 1*ATR(14, 1d) from entry or the previous day's mid, whichever tighter; TP = 1.5R; hard time exit at session/day end (flat by 23:55 UTC on crypto).
- params: buffer_atr=0.1, sl_atr=1.0, tp_r=1.5, max_trades_per_day=1
- data_needs: OHLC (intraday bars + daily aggregation from same feed)
- notes: A staple of intraday index/forex trading ("yesterday's high/low are the day's most-watched levels"). Edge is a stop-run continuation; works best on days with overnight news/vol gap. Crypto caveat: weekends print fake PDH breaks on thin books — require volume >= 1.2*SMA(volume,20) on the trigger bar as a sanity gate.

### STRATEGY: Weekly Opening Range Breakout
- family: breakout_volatility
- markets: both (crypto: week opens Monday 00:00 UTC; forex: week opens Sunday 22:00 UTC)
- timeframes: 1h, 4h
- entry_long: define range = high/low of the first 12 hours of the week. Long on close above range high + 0.2*ATR(14), valid only during the first 3 days of the week.
- entry_short: mirror of long.
- exit_rule: SL = opposite side of the opening range (or 1.5*ATR if range is wider than 1.5*ATR); TP = 2R; cancel untriggered setup after day 3.
- params: range_hours=12, buffer_atr=0.2, validity_days=3, tp_r=2.0, max_range_atr=1.5
- data_needs: OHLC
- notes: Weekly-open levels are heavily watched in BTC/ETH ("Monday range"). Skip weeks where the opening range is already > 1.5*ATR (no compression, no edge). Small sample size (1 setup/week) — slow to validate in a 1-week paper test; flag as low-signal-count.

### STRATEGY: Opening Range Breakout (ORB-30)
- family: breakout_volatility
- markets: both (crypto day open 00:00 UTC; forex: London or NY session open)
- timeframes: 5m, 15m (range built from first 30 minutes)
- entry_long: range = high/low of first 30 min of session. Long on 5m close above range high + 0.1*ATR(14, 5m). Skip if range width > 1.0*ATR(14, 1d) (too wide = no compression) or < 0.15*ATR (noise).
- entry_short: mirror of long.
- exit_rule: SL = range midpoint (Crabel-style tight stop) or 1*ATR(5m), whichever wider; TP = 2R; time exit at session close; max 1 trade per direction per session.
- params: range_minutes=30, buffer_atr=0.1, max_range_atr_d=1.0, min_range_atr_d=0.15, tp_r=2.0
- data_needs: OHLCV intraday
- notes: Documented extensively (Crabel 1990; Zarattini & Aziz 2023 paper on 5-min ORB in US equities showed strong results with 1:1 to 10:1 R targets, driven by a few huge winners). Key published refinement: only trade when the open gaps or the first bar shows directional conviction. Highly cost-sensitive on 5m crypto — taker fees must be << 0.15*ATR or the expectancy dies.

### STRATEGY: Crabel Opening Range + Stretch
- family: breakout_volatility
- markets: both (originally futures; fits forex majors and BTC/ETH 1d-open)
- timeframes: 5m, 15m
- entry_long: Stretch = SMA over past 10 days of min(open − low, high − open) of each daily bar. At today's session open O: buy stop at O + Stretch.
- entry_short: sell stop at O − Stretch.
- exit_rule: stop = O − Stretch for longs (full reverse = stop-and-reverse in Crabel's original; recommend simple stop); TP = 1.5–2R or exit at session close, whichever first. Crabel's time stop: if not profitable within ~90 min, exit.
- params: stretch_len=10, tp_r=1.5, time_stop_minutes=90
- data_needs: OHLC (daily bars for stretch + intraday for execution)
- notes: The authentic Crabel (1990) ORB variant — the Stretch adapts the trigger distance to current volatility instead of using a fixed range window. Crabel's research showed the best results on days following narrow-range days — combine with NR4/NR7 filter (below) for the full published system. Thin-liquidity sessions (crypto weekends) degrade it.

### STRATEGY: Asian Range Breakout (Tokyo Compression)
- family: breakout_volatility
- markets: both (classic forex: EURUSD/GBPUSD/USDJPY; crypto adaptation: BTC/ETH on UTC clock)
- timeframes: 5m, 15m
- entry_long: define Asian range = high/low of 00:00–07:00 UTC. Only valid if range width <= 0.6*ATR(14, 1d) (compression requirement). Buy stop at range high + 0.1*ATR, active 07:00–12:00 UTC.
- entry_short: sell stop at range low − 0.1*ATR, same window.
- exit_rule: SL = opposite side of range; TP = range width projected from breakout (1:1 projection) or 1.5R, whichever larger; cancel unfilled orders at 12:00 UTC; one fill per direction.
- params: range_start=00:00, range_end=07:00, max_range_atr=0.6, buffer_atr=0.1, trade_window_end=12:00, tp_r=1.5
- data_needs: OHLC intraday
- notes: Among the most-published forex session systems ("London breakout of the Asian box"). The compression filter is the whole edge — wide Asian ranges mean the move already happened. False breaks toward 08:00–09:00 UTC news (UK/EU data) are the main failure mode; some published variants stand aside on red-folder news days. Spread sensitivity high on 5m.

### STRATEGY: London Open Breakout (First-Hour Range)
- family: breakout_volatility
- markets: forex (GBPUSD, EURUSD primary); crypto variant uses 07:00–08:00 UTC window
- timeframes: 5m, 15m
- entry_long: range = high/low of the first 60 min after London open (08:00–09:00 local; 07:00/08:00 UTC per DST). Long on close above that range + 0.1*ATR, only if day's price is above the daily pivot P (directional bias filter). Valid 09:00–13:00 local.
- entry_short: mirror of long (close below range, price below daily pivot).
- exit_rule: SL = opposite side of the London range or 1*ATR, whichever tighter; TP = 2R; optional second target at the Asian range projection; flat by NY lunch (17:00 UTC).
- params: range_minutes=60, bias_filter=daily_pivot, tp_r=2.0, buffer_atr=0.1
- data_needs: OHLC intraday (+ prior daily bar for pivot)
- notes: Distinct from the Asian-range strategy: this one builds the range AFTER London opens and trades its continuation, so it tolerates wide Asian sessions. The pivot bias filter is the most common published addition — it roughly halves trades and removes the worst counter-day-trend fades. DST bugs are the #1 implementation pitfall; anchor on bar timestamps, not server clock.

### STRATEGY: New York Open Breakout (Pre-NY Range)
- family: breakout_volatility
- markets: forex (all USD pairs); crypto variant: 13:30 UTC US equity open window
- timeframes: 5m, 15m
- entry_long: range = high/low of 12:00–13:30 UTC (pre-NY drift / London lunch). Long on close above range + 0.1*ATR during 13:30–16:00 UTC, only in the direction of the day's trend (day trend = close vs day open AND vs VWAP side).
- entry_short: mirror of long.
- exit_rule: SL = 0.8*ATR or range opposite side; TP = 1.5R; hard flat at 19:30 UTC (before NY close liquidity fade).
- params: range_start=12:00, range_end=13:30, buffer_atr=0.1, tp_r=1.5, trend_filter=open_and_vwap
- data_needs: OHLCV intraday
- notes: The "NY continuation" play — London sets the tone, NY confirms or reverses. Published evidence is mostly prop-desk lore rather than academic; the codable variant here is the most standard form. The reversal variant ("NY fade of an overextended London move") is a mean-reversion strategy and belongs to that family — excluded here by design. 13:30 UTC US data releases (CPI/NFP) cause spread spikes: stand aside 2 min around red news if a news feed exists, else accept the noise.

### STRATEGY: Daily Pivot R1/S1 Breakout
- family: breakout_volatility
- markets: both
- timeframes: 15m, 1h
- entry_long: prior day H/L/C → P = (H+L+C)/3, R1 = 2P − L, S1 = 2P − H, R2 = P + (H − L). Long on 15m close above R1 + 0.05*ATR, once per day.
- entry_short: 15m close below S1 − 0.05*ATR, once per day.
- exit_rule: SL = P (the pivot itself); TP1 = R2 for longs (S2 for shorts); if TP1 not reached by session end, exit on time.
- params: pivot_type=floor, buffer_atr=0.05, max_trades_per_day=1
- data_needs: OHLC (intraday + prior daily bar)
- notes: Floor-trader pivots are the most codable of the pivot family (Camarilla/Woodie/Fib variants below and in notes). R1/S1 breaks are continuation plays; fades of R1/S1 are mean-reversion — keep the two in separate strategies to avoid contract ambiguity. Crypto day boundary = 00:00 UTC; use exchange-consistent daily bars.

### STRATEGY: Camarilla H4/L4 Breakout
- family: breakout_volatility
- markets: both (published mostly for forex majors and index futures)
- timeframes: 15m, 1h
- entry_long: from prior day H/L/C: range = H − L; H3 = C + range*1.1/4; L3 = C − range*1.1/4; H4 = C + range*1.1/2; L4 = C − range*1.1/2. Long on 15m close above H4 (breakout day signal), once per day.
- entry_short: 15m close below L4.
- exit_rule: SL = H3 (back inside the Camarilla band = breakout failed); TP = H5 = C + range*1.1 (extended target) or 2R, whichever first.
- params: buffer_atr=0.0, tp_r=2.0, max_trades_per_day=1
- data_needs: OHLC
- notes: Nick Stott's Camarilla (1989). The H3/L3 fade is the more famous half but is mean-reversion; H4/L4 breakout is the trend-day half and is cataloged here. Breakout days are rare (a few per month per pair) — low signal count in a 1-week paper test; mark accordingly rather than discarding for low frequency alone.

### STRATEGY: TTM Squeeze (Bollinger-in-Keltner)
- family: breakout_volatility
- markets: both
- timeframes: 1h, 4h, 1d
- entry_long: squeeze ON = BB(20, 2.0) fully inside KC(20, 1.5) (upperBB < upperKC AND lowerBB > lowerKC) on the previous bar; squeeze FIRES on first bar where BB is back outside KC. Long on fire bar if momentum > 0. Momentum = close − 0.5*((highest high(20) + lowest low(20))/2 + SMA(close,20)).
- entry_short: mirror of long (squeeze fires, momentum < 0).
- exit_rule: SL = 1.5*ATR; TP = 2.5R; or exit when momentum flips sign two bars in a row, whichever first.
- params: bb_len=20, bb_mult=2.0, kc_len=20, kc_mult=1.5, mom_len=20, sl_atr=1.5, tp_r=2.5
- data_needs: OHLC (>= 40 bars)
- notes: John Carter's TTM Squeeze is the standard published volatility-compression-release system. Carter's original momentum is a linear-regression value of close vs the Donchian midline — the simplified momentum above is the widely used codable form (TradingView "Squeeze Momentum [LazyBear]" uses exactly this). Best on 4h/1d crypto where squeezes last days; on 15m it fires constantly — add a minimum squeeze duration (>= 5 bars) gate for lower TFs.

### STRATEGY: Bollinger Band Walk (Outside-Band Continuation)
- family: breakout_volatility
- markets: both
- timeframes: 4h, 1d
- entry_long: close > upper BB(20, 2.0) AND bandwidth rising (BW = (upper−lower)/middle; BW > BW[1]) AND BW percentile over past 120 bars between 30 and 85 (expanding but not climactic). Enter on close, or next-bar limit at the band (retest variant).
- entry_short: mirror of long.
- exit_rule: exit long on first close back below the middle band (SMA20); hard SL = 2*ATR; no fixed TP (band walk = trend ride).
- params: bb_len=20, bb_mult=2.0, bw_lookback=120, bw_pct_min=30, bw_pct_max=85, sl_atr=2.0
- data_needs: OHLC (>= 130 bars)
- notes: Bollinger's own teaching: "tags of the bands are not signals — walking the band is". This is the continuation reading, opposite of the codebase's existing mean-reversion `detect_bollinger` — keep them as separate strategies and expect them to disagree; the paper test should arbitrate. Fails violently on V-tops; the BW-percentile ceiling skips blowoff entries.

### STRATEGY: Keltner Channel Breakout
- family: breakout_volatility
- markets: both
- timeframes: 1h, 4h, 1d
- entry_long: close > EMA(close,20) + 2.0*ATR(20) with ADX(14) >= 20.
- entry_short: close < EMA(close,20) − 2.0*ATR(20) with ADX(14) >= 20.
- exit_rule: SL = EMA20 (midline) — trailing automatically as EMA moves; TP = 2.5R optional; or exit on close back across the midline.
- params: ema_len=20, atr_len=20, mult=2.0, adx_min=20, tp_r=2.5
- data_needs: OHLC (>= 60 bars)
- notes: Chester Keltner's original (1960) used 10-period MA of typical price ± 10-period ATR; the EMA20/ATR20×2 form is the Linda Raschke-popularized modern standard — noted as the chosen variant. Keltner breakouts are slower and steadier than Bollinger breaks because the bands don't expand with the triggering bar's own volatility. The ADX gate is essential in crypto chop.

### STRATEGY: ATR Volatility Expansion Breakout
- family: breakout_volatility
- markets: both
- timeframes: 1h, 4h, 1d
- entry_long: ATR(14) > 1.5 * SMA(ATR(14), 50) (volatility surge) AND close > highest high of prior 20 bars. The surge must be concurrent with the breakout bar (expansion confirms the break).
- entry_short: mirror of long (ATR surge AND close < prior 20-bar low).
- exit_rule: SL = 2*ATR(14) (use the surged ATR — wider stops are the point); TP = 3R; trail chandelier 3*ATR after +2R.
- params: atr_len=14, atr_sma=50, surge_mult=1.5, donchian_len=20, sl_atr=2.0, tp_r=3.0
- data_needs: OHLC (>= 70 bars)
- notes: The codebase already has a simple `detect_atr_breakout` (close > EMA ± k*ATR style); this catalog version is the more standard published form — range break confirmed by volatility expansion (Toby Crabel's expansion principle: "big moves start from volatility expansions off contractions"). Surge-without-breakout bars are just news candles; requiring both cuts those out. Position size must scale with 1/ATR or risk balloons exactly when slippage is worst.

### STRATEGY: NR4 Breakout (Narrowest Range of 4 Days)
- family: breakout_volatility
- markets: both
- timeframes: 1d (pattern), execution on 1d or step down to 4h
- entry_long: day T range = high − low is the narrowest of the last 4 days. On T+1: buy stop at T's high + 0.05*ATR(14, 1d).
- entry_short: sell stop at T's low − 0.05*ATR.
- exit_rule: SL = opposite extreme of day T; TP = 1.5–2R; if neither side triggers within 1 day, cancel (the contraction resolved without you).
- params: lookback=4, buffer_atr=0.05, tp_r=2.0, cancel_days=1
- data_needs: OHLC daily
- notes: Tony Crabel's core pattern (1990): range contraction precedes range expansion; NR4 is the smallest reliable sample of compression. Crabel's published research found NR4 breaks work best as trend-continuation — an optional filter (only long if close > EMA20) matches his trend-day work. ID/NR4 (inside day that is also NR4) is the stronger published variant — see NRIB below for the intraday analogue. Low signal count per symbol; run across the whole symbol universe.

### STRATEGY: NR7 Breakout (Narrowest Range of 7 Days)
- family: breakout_volatility
- markets: both
- timeframes: 1d (pattern), execution on 1d or 4h
- entry_long: day T range is the narrowest of the last 7 days. On T+1: buy stop at T's high + 0.05*ATR.
- entry_short: sell stop at T's low − 0.05*ATR.
- exit_rule: SL = opposite extreme of day T; TP = 2R; cancel after 1 untriggered day.
- params: lookback=7, buffer_atr=0.05, tp_r=2.0, cancel_days=1
- data_needs: OHLC daily
- notes: Also Crabel; popularized by Connors/Raschke ("Street Smarts", 1995) who tested NR7 breaks with an entry-day-trend bias. Rarer and stronger than NR4 — a 7-day compression is a bigger coil. On BTC 1d expect ~15–25 setups/year/symbol. Same note as NR4: keep stop at the far side of the narrow day; do not tighten to the trigger bar or normal noise stops you out of the expansion.

### STRATEGY: Inside Bar Breakout (Mother-Bar Levels)
- family: breakout_volatility
- markets: both
- timeframes: 1h, 4h, 1d
- entry_long: bar T is inside bar T−1 (T.high <= T−1.high AND T.low >= T−1.low). Buy stop at T−1's high + 0.05*ATR (mother-bar high — NOT the inside bar's high). Valid next 3 bars.
- entry_short: sell stop at T−1's low − 0.05*ATR.
- exit_rule: SL = opposite mother-bar extreme (for wide mothers, midpoint + cap SL at 1.5*ATR); TP = 2R or trail 2*ATR chandelier after +1R.
- params: buffer_atr=0.05, max_sl_atr=1.5, tp_r=2.0, validity_bars=3
- data_needs: OHLC
- notes: The codebase's existing `detect_inside_bar` triggers on the INSIDE bar's high/low — that is the weaker retail variant; standard price-action teaching (and Nial Fuller / forex literature consensus) uses the MOTHER bar's extremes because the mother defines the range being resolved. Recommend upgrading or running both as separate strategies and letting the paper test arbitrate. Best on 4h/1d; on 5m inside bars are constant noise.

### STRATEGY: NRIB — Narrow-Range Inside Bar
- family: breakout_volatility
- markets: both
- timeframes: 4h, 1d
- entry_long: inside bar (as above) AND inside bar's range <= 50% of mother bar's range AND inside bar's range is the narrowest of the last 4 bars. Buy stop at mother high + 0.05*ATR.
- entry_short: sell stop at mother low − 0.05*ATR.
- exit_rule: SL = mother opposite extreme; TP = 2.5R (compression quality justifies a bigger target); cancel after 2 bars.
- params: inside_vs_mother=0.5, narrow_lookback=4, buffer_atr=0.05, tp_r=2.5, validity_bars=2
- data_needs: OHLC
- notes: The intersection of Crabel's NR4 and the inside bar — "coil within a coil". Published mostly in price-action literature rather than academia, but every component is objective. On BTC/ETH 4h it produces ~2–6 setups/month/symbol — a clean, testable frequency for the paper cycle. Avoid mothers that are themselves huge (> 2*ATR): that's post-spike digestion, not compression.

### STRATEGY: Volatility Contraction Pattern (VCP, Minervini)
- family: breakout_volatility
- markets: both (originally equities; transfers to crypto 4h/1d base breakouts; weak on forex — no volume, shallower ranges)
- timeframes: 4h, 1d
- entry_long: over lookback L=60 bars, find 2–3 successive pullbacks T1, T2, T3 (each = decline from a local high to the next local low, swing detection window 5) with contraction: T2 <= 0.6*T1 AND T3 <= 0.6*T2 AND final T3 <= 0.5*ATR(14)*3 (tight). Volume contraction: SMA(volume,10) at T3 low < SMA(volume,10) at T1 high. Pivot = high of the last contraction. Long on close above pivot with volume >= 1.5*SMA(volume,50).
- entry_short: none (VCP is a long-side accumulation pattern; short side is not a published variant).
- exit_rule: SL = low of the final contraction (T3 low); TP = 3R or trail 10/20-bar low channel after +2R; invalidate if price undercuts T3 low before triggering.
- params: lookback=60, swing_window=5, contraction_ratio=0.6, max_contractions=3, vol_dryup=1.0, breakout_vol_mult=1.5, tp_r=3.0
- data_needs: OHLCV (>= 70 bars)
- notes: Mark Minervini's signature setup ("Trade Like a Stock Market Wizard", 2013); SEPA backtests in his books are stock-specific but the contraction geometry is market-agnostic. Minervini's qualitative elements (fundamental leadership, "tennis ball action") cannot be coded — this entry codifies only the geometric core; say so in the implementation. In choppy crypto ranges, swing detection mislabels noise as contractions — require the whole structure to sit above a rising EMA50 as a regime gate.

### STRATEGY: Gap-and-Go
- family: breakout_volatility
- markets: both (crypto: weekend/CME-driven Monday gaps and post-news UTC day gaps; forex: Sunday-open gaps)
- timeframes: 5m, 15m (execution), 1d (gap detection)
- entry_long: session open gaps up: |open − prior close| >= 0.5*ATR(14, 1d). Build the first 15-min range. Long on close above that range high IF price has held above the open (no fill of > 50% of the gap yet).
- entry_short: mirror of long (gap down, holds below open, break of first-15m low).
- exit_rule: SL = low of the first-15m range (gap-and-go failures snap back fast — tight stop is the published standard); TP = 2R; time exit at session close.
- params: gap_atr=0.5, range_minutes=15, max_gap_fill=0.5, tp_r=2.0
- data_needs: OHLCV intraday (+ daily for gap sizing)
- notes: A day-trading classic from equities (gappers with catalysts). In crypto the cleanest version is the Monday 00:00 UTC gap after a weekend move; in forex, Sunday-open gaps > 0.5*ATR on majors. Gap-and-go fails when the gap has no catalyst and immediately fills — the "holds above open" condition is the filter that separates go from fill. Slippage on the trigger bar is the dominant cost; model at least 0.1*ATR adverse fill in the paper test.

### STRATEGY: CME / Weekend Gap Fill
- family: breakout_volatility
- markets: crypto (BTC primary)
- timeframes: 1h, 4h
- entry_long: detect unfilled gap between Friday CME close and Monday CME open (or spot weekend gap: Friday 21:00 UTC spot close vs Monday 00:00 UTC). If Monday trades and price sits ABOVE the gap (gap is below), long on first touch of the gap top boundary, expecting fill down to gap bottom... (direction note: fill trades TOWARD the gap: if gap is below price, that is a SHORT toward gap bottom; long version = gap is above price, buy toward gap top). Codable rule: gap_above = prior_close > current session open by >= 0.4*ATR → long at market targeting prior_close (the fill level).
- entry_short: gap_below = current session open > prior_close by >= 0.4*ATR → short targeting prior_close.
- exit_rule: TP = exact fill level (prior close); SL = 0.5*gap_size beyond entry; time stop 48h — unfilled gaps older than 2 days have materially lower fill rates.
- params: gap_atr=0.4, sl_gap_mult=0.5, time_stop_hours=48
- data_needs: multi-symbol ideally (CME BTC futures + spot); spot-only fallback = weekend gap on UTC clock
- notes: The "CME gap" meme has real microstructure backing (CME closes while spot trades), but fill statistics are era-dependent — widely cited 70–90% historical fill rates come from 2019–2021 samples and decay as the trade gets crowded. This is technically a TARGET trade (fade toward fill) rather than a momentum breakout; included here because the trigger is a volatility gap. Needs clean weekend-boundary handling in the data feed — the #1 implementation risk.

### STRATEGY: Swing Failure Pattern (False Breakout Fade)
- family: breakout_volatility
- markets: both
- timeframes: 15m, 1h, 4h
- entry_long: price trades BELOW a prior swing low (swing window 5, lookback 20 bars) but closes back ABOVE that swing low on the same or next bar (wick through, close back = failed breakdown). Long on the close of the reclaim bar.
- entry_short: price wicks above a prior swing high and closes back below it; short on the reclaim close.
- exit_rule: SL = extreme of the failure wick + 0.1*ATR buffer; TP1 = opposite side of the local range or 2R; optional runner targeting the opposite swing point.
- params: swing_window=5, lookback=20, buffer_atr=0.1, tp_r=2.0
- data_needs: OHLC
- notes: ICT/"SFP" terminology, but the pattern is old (Wyckoff spring/upthrust is the same idea; the codebase's `detect_liquidity_sweep` is a cousin — dedupe or run as variants). Statistically this is a high-win-rate, small-target fade: it monetizes stop-hunts at obvious levels. Failure mode is a genuine breakout day (trend days blow through wick stops repeatedly) — a regime gate (skip if ADX(14) > 30) is the standard fix.

### STRATEGY: Turtle Soup (Fade the 20-Day Breakout)
- family: breakout_volatility
- markets: both
- timeframes: 1d, 4h
- entry_long: price breaks BELOW the 20-bar low (touches it intrabar) but closes back above the 20-bar low on the same bar or within the next bar, AND no touch of that 20-bar low in the prior 10 bars (fresh level requirement). Long at close of the failure bar or next bar open.
- entry_short: mirror of long at the 20-bar high.
- exit_rule: SL = 0.5*ATR beyond the breakout extreme (Turtle Soup uses tight stops — the fade either works immediately or is wrong); TP = the 20-bar midpoint (mid-channel) or EMA20; time stop 5 bars.
- params: channel_len=20, fresh_level_bars=10, sl_atr=0.5, time_stop=5
- data_needs: OHLC (>= 35 bars)
- notes: Linda Raschke & Larry Connors, "Street Smarts" (1995) — deliberately named as the anti-Turtle trade; the published rules include the "fresh level" and tight-stop clauses given here. Win rate is the edge (published ~70% in their futures tests) with modest 1–1.5R average targets. Directly anti-correlated with the Donchian entries above — keep both; the paper test reveals which regime the market is paying for this week.

### STRATEGY: Breakout-Retest Continuation
- family: breakout_volatility
- markets: both
- timeframes: 1h, 4h, 1d
- entry_long: Stage 1: close > prior 20-bar high + 0.1*ATR (the breakout). Stage 2: within the next 5 bars, price pulls back to touch the broken level (low <= level + 0.3*ATR) without closing more than 0.5*ATR below it. Stage 3: enter on the first bar that closes back ABOVE the level after the touch (rejection confirmed).
- entry_short: mirror of long.
- exit_rule: SL = retest bar's low − 0.2*ATR; TP = 2.5R; if no retest within 5 bars, setup void (do not chase).
- params: donchian_len=20, retest_window=5, touch_atr=0.3, invalidation_atr=0.5, tp_r=2.5
- data_needs: OHLC
- notes: The patient man's Donchian — published widely as "break and retest" in forex education. Worse fill than the raw breakout (you sometimes miss runaway trends, which never retest) but documented higher win rate and tighter stops. The 5-bar retest window is the standard choice; on 1d extend to 10 bars. State machine needed (ARMED → RETESTING → TRIGGERED/VOID) — flag to implementers.

### STRATEGY: Volume-Confirmed Donchian Breakout
- family: breakout_volatility
- markets: crypto (volume meaningful); forex spot: use tick volume with the caveat below
- timeframes: 1h, 4h, 1d
- entry_long: close > prior 20-bar high AND trigger bar volume >= 1.5 * SMA(volume,20) AND trigger bar closes in its top 40% ((close − low)/(high − low) >= 0.6).
- entry_short: mirror of long (close < 20-bar low, volume surge, closes in bottom 40%).
- exit_rule: SL = 1.5*ATR; TP = 2.5R; exit early if the next 2 bars after entry give back > 50% of the trigger bar's range (absorption = failed break).
- params: donchian_len=20, vol_sma=20, vol_mult=1.5, close_strength=0.6, sl_atr=1.5, tp_r=2.5
- data_needs: OHLCV
- notes: The volume filter is the single most-cited breakout-quality upgrade in the literature (Donchian himself emphasized confirmation; modern crypto papers on breakout-day volume confirm the skew). Forex caveat: spot tick volume is broker-relative — normalize per broker and never compare across feeds. The "give-back early exit" rule is the published trap-door for fake breaks; it converts many small full-stop losses into scratches.

### STRATEGY: Volatility Regime Gate Breakout (ATR Percentile Filter)
- family: breakout_volatility
- markets: both
- timeframes: 4h, 1d
- entry_long: regime gate: ATR(14)/close percentile over past 100 bars must be between 20 and 80 (not dead, not blow-off) AND rising vs 10 bars ago. Only then: close > prior 20-bar high + 0.1*ATR triggers long.
- entry_short: mirror of long.
- exit_rule: SL = 2*ATR; TP = 3R; regime exit: if ATR percentile crosses above 90 while in position, tighten stop to 1*ATR (climax management).
- params: atr_len=14, pct_lookback=100, pct_min=20, pct_max=80, rise_lookback=10, donchian_len=20, sl_atr=2.0, tp_r=3.0
- data_needs: OHLC (>= 120 bars)
- notes: This codifies the standard volatility-regime overlay (used by CTAs to size/gate breakout systems) as a standalone strategy. The 20–80 band is the common published choice: below 20 there is no energy for follow-through, above 80 you are buying climaxes. Expect it to veto ~40–60% of raw Donchian signals — that is the point. Compare against the unfiltered N-Day High entry in the paper cycle to measure the gate's added value.

### STRATEGY: Chaikin Volatility Expansion Breakout
- family: breakout_volatility
- markets: both
- timeframes: 4h, 1d
- entry_long: Chaikin Volatility CV = (EMA(H−L,10) − EMA(H−L,10)[10 bars ago]) / EMA(H−L,10)[10 bars ago] * 100. Setup: CV crosses above 0 (volatility expanding) OR CV > 15 (strong expansion). Trigger: close > prior 20-bar high within 3 bars of the CV cross.
- entry_short: mirror of long.
- exit_rule: SL = 2*ATR; TP = 2.5R; exit if CV falls back below 0 for 2 consecutive bars (expansion over).
- params: cv_ema=10, cv_lookback=10, cv_threshold=15, donchian_len=20, trigger_window=3, sl_atr=2.0, tp_r=2.5
- data_needs: OHLC (>= 40 bars)
- notes: Marc Chaikin's volatility oscillator (TASC-era, well documented). Chaikin's own interpretation: rising CV from a low base marks accumulation-phase expansion — pairs naturally with range breaks. Noisier than the ATR-surge variant on low timeframes; prefer 4h+. Largely redundant with the ATR Expansion entry above (same idea, different normalizer) — run both in the paper cycle and keep the better; do not keep two copies of one edge.

---

## Implementation notes for the coding team (family-wide)

1. **Donchian indexing**: all channel levels use prior bars only (exclude the forming/current bar). Off-by-one here changes every backtest result.
2. **Session strategies (ORB, Asian, London, NY, PDH/PDL, gaps)**: the current OHLC-dict signal contract has no explicit timestamp parameter in the catalog format above — implementers must read bar timestamps from the candle dicts (add/verify a `timestamp` field) to build session windows. Without timestamps these 8 strategies cannot be coded correctly.
3. **Overlap/dedup**: NR4 vs NR7 vs NRIB, and Donchian-20 vs Volume-Confirmed vs Regime-Gated Donchian, are intentional near-duplicates. The 1-week paper cycle should treat correlated variants as one cluster when counting "winners" to avoid double-allocating capital to a single edge.
4. **Cost sensitivity ranking** (most to least): ORB-30 / Crabel Stretch / Asian-London-NY session breaks (5m) > SFP / Turtle Soup > everything daily (Turtles, NR4/7, VCP). The 5m strategies need fee+slippage well under 0.15*ATR(5m) to survive.
5. **Confidence mapping hint**: natural confidence scalars for this family = breakout bar close-strength (close position within bar), volume multiple vs SMA20, and ATR-percentile regime score. All three are computable from OHLCV alone.

# MEAN-REVERSION STRATEGY CATALOG

Family: **Mean Reversion** — all variants that fade short-term overextension back toward a statistical or institutional mean.
Compiled: 2026-07-17. Rules below are codable as-is against the existing signal contract in `core/strategies.py`
(functions receive OHLC[V] bars, return `{"action": "BUY"/"SELL", "confidence": float, "reasons": [...]}` or `None`).

Implementation notes for the coding team:
- "bar" = the timeframe listed in `timeframes`; signals evaluate on closed bars only (use `ohlc[-2]` as last closed bar if the stream passes a forming bar).
- RSI = Wilder-smoothed (matches existing `_rsi` in `core/strategies.py`). ATR = Wilder-smoothed (matches existing `_atr`).
- All thresholds are defaults; the optimizer can sweep them.
- Confidence values below are suggested starting points scaled by extremity (e.g., deeper oversold → higher confidence).

---

### STRATEGY: RSI2_Connors_Classic
- family: mean_reversion
- markets: both
- timeframes: 1h, 4h, 1d (originally daily equities; H1 validated for forex per StockSharp port)
- entry_long: close > SMA(200) AND RSI(2) < 5 → BUY at close of signal bar
- entry_short: close < SMA(200) AND RSI(2) > 95 → SELL (mirror of long)
- exit_rule: long exits when close crosses above SMA(5); short exits when close crosses below SMA(5). Optional hard stop 2×ATR(10); optional time stop 10 bars. Connors' published research found fixed stops hurt equity-index results — keep stop loose or disabled by default, rely on time stop.
- params: rsi_len=2, oversold=5, overbought=95, trend_sma=200, exit_sma=5, atr_len=10, stop_atr_mult=2.0, time_stop=10
- data_needs: OHLC
- notes: Most-researched short-term MR system (Connors & Alvarez, "Short Term Trading Strategies That Work", 2008). Published SPY win rates 70–80%, avg gain ~0.9%/trade. Degrades in sustained bear trends even with the SMA(200) filter (2008, Mar-2020). Consider RSI cross-back variant (wait for RSI(2) to tick back above 5) to cut whipsaws ~20%. Signals cluster in corrections — cap concurrent positions.

### STRATEGY: RSI2_Connors_Aggressive10
- family: mean_reversion
- markets: both
- timeframes: 1h, 4h, 1d
- entry_long: close > SMA(200) AND RSI(2) < 10 → BUY
- entry_short: close < SMA(200) AND RSI(2) > 90 → SELL
- exit_rule: long exits when RSI(2) > 65 (or close > SMA(5), whichever first); short exits when RSI(2) < 35. Time stop 7 bars.
- params: rsi_len=2, oversold=10, overbought=90, exit_rsi_long=65, exit_rsi_short=35, trend_sma=200, exit_sma=5, time_stop=7
- data_needs: OHLC
- notes: Higher-frequency variant of RSI2_Connors_Classic from the same published family (buy <10 / exit >65 tested by Connors). ~2–3× more signals, lower per-trade expectancy. Keep both variants in the paper cycle; they will correlate — treat as one cluster for risk.

### STRATEGY: RSI2_Triple_Capitulation
- family: mean_reversion
- markets: both
- timeframes: 4h, 1d
- entry_long: close > SMA(200) AND RSI(2) has closed < 10 for 3 consecutive bars → BUY on 3rd bar close
- entry_short: close < SMA(200) AND RSI(2) has closed > 90 for 3 consecutive bars → SELL
- exit_rule: exit at first close beyond SMA(5) in profit direction; hard stop 2.5×ATR(10); time stop 10 bars.
- params: rsi_len=2, streak=3, oversold=10, overbought=90, trend_sma=200, exit_sma=5, stop_atr_mult=2.5, time_stop=10
- data_needs: OHLC
- notes: Connors "pullback strength filter" variant targeting capitulation. Published backtests show 10–15% higher win rate vs single-bar trigger at cost of ~60% fewer trades. Good on BTC/ETH daily where single RSI(2) spikes are noise but 3-bar streaks mark real liquidation cascades.

### STRATEGY: RSI2_ScaleIn_TPS
- family: mean_reversion
- markets: both
- timeframes: 4h, 1d
- entry_long: close > SMA(200); buy 25% when RSI(2) < 20, add 25% at each deeper close with RSI(2) < 15, < 10, < 5 (max 4 tranches)
- entry_short: close < SMA(200); mirror: sell 25% at RSI(2) > 80, add at > 85, > 90, > 95
- exit_rule: exit entire position when RSI(2) crosses above 55 (long) / below 45 (short), or close crosses SMA(5). No per-tranche stop; portfolio-level stop 4×ATR(10) from volume-weighted average entry.
- params: rsi_len=2, tranche_levels_long=20,15,10,5, tranche_levels_short=80,85,90,95, tranche_pct=0.25, exit_rsi_long=55, exit_rsi_short=45, trend_sma=200, exit_sma=5
- data_needs: OHLC
- notes: Connors/Alvarez TPS (Trend, Pullback, Scale-in) published variant. Averaging into weakness raises win rate further but increases tail risk — sizing must treat 4 tranches as ONE position (4× tranche size = max risk budget). Requires position-state tracking beyond single-shot signal dict; implementer should emit BUY/SELL with confidence and let position manager scale, or track internally per symbol.

### STRATEGY: RSI14_Classic_Fade
- family: mean_reversion
- markets: both
- timeframes: 15m, 1h, 4h
- entry_long: RSI(14) crosses back UP through 30 after having been below 30 → BUY
- entry_short: RSI(14) crosses back DOWN through 70 after having been above 70 → SELL
- exit_rule: TP at RSI(14) = 50 (midline) or 1.5×ATR(14) from entry, whichever first; SL 1.5×ATR(14); time stop 20 bars.
- params: rsi_len=14, oversold=30, overbought=70, midline=50, tp_atr_mult=1.5, sl_atr_mult=1.5, time_stop=20
- data_needs: OHLC
- notes: The textbook Wilder oscillator fade. Cross-BACK trigger (not touch) is the codable standard — entering while RSI is still extreme catches falling knives. Weak standalone edge in trends; gate with ADX(14) < 25 (range regime) for best results. On 15m crypto, fees ≥ 8 bps round trip eat most of the edge — prefer 1h+.

### STRATEGY: ConnorsRSI_CRSI
- family: mean_reversion
- markets: both
- timeframes: 1h, 4h, 1d
- entry_long: close > SMA(200) AND CRSI < 10 → BUY, where CRSI = (RSI(3) + RSI_of_streak(2) + PercentRank(ROC(1),100)) / 3. Streak = consecutive up(+)/down(–) closes; RSI_of_streak applies RSI(2) to the integer streak series; PercentRank = % of last 100 one-bar returns smaller than today's.
- entry_short: close < SMA(200) AND CRSI > 90 → SELL (mirror)
- exit_rule: exit when CRSI crosses 50, or close crosses SMA(5); time stop 10 bars.
- params: rsi_price=3, rsi_streak=2, prank_lookback=100, oversold=10, overbought=90, trend_sma=200, exit_sma=5, time_stop=10
- data_needs: OHLC
- notes: Connors' composite oscillator ("ConnorsRSI", 2012, published with quantified backtests). More selective than RSI(2) alone because it demands magnitude + duration + percentile extremes simultaneously. Implementer must maintain the signed streak series. Published thresholds 10/90 on daily; for 1h use 15/85 to keep signal count viable.

### STRATEGY: Bollinger_PctB_Reversal
- family: mean_reversion
- markets: both
- timeframes: 15m, 1h, 4h, 1d
- entry_long: %b < 0.0 (close below lower band BB(20,2)) on bar t, then %b crosses back above 0.0 on bar t+1 → BUY. %b = (close − lower) / (upper − lower).
- entry_short: %b > 1.0 then crosses back below 1.0 → SELL (mirror)
- exit_rule: TP at middle band (SMA 20); SL at 1.5×ATR(14) beyond the extreme bar's low/high; time stop 15 bars.
- params: bb_len=20, bb_mult=2.0, atr_len=14, sl_atr_mult=1.5, time_stop=15
- data_needs: OHLC
- notes: Standard published %b fade (Bollinger's own usage: band tag + re-entry). Requiring re-entry into the band filters "walking the band" trends. In strong trends price rides the band for 10+ bars — the cross-back trigger plus ADX(14) < 25 regime filter materially improves results.

### STRATEGY: Bollinger_BandTouch_WickFade
- family: mean_reversion
- markets: both
- timeframes: 5m, 15m, 1h
- entry_long: bar low ≤ lower BB(20,2) AND close back inside bands (close > lower band) AND close > open (bullish rejection candle) → BUY
- entry_short: bar high ≥ upper BB(20,2) AND close < upper band AND close < open → SELL (mirror)
- exit_rule: TP at middle band; SL just beyond signal bar extreme (long: signal bar low − 0.25×ATR(14)); time stop 12 bars.
- params: bb_len=20, bb_mult=2.0, atr_len=14, sl_buffer_atr=0.25, time_stop=12
- data_needs: OHLC
- notes: Candle-confirmation variant used widely in crypto scalping (wicks outside bands + rejection body). More selective than PctB_Reversal because of the candle-color condition. Very cost-sensitive on 5m — demand TP distance ≥ 3× round-trip fee or skip signal.

### STRATEGY: Bollinger_Midband_Return
- family: mean_reversion
- markets: both
- timeframes: 1h, 4h
- entry_long: price has been below lower BB(20,2) within last 5 bars AND RSI(14) < 35 AND close now > previous bar high (momentum turn) → BUY
- entry_short: price above upper band within last 5 bars AND RSI(14) > 65 AND close < previous bar low → SELL (mirror)
- exit_rule: TP = middle band (SMA 20); SL = 2×ATR(14); time stop 20 bars.
- params: bb_len=20, bb_mult=2.0, lookback=5, rsi_len=14, rsi_os=35, rsi_ob=65, tp_atr_mult=0, sl_atr_mult=2.0, time_stop=20
- data_needs: OHLC
- notes: Bollinger + RSI confluence fade, a standard published combo (multiple TradingView/MT5 reference implementations). The previous-bar-high/low trigger makes it a stop-entry system — implementable with the pending_orders module. Fewer, better signals than raw band touches.

### STRATEGY: Stoch_Extreme_CrossFade
- family: mean_reversion
- markets: both
- timeframes: 15m, 1h, 4h
- entry_long: Stochastic %K(14,3) < 20 AND %K crosses above %D(3) → BUY
- entry_short: %K > 80 AND %K crosses below %D → SELL (mirror)
- exit_rule: TP when %K reaches 50 or opposite zone (50 for longs); SL 1.5×ATR(14); time stop 20 bars.
- params: k_len=14, k_smooth=3, d_len=3, oversold=20, overbought=80, tp_midline=50, sl_atr_mult=1.5, time_stop=20
- data_needs: OHLC
- notes: Classic Lane stochastic fade; the %K/%D cross inside the extreme zone is the codable standard. Slow stochastic (3,3 smoothing) is assumed. Works best on forex majors in Asian-session ranges; unreliable during news trends — add ADX < 20 gate if false signals dominate.

### STRATEGY: StochRSI_Double_Oscillator
- family: mean_reversion
- markets: crypto
- timeframes: 15m, 1h, 4h
- entry_long: StochRSI %K(14,14,3,3) < 0.2 AND crosses above %D AND RSI(14) < 40 → BUY
- entry_short: StochRSI %K > 0.8 AND crosses below %D AND RSI(14) > 60 → SELL (mirror)
- exit_rule: TP at StochRSI %K = 0.5 or RSI(14) = 50; SL 1.5×ATR(14); time stop 16 bars.
- params: rsi_len=14, stoch_len=14, k_smooth=3, d_len=3, os=0.2, ob=0.8, rsi_filter_long=40, rsi_filter_short=60, sl_atr_mult=1.5, time_stop=16
- data_needs: OHLC
- notes: StochRSI = stochastic of RSI — extremely reactive, popular in crypto (native on TradingView/Binance). Generates frequent signals; the RSI(14) filter is what keeps it mean-reversion rather than noise. High cost sensitivity; avoid below 15m.

### STRATEGY: RSI_Divergence_Fade
- family: mean_reversion
- markets: both
- timeframes: 1h, 4h, 1d
- entry_long: bullish regular divergence: price makes lower swing low (two swing lows within 30 bars, second < first by ≥ 0.2×ATR) while RSI(14) makes higher swing low at the same bars, AND RSI(14) < 40 at second low → BUY when RSI crosses back above its 3-bar SMA
- entry_short: bearish divergence: price higher swing high while RSI(14) lower swing high, RSI > 60 → SELL on RSI cross below its 3-bar SMA (mirror)
- exit_rule: TP 2×ATR(14) or RSI reaches 60 (long) / 40 (short); SL beyond divergence swing extreme; time stop 30 bars.
- params: rsi_len=14, swing_window=5, max_divergence_bars=30, rsi_long_max=40, rsi_short_min=60, tp_atr_mult=2.0, time_stop=30
- data_needs: OHLC
- notes: Use existing `_swing_highs`/`_swing_lows` helpers in core/strategies.py for pivots. Divergence detection is the most implementation-sensitive entry in this catalog — define "lower low" with an ATR buffer, not exact price. Divergences persist (double/triple divergence) in strong trends; the RSI cross trigger delays entry until momentum actually turns.

### STRATEGY: Stoch_Divergence_Fade
- family: mean_reversion
- markets: both
- timeframes: 1h, 4h
- entry_long: price lower swing low + Stochastic %D(14,3,3) higher swing low within 30 bars, %D < 25 → BUY when %K crosses above %D
- entry_short: price higher swing high + %D lower swing high, %D > 75 → SELL when %K crosses below %D (mirror)
- exit_rule: TP 1.5×ATR(14) or %D = 50; SL beyond swing extreme; time stop 25 bars.
- params: k_len=14, k_smooth=3, d_len=3, swing_window=5, max_bars=30, d_long_max=25, d_short_min=75, tp_atr_mult=1.5, time_stop=25
- data_needs: OHLC
- notes: Stochastic divergence is noisier than RSI divergence but triggers earlier; treat as complementary, not duplicate. Same pivot machinery as RSI_Divergence_Fade — implement both from one divergence detector parameterized by oscillator series.

### STRATEGY: ZScore_Price_Reversion
- family: mean_reversion
- markets: both
- timeframes: 15m, 1h, 4h, 1d
- entry_long: z = (close − SMA(50)) / StdDev(close,50); z < −2.0 AND z crosses back up (z[t] > z[t−1]) → BUY
- entry_short: z > +2.0 AND z ticks down → SELL (mirror)
- exit_rule: TP at z = 0 (i.e., price = SMA 50); SL at z = ±3.5 equivalent price level; time stop = half-life proxy: 20 bars.
- params: ma_len=50, entry_z=2.0, exit_z=0.0, stop_z=3.5, time_stop=20
- data_needs: OHLC
- notes: Single-asset statistical reversion. Requires the 50-bar close distribution to be roughly stable — fails when a new trend starts (z keeps extending). Gate with Hurst exponent < 0.5 or ADF p < 0.1 on the detrended series if regime filtering is wanted (see Hurst_Gated_ZScore). On crypto 15m/1h, z-extremes of ±2 occur in nearly every volatility expansion — the tick-back condition is essential.

### STRATEGY: VWAP_ATR_Deviation_Fade
- family: mean_reversion
- markets: both
- timeframes: 1m, 5m, 15m
- entry_long: close < session_VWAP − 2.0×ATR(14) → BUY (first bar satisfying, then cooldown 5 bars)
- entry_short: close > session_VWAP + 2.0×ATR(14) → SELL (mirror)
- exit_rule: exit when close ≥ VWAP (long) / ≤ VWAP (short); SL 1.0×ATR(14) beyond entry; time stop: end of session or 30 bars.
- params: k_atr=2.0, atr_len=14, sl_atr_mult=1.0, cooldown_bars=5, time_stop=30
- data_needs: OHLCV
- notes: StockSharp published reference rules (VWAP Mean Reversion #0235: K=2.0, 5m, ATR 14). VWAP resets each session: use 00:00 UTC for 24/7 crypto, broker day for forex. Fails hard on trend days (price can sit 3+ ATR from VWAP all day) — avoid first hour after major news; best in mid-session ranges. Needs volume — unusable on OHLC-only feeds.

### STRATEGY: VWAP_StdBand_Reversion
- family: mean_reversion
- markets: both
- timeframes: 5m, 15m
- entry_long: close < VWAP − 2.0×σ, where σ = rolling std of (typical price − VWAP) over session-so-far, then a bullish candle closes back above the lower band → BUY
- entry_short: close > VWAP + 2.0×σ then bearish candle closes back below upper band → SELL (mirror)
- exit_rule: TP = VWAP; SL = signal candle extreme −/+ 0.25×ATR(14); time stop 20 bars or session end.
- params: band_mult=2.0, atr_len=14, sl_buffer_atr=0.25, time_stop=20
- data_needs: OHLCV
- notes: Band-confirmation variant of the VWAP fade (matches published TradingView/TrendSpider std-band implementations). For crypto use rolling-24h VWAP reset at 00:00 UTC; published BTC/ETH deviation thresholds of 0.3–0.5% on 5m correspond to ~1.5–2σ. Skip signals in first 30 min after VWAP reset (σ estimate unstable with few samples).

### STRATEGY: Keltner_Midline_Reversion
- family: mean_reversion
- markets: both
- timeframes: 15m, 1h, 4h
- entry_long: close < lower Keltner (EMA(20) − 2.0×ATR(10)) AND next bar closes back above lower Keltner → BUY; target = EMA(20) midline
- entry_short: close > upper Keltner (EMA(20) + 2.0×ATR(10)) AND next bar closes back below upper → SELL (mirror)
- exit_rule: TP at EMA(20) midline; SL 1.5×ATR(10) beyond entry; time stop 15 bars.
- params: ema_len=20, atr_len=10, mult=2.0, sl_atr_mult=1.5, time_stop=15
- data_needs: OHLC
- notes: Keltner reversion is the mirror of the Keltner breakout system — codable with zero new indicators (existing `_ema`/`_atr`). Keltner bands are ATR-based so they widen in vol expansions exactly when Bollinger bands explode; re-entries beyond Keltner are rarer and cleaner than beyond BB. Often paired with BB in squeeze logic — here we use it standalone as the fade.

### STRATEGY: IBS_Classic_Daily
- family: mean_reversion
- markets: both
- timeframes: 1d (validated daily only; can test 4h)
- entry_long: IBS = (close − low)/(high − low) < 0.2 → BUY at close of signal bar
- entry_short: IBS > 0.8 → SELL at close (mirror)
- exit_rule: long exits when IBS > 0.8 (or any close > previous bar high for tighter exit); short exits when IBS < 0.2. Time stop 10 bars. No hard stop in published version; add 3×ATR(14) disaster stop for live safety.
- params: ibs_buy=0.2, ibs_sell=0.8, time_stop=10, disaster_atr_mult=3.0
- data_needs: OHLC
- notes: Pagonidis (2013, "The IBS Effect: Mean Reversion in Equity ETFs"): IBS<0.2 → +38 bps avg next day; IBS>0.8 → −13 bps. QuantifiedStrategies SPY backtest 1993–present: 68% win, PF 1.9, CAGR 12.5%. Edge documented at DAILY horizon only — no published evidence below 4h. Zero-range bar (high==low) → treat IBS as 0.5 (skip). Execution at close matters; entering next open costs ~0.2–0.5%.

### STRATEGY: IBS_TrendFiltered
- family: mean_reversion
- markets: both
- timeframes: 1d, 4h
- entry_long: IBS < 0.2 AND close > SMA(50) AND volume > 1.2×SMA(volume,20) → BUY (high-volume panic close near bar low)
- entry_short: IBS > 0.8 AND close < SMA(50) → SELL (mirror; volume filter optional)
- exit_rule: exit when IBS crosses 0.5 (midline) or close crosses SMA(10); time stop 8 bars; SL 2×ATR(14).
- params: ibs_buy=0.2, ibs_sell=0.8, trend_sma=50, exit_sma=10, vol_mult=1.2, vol_len=20, sl_atr_mult=2.0, time_stop=8
- data_needs: OHLCV
- notes: Published enhancement path for IBS (trend filter + volume filter per QuantifiedStrategies variants). Volume condition targets capitulation closes; in forex spot volume is tick-volume — acceptable proxy. On crypto daily the long side of IBS historically stronger than short (persistent long bias) — consider long-only in the paper cycle.

### STRATEGY: Overnight_Session_Gap_Fade
- family: mean_reversion
- markets: forex
- timeframes: 1h (signal evaluated at session open)
- entry_long: at first bar of new session (e.g., London 07:00 UTC), gap = (session_open − prev_session_close)/prev_session_close; gap ≤ −0.3% → BUY the gap fill
- entry_short: gap ≥ +0.3% → SELL the fill (mirror)
- exit_rule: TP = previous session close (gap fill); SL = 1.0× gap size beyond open (or 1.5×ATR(14) if gap tiny); time stop = 8 hours (fade abandoned if not filled intraday).
- params: gap_threshold=0.003, sl_gap_mult=1.0, sl_atr_mult=1.5, time_stop_hours=8, session_open_utc=7
- data_needs: OHLCV (needs reliable session boundaries)
- notes: Session-gap reversion is a documented forex effect (Tokyo→London handoff overextension). In 24/7 crypto there are no true session gaps — do NOT run on crypto; use Funding/Weekend variants instead. Skip on high-impact news mornings (gap is information, not noise). Fill rate historically ~60–70% for 0.3–0.5% gaps on majors.

### STRATEGY: Forex_Weekend_Gap_Fill
- family: mean_reversion
- markets: forex
- timeframes: 1h (evaluated at Monday open)
- entry_long: Monday open < Friday close by ≥ 0.4% → BUY, target Friday close
- entry_short: Monday open > Friday close by ≥ 0.4% → SELL, target Friday close (mirror)
- exit_rule: TP = Friday close; SL = gap size × 1.0 beyond open; time stop = 24h (most fills complete within a day or never).
- params: gap_threshold=0.004, sl_gap_mult=1.0, time_stop_hours=24
- data_needs: OHLC
- notes: Classic "weekend gaps fill" folklore, partially supported in majors (EUR/USD, USD/JPY); gap-and-go happens when weekend news is real (elections, CB surprises) — filter out gaps > 1.5% (likely news-driven continuation instead). One signal per week max per pair — tiny sample in a 1-week paper test; include for completeness but expect 0–2 trades.

### STRATEGY: Pairs_Cointegration_ZScore
- family: mean_reversion
- markets: both
- timeframes: 1h, 4h, 1d
- entry_long: for pre-qualified cointegrated pair (Y,X): spread S = log(Y) − β·log(X) (β from OLS over 90-bar formation window); z = (S − SMA(S,20))/Std(S,20); z < −2.0 → BUY spread: long Y, short X in dollar-neutral legs sized by β
- entry_short: z > +2.0 → SELL spread: short Y, long X (mirror)
- exit_rule: TP at z = 0 (full reversion); SL at |z| > 3.5 (cointegration breakdown); time stop = 2× estimated half-life, max 60 bars.
- params: formation_window=90, z_window=20, entry_z=2.0, exit_z=0.0, stop_z=3.5, max_halflife_bars=60, coint_pvalue=0.05
- data_needs: OHLC multi-symbol
- notes: Canonical stat-arb rules (Engle-Granger p<0.05 gate; ±2σ entry, 0 exit, ±3.5 stop — matches published production implementations). Re-test cointegration weekly; drop pair if p > 0.05 or half-life > 60 bars. Crypto candidates: BTC/ETH, ETH/SOL, LTC/BTC, BNB/ETH, perp vs spot same-asset (trivially cointegrated — see Funding/Basis variants). Forex candidates: EURUSD/GBPUSD, AUDUSD/NZDUSD, EURCHF/EURUSD. Needs multi-symbol data plumbing — flag to infra if data_provider can't fan out two symbols to one strategy.

### STRATEGY: OU_HalfLife_Spread_Reversion
- family: mean_reversion
- markets: both
- timeframes: 1h, 4h, 1d
- entry_long: fit OU/AR(1) on spread S over last 120 bars: regress ΔS on S_lag → coefficient α (must be < 0); half-life HL = ln(2)/|ln(1+α)|; trade ONLY if 5 ≤ HL ≤ 40 bars. Entry: z(S,window=2×HL) < −2.0 → BUY spread; scale-in second tranche at z < −3.0
- entry_short: z > +2.0 → SELL spread; second tranche at z > +3.0 (mirror)
- exit_rule: TP z = 0; SL z = ±4.0; hard time stop = 3×HL (if reversion hasn't happened in 3 half-lives, model is wrong).
- params: ou_window=120, hl_min=5, hl_max=40, entry_z=2.0, add_z=3.0, exit_z=0.0, stop_z=4.0, time_stop_hl_mult=3
- data_needs: OHLC multi-symbol (pairs) — or OHLC single-symbol when S = price − MA(price,N) as a self-spread
- notes: Chan (2013) / Avellaneda-Lee (2010) framework: HL gates tradeability (HL < 5 = noise trading costs dominate; HL > 40 = capital locked too long for a weekly cycle). HL also sets the z-score window and time stop — this is the published "half-life based entries" mechanism. OLS on log prices. Recalculate HL every bar; stop trading the pair when HL drifts outside [5,40].

### STRATEGY: RubberBand_SMA_Stretch
- family: mean_reversion
- markets: both
- timeframes: 1h, 4h, 1d
- entry_long: stretch = (close − SMA(20))/ATR(14); stretch < −2.5 AND close > open (first bullish bar after stretch) → BUY
- entry_short: stretch > +2.5 AND close < open → SELL (mirror)
- exit_rule: TP at SMA(20) (the "band" snapping back); SL 1.5×ATR(14) beyond signal-bar extreme; time stop 15 bars.
- params: sma_len=20, atr_len=14, stretch_atr=2.5, sl_atr_mult=1.5, time_stop=15
- data_needs: OHLC
- notes: "Rubber band" systems are a named folklore family (price stretched N ATRs from its mean snaps back); no single canonical source, so this codifies the most common published variant (20-SMA, ATR-normalized stretch, candle-direction trigger). ATR normalization makes one parameter set portable across BTC and EURUSD. Same shape as ZScore_Price_Reversion but volatility-normalized rather than std-normalized — expect correlated signals; dedupe in portfolio layer.

### STRATEGY: RubberBand_Pct_From_MA
- family: mean_reversion
- markets: both
- timeframes: 4h, 1d
- entry_long: close ≥ 5% below SMA(50) AND RSI(2) < 10 → BUY (deep-percent + panic trigger)
- entry_short: close ≥ 5% above SMA(50) AND RSI(2) > 90 → SELL (mirror)
- exit_rule: TP at SMA(50); SL = 1.25× the entry stretch distance (e.g., entered 5% below → stop at ~6.25% below); time stop 20 bars.
- params: ma_len=50, stretch_pct=0.05, rsi_len=2, rsi_os=10, rsi_ob=90, sl_stretch_mult=1.25, time_stop=20
- data_needs: OHLC
- notes: Connors published a "deep pullback" refinement of RSI(2): price ≥ 9% below SMA(10) as an extreme MR candidate. This variant uses the more practical 5%/SMA(50) for crypto/forex vol. Percentage (not ATR) stretch suits daily bars where ATR is already embedded in the 5% figure. Rare signals (a few per year per symbol on daily) — run on 4h in the paper cycle to get samples.

### STRATEGY: Hurst_Gated_ZScore
- family: mean_reversion
- markets: both
- timeframes: 1h, 4h
- entry_long: compute Hurst exponent H over last 100 closes (rescaled-range or lagged-variance estimate); trade only if H < 0.45 (mean-reverting regime). Then z = (close−SMA(40))/Std(40); z < −2.0 → BUY
- entry_short: H < 0.45 AND z > +2.0 → SELL (mirror)
- exit_rule: TP z = 0; SL z = ±3.0; time stop 25 bars; cancel/gate all signals while H ≥ 0.5.
- params: hurst_window=100, hurst_max=0.45, ma_len=40, entry_z=2.0, exit_z=0.0, stop_z=3.0, time_stop=25
- data_needs: OHLC
- notes: Regime-gated version of ZScore_Price_Reversion. Hurst estimation on 100 bars is noisy (±0.1); use a smoothed H (EMA of estimates over 10 bars) to avoid gate flicker. Published evidence (Chan, "Algorithmic Trading", 2013) supports ADF/Hurst gating for MR viability. Alternatively gate with ADF p-value < 0.1 on the last 100 closes — same purpose, heavier compute.

### STRATEGY: Funding_Rate_Extreme_Fade
- family: mean_reversion
- markets: crypto (perpetuals only)
- timeframes: 4h, 8h (aligned to funding epochs)
- entry_long: funding rate ≤ −0.05% (8h) AND price RSI(14) < 35 → BUY spot/perp (crowd is paying to be short → squeeze reversion)
- entry_short: funding ≥ +0.10% (8h) AND RSI(14) > 65 → SELL (mirror; positive funding means longs overcrowded)
- exit_rule: TP after funding normalizes (|funding| < 0.01%) or 2×ATR(14); SL 2×ATR(14); time stop = 3 funding epochs (24h).
- params: funding_short_thresh=0.001, funding_long_thresh=-0.0005, rsi_len=14, rsi_ob=65, rsi_os=35, tp_atr_mult=2.0, sl_atr_mult=2.0, time_stop_epochs=3
- data_needs: OHLCV + funding
- notes: Crypto-native MR signal: extreme funding marks crowded positioning that mean-reverts violently. Thresholds above are standard published values for majors (0.01%/8h is neutral baseline on Binance); alt perps need 2–3× wider. Not usable on spot-only broker — paper test only if funding feed exists in data_provider; otherwise park this one. Beware: negative funding in a crashing market can persist for days (distributive downtrends) — the RSI condition is the minimum sanity filter.

### STRATEGY: Perp_Basis_Reversion
- family: mean_reversion
- markets: crypto
- timeframes: 15m, 1h
- entry_long: basis = (perp_price − spot_price)/spot_price ≤ −0.15% → BUY perp / (or spot if only one leg available) expecting basis convergence
- entry_short: basis ≥ +0.15% → SELL perp (mirror)
- exit_rule: TP when |basis| < 0.03%; SL when |basis| > 2× entry basis; time stop 24 bars.
- params: basis_entry=0.0015, basis_exit=0.0003, basis_stop_mult=2.0, time_stop=24
- data_needs: OHLCV multi-symbol (perp + spot) + optionally funding
- notes: Perp-spot basis is mechanically cointegrated (funding forces convergence) — the cleanest MR relationship in crypto. This is the retail approximation of cash-and-carry arb. If the paper broker is spot-only, run it single-leg on spot (buy when basis very negative). Funding payments on the perp leg are a real P&L factor the paper test should model.

### STRATEGY: Overnight_Crypto_Session_Reversion
- family: mean_reversion
- markets: crypto
- timeframes: 1h
- entry_long: at 00:00 UTC, compute prior-24h return R; R ≤ −4% AND IBS(last 1h bar) < 0.3 → BUY the UTC-day change reversion
- entry_short: R ≥ +4% AND IBS > 0.7 → SELL (mirror)
- exit_rule: TP = 50% retracement of the 24h move or 1.5×ATR(14); SL 2×ATR(14); time stop 12h.
- params: day_ret_thresh=0.04, ibs_long=0.3, ibs_short=0.7, retrace_tp=0.5, tp_atr_mult=1.5, sl_atr_mult=2.0, time_stop_hours=12
- data_needs: OHLCV
- notes: Crypto adaptation of daily-reversal/overnight reversion research (IBS effect documented at daily horizon; crypto "sessions" = UTC days). Weekend/low-liquidity 24h windows degrade the signal — optional filter: skip if 24h volume < 0.7× 7-day median. Distinct from IBS_Classic_Daily by the day-return precondition (fades big days only, not every weak close).

---

## Cross-cutting implementation notes (for the build team)

1. **Regime dependency**: every strategy above loses money in strong trends. The two cheapest global gates are ADX(14) < 25 and "close on the correct side of SMA(200)" (already parameterized per strategy). Consider a portfolio-level regime switch rather than 27 individual ones.
2. **Correlation clusters** (treat as one risk unit each): {RSI2 family ×4}, {Bollinger ×3, Keltner, RubberBand ×2, ZScore, Hurst-gated}, {IBS ×2, Overnight_Crypto}, {VWAP ×2}, {Divergence ×2}, {stat-arb ×2, Perp_Basis}.
3. **Cost sensitivity ranking** (most sensitive first): VWAP 1m/5m, StochRSI 15m, BandTouch_WickFade 5m → require edge-per-trade ≥ 3× round-trip cost; least sensitive: daily IBS, Connors daily, pairs daily.
4. **Multi-symbol needs**: Pairs_Cointegration_ZScore, OU_HalfLife_Spread_Reversion, Perp_Basis_Reversion require two synchronized series; Funding_Rate_Extreme_Fade requires a funding feed. Flag to `data_provider` before the paper cycle.
5. **State requirements**: RSI2_ScaleIn_TPS and OU scale-ins need per-position tranche state; everything else is single-shot signal → position manager compatible.
6. **Suggested confidence scaling**: confidence = clip(0.5 + 0.1×(extremity beyond threshold in σ or threshold-multiples), 0.5, 0.85) — keeps the contract consistent with existing strategies (0.6–0.65 typical).

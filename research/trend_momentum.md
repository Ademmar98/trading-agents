# Trend & Momentum Strategy Catalog

Research worker: Trend & Momentum family.
Target implementation contract: each strategy receives a list of OHLC(V) candle dicts (`{"open","high","low","close","volume"}`) and returns `None` or `{"action": "BUY"/"SELL", "confidence": 0..1, "reasons": [str]}`. Exits listed below are advisory for the positions/risk layer; catalog uses ATR-based exits as the common default since that is what the codebase already supports.

Conventions used below:
- "cross above" = value two bars ago <= comparator two bars ago AND value last closed bar > comparator last closed bar (confirmed, no lookahead). Same convention as existing `detect_sma_crossover`.
- All indicator values are computed on the last CLOSED bar unless stated.
- ATR = Average True Range, Wilder smoothing, period 14 unless stated.
- R = initial risk unit (entry − stop distance).

---

### STRATEGY: SMA Golden/Death Cross (50/200)
- family: trend_momentum
- markets: both
- timeframes: 4h, 1d (crypto), 1d (forex majors)
- entry_long: SMA(close,50) crosses above SMA(close,200). Require ADX(14) >= 20 to suppress chop.
- entry_short: SMA(close,50) crosses below SMA(close,200), ADX(14) >= 20.
- exit_rule: opposite cross, or trailing stop = highest close since entry − 3*ATR(14). No fixed TP.
- params: fast=50, slow=200, adx_min=20, trail_atr_mult=3.0
- data_needs: OHLC (needs >= 210 bars warmup)
- notes: Classic, heavily documented. Very late entries/exits; whipsaws badly in ranges — the ADX gate is the standard published fix. On crypto 1d the 50/200 cross is rare (a few signals/year); on 4h it trades ~6-12x/year per major pair. Low cost sensitivity at these frequencies.

### STRATEGY: EMA 9/21 Crossover
- family: trend_momentum
- markets: both
- timeframes: 15m, 1h, 4h
- entry_long: EMA(close,9) crosses above EMA(close,21) AND close > SMA(close,200) (trend filter).
- entry_short: EMA(close,9) crosses below EMA(close,21) AND close < SMA(close,200).
- exit_rule: SL = entry − 1.5*ATR(14); TP = 2R; or exit on opposite 9/21 cross, whichever first.
- params: fast=9, slow=21, trend_sma=200, sl_atr=1.5, tp_r=2.0
- data_needs: OHLC (>= 205 bars)
- notes: Most widely published retail MA system (e.g. "9/21" in forex education). The 200-SMA side filter roughly halves trade count and is the main documented edge preserver. Sensitive to spread/fees on 15m crypto — skip pairs where 1.5*ATR < 10x taker fee.

### STRATEGY: Triple EMA Ribbon (8/13/21)
- family: trend_momentum
- markets: both
- timeframes: 1h, 4h
- entry_long: EMA8 > EMA13 > EMA21 (full bullish alignment) AND price pulls back to touch EMA13 or EMA21 without closing below EMA21, then closes back above EMA8.
- entry_short: mirror of long (EMA8 < EMA13 < EMA21, pullback to EMA13/21, close back below EMA8).
- exit_rule: SL = swing low of pullback (or 1.5*ATR); TP = 2R; trail remainder below EMA21 once +1R.
- params: e1=8, e2=13, e3=21, sl_atr=1.5, tp_r=2.0
- data_needs: OHLC
- notes: Ribbon/trend-continuation variant; avoids the lag of cross systems by buying the pullback instead. Fails in flat ribbons — require ribbon spread (EMA8−EMA21)/price > 0.15*ATR/price as a minimum-width gate.

### STRATEGY: Hull MA Crossover
- family: trend_momentum
- markets: both
- timeframes: 15m, 1h, 4h
- entry_long: HullMA(close,16) crosses above HullMA(close,55). HullMA(n) = WMA(2*WMA(close,n/2) − WMA(close,n), round(sqrt(n))).
- entry_short: HullMA(16) crosses below HullMA(55).
- exit_rule: opposite cross, or SL = 2*ATR with TP = 2.5R.
- params: fast=16, slow=55, sl_atr=2.0, tp_r=2.5
- data_needs: OHLC
- notes: Hull (2005) designed this to cut MA lag ~in half vs EMA with smoother output than TEMA. Standard published variant uses slope-change of a single HMA(55); the two-line cross given here is the more codable/common trading variant — noted as such. Whipsaws less than same-span EMA cross but still needs a regime filter in crypto chop.

### STRATEGY: TEMA / DEMA Crossover
- family: trend_momentum
- markets: both
- timeframes: 1h, 4h
- entry_long: TEMA(close,12) crosses above TEMA(close,30). TEMA(n) = 3*E1 − 3*E2 + E3 where E1=EMA(close,n), E2=EMA(E1,n), E3=EMA(E2,n).
- entry_short: TEMA(12) crosses below TEMA(30).
- exit_rule: opposite cross; hard SL = 2*ATR(14); no TP (let trend run, trail with 3*ATR chandelier after +2R).
- params: fast=12, slow=30, sl_atr=2.0, trail_atr=3.0
- data_needs: OHLC (>= 3*slow bars warmup for stable EMA seed)
- notes: Mulloy (1994, TASC). Lower lag than EMA cross but overshoots on spikes — the triple-EMA term amplifies wicks; on crypto prefer 1h+ timeframes. DEMA variant (2*E1−E2) is a drop-in alternative with slightly less overshoot.

### STRATEGY: VWMA Crossover (volume-weighted)
- family: trend_momentum
- markets: crypto (forex spot lacks true volume — use only with tick volume)
- timeframes: 15m, 1h, 4h
- entry_long: VWMA(close,20) crosses above VWMA(close,50), where VWMA(n) = Σ(close*volume,n)/Σ(volume,n).
- entry_short: VWMA(20) crosses below VWMA(50).
- exit_rule: SL = 1.5*ATR; TP = 2R; or opposite cross.
- params: fast=20, slow=50, sl_atr=1.5, tp_r=2.0
- data_needs: OHLCV
- notes: Volume weighting makes the fast line lead on volume-backed moves and lag on low-volume drifts — documented to reduce false crosses vs plain SMA in equities backtests; crypto evidence is anecdotal but logic transfers. Useless on zero-volume/illiquid books; filter pairs with median bar volume < threshold.

### STRATEGY: MACD Signal-Line Cross (12/26/9)
- family: trend_momentum
- markets: both
- timeframes: 1h, 4h, 1d
- entry_long: MACD line (EMA12−EMA26) crosses above signal line (EMA9 of MACD) while MACD line < 0 (reversal-from-below variant) OR while close > SMA200 (trend variant — pick one, default trend variant).
- entry_short: MACD crosses below signal while close < SMA200.
- exit_rule: opposite signal cross; SL = 1.5*ATR; TP = 2R.
- params: fast=12, slow=26, signal=9, trend_sma=200, sl_atr=1.5, tp_r=2.0
- data_needs: OHLC (>= 60 bars)
- notes: Already implemented in repo as `detect_macd` — extend it with the SMA200 filter, which is the standard published improvement. MACD is just an EMA-cross oscillator, so signals correlate heavily with EMA 9/21-type systems; avoid running both at full size (correlation module should catch this).

### STRATEGY: MACD Zero-Line Cross
- family: trend_momentum
- markets: both
- timeframes: 4h, 1d
- entry_long: MACD line (EMA12−EMA26) crosses above 0.
- entry_short: MACD line crosses below 0.
- exit_rule: opposite zero cross; SL = 2*ATR; trail 3*ATR after +2R.
- params: fast=12, slow=26, sl_atr=2.0, trail_atr=3.0
- data_needs: OHLC
- notes: Mathematically equivalent to EMA12/EMA26 price crossover — slower, fewer signals, better trend capture, worse in ranges than signal-line cross. Include as the low-frequency portfolio diversifier, not as a standalone edge.

### STRATEGY: MACD Histogram Reversal
- family: trend_momentum
- markets: both
- timeframes: 1h, 4h
- entry_long: histogram (MACD−signal) makes 3 consecutive rising bars while histogram < 0 AND close > EMA50 (buy weakening downside momentum in an uptrend).
- entry_short: 3 consecutive falling histogram bars while histogram > 0 AND close < EMA50.
- exit_rule: SL = 1.5*ATR; TP = 1.5R (mean-reversion-to-trend target); time-stop after 10 bars.
- params: fast=12, slow=26, signal=9, trend_ema=50, seq=3, sl_atr=1.5, tp_r=1.5, max_bars=10
- data_needs: OHLC
- notes: Pullback-continuation entry, not a cross system — earlier entry than MACD cross with worse win rate but better R-multiple. The EMA50 trend gate is essential; without it this is a coin flip in trends.

### STRATEGY: ADX/DMI Directional System (Wilder)
- family: trend_momentum
- markets: both
- timeframes: 1h, 4h, 1d
- entry_long: +DI(14) crosses above −DI(14) AND ADX(14) > 25 AND ADX rising vs prior bar.
- entry_short: −DI(14) crosses above +DI(14) AND ADX(14) > 25 AND ADX rising.
- exit_rule: opposite DI cross, or ADX falls below 20 (trend dead); SL = 2*ATR; trail 2.5*ATR after +1.5R.
- params: period=14, adx_min=25, adx_off=20, sl_atr=2.0, trail_atr=2.5
- data_needs: OHLC (>= 2*period + 10 bars for Wilder smoothing stability)
- notes: Wilder's original 1978 system; repo already has `_adx_single`/`detect_adx` helpers to reuse. DI crosses lag hard — the ADX>25-and-rising gate is the published fix. One of the best-documented trend filters in existence; even if the standalone system is marginal, its regime output feeds other strategies.

### STRATEGY: ADX Pullback Continuation
- family: trend_momentum
- markets: both
- timeframes: 4h, 1d
- entry_long: ADX(14) > 30 AND +DI > −DI AND price pulls back to EMA20 (low <= EMA20 <= high) AND current bar closes above prior bar's high (resumption trigger).
- entry_short: mirror: ADX > 30, −DI > +DI, pullback to EMA20, close below prior bar's low.
- exit_rule: SL = swing low of pullback bar cluster (or 1.5*ATR); TP = 2R; trail below EMA20 after +1R.
- params: adx_min=30, ema=20, sl_atr=1.5, tp_r=2.0
- data_needs: OHLC
- notes: "Buy strength, not crosses" — enters established trends on the first pullback. Published variants (e.g. Connors, Tharp) use 2-5 bar pullbacks; the single-bar EMA20 touch + resumption close is the most mechanically codable version. Best performer of the ADX family in trending regimes per most public backtests.

### STRATEGY: Donchian Breakout — Turtle S1 (20/10)
- family: trend_momentum
- markets: both
- timeframes: 4h (crypto), 1d (forex & crypto)
- entry_long: close > highest high of prior 20 bars (excluding current bar). Skip signal if previous S1 breakout (same direction) was profitable — Turtle "last-breakout filter": only re-enter after a losing breakout or a 55-bar breakout.
- entry_short: close < lowest low of prior 20 bars, same filter.
- exit_rule: close < lowest low of prior 10 bars (long) / > highest high of prior 10 bars (short). SL = 2*N where N = ATR(20); pyramid up to 4 units at +0.5*N intervals (pyramid optional for paper test).
- params: entry=20, exit=10, n_period=20, sl_n=2.0, last_breakout_filter=true
- data_needs: OHLC; stateful (needs to remember last breakout result per symbol)
- notes: Faith & Co original rules, fully published. The statefulness (last-breakout filter, unit sizing by N) is part of the documented edge — implement at least the breakout filter. 1d crypto: ~3-8 trades/pair/year, win rate ~35-40%, payoff > 2:1. Repo has `detect_donchian` as a stateless starting point.

### STRATEGY: Donchian Breakout — Turtle S2 (55/20)
- family: trend_momentum
- markets: both
- timeframes: 1d
- entry_long: close > highest high of prior 55 bars. No last-breakout filter (S2 takes every signal).
- entry_short: close < lowest low of prior 55 bars.
- exit_rule: close < lowest low of prior 20 bars (long) / > highest high of prior 20 (short); SL = 2*N, N = ATR(20).
- params: entry=55, exit=20, n_period=20, sl_n=2.0
- data_needs: OHLC (>= 60 bars)
- notes: The slower failsafe Turtle system — catches every major trend, horrific drawdowns in ranges. On crypto 1d, 55-bar breakouts roughly correspond to multi-week highs; historically the big BTC/ETH runners came through S2. Pair with S1 only if correlation module treats them as one position.

### STRATEGY: Ichimoku Tenkan/Kijun Cross
- family: trend_momentum
- markets: both
- timeframes: 4h, 1d
- entry_long: tenkan-sen (midpoint of 9-bar high/low) crosses above kijun-sen (midpoint of 26-bar high/low) AND cross occurs above the kumo (both span A and span B below price) — "strong bullish signal" in standard Ichimoku terminology.
- entry_short: tenkan crosses below kijun AND cross below the kumo.
- exit_rule: opposite TK cross, or close crosses kijun against position; SL = kijun value at entry (structural stop).
- params: tenkan=9, kijun=26, senkou_b=52
- data_needs: OHLC (>= 52 + 26 bars to have a fully formed cloud)
- notes: Crosses inside the kumo are "neutral" and below/above are "weak" — the kumo-location filter is the core published rule, do not skip it. Repo has `_ichimoku` helper already. On 1d crypto this trades rarely but with historically decent hit rate.

### STRATEGY: Ichimoku Kumo Breakout
- family: trend_momentum
- markets: both
- timeframes: 4h, 1d
- entry_long: close crosses above the top of the kumo (max(senkouA, senkouB)) AND future kumo (26 bars ahead) is bullish (spanA > spanB).
- entry_short: close crosses below the bottom of the kumo AND future kumo bearish (spanA < spanB).
- exit_rule: close back inside the kumo; SL = opposite kumo edge (wide — size down); TP none, trail with kumo edge.
- params: tenkan=9, kijun=26, senkou_b=52, displacement=26
- data_needs: OHLC (>= 104 bars for full cloud history)
- notes: The kumo breakout is the highest-conviction Ichimoku signal per most published treatments. Stop placement is the problem — cloud-edge stops can be 3-5 ATR wide on 1d; either accept wide stops with reduced size or substitute 2*ATR (documented pragmatic variant).

### STRATEGY: Ichimoku Full Confluence (TK + Kumo + Chikou)
- family: trend_momentum
- markets: both
- timeframes: 1d (primary), 4h
- entry_long: ALL of: close > kumo top; tenkan > kijun; chikou span (current close plotted 26 bars back) > close of 26 bars ago; future kumo bullish. Enter on the bar all four first hold simultaneously.
- entry_short: mirror (all four bearish conditions).
- exit_rule: any of: close < kijun; chikou crosses below its price line; close enters kumo. SL = 2*ATR hard backstop.
- params: tenkan=9, kijun=26, senkou_b=52, displacement=26, sl_atr=2.0
- data_needs: OHLC
- notes: The complete Hosoda system as published. Very few signals (often < 5/year/pair on 1d) but the strictest published trend confirmation available. Confidence 0.8+ warranted when it fires. Implementation must plot chikou correctly (compare today's close to close[−26], no lookahead).

### STRATEGY: Supertrend
- family: trend_momentum
- markets: both
- timeframes: 15m, 1h, 4h, 1d
- entry_long: Supertrend(10, 3.0) flips from downtrend to uptrend (close crosses above the upper band; band = HL2 ∓ multiplier*ATR(10), ratcheted: band only moves up in uptrend / down in downtrend).
- entry_short: Supertrend flips to downtrend.
- exit_rule: opposite flip (the Supertrend line IS the trailing stop); no separate TP. Optional TP at 3R for paper-test comparability.
- params: period=10, multiplier=3.0, tp_r=3.0 (optional)
- data_needs: OHLC
- notes: Extremely popular in crypto retail (TradingView default 10/3). Self-contained stop-and-reverse system — trivially codable. Whipsaws in chop: multiplier 3.0/period 10 on 4h is the documented sweet spot; on 15m crypto consider (14, 3.5) to cut trade count. Must implement the ratchet correctly (band never retreats) or results are garbage.

### STRATEGY: Parabolic SAR
- family: trend_momentum
- markets: both
- timeframes: 1h, 4h, 1d
- entry_long: SAR flips below price (dot moves from above to below). Require ADX(14) > 20 filter (standard published improvement).
- entry_short: SAR flips above price, ADX > 20.
- exit_rule: stop-and-reverse at SAR value (acceleration factor starts 0.02, +0.02 per new extreme, max 0.20). No TP.
- params: af_start=0.02, af_step=0.02, af_max=0.20, adx_min=20
- data_needs: OHLC
- notes: Wilder 1978, stop-and-reverse by design — always in the market. That property is toxic in ranges (documented win rate ~30% standalone); the ADX gate converts it into a trend-only system. Good as a trailing-exit module even if weak as an entry.

### STRATEGY: Keltner Channel Breakout (20 EMA / 2 ATR)
- family: trend_momentum
- markets: both
- timeframes: 15m, 1h, 4h
- entry_long: close crosses above EMA(close,20) + 2.0*ATR(10).
- entry_short: close crosses below EMA(close,20) − 2.0*ATR(10).
- exit_rule: close back below EMA20 (long) / above EMA20 (short); SL = 1.5*ATR(10); TP = 2R.
- params: ema=20, atr=10, mult=2.0, sl_atr=1.5, tp_r=2.0
- data_needs: OHLC
- notes: Chester Keltner original (10-bar SMA of typical price ± SMA of range); the modern codified standard is EMA20 ± 2*ATR(10) per Linda Raschke / "TTM Squeeze" literature. Repo has `detect_keltner` to extend. ATR-scaled bands adapt to volatility regimes — works better than Bollinger breakouts in trends, worse in squeezes (that's what Mass Index / squeeze strategies are for).

### STRATEGY: ROC Momentum (Rate of Change)
- family: trend_momentum
- markets: both
- timeframes: 1h, 4h, 1d
- entry_long: ROC(close,12) = (close/close[−12] − 1)*100 crosses above 0 AND close > SMA50. (1d variant: ROC(25) > +5% threshold instead of 0-cross.)
- entry_short: ROC(12) crosses below 0 AND close < SMA50.
- exit_rule: ROC crosses back through 0, or SL = 1.5*ATR, TP = 2R.
- params: period=12, trend_sma=50, threshold_pct=5.0 (1d variant), sl_atr=1.5, tp_r=2.0
- data_needs: OHLC
- notes: Pure momentum — empirically the best-documented anomaly in this entire catalog (cross-sectional and time-series momentum both have decades of academic support, e.g. Jegadeesh/Titman 1993, Moskowitz/Ooi/Pedersen 2012 covering futures incl. FX). Simple and robust; the SMA50 gate keeps it trend-aligned. Zero-cross churns — consider a ±threshold band on noisy pairs.

### STRATEGY: Linear Regression Slope Trend
- family: trend_momentum
- markets: both
- timeframes: 4h, 1d
- entry_long: slope of OLS regression of close on time over 20 bars, normalized as (slope*20/close), crosses above +0.5*ATR(14)/close equivalent — i.e. annualized-free units: slope_pct = slope/close > 0 AND r-squared of the regression > 0.5 (trend quality gate).
- entry_short: slope_pct < 0 AND r² > 0.5.
- exit_rule: slope_pct flips sign, or r² collapses < 0.2; SL = 2*ATR; TP = 2.5R.
- params: length=20, r2_min=0.5, r2_off=0.2, sl_atr=2.0, tp_r=2.5
- data_needs: OHLC
- notes: The r² quality gate is what separates this from a noisy slope flip — a steep low-r² slope is a spike, not a trend. Statistically the cleanest trend definition available; moderate publication record (common in quant CTA descriptions). Watch warmup: need full `length` bars, recompute each bar.

### STRATEGY: Dual Thrust Opening Range Breakout
- family: trend_momentum
- markets: both (natively futures/crypto 24-7; for forex use daily UTC open)
- timeframes: 5m/15m execution anchored to daily open
- entry_long: at/after session open: price > open + k1*Range, where Range = max(HH−LC, HC−LL) over prior N days (HH=highest high of N days, LC=lowest close, HC=highest close, LL=lowest low).
- entry_short: price < open − k2*Range (k2 may differ from k1, standard asymmetric).
- exit_rule: close of session (time-based, intraday system — flat by definition); hard SL = 1.0*ATR(14, daily)/4; TP optional at 1.5R.
- params: n_days=4, k1=0.5, k2=0.5, session="UTC day"
- data_needs: OHLCV (intraday bars + daily anchors)
- notes: One of the most famous Chinese CTA systems (published by FutureArb/QuantConnect community, consistently top-ranked on Chinese strategy leaderboards ~2013-2016). The Range formula (max of two span definitions) is the specific published detail most implementations get wrong. On crypto, "session" = UTC day; on forex use the pair's primary session. Per-bar repainting risk: compute Range from COMPLETED days only.

### STRATEGY: Aroon
- family: trend_momentum
- markets: both
- timeframes: 4h, 1d
- entry_long: Aroon-Up(25) crosses above Aroon-Down(25) AND Aroon-Up > 70. Aroon-Up(n) = 100*(n − bars_since_highest_high(n))/n.
- entry_short: Aroon-Down crosses above Aroon-Up AND Aroon-Down > 70.
- exit_rule: opposite cross, or both lines < 50 (trend stall); SL = 2*ATR; TP = 2R.
- params: period=25, level=70, stall=50, sl_atr=2.0, tp_r=2.0
- data_needs: OHLC
- notes: Chande 1995. Measures recency of extremes rather than price distance — complements MA systems nicely (fires early in new trends). The >70 gate is Chande's published "strong trend" threshold. Oscillator version (Up−Down > +50) is an equivalent trigger, pick one.

### STRATEGY: Vortex Indicator
- family: trend_momentum
- markets: both
- timeframes: 1h, 4h, 1d
- entry_long: VI+(14) crosses above VI−(14), where VM+ = Σ|high[i]−low[i−1]|, VM− = Σ|low[i]−high[i−1]|, VI± = VM±/ΣTR over 14 bars. Optional filter: ADX > 20.
- entry_short: VI− crosses above VI+.
- exit_rule: opposite cross; SL = 1.5*ATR; TP = 2R.
- params: period=14, adx_min=20 (optional), sl_atr=1.5, tp_r=2.0
- data_needs: OHLC
- notes: Botes & Siepman 2010 (TASC), derived from Wilder's DMI concept — signals correlate with +DI/−DI crosses (expect ~70-80% signal overlap with the ADX/DMI system; portfolio layer should dedupe). Documented edge is thin standalone; value is as a DMI-family diversifier with different parameter sensitivity.

### STRATEGY: Mass Index Reversal Bulge → Trend Continuation
- family: trend_momentum
- markets: both
- timeframes: 1h, 4h
- entry_long: Mass Index (25-bar sum of 9-bar EMA of (high−low) / 9-bar EMA of that EMA) rises above 27 then falls back below 26.5 ("reversal bulge" complete) AND EMA(close,9) rising at bulge completion → enter with the EMA direction (long).
- entry_short: bulge complete AND EMA9 falling → short.
- exit_rule: SL = 1.5*ATR; TP = 1.5R; time-stop 15 bars.
- params: ema=9, sum=25, bulge=27.0, trigger=26.5, sl_atr=1.5, tp_r=1.5, max_bars=15
- data_needs: OHLC
- notes: Dorsey's original: bulge warns of range expansion reversal, and his published rule pairs it with a 9-EMA to pick direction — codified exactly that. Honestly the weakest-documented entry in this catalog (mostly vendor backtests); include at low confidence (0.5) and let the paper cycle decide. Stateful: must track "bulge above 27 occurred, waiting for drop below 26.5".

### STRATEGY: TRIX
- family: trend_momentum
- markets: both
- timeframes: 4h, 1d
- entry_long: TRIX(15) crosses above its 9-bar signal EMA while TRIX > 0. TRIX = 100*Δ(EMA3(close,15)) where EMA3 = triple-smoothed EMA (EMA of EMA of EMA).
- entry_short: TRIX crosses below signal while TRIX < 0.
- exit_rule: opposite signal cross; SL = 2*ATR; TP = 2R.
- params: length=15, signal=9, sl_atr=2.0, tp_r=2.0
- data_needs: OHLC (>= 3*15 + 9 + 10 bars)
- notes: Hutton 1980s. Triple smoothing kills most noise — fewer, later signals than MACD; the TRIX>0 gate aligns with the dominant trend. Zero-line cross variant (no signal line) is simpler and nearly as good; catalog uses the signal-line variant as primary because it's the more published form.

### STRATEGY: KST (Know Sure Thing)
- family: trend_momentum
- markets: both
- timeframes: 4h, 1d
- entry_long: KST crosses above its 9-bar SMA signal. KST = 1*SMA(ROC10,10) + 2*SMA(ROC15,10) + 3*SMA(ROC20,10) + 4*SMA(ROC30,15) (Pring's standard short-term formula).
- entry_short: KST crosses below its signal SMA.
- exit_rule: opposite signal cross; SL = 2*ATR; TP = 2.5R.
- params: r1=10,r2=15,r3=20,r4=30, n1=10,n2=10,n3=10,n4=15, w=(1,2,3,4), signal=9, sl_atr=2.0, tp_r=2.5
- data_needs: OHLC (>= 50 bars)
- notes: Martin Pring's summed-ROC system — smoothed multi-timeframe momentum in one line. Published variants (short/intermediate/long-term KST) differ in ROC spans; the 10/15/20/30 set is Pring's standard short-term KST and the one codified here. Later than MACD, smoother; treat as a confirmation-grade momentum signal (confidence 0.6).

### STRATEGY: Elder Impulse System
- family: trend_momentum
- markets: both
- timeframes: 1h, 4h, 1d
- entry_long: bar is "green" (EMA(close,13) rising vs prior bar AND MACD-histogram(12,26,9) rising) AND previous bar was NOT green (impulse just turned on) AND close > EMA13.
- entry_short: bar is "blue/red-bearish" (EMA13 falling AND MACD-hist falling) AND previous bar was not bearish-impulse AND close < EMA13.
- exit_rule: impulse color turns off (either condition fails) — exit at market; SL = 1.5*ATR backstop; TP = 2R.
- params: ema=13, macd=(12,26,9), sl_atr=1.5, tp_r=2.0
- data_needs: OHLC
- notes: Elder ("Come Into My Trading Room", 2002) built this as a trade-PERMISSION filter (green = only longs allowed), not a standalone entry. Codified here as entry-on-impulse-onset, the common published trading variant. Rides short momentum bursts; exits fast by design. Cheap to implement on top of existing MACD helper.

### STRATEGY: Trend-Pullback Continuation (EMA20 + rejection candle)
- family: trend_momentum
- markets: both
- timeframes: 1h, 4h, 1d
- entry_long: uptrend (close > SMA50 AND SMA50 rising over 5 bars) AND price touches/dips below EMA20 intrabar AND closes back above EMA20 with a bullish rejection (close in top 50% of bar range, close > open).
- entry_short: mirror: downtrend, rally to/above EMA20, close back below with bearish rejection (close in bottom 50% of range, close < open).
- exit_rule: SL = pullback bar low − 0.5*ATR (long) / high + 0.5*ATR (short); TP = 2R; optional trail below EMA20 after +1R.
- params: trend_sma=50, pullback_ema=20, trend_lookback=5, sl_atr=0.5, tp_r=2.0
- data_needs: OHLC
- notes: The single most common discretionary trend entry ("buy the dip at the 20EMA in a trend"), codified with the rejection-candle trigger to avoid catching falling knives. Overlaps conceptually with ADX Pullback Continuation and Triple EMA Ribbon — the paper cycle should keep at most one of the three; this one has the tightest stops and best R-profile of the family.

---

## Implementation notes for the coding team (family-level)

1. **Warmup discipline**: several entries need >200 bars (any 200-SMA filter, Ichimoku cloud, 55-bar Donchian). Return `None` until enough history exists — same pattern as existing repo code.
2. **Stateful strategies**: Turtle S1 (last-breakout filter), Mass Index (bulge-then-drop), Dual Thrust (session anchoring). The current stateless `detect_*` signature handles these only via module-level caches or by treating "prior bars" in the passed OHLC slice as the state source — flag these three to the orchestrator.
3. **Correlation clusters** (dedupe in portfolio layer, not in the strategy): {EMA 9/21, MACD cross, MACD zero cross} ≈ same trade; {Turtle S1, S2, Donchian 20, Keltner breakout} overlap heavily; {ADX/DMI, Vortex} overlap; {Triple EMA, ADX pullback, EMA20 pullback} overlap. The 1-week paper test should rank within clusters and keep the best of each.
4. **Cost sensitivity ranking** (most to least): Dual Thrust (intraday), EMA 9/21 on 15m, Supertrend 15m > MACD histogram, Elder Impulse > everything on 4h/1d. On paper broker with zero fees the intraday ones will look artificially good — apply the fee model before judging winners.

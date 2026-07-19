# RESEARCH CATALOG — Volume, Order-Flow & Crypto-Microstructure Strategies

Worker: Research_Worker_VolumeOrderflow
Family tag used below: `volume_orderflow`
Count: 28 strategies

## Codebase context (verified against this repo)

- Bars are dicts: `{"open", "high", "low", "close", "volume", "ts"}` — `core/data_provider.py:77`, so **OHLCV is available today**.
- Existing signal contract (from `core/strategies.py`): strategy receives `ohlc` list and returns e.g. `{"action": "BUY", "confidence": 0.65, "reasons": [...]}` (or `None`). Entries below map: `entry_long` → `action: "BUY"`, `entry_short` → `action: "SELL"`.
- `core/indicators.py` already implements `obv(closes, volumes)` and `mfi(highs, lows, closes, volumes)` — reuse them.
- **No feeds exist yet for**: funding rates, open interest, L2 order book, liquidations, on-chain exchange flows, or perp-vs-spot multi-symbol legs. Strategies needing them are flagged `DATA_NEEDS:` in `notes` and should be stubbed/disabled in the 1-week paper cycle unless the feed is added.

## Data-needs legend

| value | meaning |
|---|---|
| OHLCV | implementable now (volume key present) |
| funding | perp funding-rate history (e.g. Binance `/fapi/v1/fundingRate`) |
| open_interest | OI history (e.g. Binance `/futures/data/openInterestHist`) |
| orderbook | L2 book snapshots (websocket depth) |
| liquidations | forced-liquidation stream (e.g. Binance `forceOrder`) |
| onchain_flows | exchange netflow data (Glassnode/CryptoQuant — paid, delayed) |
| multi-symbol | needs >1 symbol/legs simultaneously (e.g. spot + perp) |

## Index

| # | Strategy | data beyond OHLCV? |
|---|---|---|
| 1 | OBV Trend Confirmation | no |
| 2 | OBV Divergence Reversal | no |
| 3 | OBV Breakout (Accumulation Lead) | no |
| 4 | VWAP Mean Reversion | no |
| 5 | VWAP Trend Pullback | no |
| 6 | Anchored VWAP (AVWAP) Reclaim | no |
| 7 | VWAP Band Squeeze Breakout | no |
| 8 | Volume Profile POC Retest | no |
| 9 | Value-Area Breakout (80% Rule variant) | no |
| 10 | Low-Volume-Node (LVN) Vacuum | no |
| 11 | CVD Divergence (Absorption) | trades feed for real CVD |
| 12 | CVD Trend Confirmation | trades feed for real CVD |
| 13 | Order-Book Imbalance Scalp | **DATA_NEEDS: orderbook** |
| 14 | Funding-Rate Fade | **DATA_NEEDS: funding** |
| 15 | Funding Cash-and-Carry (Delta-Neutral) | **DATA_NEEDS: funding + multi-symbol** |
| 16 | OI + Funding Squeeze | **DATA_NEEDS: open_interest + funding** |
| 17 | Open-Interest Divergence | **DATA_NEEDS: open_interest** |
| 18 | Liquidation-Cascade Fade | **DATA_NEEDS: liquidations + funding** |
| 19 | Exchange-Flow Pressure | **DATA_NEEDS: onchain_flows** |
| 20 | RVOL Breakout Confirmation | no |
| 21 | Volume Dry-Up Pullback | no |
| 22 | Climactic Volume Reversal | no |
| 23 | MFI Extremes + Divergence | no |
| 24 | Chaikin A/D Divergence | no |
| 25 | Chaikin Money Flow (CMF) Filter | no |
| 26 | Ease of Movement (EMV) Zero-Line | no |
| 27 | Elder Force Index Pullback | no |
| 28 | Klinger Volume Oscillator Cross | no |

---

### STRATEGY: OBV Trend Confirmation
- family: volume_orderflow
- markets: both
- timeframes: 1h, 4h, 1d
- entry_long: close > EMA(close, 50) AND OBV > EMA(OBV, 21) AND OBV has made a higher high over the last 20 bars → BUY at close.
- entry_short: mirror of long (close < EMA50, OBV < EMA21(OBV), OBV lower low over 20 bars)
- exit_rule: SL = entry − 2.0×ATR(14); TP = 3R; OR signal-exit when OBV crosses below EMA21(OBV) (long side).
- params: ema_price=50, obv_ema=21, hh_lookback=20, atr=14, sl_mult=2.0, rr=3.0
- data_needs: OHLCV
- notes: Granville OBV; use existing `core/indicators.py:obv`. Lagging in chop — pairs poorly with ranging regimes; optional ADX(14)>20 filter. Crypto volume is wash-trade noisy on small venues; Binance BTC/ETH majors are cleanest.

### STRATEGY: OBV Divergence Reversal
- family: volume_orderflow
- markets: both
- timeframes: 1h, 4h
- entry_long: over lookback=14 bars, price prints a lower swing-low (fractal window=5) while OBV prints a higher low → armed; trigger BUY on first close above the prior bar's high.
- entry_short: mirror of long (price higher swing-high, OBV lower high; trigger = close below prior bar's low)
- exit_rule: SL = divergence swing-low − 0.5×ATR(14); TP = 2R or opposing swing point, whichever first; hard time-stop 20 bars.
- params: lookback=14, swing_window=5, atr=14, sl_buf=0.5, rr=2.0, time_stop=20
- data_needs: OHLCV
- notes: `_swing_highs/_swing_lows` helpers already exist in `core/strategies.py` — reuse. Divergences fail in strong trends; gate with ADX(14)<25 or take only at HTF range extremes. Standard published variant (Granville divergence) codified.

### STRATEGY: OBV Breakout (Accumulation Lead)
- family: volume_orderflow
- markets: crypto
- timeframes: 4h, 1d
- entry_long: OBV closes above its own 20-bar high while price close is still BELOW its 20-bar high (volume leads price) → BUY at close.
- entry_short: mirror of long (OBV breaks 20-bar low while price still above its 20-bar low)
- exit_rule: SL = 1.5×ATR(14); TP/trail: once price makes its own 20-bar high, switch to 2×ATR chandelier trail; time-stop 30 bars if price never confirms.
- params: obv_lookback=20, atr=14, sl_mult=1.5, trail_mult=2.0, time_stop=30
- data_needs: OHLCV
- notes: "Smart money accumulates before the breakout" heuristic. Decent on alt majors; high false-positive rate in thin alts where a single wallet moves OBV.

### STRATEGY: VWAP Mean Reversion
- family: volume_orderflow
- markets: both (crypto sessions = UTC day, reset 00:00 UTC)
- timeframes: 5m, 15m, 1h
- entry_long: close < session_VWAP − 2.0×σ (σ = session volume-weighted std dev of price) AND RSI(14) < 30 → BUY at close; target = session VWAP.
- entry_short: mirror of long (close > VWAP + 2.0σ AND RSI(14) > 70)
- exit_rule: TP at first touch of session VWAP; SL = 1.0×ATR(14) beyond entry; hard flatten at session end (23:55 UTC) — no overnight carry.
- params: dev_mult=2.0, rsi=14, rsi_os=30, rsi_ob=70, atr=14, session=utc_day
- data_needs: OHLCV
- notes: Fade only in ranging conditions — disable when ADX(14) > 25 or on major news (funding print, CPI). Taker fees + spread on 5m can erase the whole edge; paper broker should model limit fills at band touch, not market.

### STRATEGY: VWAP Trend Pullback
- family: volume_orderflow
- markets: both
- timeframes: 15m, 1h
- entry_long: uptrend filter: close > session_VWAP AND VWAP slope positive over 10 bars; then wait for a bar whose low touches/pierces VWAP but closes back above it (rejection wick) → BUY at that close.
- entry_short: mirror of long (close < VWAP, VWAP sloping down, high pierces VWAP and closes below)
- exit_rule: SL = pullback bar low − 0.3×ATR(14); TP = prior swing high or 2R; optional trail along VWAP once +1R.
- params: slope_bars=10, atr=14, sl_buf=0.3, rr=2.0
- data_needs: OHLCV
- notes: Institutional-benchmark logic (algos defend VWAP). Skip first 30 min after 00:00 UTC (VWAP unstable while few bars in session). One entry per pullback leg to avoid re-entry churn.

### STRATEGY: Anchored VWAP (AVWAP) Reclaim
- family: volume_orderflow
- markets: both
- timeframes: 1h, 4h
- entry_long: anchor VWAP at the lowest low of the last 50 bars (codified anchor). BUY when price has been below AVWAP for ≥10 consecutive bars and then closes back above it (reclaim); alternatively on a pullback-hold: price above AVWAP, dips to touch it, closes above.
- entry_short: mirror of long (anchor = highest high of last 50 bars; loss of AVWAP after ≥10 bars above)
- exit_rule: SL = 1.0×ATR(14) on the far side of AVWAP; TP = 1:1 measured move to prior swing extreme; trail behind AVWAP after +1R.
- params: anchor_lookback=50, reclaim_bars=10, atr=14, sl_mult=1.0
- data_needs: OHLCV
- notes: Brian Shannon's AVWAP. Anchor choice is the subjective part — the "extreme of last 50 bars" rule is the standard codable variant; note that event anchors (major low, listing date) are better but not automatable generically.

### STRATEGY: VWAP Band Squeeze Breakout
- family: volume_orderflow
- markets: crypto
- timeframes: 15m, 1h
- entry_long: band width (2σ around session VWAP) at its narrowest of the last 20 bars (squeeze) AND close breaks above VWAP + 1.5σ with volume ≥ 1.5×SMA(volume,20) → BUY at close.
- entry_short: mirror of long (break below VWAP − 1.5σ on volume)
- exit_rule: SL = session VWAP (midline); TP = VWAP + 2.5σ band touch; or trail 2×ATR(14) if breakout runs.
- params: dev_entry=1.5, dev_tp=2.5, squeeze_lookback=20, vol_mult=1.5, atr=14
- data_needs: OHLCV
- notes: Volatility-expansion play; the volume condition is what separates it from plain Bollinger breaks — do not drop it. Weekends produce fake squeezes in crypto (thin tape).

### STRATEGY: Volume Profile POC Retest
- family: volume_orderflow
- markets: both
- timeframes: 1h, 4h
- entry_long: build fixed-range volume profile over last 100 bars (24 rows, price-range/24 each; distribute each bar's volume uniformly across rows it spans). Price is above POC, pulls back into POC row, and closes back above POC → BUY.
- entry_short: mirror of long (price below POC, rallies into POC, closes back below)
- exit_rule: SL = 0.75×ATR(14) beyond POC; TP = Value Area High (70% VA) for longs; time-stop 15 bars.
- params: profile_lookback=100, rows=24, va_pct=0.70, atr=14, sl_mult=0.75, time_stop=15
- data_needs: OHLCV
- notes: Bar-based profile is an approximation — real tick profile needs trade data; uniform volume spread across the bar is the standard proxy. POC acts as magnet/support because that price had max two-sided trade.

### STRATEGY: Value-Area Breakout (80% Rule variant)
- family: volume_orderflow
- markets: both
- timeframes: 1h, 4h (profile anchored on prior UTC day)
- entry_long: current session opens INSIDE prior day's value area (VAL–VAH, 70%) and then closes above VAH with volume ≥ 1.5×SMA(volume,20) → BUY; target = VAH + 1.0×(VAH − VAL).
- entry_short: mirror of long (open inside VA, close below VAL on volume; target = VAL − VA height)
- exit_rule: SL = back inside VA (VAH − 0.5×ATR for longs); TP = measured VA-height target; exit early on 2 consecutive closes back inside the VA (failed breakout).
- params: va_pct=0.70, anchor=prior_utc_day, vol_mult=1.5, atr=14, sl_buf=0.5
- data_needs: OHLCV
- notes: Steidlmayer's "80% rule" is strictly about RE-ENTRY into value; the breakout-acceptance variant codified here is the common retail-codable version — flagged as variant. Acceptance (closes), not wicks, is the trigger.

### STRATEGY: Low-Volume-Node (LVN) Vacuum
- family: volume_orderflow
- markets: crypto
- timeframes: 1h, 4h
- entry_long: on the 100-bar profile (24 rows), identify LVN rows (volume < 0.5× mean row volume) sitting between price and the next HVN above. BUY when price enters the LVN zone from below (close inside LVN row) → target the HVN/POC above (price "vacuums" through low-liquidity zones fast).
- entry_short: mirror of long (price enters LVN from above, target HVN below)
- exit_rule: SL = LVN far edge − 0.5×ATR(14); TP = center of target HVN; strict time-stop 10 bars — the thesis is SPEED, stale trade = wrong.
- params: profile_lookback=100, rows=24, lvn_mult=0.5, atr=14, time_stop=10
- data_needs: OHLCV
- notes: Works best in ranging crypto regimes (mean-reverting between HVN shelves). In strong trends LVNs get traversed AGAINST you via continuation — pair with ADX<25 filter.

### STRATEGY: CVD Divergence (Absorption)
- family: volume_orderflow
- markets: crypto (forex n/a — no centralized tape)
- timeframes: 15m, 1h, 4h
- entry_long: over lookback=20 bars, price makes a lower low while CVD makes a higher low (seller exhaustion / passive absorption) → armed; trigger BUY on close above prior bar high. Proxy CVD = cumulative sum of sign(close−open)×volume.
- entry_short: mirror of long (price higher high, CVD lower high = buyer exhaustion)
- exit_rule: SL = divergence extreme − 0.5×ATR(14); TP = 2R or prior swing; time-stop 15 bars.
- params: lookback=20, atr=14, sl_buf=0.5, rr=2.0, time_stop=15
- data_needs: OHLCV (proxy CVD); production-grade needs exchange taker buy/sell volume (Binance aggTrades) — DATA_NEEDS: trades feed for true CVD
- notes: OHLC bar proxy delta is crude (whole bar counted one way). True CVD divergence on Binance perps is one of the best-documented crypto edges; proxy version still beats nothing but expect degraded win rate. Note in code which version is live.

### STRATEGY: CVD Trend Confirmation
- family: volume_orderflow
- markets: crypto
- timeframes: 1h, 4h
- entry_long: price in uptrend (higher highs over 20 bars) AND CVD at a new 20-bar high (aggressive market buyers confirming) AND price pulls back to close ≥ EMA(close,20) → BUY at close.
- entry_short: mirror of long (lower lows, CVD new 20-bar lows, pullback to EMA20 from below)
- exit_rule: SL = 1.5×ATR(14); TP = 2.5R; signal-exit if CVD diverges (price new high, CVD not) while in trade.
- params: hh_lookback=20, ema=20, atr=14, sl_mult=1.5, rr=2.5
- data_needs: OHLCV (proxy) / trades feed for real CVD (see #11)
- notes: Companion to #11 — uses CVD as filter not trigger. If price rises but CVD falls, the move is short-covering, fragile; that condition doubles as an exit.

### STRATEGY: Order-Book Imbalance Scalp
- family: volume_orderflow
- markets: crypto (forex: only with ECN depth feed)
- timeframes: 1m (microstructure; operates on book snapshots, not bars)
- entry_long: top-5-level bid volume / ask volume > 3.0 for 3 consecutive 1s snapshots AND last price ≥ 1m micro-VWAP → BUY; hold 30–120s.
- entry_short: mirror of long (ask/bid ratio > 3.0, price ≤ micro-VWAP)
- exit_rule: TP = +0.4×ATR(1m); SL = −0.4×ATR(1m); hard time-stop 120s; instant exit if imbalance flips sign.
- params: levels=5, imb_ratio=3.0, snapshots=3, tp_atr=0.4, sl_atr=0.4, max_hold_s=120
- data_needs: orderbook (L2) + OHLCV — DATA_NEEDS: L2 book websocket; NOT implementable on current feeds
- notes: Extremely latency- and fee-sensitive. Paper fills will flatter results wildly — if papered, model taker fee both sides + ≥1 tick slippage + queue position penalty, else expect live-vs-paper collapse. Spoofing makes static imbalance signals decay fast.

### STRATEGY: Funding-Rate Fade (Extreme Funding Contrarian)
- family: volume_orderflow
- markets: crypto (perps only)
- timeframes: 4h, 1h execution around 8h funding timestamps
- entry_long: funding rate ≤ −0.05% for ≥2 consecutive 8h prints (crowded shorts) AND momentum stalls (last 4h close > prior 4h close) → BUY perp. You also RECEIVE funding while holding.
- entry_short: mirror of long (funding ≥ +0.10% for 2 prints AND last 4h close < prior 4h close → SELL perp)
- exit_rule: TP when funding normalizes into ±0.01% band OR after 2 funding intervals (16h), whichever first; SL = 1.5×ATR(4h).
- params: fund_long=-0.0005, fund_short=0.001, confirm_prints=2, max_hold_h=24, atr=14
- data_needs: funding + OHLCV — DATA_NEEDS: funding-rate history feed
- notes: Extreme funding = crowded positioning; the momentum-stall filter is essential (do NOT fade a live squeeze). Thresholds are Binance-style 8h funding; other venues (1h/4h intervals, capped rates) need re-scaling. Asymmetric thresholds (shorts crowd harder) are intentional.

### STRATEGY: Funding Cash-and-Carry (Delta-Neutral)
- family: volume_orderflow
- markets: crypto (spot + perp on same venue)
- timeframes: position trade; checked at each 8h funding print
- entry_long: when annualized funding > 10% (8h print > ~0.0114%) AND perp-spot basis within ±0.3% band → SHORT perp + BUY spot, equal notional, 1x leverage on perp leg. PnL = funding receipts + basis convergence; market-direction neutral.
- entry_short: reverse carry when funding < −10% annualized: LONG perp + SHORT spot — requires margin borrow on spot; often impractical/expensive, so default = disabled, note as optional.
- exit_rule: unwind both legs when annualized funding < 3% OR basis flips sign; rebalance legs if delta drifts > 2% of notional; emergency unwind if perp margin ratio > 50%.
- params: apr_entry=0.10, apr_exit=0.03, basis_band=0.003, rebalance_drift=0.02, leverage=1.0
- data_needs: funding + multi-symbol (spot + perp legs) + OHLCV — DATA_NEEDS: funding feed and dual-venue/dual-instrument execution
- notes: Not a signal strategy — a yield strategy; paper broker must model two legs, funding cashflows, and borrow fees. Real risks: liquidation of the perp leg during wicks (keep 1x, isolated margin), exchange counterparty risk, fee drag on rebalance. Returns are real but modest; capital-heavy.

### STRATEGY: OI + Funding Squeeze
- family: volume_orderflow
- markets: crypto (perps)
- timeframes: 1h, 4h
- entry_long: open interest at a 30-day high AND funding ≤ 0 (shorts crowding) AND price closes above its 20-bar high → BUY: trapped shorts fuel a squeeze.
- entry_short: mirror of long (OI 30d high AND funding ≥ +0.05% AND close below 20-bar low → SELL: long-liquidation cascade setup)
- exit_rule: SL = 1.5×ATR(14); TP when OI falls ≥ 15% from its peak (deleveraging complete) or 3R, whichever first; time-stop 48h.
- params: oi_lookback_d=30, fund_short_thresh=0.0, fund_long_thresh=0.0005, break_n=20, oi_drop_exit=0.15, atr=14, time_stop_h=48
- data_needs: open_interest + funding + OHLCV — DATA_NEEDS: OI history + funding feeds
- notes: Classic squeeze template: high OI = large levered positioning; adverse price move forces unwinds that accelerate the move. The OI-drop exit is the key — squeezes die when the fuel (OI) is spent.

### STRATEGY: Open-Interest Divergence
- family: volume_orderflow
- markets: crypto (perps)
- timeframes: 4h, 1d
- entry_long: OI 14-bar change > +15% (new positions building) AND price compressed in a <5% range over 14 bars → BUY only on close above the 14-bar range high (OI-backed breakout in direction of resolution).
- entry_short: mirror of long (same OI buildup, close below range low)
- exit_rule: SL = 2.0×ATR(14) or range mid, whichever tighter; TP = 3R; signal-exit if OI drops > 10% while in position (conviction leaving).
- params: oi_change=0.15, range_pct=0.05, lookback=14, atr=14, rr=3.0, oi_exit=-0.10
- data_needs: open_interest + OHLCV — DATA_NEEDS: OI history feed
- notes: Interpretation matrix (standard): price↑+OI↑ = strong trend; price↑+OI↓ = short squeeze, fragile; price↓+OI↑ = distribution/strong downtrend; price↓+OI↓ = long capitulation, near floor. Entry here codifies the "OI↑ + compression" cell only — highest information content.

### STRATEGY: Liquidation-Cascade Fade
- family: volume_orderflow
- markets: crypto (perps)
- timeframes: 5m, 15m execution; cascade detection on 1h aggregation
- entry_long: long-liquidation notional in the last 1h > 99th percentile of trailing 30d AND price dropped > 3×ATR(1h) within ≤4h AND funding flipped negative → capitulation; BUY on first 15m close back above the cascade window's VWAP.
- entry_short: mirror of long (short-liquidation spike, price up >3×ATR, funding flipped positive → SELL the blow-off retrace)
- exit_rule: TP = 50% retracement of the cascade leg OR 24h time-stop, whichever first; SL = cascade wick extreme − 0.5×ATR(15m).
- params: liq_pctile=99, drop_mult=3.0, cascade_window_h=4, retrace_tp=0.5, atr=14, time_stop_h=24
- data_needs: liquidations + funding + OHLCV — DATA_NEEDS: forced-liquidation stream (e.g. Binance forceOrder) + funding feed
- notes: Capitulation wicks are where forced flow exhausts itself — one of the most documented crypto-specific edges (2020–2024 crash studies). Position size small: SLs are wide, fills at cascade lows are terrible, and paper fills here are UNREALISTIC — penalize paper fills at wick prices by ≥0.3% extra slippage.

### STRATEGY: Exchange-Flow Pressure (On-Chain)
- family: volume_orderflow
- markets: crypto (BTC, ETH — assets with labeled-wallet coverage)
- timeframes: 4h, 1d (slow signal; thesis horizon 3–10 days)
- entry_long: 7d exchange netflow < −1.0% of tracked exchange balance (coins leaving exchanges = accumulation) AND close > EMA(close,50) → BUY pullbacks to EMA(close,20).
- entry_short: 24h net inflow > +1.5% of exchange balance (distribution intent) AND price rejected at/below EMA50 (close back under it) → SELL.
- exit_rule: SL = 2.0×ATR(4h); TP = 3R; thesis-invalidation exit when netflow regime flips sign for 3 consecutive days.
- params: outflow_7d=-0.01, inflow_24h=0.015, ema_trend=50, ema_entry=20, atr=14, rr=3.0
- data_needs: OHLCV + onchain_flows — DATA_NEEDS: exchange-netflow feed (Glassnode/CryptoQuant; paid API, ~1h lag)
- notes: Slow, position-sizing-grade signal, not a scalper. Wallet labeling changes over time (new exchange wallets get tagged late), which restates history — freeze a data vintage for backtests. Skip for the 1-week paper cycle unless a free netflow proxy (e.g. CryptoQuant public charts scraped) is wired up.

### STRATEGY: RVOL Breakout Confirmation
- family: volume_orderflow
- markets: both
- timeframes: 15m, 1h, 4h
- entry_long: close breaks above the 20-bar high AND bar volume ≥ 2.0×SMA(volume,20) AND close in the top 30% of the bar's range → BUY at close.
- entry_short: mirror of long (close below 20-bar low, volume ≥2×, close in bottom 30%)
- exit_rule: SL = breakout bar low (long) or 1.5×ATR(14), whichever tighter; TP = 2.5R; after +2R trail with 10-bar low.
- params: break_n=20, vol_mult=2.0, close_pct=0.70, atr=14, rr=2.5, trail_n=10
- data_needs: OHLCV
- notes: Bread-and-butter volume-confirmation strategy. The close-location filter kills wick-fakeouts; do not drop it. In crypto, downgrade or skip signals printed on weekends/holidays (thin tape = fake RVOL).

### STRATEGY: Volume Dry-Up Pullback
- family: volume_orderflow
- markets: both
- timeframes: 1h, 4h, 1d
- entry_long: uptrend (close > EMA(close,50) and higher swing highs); pullback of ≥3 consecutive bars with volume declining each bar AND final pullback bar volume < 0.6×SMA(volume,20) → BUY on break of the pullback high (stop order at pullback high + 0.1×ATR).
- entry_short: mirror of long (downtrend, low-volume rally, stop entry below pullback low)
- exit_rule: SL = pullback low − 0.3×ATR(14); TP = prior swing high, then trail 2×ATR if it breaks.
- params: ema=50, pullback_min=3, dry_mult=0.6, atr=14, entry_buf=0.1, sl_buf=0.3
- data_needs: OHLCV
- notes: Wyckoff-flavored logic: declining volume on a pullback = no selling pressure, trend resumes. Fails when the dry-up is actually pre-news paralysis — optional filter: skip if funding print or major macro event within next 4h.

### STRATEGY: Climactic Volume Reversal
- family: volume_orderflow
- markets: both
- timeframes: 15m, 1h, 4h
- entry_long: after ≥5 consecutive down-closed bars, a bar prints volume > 3.0×SMA(volume,20) AND range > 2.0×ATR(14) AND closes in its top 40% (stopping volume / selling climax) → BUY next bar if it holds above the climax bar's midpoint.
- entry_short: mirror of long (≥5 up bars, buying climax closing in bottom 40%)
- exit_rule: SL = climax bar extreme − 0.5×ATR(14); TP = 2R or first touch of EMA(close,20); time-stop 10 bars.
- params: prior_bars=5, vol_mult=3.0, range_mult=2.0, close_zone=0.40, atr=14, rr=2.0, time_stop=10
- data_needs: OHLCV
- notes: Wyckoff SC/BC. Hit rate improves markedly when the climax coincides with a liquidation spike (#18) or occurs at an HTF level (prior day low, weekly POC). In strong downtrends climaxes come in pairs — the second one works more often.

### STRATEGY: MFI Extremes + Divergence
- family: volume_orderflow
- markets: both
- timeframes: 1h, 4h
- entry_long: MFI(14) crosses back up through 20 after being below it → BUY at close; stronger variant (use when ADX<25): price lower low + MFI higher low divergence with MFI < 30, trigger = close above prior bar high.
- entry_short: mirror of long (MFI crosses down through 80; bearish divergence variant)
- exit_rule: SL = 1.5×ATR(14); TP = MFI reaches 50 (midline) or opposite extreme (80 for longs); time-stop 15 bars.
- params: mfi=14, os=20, ob=80, atr=14, time_stop=15
- data_needs: OHLCV
- notes: Volume-weighted RSI; use existing `core/indicators.py:mfi`. Raw extreme-crossing is mediocre in trends (MFI pins at 0/100 for days) — the divergence variant is the real edge; keep the ADX regime gate.

### STRATEGY: Chaikin A/D Divergence
- family: volume_orderflow
- markets: both
- timeframes: 4h, 1d
- entry_long: over lookback=20 bars, price prints lower swing-low while the Chaikin Accumulation/Distribution Line prints a higher low (accumulation under weakness) → armed; trigger BUY on close above EMA(close,20).
- entry_short: mirror of long (price higher high, ADL lower high = distribution)
- exit_rule: SL = divergence swing extreme − 0.5×ATR(14); TP = 2R; signal-exit if ADL makes a new extreme against the position.
- params: lookback=20, swing_window=5, ema=20, atr=14, rr=2.0
- data_needs: OHLCV
- notes: ADL = cumulative sum of CLV×volume where CLV = ((close−low)−(high−close))/(high−low), guard div-by-zero on doji bars. Gap-heavy symbols distort CLV (gap with close near high = full credit); acceptable on crypto's 24/7 tape, worse on forex weekend gaps.

### STRATEGY: Chaikin Money Flow (CMF) Filter
- family: volume_orderflow
- markets: both
- timeframes: 4h, 1d
- entry_long: CMF(20) crosses above +0.05 while close > EMA(close,50) → BUY at close.
- entry_short: mirror of long (CMF crosses below −0.05 while close < EMA50)
- exit_rule: SL = 2.0×ATR(14); signal-exit on CMF zero cross; optional TP = 2.5R.
- params: cmf=20, thr=0.05, ema=50, atr=14, rr=2.5
- data_needs: OHLCV
- notes: CMF = SMA(20) of CLV×volume / SMA(20) of volume. The ±0.05 deadband is the standard fix for zero-line whipsaw (Marc Chaikin's published guidance). Works as a standalone entry or as a volume filter stacked onto other families' signals.

### STRATEGY: Ease of Movement (EMV) Zero-Line
- family: volume_orderflow
- markets: both
- timeframes: 4h, 1d (too noisy below 4h)
- entry_long: EMV(14) (SMA-14 smoothed) crosses above zero while close > EMA(close,50) → BUY: price moving up with low volume-effort = path of least resistance.
- entry_short: mirror of long (EMV crosses below zero, close < EMA50)
- exit_rule: SL = 2.0×ATR(14); signal-exit on opposite EMV zero cross; optional TP = 2.5R.
- params: emv_smooth=14, ema=50, atr=14, rr=2.5
- data_needs: OHLCV
- notes: EMV = ((midpoint_t − midpoint_{t−1}) × (high−low)) / volume, scaled (use ×1e8 /(volume/(high−low)) box-ratio form; normalize scale per-symbol or signals won't compare across symbols). Raw 1-bar EMV is useless — the 14-bar smoothing is mandatory.

### STRATEGY: Elder Force Index Pullback
- family: volume_orderflow
- markets: both
- timeframes: 1h, 4h, 1d
- entry_long: trend up (EMA(close,13) rising AND close > EMA(close,50)); 2-period Force Index dips below zero (short-term sellers in an uptrend = pullback) → place BUY stop at signal bar high + 0.1×ATR(14), valid 2 bars.
- entry_short: mirror of long (downtrend, FI(2) pops above zero, sell stop below bar low)
- exit_rule: SL = signal bar low − 0.3×ATR(14); TP = prior swing extreme or 2R; Elder's style: take partial at 1R, move stop to breakeven.
- params: fi_fast=2, fi_slow=13, ema_trend=50, atr=14, entry_buf=0.1, sl_buf=0.3, rr=2.0
- data_needs: OHLCV
- notes: Force Index = (close_t − close_{t−1}) × volume; FI(2) = 2-EMA of raw FI for timing, FI(13) for trend (per Elder's "Come Into My Trading Room"). Alternative standalone variant: FI(13) zero-line cross as a trend signal — weaker, not recommended alone.

### STRATEGY: Klinger Volume Oscillator Cross
- family: volume_orderflow
- markets: both
- timeframes: 4h, 1d
- entry_long: KVO(34,55) crosses above its 13-period signal EMA while close > EMA(close,50) → BUY at close (volume-force confirming trend).
- entry_short: mirror of long (KVO crosses below signal, close < EMA50)
- exit_rule: SL = 2.0×ATR(14); signal-exit on opposite KVO/signal cross; optional TP = 2.5R.
- params: kvo_fast=34, kvo_slow=55, kvo_sig=13, ema=50, atr=14, rr=2.5
- data_needs: OHLCV
- notes: KVO = EMA34−EMA55 of "volume force" (trend-signed typical-price-sum × volume × scaling); needs careful implementation of the trend flag (sign of HLC-sum change). Least famous entry in this catalog — include as diversification, not as a core bet.

---

## Implementation-team notes (whole family)

1. **Signal contract mapping**: `entry_long` trigger → `{"action": "BUY", "confidence": c, "reasons": [...]}`; `entry_short` → `"SELL"`. Encode SL/TP from `exit_rule` into `reasons` metadata or a sidecar risk dict, per the team's existing convention.
2. **Volume is already on the bars** (`"volume"` key, Binance base-asset volume) — no data work needed for the 21 OHLCV strategies.
3. **Feed gaps (DATA_NEEDS)**: #13 (orderbook), #14–16 (funding), #16–17 (open interest), #18 (liquidations), #19 (on-chain flows), #15 (multi-symbol legs). Cheapest additions for the paper cycle: funding + OI (both are free public Binance REST endpoints, 5-min/8h granularity). Orderbook scalping (#13) and on-chain flows (#19) are NOT worth wiring for a 1-week cycle.
4. **Proxy CVD caveat (#11–12)**: OHLC-proxy delta overstates agreement with price by construction (sign comes from the bar itself). Treat paper results as an upper bound.
5. **Volume-profile family (#8–10)** shares one profile builder — implement once, reuse.
6. **Cost sensitivity ranking** (most sensitive first): #13 book imbalance ≫ #4 VWAP mean reversion > #22 climax > #18 liquidation fade. Apply conservative fee+slippage models to these in the paper broker or the 1-week results will lie.

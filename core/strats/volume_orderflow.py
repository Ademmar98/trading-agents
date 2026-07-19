"""Volume & Order-Flow strategy family (per-tag signals).

Implements the OHLCV-compatible entries from research/volume_orderflow.md
(28-entry catalog) under the existing signal contract: each function takes an
OHLC bar list (dicts with open/high/low/close/volume/ts; last bar CLOSED) and
returns {"action", "confidence", "reasons", "strategy", ...} or None, exactly
like core/strategies.py. Every signal dict carries its own unique strategy
tag so per-strategy stats stay attributable (same per-tag pattern as
core/swing.py and core/scalp15.py). SELL means "exit long / stay flat" on the
spot-only paper broker. Session logic uses the bar "ts" epoch (UTC).

Implemented (catalog # -> tag):
  #1  OBV Trend Confirmation          -> VO - OBV Trend Confirm
  #2  OBV Divergence Reversal         -> VO - OBV Divergence
  #3  OBV Breakout (Accum. Lead)      -> VO - OBV Breakout Lead
  #4  VWAP Mean Reversion             -> VO - VWAP Mean Reversion
  #5  VWAP Trend Pullback             -> VO - VWAP Pullback
  #6  Anchored VWAP Reclaim           -> VO - AVWAP Reclaim
  #7  VWAP Band Squeeze Breakout      -> VO - VWAP Squeeze Break
  #8  Volume Profile POC Retest       -> VO - POC Retest
  #9  Value-Area Breakout (variant)   -> VO - Value Area Break
  #10 Low-Volume-Node Vacuum          -> VO - LVN Vacuum
  #11 CVD Divergence (OHLC proxy)     -> VO - CVD Proxy Divergence
  #12 CVD Trend Confirmation (proxy)  -> VO - CVD Proxy Trend
  #20 RVOL Breakout Confirmation      -> VO - RVOL Breakout
  #21 Volume Dry-Up Pullback          -> VO - Volume Dry-Up
  #22 Climactic Volume Reversal       -> VO - Climactic Reversal
  #23 MFI Extremes + Divergence       -> VO - MFI Extremes
  #24 Chaikin A/D Divergence          -> VO - Chaikin ADL Divergence
  #25 Chaikin Money Flow Filter       -> VO - CMF Filter
  #26 Ease of Movement Zero-Line      -> VO - EMV Zero Cross
  #27 Elder Force Index Pullback      -> VO - Force Index Pullback
  #28 Klinger Volume Oscillator Cross -> VO - Klinger Cross

SKIPPED_DATA_NEEDS (NOT faked — current data layer is OHLCV only):
  #13 Order-Book Imbalance Scalp   - orderbook (L2 depth websocket snapshots)
  #14 Funding-Rate Fade            - funding (perp funding-rate history, e.g.
                                     Binance /fapi/v1/fundingRate)
  #15 Funding Cash-and-Carry       - funding + multi-symbol (spot + perp legs
                                     on same venue, dual-leg execution)
  #16 OI + Funding Squeeze         - open_interest (OI history, e.g. Binance
                                     /futures/data/openInterestHist) + funding
  #17 Open-Interest Divergence     - open_interest (OI history feed)
  #18 Liquidation-Cascade Fade     - liquidations (forced-liquidation stream,
                                     e.g. Binance forceOrder) + funding
  #19 Exchange-Flow Pressure       - onchain_flows (exchange-netflow feed,
                                     Glassnode/CryptoQuant; paid, delayed)

Caveat per catalog notes: #11/#12 run on the OHLC-proxy CVD
(cumsum(sign(close-open)*volume)) which overstates agreement with price by
construction — treat paper results as an upper bound. The volume-profile
family (#8-10) shares the single _volume_profile builder below.
"""
from datetime import datetime, timezone

from core.indicators import sma, ema, ema_all, rsi, atr, mfi
from core.strategies import _swing_highs, _swing_lows


def _closes(ohlc):
    return [b["close"] for b in ohlc]


def _highs(ohlc):
    return [b["high"] for b in ohlc]


def _lows(ohlc):
    return [b["low"] for b in ohlc]


def _volumes(ohlc):
    return [b["volume"] for b in ohlc]


def _sig(action, tag, confidence, price, reasons):
    return {
        "action": action,
        "strategy": tag,
        "confidence": confidence,
        "price": price,
        "reasons": [f"{tag}: {r}" for r in reasons],
    }


def _obv_series(closes, volumes):
    out = [0.0]
    for i in range(1, len(closes)):
        if closes[i] > closes[i - 1]:
            out.append(out[-1] + volumes[i])
        elif closes[i] < closes[i - 1]:
            out.append(out[-1] - volumes[i])
        else:
            out.append(out[-1])
    return out


def _proxy_cvd_series(ohlc):
    out = [0.0]
    for b in ohlc:
        d = b["close"] - b["open"]
        step = b["volume"] if d > 0 else (-b["volume"] if d < 0 else 0.0)
        out.append(out[-1] + step)
    return out[1:]


def _session_bars(ohlc):
    """Bars of the last bar's UTC day (crypto session = UTC day, catalog #4)."""
    day = datetime.fromtimestamp(ohlc[-1]["ts"], tz=timezone.utc).date()
    return [b for b in ohlc
            if datetime.fromtimestamp(b["ts"], tz=timezone.utc).date() == day]


def _session_vwap(session):
    """Session VWAP and volume-weighted std dev of typical price."""
    tps = [(b["high"] + b["low"] + b["close"]) / 3 for b in session]
    vols = [b["volume"] for b in session]
    tv = sum(vols)
    if not session or tv <= 0:
        return None, None
    vwap = sum(tp * v for tp, v in zip(tps, vols)) / tv
    var = sum(v * (tp - vwap) ** 2 for tp, v in zip(tps, vols)) / tv
    return vwap, var ** 0.5


def _volume_profile(bars, rows=24, va_pct=0.70):
    """Fixed-range volume profile: each bar's volume spread uniformly across
    the rows its high-low range spans (standard bar-based proxy, catalog #8).
    Returns row grid + POC + 70% value area, or None on a flat range."""
    lo = min(b["low"] for b in bars)
    hi = max(b["high"] for b in bars)
    if hi <= lo:
        return None
    step = (hi - lo) / rows
    vols = [0.0] * rows
    for b in bars:
        k_lo = min(rows - 1, max(0, int((b["low"] - lo) / step)))
        k_hi = min(rows - 1, max(0, int((b["high"] - lo) / step)))
        span = max(1, k_hi - k_lo + 1)
        for k in range(k_lo, k_hi + 1):
            vols[k] += b["volume"] / span
    poc = max(range(rows), key=lambda k: vols[k])
    total = sum(vols)
    target = total * va_pct
    acc = vols[poc]
    up = dn = poc
    while acc < target and (up < rows - 1 or dn > 0):
        up_v = vols[up + 1] if up < rows - 1 else -1.0
        dn_v = vols[dn - 1] if dn > 0 else -1.0
        if up_v >= dn_v:
            up += 1
            acc += vols[up]
        else:
            dn -= 1
            acc += vols[dn]
    return {
        "lo": lo, "hi": hi, "step": step, "vols": vols, "poc": poc,
        "poc_price": lo + (poc + 0.5) * step,
        "vah": lo + (up + 1) * step, "val": lo + dn * step,
        "mean_row": total / rows,
    }


# ---------------------------------------------------------------------------
# #1 — OBV Trend Confirmation (Granville)
# ---------------------------------------------------------------------------

def obv_trend_confirm(ohlc, ema_price=50, obv_ema=21, hh_lookback=20):
    """close > EMA(close,50) AND OBV > EMA(OBV,21) AND OBV higher high over
    the last 20 bars -> BUY (mirror for SELL)."""
    tag = "VO - OBV Trend Confirm"
    need = max(ema_price, hh_lookback + 1) + 5
    if len(ohlc) < need:
        return None
    closes = _closes(ohlc)
    obvs = _obv_series(closes, _volumes(ohlc))
    e50 = ema(closes, ema_price)
    obv_e = ema(obvs, obv_ema)
    if not e50 or obv_e is None:
        return None
    price = closes[-1]
    obv_now = obvs[-1]
    prior_obv_hi = max(obvs[-hh_lookback - 1:-1])
    prior_obv_lo = min(obvs[-hh_lookback - 1:-1])
    if price > e50 and obv_now > obv_e and obv_now > prior_obv_hi:
        return _sig("BUY", tag, 0.60, price,
                    [f"close > EMA{ema_price}, OBV > EMA{obv_ema}(OBV), "
                     f"OBV new {hh_lookback}-bar high"])
    if price < e50 and obv_now < obv_e and obv_now < prior_obv_lo:
        return _sig("SELL", tag, 0.60, price,
                    [f"close < EMA{ema_price}, OBV < EMA{obv_ema}(OBV), "
                     f"OBV new {hh_lookback}-bar low"])
    return None


# ---------------------------------------------------------------------------
# #2 — OBV Divergence Reversal
# ---------------------------------------------------------------------------

def obv_divergence(ohlc, lookback=14, swing_window=5):
    """Price lower swing-low while OBV prints a higher low over `lookback`
    bars -> armed; trigger BUY on first close above the prior bar's high
    (mirror for SELL)."""
    tag = "VO - OBV Divergence"
    if len(ohlc) < lookback + swing_window * 2 + 2:
        return None
    closes = _closes(ohlc)
    obvs = _obv_series(closes, _volumes(ohlc))
    base = len(ohlc) - lookback
    lows = [(i, v) for i, v, _ in _swing_lows(ohlc, swing_window) if i >= base]
    highs = [(i, v) for i, v, _ in _swing_highs(ohlc, swing_window) if i >= base]
    price = closes[-1]
    if len(lows) >= 2:
        (i1, l1), (i2, l2) = lows[-2], lows[-1]
        if l2 < l1 and obvs[i2] > obvs[i1] and price > ohlc[-2]["high"]:
            return _sig("BUY", tag, 0.62, price,
                        [f"price LL {l1:.6g}->{l2:.6g} vs OBV HL "
                         f"{obvs[i1]:.0f}->{obvs[i2]:.0f}; close over prior high"])
    if len(highs) >= 2:
        (i1, h1), (i2, h2) = highs[-2], highs[-1]
        if h2 > h1 and obvs[i2] < obvs[i1] and price < ohlc[-2]["low"]:
            return _sig("SELL", tag, 0.62, price,
                        [f"price HH {h1:.6g}->{h2:.6g} vs OBV LH "
                         f"{obvs[i1]:.0f}->{obvs[i2]:.0f}; close under prior low"])
    return None


# ---------------------------------------------------------------------------
# #3 — OBV Breakout (Accumulation Lead)
# ---------------------------------------------------------------------------

def obv_breakout_lead(ohlc, lookback=20):
    """OBV closes above its own 20-bar high while price close is still BELOW
    its 20-bar high (volume leads price) -> BUY (mirror for SELL)."""
    tag = "VO - OBV Breakout Lead"
    if len(ohlc) < lookback + 2:
        return None
    closes = _closes(ohlc)
    obvs = _obv_series(closes, _volumes(ohlc))
    price = closes[-1]
    obv_hi = max(obvs[-lookback - 1:-1])
    obv_lo = min(obvs[-lookback - 1:-1])
    px_hi = max(closes[-lookback - 1:-1])
    px_lo = min(closes[-lookback - 1:-1])
    if obvs[-1] > obv_hi and price < px_hi:
        return _sig("BUY", tag, 0.58, price,
                    [f"OBV over {lookback}-bar high while price still below "
                     f"its {lookback}-bar high (accumulation lead)"])
    if obvs[-1] < obv_lo and price > px_lo:
        return _sig("SELL", tag, 0.58, price,
                    [f"OBV under {lookback}-bar low while price still above "
                     f"its {lookback}-bar low (distribution lead)"])
    return None


# ---------------------------------------------------------------------------
# #4 — VWAP Mean Reversion (session = UTC day)
# ---------------------------------------------------------------------------

def vwap_mean_reversion(ohlc, dev_mult=2.0, rsi_os=30, rsi_ob=70):
    """close < session_VWAP - 2.0x sigma AND RSI(14) < 30 -> BUY, target the
    session VWAP (mirror for SELL)."""
    tag = "VO - VWAP Mean Reversion"
    if len(ohlc) < 20:
        return None
    session = _session_bars(ohlc)
    if len(session) < 5:
        return None
    vwap, sd = _session_vwap(session)
    if vwap is None or not sd:
        return None
    closes = _closes(ohlc)
    price = closes[-1]
    r = rsi(closes)
    if price < vwap - dev_mult * sd and r < rsi_os:
        return _sig("BUY", tag, 0.60, price,
                    [f"close {dev_mult}sigma under session VWAP {vwap:.6g}, "
                     f"RSI {r:.0f} < {rsi_os}; target VWAP"])
    if price > vwap + dev_mult * sd and r > rsi_ob:
        return _sig("SELL", tag, 0.60, price,
                    [f"close {dev_mult}sigma over session VWAP {vwap:.6g}, "
                     f"RSI {r:.0f} > {rsi_ob}; target VWAP"])
    return None


# ---------------------------------------------------------------------------
# #5 — VWAP Trend Pullback
# ---------------------------------------------------------------------------

def vwap_trend_pullback(ohlc, slope_bars=10):
    """Uptrend: close > session VWAP with VWAP slope positive over 10 bars;
    a bar whose low pierces VWAP but closes back above it -> BUY (mirror)."""
    tag = "VO - VWAP Pullback"
    if len(ohlc) < slope_bars + 20:
        return None
    session = _session_bars(ohlc)
    if len(session) < slope_bars + 2:
        return None
    vwap, _ = _session_vwap(session)
    vwap_prev, _ = _session_vwap(session[:-slope_bars])
    if vwap is None or vwap_prev is None:
        return None
    last = ohlc[-1]
    price = last["close"]
    if vwap > vwap_prev and price > vwap and last["low"] <= vwap:
        return _sig("BUY", tag, 0.58, price,
                    [f"VWAP rising over {slope_bars} bars; low pierced VWAP "
                     f"{vwap:.6g} but closed back above"])
    if vwap < vwap_prev and price < vwap and last["high"] >= vwap:
        return _sig("SELL", tag, 0.58, price,
                    [f"VWAP falling over {slope_bars} bars; high pierced VWAP "
                     f"{vwap:.6g} but closed back below"])
    return None


# ---------------------------------------------------------------------------
# #6 — Anchored VWAP (AVWAP) Reclaim, anchor = extreme of last 50 bars
# ---------------------------------------------------------------------------

def avwap_reclaim(ohlc, anchor_lookback=50, reclaim_bars=10):
    """Anchor VWAP at the lowest low of the last 50 bars. BUY when price has
    closed below AVWAP for >=10 consecutive bars then closes back above
    (mirror: anchored at highest high, loss after >=10 bars above)."""
    tag = "VO - AVWAP Reclaim"
    if len(ohlc) < anchor_lookback + reclaim_bars + 1:
        return None
    lows = _lows(ohlc)
    highs = _highs(ohlc)
    closes = _closes(ohlc)
    price = closes[-1]

    anchor_lo_i = min(range(len(ohlc) - anchor_lookback, len(ohlc)), key=lambda i: lows[i])
    seg_lo = ohlc[anchor_lo_i:]
    v_lo, _ = _session_vwap(seg_lo)
    below_run = 0
    for c in reversed(closes[anchor_lo_i + 1:-1]):
        if c < v_lo:
            below_run += 1
        else:
            break
    if v_lo and below_run >= reclaim_bars and price > v_lo and closes[-2] < v_lo:
        return _sig("BUY", tag, 0.60, price,
                    [f"reclaimed AVWAP {v_lo:.6g} (anchored {anchor_lookback}-bar "
                     f"low) after {below_run} closes below"])

    anchor_hi_i = max(range(len(ohlc) - anchor_lookback, len(ohlc)), key=lambda i: highs[i])
    seg_hi = ohlc[anchor_hi_i:]
    v_hi, _ = _session_vwap(seg_hi)
    above_run = 0
    for c in reversed(closes[anchor_hi_i + 1:-1]):
        if c > v_hi:
            above_run += 1
        else:
            break
    if v_hi and above_run >= reclaim_bars and price < v_hi and closes[-2] > v_hi:
        return _sig("SELL", tag, 0.60, price,
                    [f"lost AVWAP {v_hi:.6g} (anchored {anchor_lookback}-bar "
                     f"high) after {above_run} closes above"])
    return None


# ---------------------------------------------------------------------------
# #7 — VWAP Band Squeeze Breakout
# ---------------------------------------------------------------------------

def vwap_squeeze_breakout(ohlc, dev_entry=1.5, squeeze_lookback=20,
                          vol_mult=1.5):
    """Band width (2sigma around session VWAP) at its narrowest of the last
    20 bars AND close breaks above VWAP + 1.5sigma with volume >= 1.5x
    SMA(volume,20) -> BUY (mirror for SELL)."""
    tag = "VO - VWAP Squeeze Break"
    if len(ohlc) < squeeze_lookback + 25:
        return None
    session = _session_bars(ohlc)
    if len(session) < squeeze_lookback + 5:
        return None
    widths = []
    for k in range(5, len(session) + 1):
        _, sd_k = _session_vwap(session[:k])
        widths.append(4 * sd_k if sd_k else 0.0)
    vwap, sd = _session_vwap(session)
    if vwap is None or not sd or len(widths) < squeeze_lookback:
        return None
    squeeze = widths[-1] <= min(widths[-squeeze_lookback:])
    vols = _volumes(ohlc)
    v_sma = sma(vols, 20)
    if not v_sma:
        return None
    price = _closes(ohlc)[-1]
    rvol_ok = vols[-1] >= vol_mult * v_sma
    if squeeze and rvol_ok and price > vwap + dev_entry * sd:
        return _sig("BUY", tag, 0.62, price,
                    [f"narrowest band of {squeeze_lookback} bars; close above "
                     f"VWAP+{dev_entry}sigma on {vols[-1] / v_sma:.1f}x volume"])
    if squeeze and rvol_ok and price < vwap - dev_entry * sd:
        return _sig("SELL", tag, 0.62, price,
                    [f"narrowest band of {squeeze_lookback} bars; close below "
                     f"VWAP-{dev_entry}sigma on {vols[-1] / v_sma:.1f}x volume"])
    return None


# ---------------------------------------------------------------------------
# #8 — Volume Profile POC Retest (100-bar, 24-row profile)
# ---------------------------------------------------------------------------

def poc_retest(ohlc, profile_lookback=100, rows=24):
    """Price above the 100-bar profile POC, pulls back into the POC row and
    closes back above POC -> BUY (mirror for SELL)."""
    tag = "VO - POC Retest"
    if len(ohlc) < profile_lookback + 2:
        return None
    prof = _volume_profile(ohlc[-profile_lookback - 1:-1], rows=rows)
    if not prof:
        return None
    poc = prof["poc_price"]
    step = prof["step"]
    row_top = poc + step / 2
    row_bot = poc - step / 2
    last = ohlc[-1]
    price = last["close"]
    prev = ohlc[-2]
    # "price above POC, pulls back into the POC row": the prior bar must have
    # traded entirely above the row, else a flat tape retests the POC forever.
    if prev["low"] > row_top and last["low"] <= row_top and price > row_top:
        return _sig("BUY", tag, 0.58, price,
                    [f"pullback into POC {poc:.6g} row held; closed back above "
                     f"(target value-area high {prof['vah']:.6g})"])
    if prev["high"] < row_bot and last["high"] >= row_bot and price < row_bot:
        return _sig("SELL", tag, 0.58, price,
                    [f"rally into POC {poc:.6g} row rejected; closed back below "
                     f"(target value-area low {prof['val']:.6g})"])
    return None


# ---------------------------------------------------------------------------
# #9 — Value-Area Breakout (80% Rule variant; profile on prior UTC day)
# ---------------------------------------------------------------------------

def value_area_breakout(ohlc, vol_mult=1.5, va_pct=0.70):
    """Current session opens INSIDE prior UTC day's 70% value area, then
    closes above VAH with volume >= 1.5x SMA(volume,20) -> BUY (mirror)."""
    tag = "VO - Value Area Break"
    if len(ohlc) < 40:
        return None
    from datetime import timedelta
    last_day = datetime.fromtimestamp(ohlc[-1]["ts"], tz=timezone.utc).date()
    prev_day = last_day - timedelta(days=1)
    prior = [b for b in ohlc
             if datetime.fromtimestamp(b["ts"], tz=timezone.utc).date() == prev_day]
    today = [b for b in ohlc
             if datetime.fromtimestamp(b["ts"], tz=timezone.utc).date() == last_day]
    if len(prior) < 20 or len(today) < 2:
        return None
    prof = _volume_profile(prior, va_pct=va_pct)
    if not prof:
        return None
    open_today = today[0]["open"]
    if not (prof["val"] <= open_today <= prof["vah"]):
        return None
    vols = _volumes(ohlc)
    v_sma = sma(vols, 20)
    if not v_sma or vols[-1] < vol_mult * v_sma:
        return None
    price = _closes(ohlc)[-1]
    if price > prof["vah"]:
        return _sig("BUY", tag, 0.60, price,
                    [f"opened inside prior VA ({prof['val']:.6g}-"
                     f"{prof['vah']:.6g}), closed above VAH on "
                     f"{vols[-1] / v_sma:.1f}x volume; target VAH + VA height"])
    if price < prof["val"]:
        return _sig("SELL", tag, 0.60, price,
                    [f"opened inside prior VA ({prof['val']:.6g}-"
                     f"{prof['vah']:.6g}), closed below VAL on "
                     f"{vols[-1] / v_sma:.1f}x volume; target VAL - VA height"])
    return None


# ---------------------------------------------------------------------------
# #10 — Low-Volume-Node (LVN) Vacuum
# ---------------------------------------------------------------------------

def lvn_vacuum(ohlc, profile_lookback=100, rows=24, lvn_mult=0.5):
    """On the 100-bar profile, LVN rows (volume < 0.5x mean row volume) sit
    between price and the next HVN. BUY when the close enters an LVN row from
    below -> target the HVN above (mirror for SELL)."""
    tag = "VO - LVN Vacuum"
    if len(ohlc) < profile_lookback + 2:
        return None
    prof = _volume_profile(ohlc[-profile_lookback - 1:-1], rows=rows)
    if not prof or prof["mean_row"] <= 0:
        return None
    step = prof["step"]
    lo = prof["lo"]
    prev_close = ohlc[-2]["close"]
    price = _closes(ohlc)[-1]
    k_now = min(rows - 1, max(0, int((price - lo) / step)))
    k_prev = min(rows - 1, max(0, int((prev_close - lo) / step)))
    lvn = [k for k, v in enumerate(prof["vols"]) if v < lvn_mult * prof["mean_row"]]
    hvn = [k for k, v in enumerate(prof["vols"]) if v > prof["mean_row"]]
    if not lvn or not hvn:
        return None
    if k_now in lvn and k_prev < k_now:
        above = [k for k in hvn if k > k_now]
        if above:
            tgt = lo + (min(above) + 0.5) * step
            return _sig("BUY", tag, 0.55, price,
                        [f"entered LVN row {k_now} from below; vacuum target "
                         f"HVN {tgt:.6g}"])
    if k_now in lvn and k_prev > k_now:
        below = [k for k in hvn if k < k_now]
        if below:
            tgt = lo + (max(below) + 0.5) * step
            return _sig("SELL", tag, 0.55, price,
                        [f"entered LVN row {k_now} from above; vacuum target "
                         f"HVN {tgt:.6g}"])
    return None


# ---------------------------------------------------------------------------
# #11 — CVD Divergence, OHLC-proxy CVD (catalog: proxy still beats nothing;
# true CVD needs the trades feed — see SKIPPED_DATA_NEEDS note in docstring)
# ---------------------------------------------------------------------------

def cvd_proxy_divergence(ohlc, lookback=20):
    """Over lookback=20 bars price makes a lower low while proxy CVD makes a
    higher low (seller exhaustion) -> armed; BUY on close above prior bar's
    high (mirror for SELL)."""
    tag = "VO - CVD Proxy Divergence"
    if len(ohlc) < lookback + 2:
        return None
    closes = _closes(ohlc)
    cvd = _proxy_cvd_series(ohlc)
    window = ohlc[-lookback - 1:-1]
    cvd_w = cvd[-lookback - 1:-1]
    mid = len(window) // 2
    p1_lo = min(b["low"] for b in window[:mid])
    p2_lo = min(b["low"] for b in window[mid:])
    p1_hi = max(b["high"] for b in window[:mid])
    p2_hi = max(b["high"] for b in window[mid:])
    price = closes[-1]
    if (p2_lo < p1_lo and min(cvd_w[mid:]) > min(cvd_w[:mid])
            and price > ohlc[-2]["high"]):
        return _sig("BUY", tag, 0.57, price,
                    [f"proxy CVD higher low vs price lower low over "
                     f"{lookback} bars; close over prior high"])
    if (p2_hi > p1_hi and max(cvd_w[mid:]) < max(cvd_w[:mid])
            and price < ohlc[-2]["low"]):
        return _sig("SELL", tag, 0.57, price,
                    [f"proxy CVD lower high vs price higher high over "
                     f"{lookback} bars; close under prior low"])
    return None


# ---------------------------------------------------------------------------
# #12 — CVD Trend Confirmation (OHLC proxy)
# ---------------------------------------------------------------------------

def cvd_proxy_trend(ohlc, hh_lookback=20, ema_period=20):
    """Price higher highs over 20 bars AND proxy CVD at a new 20-bar high AND
    price pulls back to close >= EMA(close,20) -> BUY (mirror for SELL)."""
    tag = "VO - CVD Proxy Trend"
    if len(ohlc) < hh_lookback + ema_period + 2:
        return None
    closes = _closes(ohlc)
    cvd = _proxy_cvd_series(ohlc)
    e20 = ema(closes, ema_period)
    if not e20:
        return None
    price = closes[-1]
    px_prior_hi = max(_highs(ohlc)[-hh_lookback - 1:-1])
    px_prior_lo = min(_lows(ohlc)[-hh_lookback - 1:-1])
    cvd_prior_hi = max(cvd[-hh_lookback - 1:-1])
    cvd_prior_lo = min(cvd[-hh_lookback - 1:-1])
    uptrend = ohlc[-1]["high"] > px_prior_hi or closes[-1] > closes[-ema_period]
    dntrend = ohlc[-1]["low"] < px_prior_lo or closes[-1] < closes[-ema_period]
    if uptrend and cvd[-1] > cvd_prior_hi and price >= e20:
        return _sig("BUY", tag, 0.58, price,
                    [f"uptrend + proxy CVD new {hh_lookback}-bar high; "
                     f"pullback held EMA{ema_period} {e20:.6g}"])
    if dntrend and cvd[-1] < cvd_prior_lo and price <= e20:
        return _sig("SELL", tag, 0.58, price,
                    [f"downtrend + proxy CVD new {hh_lookback}-bar low; "
                     f"rally capped by EMA{ema_period} {e20:.6g}"])
    return None


# ---------------------------------------------------------------------------
# #20 — RVOL Breakout Confirmation
# ---------------------------------------------------------------------------

def rvol_breakout(ohlc, break_n=20, vol_mult=2.0, close_pct=0.70):
    """Close breaks above the 20-bar high AND bar volume >= 2.0x
    SMA(volume,20) AND close in the top 30% of the bar's range -> BUY
    (mirror for SELL)."""
    tag = "VO - RVOL Breakout"
    if len(ohlc) < break_n + 21:
        return None
    vols = _volumes(ohlc)
    v_sma = sma(vols, 20)
    if not v_sma or vols[-1] < vol_mult * v_sma:
        return None
    last = ohlc[-1]
    rng = last["high"] - last["low"]
    if rng <= 0:
        return None
    loc = (last["close"] - last["low"]) / rng
    prior_hi = max(_highs(ohlc)[-break_n - 1:-1])
    prior_lo = min(_lows(ohlc)[-break_n - 1:-1])
    price = last["close"]
    if price > prior_hi and loc >= close_pct:
        return _sig("BUY", tag, 0.63, price,
                    [f"close over {break_n}-bar high {prior_hi:.6g} on "
                     f"{vols[-1] / v_sma:.1f}x volume, close at {loc:.0%} of range"])
    if price < prior_lo and loc <= 1 - close_pct:
        return _sig("SELL", tag, 0.63, price,
                    [f"close under {break_n}-bar low {prior_lo:.6g} on "
                     f"{vols[-1] / v_sma:.1f}x volume, close at {loc:.0%} of range"])
    return None


# ---------------------------------------------------------------------------
# #21 — Volume Dry-Up Pullback (Wyckoff-flavored)
# ---------------------------------------------------------------------------

def volume_dryup_pullback(ohlc, ema_period=50, pullback_min=3, dry_mult=0.6):
    """Uptrend (close > EMA50); pullback of >=3 consecutive down bars with
    volume declining each bar AND final bar volume < 0.6x SMA(volume,20);
    BUY on break of the pullback high (mirror for SELL)."""
    tag = "VO - Volume Dry-Up"
    if len(ohlc) < ema_period + pullback_min + 2:
        return None
    closes = _closes(ohlc)
    vols = _volumes(ohlc)
    e50 = ema(closes, ema_period)
    v_sma = sma(vols, 20)
    if not e50 or not v_sma:
        return None
    price = closes[-1]
    down_run = 0
    for i in range(len(ohlc) - 2, 0, -1):
        if closes[i] < closes[i - 1]:
            down_run += 1
        else:
            break
    up_run = 0
    for i in range(len(ohlc) - 2, 0, -1):
        if closes[i] > closes[i - 1]:
            up_run += 1
        else:
            break
    if price > e50 and down_run >= pullback_min:
        leg = vols[-down_run - 1:-1]
        if (all(leg[k] < leg[k - 1] for k in range(1, len(leg)))
                and leg[-1] < dry_mult * v_sma
                and price > max(_highs(ohlc)[-down_run - 1:-1])):
            return _sig("BUY", tag, 0.60, price,
                        [f"{down_run}-bar pullback on declining volume, last "
                         f"{leg[-1] / v_sma:.1%} of SMA20; broke pullback high"])
    if price < e50 and up_run >= pullback_min:
        leg = vols[-up_run - 1:-1]
        if (all(leg[k] < leg[k - 1] for k in range(1, len(leg)))
                and leg[-1] < dry_mult * v_sma
                and price < min(_lows(ohlc)[-up_run - 1:-1])):
            return _sig("SELL", tag, 0.60, price,
                        [f"{up_run}-bar rally on declining volume, last "
                         f"{leg[-1] / v_sma:.1%} of SMA20; broke pullback low"])
    return None


# ---------------------------------------------------------------------------
# #22 — Climactic Volume Reversal (Wyckoff SC/BC)
# ---------------------------------------------------------------------------

def climactic_reversal(ohlc, prior_bars=5, vol_mult=3.0, range_mult=2.0,
                       close_zone=0.40):
    """After >=5 consecutive down-closed bars, a bar prints volume > 3x
    SMA(volume,20) AND range > 2x ATR(14) AND closes in its top 40% (selling
    climax); BUY when the next bar holds above the climax bar's midpoint
    (mirror for SELL)."""
    tag = "VO - Climactic Reversal"
    if len(ohlc) < prior_bars + 22:
        return None
    closes = _closes(ohlc)
    vols = _volumes(ohlc)
    highs = _highs(ohlc)
    lows = _lows(ohlc)
    atr_v = atr(highs, lows, closes)
    v_sma = sma(vols[:-1], 20)
    if not atr_v or not v_sma:
        return None
    climax = ohlc[-2]
    last = ohlc[-1]
    down_run = all(closes[-prior_bars - 2 + k] < closes[-prior_bars - 3 + k]
                   for k in range(prior_bars))
    up_run = all(closes[-prior_bars - 2 + k] > closes[-prior_bars - 3 + k]
                 for k in range(prior_bars))
    rng = climax["high"] - climax["low"]
    if rng <= 0:
        return None
    loc = (climax["close"] - climax["low"]) / rng
    mid = (climax["high"] + climax["low"]) / 2
    vol_ok = climax["volume"] > vol_mult * v_sma
    range_ok = rng > range_mult * atr_v
    if down_run and vol_ok and range_ok and loc >= 1 - close_zone and last["close"] > mid:
        return _sig("BUY", tag, 0.62, last["close"],
                    [f"selling climax after {prior_bars} down bars: "
                     f"{climax['volume'] / v_sma:.1f}x volume, range "
                     f"{rng / atr_v:.1f}x ATR, closed top {loc:.0%}; held midpoint"])
    if up_run and vol_ok and range_ok and loc <= close_zone and last["close"] < mid:
        return _sig("SELL", tag, 0.62, last["close"],
                    [f"buying climax after {prior_bars} up bars: "
                     f"{climax['volume'] / v_sma:.1f}x volume, range "
                     f"{rng / atr_v:.1f}x ATR, closed bottom {loc:.0%}; lost midpoint"])
    return None


# ---------------------------------------------------------------------------
# #23 — MFI Extremes + Divergence (uses core.indicators.mfi)
# ---------------------------------------------------------------------------

def _mfi_series(ohlc, period=14):
    """MFI(14) at each bar index (None before warmup) for cross detection."""
    highs, lows, closes, vols = _highs(ohlc), _lows(ohlc), _closes(ohlc), _volumes(ohlc)
    out = [None] * len(ohlc)
    for i in range(period + 1, len(ohlc) + 1):
        out[i - 1] = mfi(highs[:i], lows[:i], closes[:i], vols[:i], period)
    return out


def mfi_extremes(ohlc, period=14, os_=20, ob=80):
    """MFI(14) crosses back up through 20 after being below it -> BUY
    (mirror: crosses down through 80 -> SELL)."""
    tag = "VO - MFI Extremes"
    if len(ohlc) < period + 8:
        return None
    series = _mfi_series(ohlc, period)
    cur, prev = series[-1], series[-2]
    if cur is None or prev is None:
        return None
    price = _closes(ohlc)[-1]
    recent = [v for v in series[-8:] if v is not None]
    if prev < os_ <= cur or (cur > os_ and min(recent) < os_ and prev <= os_):
        return _sig("BUY", tag, 0.58, price,
                    [f"MFI({period}) crossed up through {os_} ({prev:.0f} -> {cur:.0f})"])
    if prev > ob >= cur or (cur < ob and max(recent) > ob and prev >= ob):
        return _sig("SELL", tag, 0.58, price,
                    [f"MFI({period}) crossed down through {ob} ({prev:.0f} -> {cur:.0f})"])
    return None


# ---------------------------------------------------------------------------
# #24 — Chaikin A/D Divergence
# ---------------------------------------------------------------------------

def _adl_series(ohlc):
    """Chaikin Accumulation/Distribution line; CLV guarded on doji bars."""
    out = [0.0]
    for b in ohlc:
        rng = b["high"] - b["low"]
        clv = (((b["close"] - b["low"]) - (b["high"] - b["close"])) / rng
               if rng > 0 else 0.0)
        out.append(out[-1] + clv * b["volume"])
    return out[1:]


def chaikin_adl_divergence(ohlc, lookback=20, ema_period=20):
    """Price lower swing-low while the Chaikin A/D line prints a higher low
    (accumulation under weakness) -> armed; BUY on close above EMA(close,20)
    (mirror for SELL)."""
    tag = "VO - Chaikin ADL Divergence"
    if len(ohlc) < lookback + ema_period + 2:
        return None
    closes = _closes(ohlc)
    adl = _adl_series(ohlc)
    e20 = ema(closes, ema_period)
    if not e20:
        return None
    window = ohlc[-lookback - 1:-1]
    adl_w = adl[-lookback - 1:-1]
    mid = len(window) // 2
    p1_lo = min(b["low"] for b in window[:mid])
    p2_lo = min(b["low"] for b in window[mid:])
    p1_hi = max(b["high"] for b in window[:mid])
    p2_hi = max(b["high"] for b in window[mid:])
    price = closes[-1]
    if p2_lo < p1_lo and min(adl_w[mid:]) > min(adl_w[:mid]) and price > e20:
        return _sig("BUY", tag, 0.57, price,
                    [f"ADL higher low vs price lower low over {lookback} bars; "
                     f"close above EMA{ema_period}"])
    if p2_hi > p1_hi and max(adl_w[mid:]) < max(adl_w[:mid]) and price < e20:
        return _sig("SELL", tag, 0.57, price,
                    [f"ADL lower high vs price higher high over {lookback} bars; "
                     f"close below EMA{ema_period}"])
    return None


# ---------------------------------------------------------------------------
# #25 — Chaikin Money Flow (CMF) Filter
# ---------------------------------------------------------------------------

def _cmf_series(ohlc, period=20):
    mfv = []
    for b in ohlc:
        rng = b["high"] - b["low"]
        clv = (((b["close"] - b["low"]) - (b["high"] - b["close"])) / rng
               if rng > 0 else 0.0)
        mfv.append(clv * b["volume"])
    vols = _volumes(ohlc)
    out = [None] * len(ohlc)
    for i in range(period, len(ohlc) + 1):
        v_sum = sum(vols[i - period:i])
        out[i - 1] = sum(mfv[i - period:i]) / v_sum if v_sum > 0 else 0.0
    return out


def cmf_filter(ohlc, period=20, thr=0.05, ema_period=50):
    """CMF(20) crosses above +0.05 while close > EMA(close,50) -> BUY
    (mirror: crosses below -0.05 while close < EMA50 -> SELL)."""
    tag = "VO - CMF Filter"
    if len(ohlc) < ema_period + period + 2:
        return None
    closes = _closes(ohlc)
    e50 = ema(closes, ema_period)
    series = _cmf_series(ohlc, period)
    cur, prev = series[-1], series[-2]
    if not e50 or cur is None or prev is None:
        return None
    price = closes[-1]
    if prev <= thr < cur and price > e50:
        return _sig("BUY", tag, 0.58, price,
                    [f"CMF({period}) crossed +{thr} ({prev:+.3f} -> {cur:+.3f}) "
                     f"with close > EMA{ema_period}"])
    if prev >= -thr > cur and price < e50:
        return _sig("SELL", tag, 0.58, price,
                    [f"CMF({period}) crossed -{thr} ({prev:+.3f} -> {cur:+.3f}) "
                     f"with close < EMA{ema_period}"])
    return None


# ---------------------------------------------------------------------------
# #26 — Ease of Movement (EMV) Zero-Line
# ---------------------------------------------------------------------------

def _emv_series(ohlc, scale=1e8):
    """Raw 1-bar EMV in the scaled box-ratio form (catalog #26)."""
    out = [0.0]
    for i in range(1, len(ohlc)):
        mid_move = ((ohlc[i]["high"] + ohlc[i]["low"]) / 2
                    - (ohlc[i - 1]["high"] + ohlc[i - 1]["low"]) / 2)
        rng = ohlc[i]["high"] - ohlc[i]["low"]
        box = (ohlc[i]["volume"] / scale) / rng if rng > 0 else 0.0
        out.append(mid_move / box if box > 0 else 0.0)
    return out


def emv_zero_cross(ohlc, smooth=14, ema_period=50):
    """EMV(14) (SMA-14 smoothed) crosses above zero while close > EMA50 ->
    BUY (mirror for SELL). 4h/1d only per catalog — too noisy below 4h."""
    tag = "VO - EMV Zero Cross"
    if len(ohlc) < ema_period + smooth + 2:
        return None
    closes = _closes(ohlc)
    e50 = ema(closes, ema_period)
    emv = _emv_series(ohlc)
    if not e50 or len(emv) < smooth + 1:
        return None
    cur = sum(emv[-smooth:]) / smooth
    prev = sum(emv[-smooth - 1:-1]) / smooth
    price = closes[-1]
    if prev <= 0 < cur and price > e50:
        return _sig("BUY", tag, 0.55, price,
                    [f"EMV({smooth}) crossed above zero with close > EMA{ema_period}"])
    if prev >= 0 > cur and price < e50:
        return _sig("SELL", tag, 0.55, price,
                    [f"EMV({smooth}) crossed below zero with close < EMA{ema_period}"])
    return None


# ---------------------------------------------------------------------------
# #27 — Elder Force Index Pullback
# ---------------------------------------------------------------------------

def force_index_pullback(ohlc, fi_fast=2, fi_trend=13, ema_trend=50):
    """Trend up (EMA(close,13) rising AND close > EMA50); 2-period Force
    Index dips below zero on the signal bar and price breaks that bar's high
    -> BUY (mirror for SELL). FI = (close_t - close_t-1) x volume."""
    tag = "VO - Force Index Pullback"
    if len(ohlc) < ema_trend + fi_trend + 3:
        return None
    closes = _closes(ohlc)
    vols = _volumes(ohlc)
    fi_raw = [0.0] + [(closes[i] - closes[i - 1]) * vols[i]
                      for i in range(1, len(ohlc))]
    fi2 = ema_all(fi_raw, fi_fast)
    e13 = ema_all(closes, fi_trend)
    e50 = ema(closes, ema_trend)
    if len(fi2) < 2 or len(e13) < 2 or not e50:
        return None
    price = closes[-1]
    e13_up = e13[-1] > e13[-2]
    e13_dn = e13[-1] < e13[-2]
    dipped = fi2[-2] < 0
    popped = fi2[-2] > 0
    if e13_up and price > e50 and dipped and price > ohlc[-2]["high"]:
        return _sig("BUY", tag, 0.58, price,
                    [f"uptrend; FI({fi_fast}) dipped below zero (pullback) and "
                     f"price broke the signal bar high"])
    if e13_dn and price < e50 and popped and price < ohlc[-2]["low"]:
        return _sig("SELL", tag, 0.58, price,
                    [f"downtrend; FI({fi_fast}) popped above zero (rally) and "
                     f"price broke the signal bar low"])
    return None


# ---------------------------------------------------------------------------
# #28 — Klinger Volume Oscillator Cross
# ---------------------------------------------------------------------------

def _kvo_series(ohlc, fast=34, slow=55, sig=13):
    """KVO = EMA(fast) - EMA(slow) of volume force; signal = EMA(sig) of KVO.
    Trend flag = sign of HLC-sum change (held when flat). Also returns the
    trend-flag series — the catalog warns the flag needs care: on chop the
    flag flips every bar and KVO/signal crosses are pure whipsaw, so callers
    should demand trend persistence before trusting a cross."""
    if len(ohlc) < slow + sig + 2:
        return None, None, None
    hlc = [b["high"] + b["low"] + b["close"] for b in ohlc]
    vf = [0.0]
    trends = [1]
    trend = 1
    cm = 0.0
    for i in range(1, len(ohlc)):
        if hlc[i] > hlc[i - 1]:
            new_trend = 1
        elif hlc[i] < hlc[i - 1]:
            new_trend = -1
        else:
            new_trend = trend
        dm = ohlc[i]["high"] - ohlc[i]["low"]
        cm = cm + dm if new_trend == trend else dm
        trend = new_trend
        trends.append(trend)
        ratio = abs(2 * (dm / cm) - 1) if cm > 0 else 0.0
        vf.append(ohlc[i]["volume"] * ratio * trend * 100.0)
    ef = ema_all(vf, fast)
    es = ema_all(vf, slow)
    off = len(ef) - len(es)
    kvo = [ef[i + off] - es[i] for i in range(len(es))]
    signal = ema_all(kvo, sig)
    off2 = len(kvo) - len(signal)
    kvo = kvo[off2:]
    trends = trends[-len(signal):]
    return kvo, signal, trends


def klinger_cross(ohlc, fast=34, slow=55, sig=13, ema_period=50,
                  min_trend_bars=3):
    """KVO(34,55) crosses above its 13-period signal EMA while close >
    EMA(close,50) -> BUY (mirror for SELL). The HLC trend flag must have
    held the cross direction for >=3 bars — otherwise a choppy tape flips
    the flag every bar and the cross is zero-line whipsaw, not volume force."""
    tag = "VO - Klinger Cross"
    if len(ohlc) < slow + sig + ema_period + 2:
        return None
    closes = _closes(ohlc)
    e50 = ema(closes, ema_period)
    kvo, signal, trends = _kvo_series(ohlc, fast, slow, sig)
    if not e50 or not kvo or not signal or len(kvo) < 2:
        return None

    def _trend_run(val):
        run = 0
        for t in reversed(trends):
            if t == val:
                run += 1
            else:
                break
        return run
    price = closes[-1]
    if (kvo[-2] <= signal[-2] and kvo[-1] > signal[-1] and price > e50
            and _trend_run(1) >= min_trend_bars):
        return _sig("BUY", tag, 0.55, price,
                    [f"KVO({fast},{slow}) crossed above signal({sig}) with "
                     f"close > EMA{ema_period}"])
    if (kvo[-2] >= signal[-2] and kvo[-1] < signal[-1] and price < e50
            and _trend_run(-1) >= min_trend_bars):
        return _sig("SELL", tag, 0.55, price,
                    [f"KVO({fast},{slow}) crossed below signal({sig}) with "
                     f"close < EMA{ema_period}"])
    return None


# ---------------------------------------------------------------------------
# registry (same shape as core.strategies.ALL_STRATEGIES)
# ---------------------------------------------------------------------------

STRATEGIES = [
    ("VO - OBV Trend Confirm", obv_trend_confirm),
    ("VO - OBV Divergence", obv_divergence),
    ("VO - OBV Breakout Lead", obv_breakout_lead),
    ("VO - VWAP Mean Reversion", vwap_mean_reversion),
    ("VO - VWAP Pullback", vwap_trend_pullback),
    ("VO - AVWAP Reclaim", avwap_reclaim),
    ("VO - VWAP Squeeze Break", vwap_squeeze_breakout),
    ("VO - POC Retest", poc_retest),
    ("VO - Value Area Break", value_area_breakout),
    ("VO - LVN Vacuum", lvn_vacuum),
    ("VO - CVD Proxy Divergence", cvd_proxy_divergence),
    ("VO - CVD Proxy Trend", cvd_proxy_trend),
    ("VO - RVOL Breakout", rvol_breakout),
    ("VO - Volume Dry-Up", volume_dryup_pullback),
    ("VO - Climactic Reversal", climactic_reversal),
    ("VO - MFI Extremes", mfi_extremes),
    ("VO - Chaikin ADL Divergence", chaikin_adl_divergence),
    ("VO - CMF Filter", cmf_filter),
    ("VO - EMV Zero Cross", emv_zero_cross),
    ("VO - Force Index Pullback", force_index_pullback),
    ("VO - Klinger Cross", klinger_cross),
]

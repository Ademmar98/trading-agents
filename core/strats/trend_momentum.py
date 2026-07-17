"""Trend & Momentum strategy family — per-tag signal functions.

Implements the 29-strategy catalog in research/trend_momentum.md. Every
function takes a list of closed OHLC(V) candle dicts
({"open","high","low","close","volume","ts",...}, last bar CLOSED) and
returns None or {"action": "BUY"/"SELL", "confidence": 0..1,
"reasons": [str], "strategy": <unique tag>} so per-strategy stats stay
attributable (same contract as core/scalp15.py and core/swing.py).

House cross convention (catalog "Conventions", matching
core/strategies.py detect_sma_crossover): "cross above" = value at bar -3
<= comparator at bar -3 AND value at last closed bar > comparator.

Stateful catalog entries are implemented STATELESSLY by recomputing the
state from the passed OHLC window (see notes on each):
- Turtle S1 last-breakout filter: the window is replayed with the
  published exit rules (10-bar Donchian exit / 2*N stop) to determine
  whether the previous same-direction breakout was profitable.
- Mass Index two-stage bulge: the MI series is scanned for the
  "above 27 -> back below 26.5" sequence; a signal fires only when the
  drop below 26.5 completes on the last closed bar.
- Dual Thrust session anchor: sessions are re-derived from candle "ts"
  (UTC days) on every call; Range uses COMPLETED days only. Candles
  without "ts" return None (documented limitation).
"""
import logging
from math import sqrt

_log = logging.getLogger("strats.trend_momentum")

MIN_BARS = 60


# ---------------------------------------------------------------- helpers

def _closes(ohlc):
    return [c["close"] for c in ohlc]


def _highs(ohlc):
    return [c["high"] for c in ohlc]


def _lows(ohlc):
    return [c["low"] for c in ohlc]


def _vols(ohlc):
    return [c.get("volume", 0.0) for c in ohlc]


def _sig(tag, action, confidence, reasons):
    return {"action": action, "confidence": confidence,
            "reasons": reasons, "strategy": tag}


def _crossed_up(a, b):
    """House convention: a was <= b three bars back, a > b now."""
    return a[-3] <= b[-3] and a[-1] > b[-1]


def _crossed_down(a, b):
    return a[-3] >= b[-3] and a[-1] < b[-1]


def _ema_series(vals, period):
    """EMA seeded with the SMA of the first `period` values (repo style)."""
    if len(vals) < period:
        return [None] * len(vals)
    k = 2 / (period + 1)
    out = [None] * (period - 1)
    e = sum(vals[:period]) / period
    out.append(e)
    for v in vals[period:]:
        e = v * k + e * (1 - k)
        out.append(e)
    return out


def _sma_series(vals, period):
    out = [None] * len(vals)
    if len(vals) < period:
        return out
    run = sum(vals[:period])
    out[period - 1] = run / period
    for i in range(period, len(vals)):
        run += vals[i] - vals[i - period]
        out[i] = run / period
    return out


def _wma_series(vals, period):
    out = [None] * len(vals)
    den = period * (period + 1) / 2.0
    for i in range(period - 1, len(vals)):
        num = 0.0
        for j in range(period):
            num += vals[i - j] * (period - j)
        out[i] = num / den
    return out


def _hma_series(vals, period):
    """Hull MA: WMA(2*WMA(n/2) - WMA(n), round(sqrt(n)))."""
    half = max(int(period / 2), 1)
    root = max(int(round(sqrt(period))), 1)
    w_half = _wma_series(vals, half)
    w_full = _wma_series(vals, period)
    raw = []
    for i in range(len(vals)):
        if w_half[i] is None or w_full[i] is None:
            raw.append(None)
        else:
            raw.append(2 * w_half[i] - w_full[i])
    base = next((i for i, v in enumerate(raw) if v is not None), len(vals))
    compact = [v for v in raw if v is not None]
    smoothed = _wma_series(compact, root)
    out = [None] * base + smoothed
    return out[: len(vals)] + [None] * max(0, len(vals) - len(out))


def _tema_series(vals, period):
    """TEMA = 3*E1 - 3*E2 + E3 (Mulloy 1994)."""
    e1 = _ema_series(vals, period)
    e1c = [v for v in e1 if v is not None]
    e2c = _ema_series(e1c, period)
    e2c = [v for v in e2c if v is not None]
    e3c = _ema_series(e2c, period)
    e3c = [v for v in e3c if v is not None]
    n = len(e3c)
    if n == 0:
        return [None] * len(vals)
    tema = [3 * e1c[-n + i] - 3 * e2c[-n + i] + e3c[i] for i in range(n)]
    pad = len(vals) - n
    return [None] * pad + tema


def _vwma_series(closes, vols, period):
    out = [None] * len(closes)
    for i in range(period - 1, len(closes)):
        pv = 0.0
        vv = 0.0
        for j in range(i - period + 1, i + 1):
            pv += closes[j] * vols[j]
            vv += vols[j]
        out[i] = pv / vv if vv > 0 else None
    return out


def _tr_list(ohlc):
    trs = [None]
    for i in range(1, len(ohlc)):
        h, l, pc = ohlc[i]["high"], ohlc[i]["low"], ohlc[i - 1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return trs


def _atr_series(ohlc, period=14):
    """Wilder-smoothed ATR, None-padded to len(ohlc)."""
    n = len(ohlc)
    out = [None] * n
    trs = _tr_list(ohlc)
    if n < period + 1:
        return out
    atr = sum(trs[1: period + 1]) / period
    out[period] = atr
    for i in range(period + 1, n):
        atr = (atr * (period - 1) + trs[i]) / period
        out[i] = atr
    return out


def _adx_series(ohlc, period=14):
    """Wilder ADX/+DI/-DI, each None-padded to len(ohlc)."""
    n = len(ohlc)
    adx_out = [None] * n
    pdi_out = [None] * n
    ndi_out = [None] * n
    if n < 2 * period + 1:
        return adx_out, pdi_out, ndi_out
    trs = [None]
    pdm = [None]
    ndm = [None]
    for i in range(1, n):
        h, l = ohlc[i]["high"], ohlc[i]["low"]
        ph, pl, pc = ohlc[i - 1]["high"], ohlc[i - 1]["low"], ohlc[i - 1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
        up = h - ph
        dn = pl - l
        pdm.append(up if (up > dn and up > 0) else 0.0)
        ndm.append(dn if (dn > up and dn > 0) else 0.0)

    def _wilder_seed(vals, idx):
        return sum(vals[idx - period + 1: idx + 1]) / period

    s_tr = _wilder_seed(trs, period)
    s_p = _wilder_seed(pdm, period)
    s_n = _wilder_seed(ndm, period)
    dxs = []

    def _dis(s_tr, s_p, s_n):
        pdi = 100 * s_p / s_tr if s_tr > 0 else 0.0
        ndi = 100 * s_n / s_tr if s_tr > 0 else 0.0
        dx = 100 * abs(pdi - ndi) / (pdi + ndi) if (pdi + ndi) > 0 else 0.0
        return pdi, ndi, dx

    pdi, ndi, dx = _dis(s_tr, s_p, s_n)
    pdi_out[period] = pdi
    ndi_out[period] = ndi
    dxs.append(dx)
    for i in range(period + 1, n):
        s_tr = (s_tr * (period - 1) + trs[i]) / period
        s_p = (s_p * (period - 1) + pdm[i]) / period
        s_n = (s_n * (period - 1) + ndm[i]) / period
        pdi, ndi, dx = _dis(s_tr, s_p, s_n)
        pdi_out[i] = pdi
        ndi_out[i] = ndi
        dxs.append(dx)
        if len(dxs) == period:
            adx_out[i] = sum(dxs) / period
        elif len(dxs) > period:
            adx_out[i] = (adx_out[i - 1] * (period - 1) + dx) / period
    return adx_out, pdi_out, ndi_out


def _macd_series(closes, fast=12, slow=26, signal=9):
    """(macd, signal, hist), each None-padded to len(closes)."""
    ef = _ema_series(closes, fast)
    es = _ema_series(closes, slow)
    macd = [None] * len(closes)
    for i in range(len(closes)):
        if ef[i] is not None and es[i] is not None:
            macd[i] = ef[i] - es[i]
    mvals = [v for v in macd if v is not None]
    sig_c = _ema_series(mvals, signal)
    base = len(closes) - len(mvals)
    sig = [None] * base + sig_c
    hist = [None] * len(closes)
    for i in range(len(closes)):
        if macd[i] is not None and sig[i] is not None:
            hist[i] = macd[i] - sig[i]
    return macd, sig, hist


def _supertrend(ohlc, period=10, mult=3.0):
    """(trend, st_line): trend +1 up / -1 down, None-padded.

    Ratchet rule honored: the upper band only moves down while in a
    downtrend, the lower band only moves up while in an uptrend.
    """
    n = len(ohlc)
    trend = [None] * n
    line = [None] * n
    atrs = _atr_series(ohlc, period)
    first = next((i for i, a in enumerate(atrs) if a is not None), None)
    if first is None:
        return trend, line
    prev_up = None
    prev_dn = None
    prev_trend = 1
    for i in range(first, n):
        hl2 = (ohlc[i]["high"] + ohlc[i]["low"]) / 2.0
        up = hl2 + mult * atrs[i]
        dn = hl2 - mult * atrs[i]
        pc = ohlc[i - 1]["close"]
        if prev_up is not None:
            up = up if (up < prev_up or pc > prev_up) else prev_up
            dn = dn if (dn > prev_dn or pc < prev_dn) else prev_dn
        if prev_trend == 1:
            t = 1 if ohlc[i]["close"] >= (prev_dn if prev_dn is not None else dn) else -1
        else:
            t = -1 if ohlc[i]["close"] <= (prev_up if prev_up is not None else up) else 1
        trend[i] = t
        line[i] = dn if t == 1 else up
        prev_up, prev_dn, prev_trend = up, dn, t
    return trend, line


def _psar(ohlc, af_start=0.02, af_step=0.02, af_max=0.20):
    """Wilder Parabolic SAR series, None-padded (first bar None)."""
    n = len(ohlc)
    out = [None] * n
    if n < 3:
        return out
    long = ohlc[1]["close"] >= ohlc[0]["close"]
    sar = ohlc[0]["low"] if long else ohlc[0]["high"]
    ep = ohlc[1]["high"] if long else ohlc[1]["low"]
    af = af_start
    out[1] = sar
    for i in range(2, n):
        sar = sar + af * (ep - sar)
        if long:
            sar = min(sar, ohlc[i - 1]["low"], ohlc[i - 2]["low"])
            if ohlc[i]["low"] < sar:
                long = False
                sar = ep
                ep = ohlc[i]["low"]
                af = af_start
            else:
                if ohlc[i]["high"] > ep:
                    ep = ohlc[i]["high"]
                    af = min(af + af_step, af_max)
        else:
            sar = max(sar, ohlc[i - 1]["high"], ohlc[i - 2]["high"])
            if ohlc[i]["high"] > sar:
                long = True
                sar = ep
                ep = ohlc[i]["high"]
                af = af_start
            else:
                if ohlc[i]["low"] < ep:
                    ep = ohlc[i]["low"]
                    af = min(af + af_step, af_max)
        out[i] = sar
    return out


def _linreg_slope_r2(vals):
    """OLS slope of vals on t=0..n-1 plus r-squared."""
    n = len(vals)
    if n < 2:
        return 0.0, 0.0
    sx = n * (n - 1) / 2.0
    sxx = n * (n - 1) * (2 * n - 1) / 6.0
    sy = sum(vals)
    sxy = sum(i * v for i, v in enumerate(vals))
    den = n * sxx - sx * sx
    if den == 0:
        return 0.0, 0.0
    slope = (n * sxy - sx * sy) / den
    mean = sy / n
    intercept = mean - slope * (sx / n)
    ss_tot = sum((v - mean) ** 2 for v in vals)
    ss_res = sum((v - (intercept + slope * i)) ** 2 for i, v in enumerate(vals))
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0
    return slope, r2


def _mass_index_series(ohlc, ema_p=9, sum_p=25):
    """Dorsey Mass Index: sum_p-sum of EMA(h-l)/EMA(EMA(h-l))."""
    n = len(ohlc)
    rng = [c["high"] - c["low"] for c in ohlc]
    e1 = _ema_series(rng, ema_p)
    e1c = [v for v in e1 if v is not None]
    e2c = _ema_series(e1c, ema_p)
    e2c = [v for v in e2c if v is not None]
    ratio = [None] * n
    m = len(e2c)
    off = n - m
    for i in range(m):
        if e2c[i]:
            ratio[off + i] = e1c[-m + i] / e2c[i]
    out = [None] * n
    rvals = [(i, v) for i, v in enumerate(ratio) if v is not None]
    for k in range(sum_p - 1, len(rvals)):
        out[rvals[k][0]] = sum(v for _, v in rvals[k - sum_p + 1: k + 1])
    return out


def _trix_series(closes, length=15):
    """TRIX = 100 * 1-bar ROC of the triple-smoothed EMA."""
    e1 = _ema_series(closes, length)
    e1c = [v for v in e1 if v is not None]
    e2 = [v for v in _ema_series(e1c, length) if v is not None]
    e3 = [v for v in _ema_series(e2, length) if v is not None]
    trix = [None]
    for i in range(1, len(e3)):
        if e3[i - 1]:
            trix.append(100.0 * (e3[i] - e3[i - 1]) / e3[i - 1])
        else:
            trix.append(None)
    pad = len(closes) - len(trix)
    return [None] * pad + trix


def _kst_series(closes, r=(10, 15, 20, 30), n=(10, 10, 10, 15), w=(1, 2, 3, 4)):
    """Pring short-term KST; returns (kst, signal) None-padded."""

    def _roc(vals, p):
        out = [None] * len(vals)
        for i in range(p, len(vals)):
            if vals[i - p]:
                out[i] = (vals[i] / vals[i - p] - 1.0) * 100.0
        return out

    def _sma_skip(vals, p):
        out = [None] * len(vals)
        for i in range(len(vals)):
            win = [v for v in vals[i - p + 1: i + 1] if v is not None]
            if len(win) == p:
                out[i] = sum(win) / p
        return out

    terms = []
    for rp, np_, wp in zip(r, n, w):
        sm = _sma_skip(_roc(closes, rp), np_)
        terms.append([v * wp if v is not None else None for v in sm])
    kst = [None] * len(closes)
    for i in range(len(closes)):
        if all(t[i] is not None for t in terms):
            kst[i] = sum(t[i] for t in terms)
    kvals = [v for v in kst if v is not None]
    sig_c = _sma_series(kvals, 9)
    pad = len(closes) - len(kvals)
    return kst, [None] * pad + sig_c


def _aroon_series(ohlc, period=25):
    n = len(ohlc)
    up = [None] * n
    dn = [None] * n
    for i in range(period - 1, n):
        win_h = [ohlc[j]["high"] for j in range(i - period + 1, i + 1)]
        win_l = [ohlc[j]["low"] for j in range(i - period + 1, i + 1)]
        since_high = period - 1 - max(k for k, v in enumerate(win_h) if v == max(win_h))
        since_low = period - 1 - max(k for k, v in enumerate(win_l) if v == min(win_l))
        up[i] = 100.0 * (period - since_high) / period
        dn[i] = 100.0 * (period - since_low) / period
    return up, dn


def _vortex_series(ohlc, period=14):
    n = len(ohlc)
    vip = [None] * n
    vim = [None] * n
    for i in range(period, n):
        vm_p = vm_m = tr_s = 0.0
        for j in range(i - period + 1, i + 1):
            vm_p += abs(ohlc[j]["high"] - ohlc[j - 1]["low"])
            vm_m += abs(ohlc[j]["low"] - ohlc[j - 1]["high"])
            h, l, pc = ohlc[j]["high"], ohlc[j]["low"], ohlc[j - 1]["close"]
            tr_s += max(h - l, abs(h - pc), abs(l - pc))
        if tr_s > 0:
            vip[i] = vm_p / tr_s
            vim[i] = vm_m / tr_s
    return vip, vim


def _ichimoku_series(ohlc, tenkan=9, kijun=26, senkou_b=52):
    """(tenkan, kijun, spanA, spanB) raw per-bar series, None-padded.

    The cloud AT bar i is (spanA[i-26], spanB[i-26]) — spans are plotted
    26 bars ahead; "future kumo" at bar i is (spanA[i], spanB[i]).
    """
    n = len(ohlc)
    t_out = [None] * n
    k_out = [None] * n
    a_out = [None] * n
    b_out = [None] * n
    for i in range(n):
        if i >= tenkan - 1:
            w = ohlc[i - tenkan + 1: i + 1]
            t_out[i] = (max(c["high"] for c in w) + min(c["low"] for c in w)) / 2.0
        if i >= kijun - 1:
            w = ohlc[i - kijun + 1: i + 1]
            k_out[i] = (max(c["high"] for c in w) + min(c["low"] for c in w)) / 2.0
        if t_out[i] is not None and k_out[i] is not None:
            a_out[i] = (t_out[i] + k_out[i]) / 2.0
        if i >= senkou_b - 1:
            w = ohlc[i - senkou_b + 1: i + 1]
            b_out[i] = (max(c["high"] for c in w) + min(c["low"] for c in w)) / 2.0
    return t_out, k_out, a_out, b_out


def _cloud_at(a_out, b_out, i, disp=26):
    """Kumo top/bottom visible at bar i (spans plotted `disp` ahead)."""
    j = i - disp
    if j < 0 or a_out[j] is None or b_out[j] is None:
        return None, None
    return max(a_out[j], b_out[j]), min(a_out[j], b_out[j])


# ---------------------------------------------------------------- signals
# Each entry below traces to one `### STRATEGY:` block in
# research/trend_momentum.md; the catalog title is cited in the docstring.


def _ma_cross(ohlc, fast_series, slow_series, tag, conf, label,
              long_ok=None, short_ok=None, min_bars=MIN_BARS):
    """Parameterized two-MA crossover (catalog cross convention)."""
    if len(ohlc) < min_bars:
        return None
    f = fast_series
    s = slow_series
    if len(f) < 3 or len(s) < 3 or f[-3] is None or s[-3] is None \
            or f[-1] is None or s[-1] is None:
        return None
    if _crossed_up(f, s) and (long_ok is None or long_ok):
        return _sig(tag, "BUY", conf, [f"{label} bullish cross"])
    if _crossed_down(f, s) and (short_ok is None or short_ok):
        return _sig(tag, "SELL", conf, [f"{label} bearish cross"])
    return None


def sig_sma_cross_50_200(ohlc):
    """Catalog: SMA Golden/Death Cross (50/200) with ADX(14) >= 20 gate."""
    tag = "tm_sma_cross_50_200"
    if len(ohlc) < 210:
        return None
    closes = _closes(ohlc)
    adx, _, _ = _adx_series(ohlc, 14)
    gate = adx[-1] is not None and adx[-1] >= 20
    return _ma_cross(ohlc, _sma_series(closes, 50), _sma_series(closes, 200),
                     tag, 0.6, "SMA 50/200 + ADX>=20",
                     long_ok=gate, short_ok=gate, min_bars=210)


def sig_ema_cross_9_21(ohlc):
    """Catalog: EMA 9/21 Crossover with SMA200 trend filter."""
    tag = "tm_ema_cross_9_21"
    if len(ohlc) < 205:
        return None
    closes = _closes(ohlc)
    sma200 = _sma_series(closes, 200)
    return _ma_cross(ohlc, _ema_series(closes, 9), _ema_series(closes, 21),
                     tag, 0.6, "EMA 9/21 vs SMA200",
                     long_ok=sma200[-1] is not None and closes[-1] > sma200[-1],
                     short_ok=sma200[-1] is not None and closes[-1] < sma200[-1],
                     min_bars=205)


def sig_triple_ema_ribbon(ohlc, e1=8, e2=13, e3=21):
    """Catalog: Triple EMA Ribbon (8/13/21) pullback-continuation entry."""
    tag = "tm_ema_ribbon_8_13_21"
    if len(ohlc) < MIN_BARS:
        return None
    closes = _closes(ohlc)
    s1, s2, s3 = _ema_series(closes, e1), _ema_series(closes, e2), _ema_series(closes, e3)
    atrs = _atr_series(ohlc, 14)
    if atrs[-1] is None or s3[-1] is None:
        return None
    width = abs(s1[-1] - s3[-1])
    if width < 0.15 * atrs[-1]:  # catalog minimum ribbon-width gate
        return None
    bull = s1[-1] > s2[-1] > s3[-1]
    bear = s1[-1] < s2[-1] < s3[-1]
    if not (bull or bear):
        return None
    # pullback within the last 4 bars: tag of EMA13/EMA21 without closing
    # through EMA21, then a close back on the trend side of EMA8.
    touched = False
    for i in range(max(e3, len(ohlc) - 4), len(ohlc) - 1):
        if s3[i] is None:
            continue
        if bull and ohlc[i]["low"] <= s2[i] and ohlc[i]["close"] >= s3[i]:
            touched = True
        if bear and ohlc[i]["high"] >= s2[i] and ohlc[i]["close"] <= s3[i]:
            touched = True
    if not touched:
        return None
    if bull and closes[-1] > s1[-1]:
        return _sig(tag, "BUY", 0.6, ["Bull ribbon 8>13>21, pullback held EMA21, close > EMA8"])
    if bear and closes[-1] < s1[-1]:
        return _sig(tag, "SELL", 0.6, ["Bear ribbon 8<13<21, pullback held EMA21, close < EMA8"])
    return None


def sig_hma_cross_16_55(ohlc):
    """Catalog: Hull MA Crossover (16/55)."""
    closes = _closes(ohlc)
    return _ma_cross(ohlc, _hma_series(closes, 16), _hma_series(closes, 55),
                     "tm_hma_cross_16_55", 0.55, "Hull MA 16/55", min_bars=80)


def sig_tema_cross_12_30(ohlc):
    """Catalog: TEMA Crossover (12/30); DEMA variant noted, TEMA primary."""
    closes = _closes(ohlc)
    return _ma_cross(ohlc, _tema_series(closes, 12), _tema_series(closes, 30),
                     "tm_tema_cross_12_30", 0.55, "TEMA 12/30", min_bars=100)


def sig_vwma_cross_20_50(ohlc):
    """Catalog: VWMA Crossover (20/50); needs real volume (crypto)."""
    tag = "tm_vwma_cross_20_50"
    if len(ohlc) < MIN_BARS:
        return None
    vols = _vols(ohlc)
    if sorted(vols[-50:])[25] <= 0:  # median bar volume guard (catalog note)
        return None
    closes = _closes(ohlc)
    return _ma_cross(ohlc, _vwma_series(closes, vols, 20),
                     _vwma_series(closes, vols, 50),
                     tag, 0.55, "VWMA 20/50")


def sig_macd_signal_cross(ohlc):
    """Catalog: MACD Signal-Line Cross (12/26/9), trend variant (SMA200)."""
    tag = "tm_macd_signal_cross"
    if len(ohlc) < 205:
        return None
    closes = _closes(ohlc)
    macd, signal, _ = _macd_series(closes)
    sma200 = _sma_series(closes, 200)
    if sma200[-1] is None or signal[-3] is None:
        return None
    if _crossed_up(macd, signal) and closes[-1] > sma200[-1]:
        return _sig(tag, "BUY", 0.55, ["MACD signal-line bull cross above SMA200"])
    if _crossed_down(macd, signal) and closes[-1] < sma200[-1]:
        return _sig(tag, "SELL", 0.55, ["MACD signal-line bear cross below SMA200"])
    return None


def sig_macd_zero_cross(ohlc):
    """Catalog: MACD Zero-Line Cross (12/26)."""
    tag = "tm_macd_zero_cross"
    if len(ohlc) < MIN_BARS:
        return None
    macd, _, _ = _macd_series(_closes(ohlc))
    if macd[-3] is None:
        return None
    if macd[-3] <= 0 < macd[-1]:
        return _sig(tag, "BUY", 0.5, ["MACD line crossed above zero"])
    if macd[-3] >= 0 > macd[-1]:
        return _sig(tag, "SELL", 0.5, ["MACD line crossed below zero"])
    return None


def sig_macd_hist_reversal(ohlc, seq=3):
    """Catalog: MACD Histogram Reversal (3 rising bars < 0, EMA50 gate)."""
    tag = "tm_macd_hist_reversal"
    if len(ohlc) < MIN_BARS:
        return None
    closes = _closes(ohlc)
    _, _, hist = _macd_series(closes)
    ema50 = _ema_series(closes, 50)
    h = hist[-(seq + 1):]
    if ema50[-1] is None or any(v is None for v in h):
        return None
    rising = all(h[i] < h[i + 1] for i in range(len(h) - 1))
    falling = all(h[i] > h[i + 1] for i in range(len(h) - 1))
    if rising and hist[-1] < 0 and closes[-1] > ema50[-1]:
        return _sig(tag, "BUY", 0.55, ["MACD hist rising 3 bars below zero in uptrend"])
    if falling and hist[-1] > 0 and closes[-1] < ema50[-1]:
        return _sig(tag, "SELL", 0.55, ["MACD hist falling 3 bars above zero in downtrend"])
    return None


def sig_adx_dmi_cross(ohlc, period=14, adx_min=25):
    """Catalog: ADX/DMI Directional System (Wilder) — DI cross + ADX>25 rising."""
    tag = "tm_adx_dmi_cross"
    if len(ohlc) < 2 * period + 10:
        return None
    adx, pdi, ndi = _adx_series(ohlc, period)
    if adx[-1] is None or adx[-2] is None or pdi[-3] is None:
        return None
    rising = adx[-1] > adx[-2]
    if _crossed_up(pdi, ndi) and adx[-1] > adx_min and rising:
        return _sig(tag, "BUY", 0.6, [f"+DI crossed above -DI, ADX {adx[-1]:.0f} rising"])
    if _crossed_down(pdi, ndi) and adx[-1] > adx_min and rising:
        return _sig(tag, "SELL", 0.6, [f"-DI crossed above +DI, ADX {adx[-1]:.0f} rising"])
    return None


def sig_adx_pullback(ohlc, adx_min=30, ema_p=20):
    """Catalog: ADX Pullback Continuation — EMA20 tag + resumption close."""
    tag = "tm_adx_pullback"
    if len(ohlc) < MIN_BARS:
        return None
    adx, pdi, ndi = _adx_series(ohlc, 14)
    ema20 = _ema_series(_closes(ohlc), ema_p)
    if adx[-1] is None or ema20[-2] is None:
        return None
    prev = ohlc[-2]
    cur = ohlc[-1]
    touched = prev["low"] <= ema20[-2] <= prev["high"]
    if adx[-1] > adx_min and pdi[-1] > ndi[-1] and touched \
            and cur["close"] > prev["high"]:
        return _sig(tag, "BUY", 0.65, [f"ADX {adx[-1]:.0f}>30 uptrend, EMA20 tag, resumption close"])
    if adx[-1] > adx_min and ndi[-1] > pdi[-1] and touched \
            and cur["close"] < prev["low"]:
        return _sig(tag, "SELL", 0.65, [f"ADX {adx[-1]:.0f}>30 downtrend, EMA20 tag, resumption close"])
    return None


def sig_turtle_s1(ohlc, entry=20, exit_p=10, n_period=20, sl_n=2.0):
    """Catalog: Donchian Breakout - Turtle S1 (20/10).

    Last-breakout filter implemented STATELESSLY: the OHLC window is
    replayed; each same-direction breakout's hypothetical outcome (2*N stop
    or 10-bar Donchian exit) is resolved, and a new signal is emitted only
    when the previous same-direction breakout was a LOSS (or is the first).
    """
    tag = "tm_turtle_s1_20_10"
    if len(ohlc) < entry + n_period + 5:
        return None
    closes = _closes(ohlc)
    atrs = _atr_series(ohlc, n_period)
    pos = {"long": None, "short": None}
    last_win = {"long": None, "short": None}  # None = no prior breakout
    fired = None
    for i in range(entry + 1, len(ohlc)):
        hh = max(c["high"] for c in ohlc[i - entry: i])
        ll = min(c["low"] for c in ohlc[i - entry: i])
        ex_hh = max(c["high"] for c in ohlc[max(0, i - exit_p): i])
        ex_ll = min(c["low"] for c in ohlc[max(0, i - exit_p): i])
        # resolve open simulated trades first (stop checked before exit)
        for side, is_long in (("long", True), ("short", False)):
            p = pos[side]
            if p is None:
                continue
            n_val = p["n"]
            if is_long:
                if ohlc[i]["low"] <= p["entry"] - sl_n * n_val:
                    last_win[side] = False
                    pos[side] = None
                elif closes[i] < ex_ll:
                    last_win[side] = closes[i] > p["entry"]
                    pos[side] = None
            else:
                if ohlc[i]["high"] >= p["entry"] + sl_n * n_val:
                    last_win[side] = False
                    pos[side] = None
                elif closes[i] > ex_hh:
                    last_win[side] = closes[i] < p["entry"]
                    pos[side] = None
        if atrs[i] is None:
            continue
        long_bo = closes[i] > hh and closes[i - 1] <= max(c["high"] for c in ohlc[i - entry - 1: i - 1] or ohlc[i - entry: i])
        short_bo = closes[i] < ll and closes[i - 1] >= min(c["low"] for c in ohlc[i - entry - 1: i - 1] or ohlc[i - entry: i])
        if long_bo and pos["long"] is None and last_win["long"] is not True:
            pos["long"] = {"entry": closes[i], "n": atrs[i]}
            if i == len(ohlc) - 1:
                fired = _sig(tag, "BUY", 0.65,
                             ["Turtle S1 20-bar breakout (last-breakout filter passed)"])
        if short_bo and pos["short"] is None and last_win["short"] is not True:
            pos["short"] = {"entry": closes[i], "n": atrs[i]}
            if i == len(ohlc) - 1:
                fired = _sig(tag, "SELL", 0.65,
                             ["Turtle S1 20-bar breakdown (last-breakout filter passed)"])
    return fired


def sig_turtle_s2(ohlc, entry=55):
    """Catalog: Donchian Breakout - Turtle S2 (55/20); takes every signal."""
    tag = "tm_turtle_s2_55_20"
    if len(ohlc) < entry + 3:
        return None
    closes = _closes(ohlc)
    hh = max(c["high"] for c in ohlc[-entry - 1: -1])
    ll = min(c["low"] for c in ohlc[-entry - 1: -1])
    if closes[-1] > hh and closes[-2] <= max(c["high"] for c in ohlc[-entry - 2: -2]):
        return _sig(tag, "BUY", 0.65, ["Turtle S2 55-bar breakout"])
    if closes[-1] < ll and closes[-2] >= min(c["low"] for c in ohlc[-entry - 2: -2]):
        return _sig(tag, "SELL", 0.65, ["Turtle S2 55-bar breakdown"])
    return None


def sig_ichimoku_tk_cross(ohlc):
    """Catalog: Ichimoku Tenkan/Kijun Cross with kumo-location filter."""
    tag = "tm_ichimoku_tk_cross"
    if len(ohlc) < 80:
        return None
    t, k, a, b = _ichimoku_series(ohlc)
    if t[-3] is None or k[-3] is None:
        return None
    top, bot = _cloud_at(a, b, len(ohlc) - 1)
    if top is None:
        return None
    close = ohlc[-1]["close"]
    if _crossed_up(t, k) and close > top:
        return _sig(tag, "BUY", 0.6, ["Bullish TK cross above the kumo"])
    if _crossed_down(t, k) and close < bot:
        return _sig(tag, "SELL", 0.6, ["Bearish TK cross below the kumo"])
    return None


def sig_ichimoku_kumo_breakout(ohlc, disp=26):
    """Catalog: Ichimoku Kumo Breakout with future-kumo direction filter."""
    tag = "tm_ichimoku_kumo_breakout"
    if len(ohlc) < 104:
        return None
    t, k, a, b = _ichimoku_series(ohlc)
    i = len(ohlc) - 1
    top, bot = _cloud_at(a, b, i, disp)
    ptop, pbot = _cloud_at(a, b, i - 1, disp)
    if top is None or ptop is None or a[-1] is None or b[-1] is None:
        return None
    close = ohlc[-1]["close"]
    prev_close = ohlc[-2]["close"]
    future_bull = a[-1] > b[-1]
    future_bear = a[-1] < b[-1]
    if close > top and prev_close <= ptop and future_bull:
        return _sig(tag, "BUY", 0.65, ["Kumo breakout up, future kumo bullish"])
    if close < bot and prev_close >= pbot and future_bear:
        return _sig(tag, "SELL", 0.65, ["Kumo breakout down, future kumo bearish"])
    return None


def sig_ichimoku_full_confluence(ohlc, disp=26):
    """Catalog: Ichimoku Full Confluence (TK + Kumo + Chikou)."""
    tag = "tm_ichimoku_full_confluence"
    if len(ohlc) < 104:
        return None
    t, k, a, b = _ichimoku_series(ohlc)
    closes = _closes(ohlc)

    def _state(i):
        top, bot = _cloud_at(a, b, i, disp)
        if top is None or t[i] is None or k[i] is None or i - disp < 0:
            return None
        if a[i] is None or b[i] is None:
            return None
        bull = closes[i] > top and t[i] > k[i] and closes[i] > closes[i - disp] and a[i] > b[i]
        bear = closes[i] < bot and t[i] < k[i] and closes[i] < closes[i - disp] and a[i] < b[i]
        return bull, bear

    cur = _state(len(ohlc) - 1)
    prev = _state(len(ohlc) - 2)
    if cur is None or prev is None:
        return None
    if cur[0] and not prev[0]:
        return _sig(tag, "BUY", 0.8, ["Ichimoku full bullish confluence (cloud+TK+chikou+future)"])
    if cur[1] and not prev[1]:
        return _sig(tag, "SELL", 0.8, ["Ichimoku full bearish confluence (cloud+TK+chikou+future)"])
    return None


def sig_supertrend(ohlc, period=10, mult=3.0):
    """Catalog: Supertrend (10, 3.0) flip; band is the trailing stop."""
    tag = "tm_supertrend_10_3"
    if len(ohlc) < MIN_BARS:
        return None
    trend, _ = _supertrend(ohlc, period, mult)
    if trend[-1] is None or trend[-2] is None:
        return None
    if trend[-2] == -1 and trend[-1] == 1:
        return _sig(tag, "BUY", 0.6, ["Supertrend flipped up"])
    if trend[-2] == 1 and trend[-1] == -1:
        return _sig(tag, "SELL", 0.6, ["Supertrend flipped down"])
    return None


def sig_parabolic_sar(ohlc, adx_min=20):
    """Catalog: Parabolic SAR flip with ADX(14) > 20 trend gate."""
    tag = "tm_parabolic_sar"
    if len(ohlc) < MIN_BARS:
        return None
    sar = _psar(ohlc)
    adx, _, _ = _adx_series(ohlc, 14)
    if sar[-1] is None or sar[-2] is None or adx[-1] is None:
        return None
    if adx[-1] <= adx_min:
        return None
    close = _closes(ohlc)
    if sar[-2] > close[-2] and sar[-1] < close[-1]:
        return _sig(tag, "BUY", 0.5, [f"PSAR flipped below price, ADX {adx[-1]:.0f}"])
    if sar[-2] < close[-2] and sar[-1] > close[-1]:
        return _sig(tag, "SELL", 0.5, [f"PSAR flipped above price, ADX {adx[-1]:.0f}"])
    return None


def sig_keltner_breakout(ohlc, ema_p=20, atr_p=10, mult=2.0):
    """Catalog: Keltner Channel Breakout (EMA20 +/- 2*ATR(10))."""
    tag = "tm_keltner_breakout_20_2"
    if len(ohlc) < MIN_BARS:
        return None
    closes = _closes(ohlc)
    ema = _ema_series(closes, ema_p)
    atrs = _atr_series(ohlc, atr_p)
    if ema[-1] is None or atrs[-1] is None or ema[-2] is None or atrs[-2] is None:
        return None
    up, lo = ema[-1] + mult * atrs[-1], ema[-1] - mult * atrs[-1]
    pup, plo = ema[-2] + mult * atrs[-2], ema[-2] - mult * atrs[-2]
    if closes[-1] > up and closes[-2] <= pup:
        return _sig(tag, "BUY", 0.55, ["Close above upper Keltner (EMA20 + 2*ATR10)"])
    if closes[-1] < lo and closes[-2] >= plo:
        return _sig(tag, "SELL", 0.55, ["Close below lower Keltner (EMA20 - 2*ATR10)"])
    return None


def sig_roc_momentum(ohlc, period=12, trend_sma=50):
    """Catalog: ROC Momentum — ROC(12) zero cross with SMA50 gate."""
    tag = "tm_roc_momentum_12"
    if len(ohlc) < trend_sma + period + 3:
        return None
    closes = _closes(ohlc)
    sma50 = _sma_series(closes, trend_sma)
    roc = [None] * len(closes)
    for i in range(period, len(closes)):
        if closes[i - period]:
            roc[i] = (closes[i] / closes[i - period] - 1.0) * 100.0
    if roc[-3] is None or sma50[-1] is None:
        return None
    if roc[-3] <= 0 < roc[-1] and closes[-1] > sma50[-1]:
        return _sig(tag, "BUY", 0.6, ["ROC(12) crossed above 0 above SMA50"])
    if roc[-3] >= 0 > roc[-1] and closes[-1] < sma50[-1]:
        return _sig(tag, "SELL", 0.6, ["ROC(12) crossed below 0 below SMA50"])
    return None


def sig_linreg_slope(ohlc, length=20, r2_min=0.5):
    """Catalog: Linear Regression Slope Trend with r-squared quality gate."""
    tag = "tm_linreg_slope_20"
    if len(ohlc) < length + 5:
        return None
    closes = _closes(ohlc)
    slope, r2 = _linreg_slope_r2(closes[-length:])
    pslope, pr2 = _linreg_slope_r2(closes[-length - 1: -1])
    if not closes[-1]:
        return None
    bull = slope > 0 and r2 > r2_min
    bear = slope < 0 and r2 > r2_min
    pbull = pslope > 0 and pr2 > r2_min
    pbear = pslope < 0 and pr2 > r2_min
    if bull and not pbull:
        return _sig(tag, "BUY", 0.6, [f"LinReg slope > 0, r2 {r2:.2f}"])
    if bear and not pbear:
        return _sig(tag, "SELL", 0.6, [f"LinReg slope < 0, r2 {r2:.2f}"])
    return None


def sig_dual_thrust(ohlc, n_days=4, k1=0.5, k2=0.5, day_secs=86400):
    """Catalog: Dual Thrust Opening Range Breakout.

    STATELESS session anchoring: UTC days are re-derived from candle "ts"
    on every call; Range = max(HH-LC, HC-LL) over the prior n COMPLETED
    days (catalog's key formula). Candles without "ts" return None.
    Fires on the bar whose close first crosses the thrust level.
    """
    tag = "tm_dual_thrust"
    if len(ohlc) < n_days + 3 or "ts" not in ohlc[-1]:
        return None
    days = {}
    for c in ohlc:
        days.setdefault(int(c["ts"] // day_secs), []).append(c)
    keys = sorted(days)
    if len(keys) < n_days + 1:
        return None
    today = days[keys[-1]]
    prior = [c for k in keys[-n_days - 1: -1] for c in days[k]]
    hh = max(c["high"] for c in prior)
    ll = min(c["low"] for c in prior)
    hc = max(c["close"] for c in prior)
    lc = min(c["close"] for c in prior)
    rng = max(hh - lc, hc - ll)
    if rng <= 0:
        return None
    open_ = today[0]["open"]
    up_lvl, dn_lvl = open_ + k1 * rng, open_ - k2 * rng
    close = ohlc[-1]["close"]
    prev_close = ohlc[-2]["close"] if ohlc[-2]["ts"] // day_secs == ohlc[-1]["ts"] // day_secs else open_
    if close > up_lvl and prev_close <= up_lvl:
        return _sig(tag, "BUY", 0.55, [f"Dual Thrust up break (open {open_:.5f} + {k1}*Range {rng:.5f})"])
    if close < dn_lvl and prev_close >= dn_lvl:
        return _sig(tag, "SELL", 0.55, [f"Dual Thrust down break (open {open_:.5f} - {k2}*Range {rng:.5f})"])
    return None


def sig_aroon(ohlc, period=25, level=70):
    """Catalog: Aroon (25) cross with >70 strong-trend gate (Chande)."""
    tag = "tm_aroon_25"
    if len(ohlc) < period + 5:
        return None
    up, dn = _aroon_series(ohlc, period)
    if up[-3] is None or dn[-3] is None:
        return None
    if _crossed_up(up, dn) and up[-1] > level:
        return _sig(tag, "BUY", 0.55, [f"Aroon-Up crossed {up[-1]:.0f} > 70"])
    if _crossed_down(up, dn) and dn[-1] > level:
        return _sig(tag, "SELL", 0.55, [f"Aroon-Down crossed {dn[-1]:.0f} > 70"])
    return None


def sig_vortex(ohlc, period=14):
    """Catalog: Vortex Indicator (14) VI+/VI- cross."""
    tag = "tm_vortex_14"
    if len(ohlc) < period + 5:
        return None
    vip, vim = _vortex_series(ohlc, period)
    if vip[-3] is None or vim[-3] is None:
        return None
    if _crossed_up(vip, vim):
        return _sig(tag, "BUY", 0.5, ["VI+ crossed above VI-"])
    if _crossed_down(vip, vim):
        return _sig(tag, "SELL", 0.5, ["VI- crossed above VI+"])
    return None


def sig_mass_index_bulge(ohlc, ema_p=9, sum_p=25, bulge=27.0, trigger=26.5):
    """Catalog: Mass Index Reversal Bulge -> Trend Continuation.

    STATELESS two-stage: the MI series is replayed; a "bulge armed -> drop
    below trigger" completion event is tracked, and a signal fires only
    when the completion bar IS the last closed bar. Direction from the
    9-EMA slope, per Dorsey's published rule. Low confidence per catalog.
    """
    tag = "tm_mass_index_bulge"
    if len(ohlc) < 2 * ema_p + sum_p + 5:
        return None
    mi = _mass_index_series(ohlc, ema_p, sum_p)
    armed = False
    completed_at = None
    for i, v in enumerate(mi):
        if v is None:
            continue
        if v >= bulge:
            armed = True
        elif armed and v < trigger:
            completed_at = i
            armed = False
    if completed_at != len(ohlc) - 1:
        return None
    closes = _closes(ohlc)
    e9 = _ema_series(closes, ema_p)
    if e9[-1] is None or e9[-2] is None:
        return None
    if e9[-1] > e9[-2]:
        return _sig(tag, "BUY", 0.5, ["Mass Index bulge complete, EMA9 rising"])
    if e9[-1] < e9[-2]:
        return _sig(tag, "SELL", 0.5, ["Mass Index bulge complete, EMA9 falling"])
    return None


def sig_trix(ohlc, length=15, signal=9):
    """Catalog: TRIX (15) signal-line cross with zero-side gate."""
    tag = "tm_trix_15_9"
    if len(ohlc) < 3 * length + signal + 10:
        return None
    trix = _trix_series(_closes(ohlc), length)
    vals = [v for v in trix if v is not None]
    sig_c = _ema_series(vals, signal)
    pad = len(ohlc) - len(vals)
    sig_line = [None] * pad + sig_c
    if len(vals) < 3 or sig_line[-3] is None or trix[-3] is None:
        return None
    if _crossed_up(trix, sig_line) and trix[-1] > 0:
        return _sig(tag, "BUY", 0.55, ["TRIX crossed above signal above zero"])
    if _crossed_down(trix, sig_line) and trix[-1] < 0:
        return _sig(tag, "SELL", 0.55, ["TRIX crossed below signal below zero"])
    return None


def sig_kst(ohlc):
    """Catalog: KST (Know Sure Thing) — Pring short-term formula + SMA9 signal."""
    tag = "tm_kst"
    if len(ohlc) < 60:
        return None
    kst, sig_line = _kst_series(_closes(ohlc))
    if kst[-3] is None or sig_line[-3] is None:
        return None
    if _crossed_up(kst, sig_line):
        return _sig(tag, "BUY", 0.6, ["KST crossed above signal"])
    if _crossed_down(kst, sig_line):
        return _sig(tag, "SELL", 0.6, ["KST crossed below signal"])
    return None


def sig_elder_impulse(ohlc, ema_p=13):
    """Catalog: Elder Impulse System — entry on impulse onset."""
    tag = "tm_elder_impulse"
    if len(ohlc) < MIN_BARS:
        return None
    closes = _closes(ohlc)
    ema13 = _ema_series(closes, ema_p)
    _, _, hist = _macd_series(closes)
    if ema13[-2] is None or hist[-2] is None:
        return None

    def _green(i):
        return ema13[i] > ema13[i - 1] and hist[i] > hist[i - 1]

    def _red(i):
        return ema13[i] < ema13[i - 1] and hist[i] < hist[i - 1]

    if _green(-1) and not _green(-2) and closes[-1] > ema13[-1]:
        return _sig(tag, "BUY", 0.55, ["Elder impulse turned green above EMA13"])
    if _red(-1) and not _red(-2) and closes[-1] < ema13[-1]:
        return _sig(tag, "SELL", 0.55, ["Elder impulse turned bearish below EMA13"])
    return None


def sig_trend_pullback_ema20(ohlc, trend_sma=50, ema_p=20, lookback=5):
    """Catalog: Trend-Pullback Continuation (EMA20 + rejection candle)."""
    tag = "tm_trend_pullback_ema20"
    if len(ohlc) < trend_sma + lookback + 2:
        return None
    closes = _closes(ohlc)
    sma50 = _sma_series(closes, trend_sma)
    ema20 = _ema_series(closes, ema_p)
    i = len(ohlc) - 1
    if sma50[-1] is None or sma50[-1 - lookback] is None or ema20[-1] is None:
        return None
    c = ohlc[-1]
    rng = c["high"] - c["low"]
    if rng <= 0:
        return None
    pos_in_range = (c["close"] - c["low"]) / rng
    up_trend = closes[-1] > sma50[-1] and sma50[-1] > sma50[-1 - lookback]
    dn_trend = closes[-1] < sma50[-1] and sma50[-1] < sma50[-1 - lookback]
    if up_trend and c["low"] < ema20[-1] and c["close"] > ema20[-1] \
            and pos_in_range >= 0.5 and c["close"] > c["open"]:
        return _sig(tag, "BUY", 0.65, ["Uptrend dip below EMA20, bullish rejection close"])
    if dn_trend and c["high"] > ema20[-1] and c["close"] < ema20[-1] \
            and pos_in_range <= 0.5 and c["close"] < c["open"]:
        return _sig(tag, "SELL", 0.65, ["Downtrend rally above EMA20, bearish rejection close"])
    return None


# ---------------------------------------------------------------- registry

TREND_MOMENTUM_STRATEGIES = [
    ("tm_sma_cross_50_200", sig_sma_cross_50_200),
    ("tm_ema_cross_9_21", sig_ema_cross_9_21),
    ("tm_ema_ribbon_8_13_21", sig_triple_ema_ribbon),
    ("tm_hma_cross_16_55", sig_hma_cross_16_55),
    ("tm_tema_cross_12_30", sig_tema_cross_12_30),
    ("tm_vwma_cross_20_50", sig_vwma_cross_20_50),
    ("tm_macd_signal_cross", sig_macd_signal_cross),
    ("tm_macd_zero_cross", sig_macd_zero_cross),
    ("tm_macd_hist_reversal", sig_macd_hist_reversal),
    ("tm_adx_dmi_cross", sig_adx_dmi_cross),
    ("tm_adx_pullback", sig_adx_pullback),
    ("tm_turtle_s1_20_10", sig_turtle_s1),
    ("tm_turtle_s2_55_20", sig_turtle_s2),
    ("tm_ichimoku_tk_cross", sig_ichimoku_tk_cross),
    ("tm_ichimoku_kumo_breakout", sig_ichimoku_kumo_breakout),
    ("tm_ichimoku_full_confluence", sig_ichimoku_full_confluence),
    ("tm_supertrend_10_3", sig_supertrend),
    ("tm_parabolic_sar", sig_parabolic_sar),
    ("tm_keltner_breakout_20_2", sig_keltner_breakout),
    ("tm_roc_momentum_12", sig_roc_momentum),
    ("tm_linreg_slope_20", sig_linreg_slope),
    ("tm_dual_thrust", sig_dual_thrust),
    ("tm_aroon_25", sig_aroon),
    ("tm_vortex_14", sig_vortex),
    ("tm_mass_index_bulge", sig_mass_index_bulge),
    ("tm_trix_15_9", sig_trix),
    ("tm_kst", sig_kst),
    ("tm_elder_impulse", sig_elder_impulse),
    ("tm_trend_pullback_ema20", sig_trend_pullback_ema20),
]


# registry (same shape as core.strategies.ALL_STRATEGIES)
# ---------------------------------------------------------------------------

STRATEGIES = [
    ("Trend - SMA Cross 50/200 + ADX", sig_sma_cross_50_200),
    ("Trend - EMA Cross 9/21 + SMA200", sig_ema_cross_9_21),
    ("Trend - Triple EMA Ribbon 8/13/21", sig_triple_ema_ribbon),
    ("Trend - Hull MA Cross 16/55", sig_hma_cross_16_55),
    ("Trend - TEMA Cross 12/30", sig_tema_cross_12_30),
    ("Trend - VWMA Cross 20/50", sig_vwma_cross_20_50),
    ("Trend - MACD Signal Cross + SMA200", sig_macd_signal_cross),
    ("Trend - MACD Zero-Line Cross", sig_macd_zero_cross),
    ("Trend - MACD Histogram Reversal", sig_macd_hist_reversal),
    ("Trend - ADX/DMI Cross", sig_adx_dmi_cross),
    ("Trend - ADX Pullback Continuation", sig_adx_pullback),
    ("Trend - Turtle S1 Donchian 20/10", sig_turtle_s1),
    ("Trend - Turtle S2 Donchian 55/20", sig_turtle_s2),
    ("Trend - Ichimoku TK Cross", sig_ichimoku_tk_cross),
    ("Trend - Ichimoku Kumo Breakout", sig_ichimoku_kumo_breakout),
    ("Trend - Ichimoku Full Confluence", sig_ichimoku_full_confluence),
    ("Trend - Supertrend 10/3", sig_supertrend),
    ("Trend - Parabolic SAR + ADX", sig_parabolic_sar),
    ("Trend - Keltner Breakout 20/2ATR", sig_keltner_breakout),
    ("Trend - ROC Momentum 12", sig_roc_momentum),
    ("Trend - LinReg Slope + R2 Gate", sig_linreg_slope),
    ("Trend - Dual Thrust Opening Range", sig_dual_thrust),
    ("Trend - Aroon 25", sig_aroon),
    ("Trend - Vortex 14", sig_vortex),
    ("Trend - Mass Index Bulge", sig_mass_index_bulge),
    ("Trend - TRIX 15/9", sig_trix),
    ("Trend - KST", sig_kst),
    ("Trend - Elder Impulse", sig_elder_impulse),
    ("Trend - EMA20 Pullback Rejection", sig_trend_pullback_ema20),
]


def scan_trend_momentum(ohlc, exclude_tags=None):
    """Run the whole family on one closed-bar OHLC series.

    Returns the raw per-tag signal dicts (one per strategy that fired),
    mirroring core/strategies.py scan_symbol's per-strategy attribution
    but WITHOUT merging actions — the learning loop needs per-tag stats.
    """
    signals = []
    for tag, fn in TREND_MOMENTUM_STRATEGIES:
        if exclude_tags and tag in exclude_tags:
            continue
        try:
            sig = fn(ohlc)
            if sig:
                signals.append(sig)
        except Exception as e:  # never let one strategy kill the scan
            _log.warning("trend_momentum %s: %s", tag, e)
    return signals

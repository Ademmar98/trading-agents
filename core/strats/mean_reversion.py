"""Mean-reversion strategy family — per-tag signal functions.

Every entry in research/mean_reversion.md that is codable against OHLC/OHLCV
single-symbol bars is implemented here. Contract matches core/strategies.py
(scan_symbol ~line 753): each function receives the OHLC[V] candle list
(fields: open/high/low/close/volume/ts, last bar CLOSED) and returns
{"action": "BUY"/"SELL", "confidence": float, "reasons": [...]} or None.
Like core/swing.py, each returned dict also carries its own unique machine
tag in "strategy" (e.g. "mr_rsi2_connors_classic") so per-strategy stats
stay attributable. Registry at the bottom: MEAN_REVERSION_STRATEGIES as
(display_name, fn) tuples mirroring ALL_STRATEGIES for the integrator to
splice into core/strategies.py.

Catalog traceability (research/mean_reversion.md, 29 entries):
- 26 implemented below, named exactly after their catalog entries.
- RSI2_ScaleIn_TPS: catalog allows "emit BUY/SELL with confidence and let
  position manager scale" — implemented STATELESS: fires on the bar RSI(2)
  crosses below/above each successive tranche level, tranche number in the
  reasons; sizing/aggregation is the position manager's job.
- OU_HalfLife_Spread_Reversion: implemented via the catalog-sanctioned
  single-series proxy (data_needs line: "OHLC single-symbol when
  S = price - MA(price,N) as a self-spread"); AR(1)/half-life gate, z-window
  and time logic all follow the pairs spec.

SKIPPED (not OHLC/OHLCV single-series codable; flagged per catalog
cross-cutting note 4):
- Pairs_Cointegration_ZScore — needs two synchronized symbols; the catalog
  offers no single-series proxy. Blocked on data_provider multi-symbol fan-out.
- Perp_Basis_Reversion — needs perp + spot series simultaneously; even the
  single-leg fallback needs the basis (both prices). Blocked on multi-symbol.
- Funding_Rate_Extreme_Fade — needs a funding-rate feed beyond OHLCV;
  catalog says "paper test only if funding feed exists ... otherwise park".

Indicator conventions: RSI/ATR are Wilder-smoothed (core.indicators matches
the catalog's reference to _rsi/_atr in core/strategies.py); Bollinger uses
sample stdev like core.indicators.bollinger_bands. Suggested confidence
scaling follows catalog note 6: clip(0.5 + 0.1 x extremity, 0.5, 0.85).
Session/VWAP functions reset at 00:00 UTC (crypto convention per catalog);
they require 'ts' and volume and return None on OHLC-only feeds.
"""
import math
from datetime import datetime, timezone
from statistics import mean, pstdev, stdev

from core.indicators import sma as _sma, ema as _ema, ema_all as _ema_all
from core.indicators import rsi as _rsi, atr as _atr

MIN_OHLC = 30  # mirrors core.strategies.MIN_OHLC
SECONDS_PER_DAY = 86400


# ---------------------------------------------------------------- helpers

def _series(ohlc, field):
    return [b[field] for b in ohlc]


def _conf(extremity, base=0.5):
    """Catalog note 6: clip(0.5 + 0.1 x extremity, 0.5, 0.85)."""
    return max(0.5, min(0.85, base + 0.1 * max(0.0, extremity)))


def _signal(action, tag, confidence, reasons):
    return {"action": action, "strategy": tag,
            "confidence": round(confidence, 3), "reasons": reasons}


def _rsi_series(closes, period, count):
    """Last `count` Wilder RSI values, rsi_series[j] evaluated at the bar
    len(closes)-count+j (path-dependent smoothing preserved)."""
    n = len(closes)
    start = max(period + 1, n - count + 1)
    return [_rsi(closes[:i], period) for i in range(start, n + 1)]


def _atr_at(highs, lows, closes, period=14):
    return _atr(highs, lows, closes, period)


def _stdev(window):
    """Sample stdev (matches core.indicators.bollinger_bands); None if degenerate."""
    if len(window) < 2:
        return None
    sd = stdev(window)
    return sd if sd > 0 else None


def _swing_highs(ohlc, window=5):
    """Local copy of the pivot helper in core/strategies.py (kept local so this
    module never imports core.strategies — the integrator will import us)."""
    highs = []
    for i in range(window, len(ohlc) - window):
        if all(ohlc[i]["high"] >= ohlc[i - j]["high"] for j in range(1, window + 1)) and \
           all(ohlc[i]["high"] >= ohlc[i + j]["high"] for j in range(1, window + 1)):
            highs.append((i, ohlc[i]["high"]))
    return highs


def _swing_lows(ohlc, window=5):
    lows = []
    for i in range(window, len(ohlc) - window):
        if all(ohlc[i]["low"] <= ohlc[i - j]["low"] for j in range(1, window + 1)) and \
           all(ohlc[i]["low"] <= ohlc[i + j]["low"] for j in range(1, window + 1)):
            lows.append((i, ohlc[i]["low"]))
    return lows


def _stochastic(highs, lows, closes, k_len=14, k_smooth=3, d_len=3):
    """Slow stochastic: returns (k_list, d_list) aligned to bars, None-padded."""
    n = len(closes)
    raw = [None] * n
    for i in range(k_len - 1, n):
        hh = max(highs[i - k_len + 1:i + 1])
        ll = min(lows[i - k_len + 1:i + 1])
        raw[i] = 50.0 if hh == ll else (closes[i] - ll) / (hh - ll) * 100.0
    k_out = [None] * n
    for i in range(k_len + k_smooth - 2, n):
        k_out[i] = sum(raw[i - k_smooth + 1:i + 1]) / k_smooth
    d_out = [None] * n
    for i in range(k_len + k_smooth + d_len - 3, n):
        d_out[i] = sum(k_out[i - d_len + 1:i + 1]) / d_len
    return k_out, d_out


def _streak_series(closes):
    """Signed streak: +n after n consecutive up closes, -n after n down, 0 on flat."""
    streaks = [0] * len(closes)
    for i in range(1, len(closes)):
        if closes[i] > closes[i - 1]:
            streaks[i] = streaks[i - 1] + 1 if streaks[i - 1] > 0 else 1
        elif closes[i] < closes[i - 1]:
            streaks[i] = streaks[i - 1] - 1 if streaks[i - 1] < 0 else -1
        else:
            streaks[i] = 0
    return streaks


def _ibs(bar):
    rng = bar["high"] - bar["low"]
    if rng <= 0:
        return 0.5  # catalog: zero-range bar -> treat as 0.5 (no signal)
    return (bar["close"] - bar["low"]) / rng


def _session_start_index(ohlc):
    """Index of the first bar of the last bar's UTC day; None if no ts."""
    ts = ohlc[-1].get("ts")
    if ts is None:
        return None
    day0 = (int(ts) // SECONDS_PER_DAY) * SECONDS_PER_DAY
    for i in range(len(ohlc) - 1, -1, -1):
        if (ohlc[i].get("ts") or 0) < day0:
            return i + 1
    return 0


def _vwap(bars):
    pv = 0.0
    vv = 0.0
    for b in bars:
        v = b.get("volume") or 0.0
        pv += ((b["high"] + b["low"] + b["close"]) / 3.0) * v
        vv += v
    if vv <= 0:
        return None
    return pv / vv


def _utc_weekday(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).weekday()


# ---------------------------------------------------------------- RSI(2) / Connors family

def rsi2_connors_classic(ohlc, rsi_len=2, oversold=5.0, overbought=95.0,
                         trend_sma=200):
    """Catalog: RSI2_Connors_Classic — close > SMA200 & RSI(2)<5 -> BUY;
    close < SMA200 & RSI(2)>95 -> SELL."""
    tag = "mr_rsi2_connors_classic"
    if len(ohlc) < trend_sma + rsi_len + 1:
        return None
    closes = _series(ohlc, "close")
    trend = _sma(closes, trend_sma)
    r = _rsi(closes, rsi_len)
    if trend is None:
        return None
    if closes[-1] > trend and r < oversold:
        return _signal("BUY", tag, _conf((oversold - r) / oversold),
                       [f"{tag}: RSI({rsi_len}) {r:.1f} < {oversold} above SMA{trend_sma}"])
    if closes[-1] < trend and r > overbought:
        return _signal("SELL", tag, _conf((r - overbought) / (100 - overbought)),
                       [f"{tag}: RSI({rsi_len}) {r:.1f} > {overbought} below SMA{trend_sma}"])
    return None


def rsi2_connors_aggressive10(ohlc, rsi_len=2, oversold=10.0, overbought=90.0,
                              trend_sma=200):
    """Catalog: RSI2_Connors_Aggressive10 — wider 10/90 bands, more signals."""
    tag = "mr_rsi2_connors_aggressive10"
    if len(ohlc) < trend_sma + rsi_len + 1:
        return None
    closes = _series(ohlc, "close")
    trend = _sma(closes, trend_sma)
    r = _rsi(closes, rsi_len)
    if trend is None:
        return None
    if closes[-1] > trend and r < oversold:
        return _signal("BUY", tag, _conf((oversold - r) / oversold),
                       [f"{tag}: RSI({rsi_len}) {r:.1f} < {oversold} above SMA{trend_sma}"])
    if closes[-1] < trend and r > overbought:
        return _signal("SELL", tag, _conf((r - overbought) / (100 - overbought)),
                       [f"{tag}: RSI({rsi_len}) {r:.1f} > {overbought} below SMA{trend_sma}"])
    return None


def rsi2_triple_capitulation(ohlc, rsi_len=2, streak=3, oversold=10.0,
                             overbought=90.0, trend_sma=200):
    """Catalog: RSI2_Triple_Capitulation — RSI(2) closed beyond the band for
    `streak` consecutive bars, trend-filtered by SMA200."""
    tag = "mr_rsi2_triple_capitulation"
    if len(ohlc) < trend_sma + rsi_len + streak:
        return None
    closes = _series(ohlc, "close")
    trend = _sma(closes, trend_sma)
    rs = _rsi_series(closes, rsi_len, streak)
    if trend is None or len(rs) < streak:
        return None
    if closes[-1] > trend and all(v < oversold for v in rs):
        depth = min(rs)
        return _signal("BUY", tag, _conf((oversold - depth) / oversold),
                       [f"{tag}: RSI({rsi_len}) < {oversold} for {streak} bars "
                        f"(min {depth:.1f}) above SMA{trend_sma}"])
    if closes[-1] < trend and all(v > overbought for v in rs):
        peak = max(rs)
        return _signal("SELL", tag, _conf((peak - overbought) / (100 - overbought)),
                       [f"{tag}: RSI({rsi_len}) > {overbought} for {streak} bars "
                        f"(max {peak:.1f}) below SMA{trend_sma}"])
    return None


def rsi2_scalein_tps(ohlc, rsi_len=2, tranche_levels_long=(20.0, 15.0, 10.0, 5.0),
                     tranche_levels_short=(80.0, 85.0, 90.0, 95.0), trend_sma=200):
    """Catalog: RSI2_ScaleIn_TPS — stateless per-tranche emission: fires when
    RSI(2) crosses below the next long tranche (above for shorts) on the last
    bar; the position manager aggregates tranches as ONE position."""
    tag = "mr_rsi2_scalein_tps"
    if len(ohlc) < trend_sma + rsi_len + 2:
        return None
    closes = _series(ohlc, "close")
    trend = _sma(closes, trend_sma)
    rs = _rsi_series(closes, rsi_len, 2)
    if trend is None or len(rs) < 2:
        return None
    prev_r, cur_r = rs[-2], rs[-1]
    if closes[-1] > trend:
        for idx, level in enumerate(tranche_levels_long):
            if prev_r >= level > cur_r:
                return _signal("BUY", tag, _conf(idx + (level - cur_r) / max(level, 1e-9)),
                               [f"{tag}: tranche {idx + 1}/{len(tranche_levels_long)} — "
                                f"RSI({rsi_len}) crossed below {level} (now {cur_r:.1f})"])
    if closes[-1] < trend:
        for idx, level in enumerate(tranche_levels_short):
            if prev_r <= level < cur_r:
                return _signal("SELL", tag, _conf(idx + (cur_r - level) / max(100 - level, 1e-9)),
                               [f"{tag}: tranche {idx + 1}/{len(tranche_levels_short)} — "
                                f"RSI({rsi_len}) crossed above {level} (now {cur_r:.1f})"])
    return None


def rsi14_classic_fade(ohlc, rsi_len=14, oversold=30.0, overbought=70.0):
    """Catalog: RSI14_Classic_Fade — cross-BACK trigger: RSI(14) crosses up
    through 30 after being below -> BUY; crosses down through 70 -> SELL."""
    tag = "mr_rsi14_classic_fade"
    if len(ohlc) < rsi_len + 2:
        return None
    closes = _series(ohlc, "close")
    rs = _rsi_series(closes, rsi_len, 2)
    if len(rs) < 2:
        return None
    prev_r, cur_r = rs[-2], rs[-1]
    if prev_r < oversold <= cur_r:
        return _signal("BUY", tag, _conf((oversold - prev_r) / oversold),
                       [f"{tag}: RSI({rsi_len}) crossed up {prev_r:.1f} -> {cur_r:.1f} through {oversold}"])
    if prev_r > overbought >= cur_r:
        return _signal("SELL", tag, _conf((prev_r - overbought) / (100 - overbought)),
                       [f"{tag}: RSI({rsi_len}) crossed down {prev_r:.1f} -> {cur_r:.1f} through {overbought}"])
    return None


def connors_rsi_crsi(ohlc, rsi_price=3, rsi_streak=2, prank_lookback=100,
                     oversold=10.0, overbought=90.0, trend_sma=200):
    """Catalog: ConnorsRSI_CRSI — CRSI = (RSI(3) + RSI_of_streak(2) +
    PercentRank(ROC(1),100)) / 3; trend-filtered by SMA200."""
    tag = "mr_connors_rsi_crsi"
    need = max(trend_sma, prank_lookback) + 2
    if len(ohlc) < need:
        return None
    closes = _series(ohlc, "close")
    trend = _sma(closes, trend_sma)
    if trend is None:
        return None
    rsi_price_v = _rsi(closes, rsi_price)
    streaks = _streak_series(closes)
    rsi_streak_v = _rsi(streaks, rsi_streak)
    roc_today = closes[-1] - closes[-2]
    window = [closes[i] - closes[i - 1] for i in range(len(closes) - prank_lookback, len(closes) - 1)]
    prank = 100.0 * sum(1 for r in window if r < roc_today) / len(window)
    crsi = (rsi_price_v + rsi_streak_v + prank) / 3.0
    if closes[-1] > trend and crsi < oversold:
        return _signal("BUY", tag, _conf((oversold - crsi) / oversold),
                       [f"{tag}: CRSI {crsi:.1f} < {oversold} "
                        f"(RSI{rsi_price} {rsi_price_v:.1f}, streakRSI {rsi_streak_v:.1f}, "
                        f"%rank {prank:.0f}) above SMA{trend_sma}"])
    if closes[-1] < trend and crsi > overbought:
        return _signal("SELL", tag, _conf((crsi - overbought) / (100 - overbought)),
                       [f"{tag}: CRSI {crsi:.1f} > {overbought} "
                        f"(RSI{rsi_price} {rsi_price_v:.1f}, streakRSI {rsi_streak_v:.1f}, "
                        f"%rank {prank:.0f}) below SMA{trend_sma}"])
    return None


# ---------------------------------------------------------------- Bollinger family

def _bb_at(closes, end, period, mult):
    """(middle, upper, lower) of BB(period, mult) on closes[:end]; None if short."""
    if end < period:
        return None
    win = closes[end - period:end]
    mid = mean(win)
    sd = _stdev(win)
    if sd is None:
        return None
    return mid, mid + mult * sd, mid - mult * sd


def _pctb(close, bands):
    mid, upper, lower = bands
    width = upper - lower
    if width <= 0:
        return 0.5
    return (close - lower) / width


def bollinger_pctb_reversal(ohlc, bb_len=20, bb_mult=2.0):
    """Catalog: Bollinger_PctB_Reversal — %b<0 on bar t then crosses back
    above 0 on t+1 -> BUY; %b>1 then back below 1 -> SELL."""
    tag = "mr_bollinger_pctb_reversal"
    closes = _series(ohlc, "close")
    if len(closes) < bb_len + 1:
        return None
    prev_b = _bb_at(closes, len(closes) - 1, bb_len, bb_mult)
    cur_b = _bb_at(closes, len(closes), bb_len, bb_mult)
    if not prev_b or not cur_b:
        return None
    prev_pb = _pctb(closes[-2], prev_b)
    cur_pb = _pctb(closes[-1], cur_b)
    if prev_pb < 0.0 < cur_pb:
        return _signal("BUY", tag, _conf(-prev_pb),
                       [f"{tag}: %b re-entry {prev_pb:.2f} -> {cur_pb:.2f}"])
    if prev_pb > 1.0 > cur_pb:
        return _signal("SELL", tag, _conf(prev_pb - 1.0),
                       [f"{tag}: %b re-entry {prev_pb:.2f} -> {cur_pb:.2f}"])
    return None


def bollinger_bandtouch_wickfade(ohlc, bb_len=20, bb_mult=2.0):
    """Catalog: Bollinger_BandTouch_WickFade — wick beyond the band, body
    closes back inside with rejection-candle color."""
    tag = "mr_bollinger_bandtouch_wickfade"
    closes = _series(ohlc, "close")
    if len(closes) < bb_len:
        return None
    bands = _bb_at(closes, len(closes), bb_len, bb_mult)
    if not bands:
        return None
    _, upper, lower = bands
    bar = ohlc[-1]
    if bar["low"] <= lower < bar["close"] and bar["close"] > bar["open"]:
        wick = (lower - bar["low"]) / (upper - lower) if upper > lower else 0.0
        return _signal("BUY", tag, _conf(wick * 4),
                       [f"{tag}: wick below lower band, bullish close back inside"])
    if bar["high"] >= upper > bar["close"] and bar["close"] < bar["open"]:
        wick = (bar["high"] - upper) / (upper - lower) if upper > lower else 0.0
        return _signal("SELL", tag, _conf(wick * 4),
                       [f"{tag}: wick above upper band, bearish close back inside"])
    return None


def bollinger_midband_return(ohlc, bb_len=20, bb_mult=2.0, lookback=5,
                             rsi_len=14, rsi_os=35.0, rsi_ob=65.0):
    """Catalog: Bollinger_Midband_Return — band breach within `lookback` bars,
    RSI extreme, and a momentum-turn trigger through the previous bar's high/low."""
    tag = "mr_bollinger_midband_return"
    closes = _series(ohlc, "close")
    if len(closes) < bb_len + lookback + 1:
        return None
    r = _rsi(closes, rsi_len)
    breached_below = False
    breached_above = False
    for end in range(len(closes) - lookback, len(closes)):
        bands = _bb_at(closes, end, bb_len, bb_mult)
        if not bands:
            continue
        _, upper, lower = bands
        if closes[end - 1] < lower:
            breached_below = True
        if closes[end - 1] > upper:
            breached_above = True
    if breached_below and r < rsi_os and closes[-1] > ohlc[-2]["high"]:
        return _signal("BUY", tag, _conf((rsi_os - r) / rsi_os),
                       [f"{tag}: lower-band breach in {lookback} bars, RSI {r:.1f}, "
                        f"close over prev high {ohlc[-2]['high']:.5f}"])
    if breached_above and r > rsi_ob and closes[-1] < ohlc[-2]["low"]:
        return _signal("SELL", tag, _conf((r - rsi_ob) / (100 - rsi_ob)),
                       [f"{tag}: upper-band breach in {lookback} bars, RSI {r:.1f}, "
                        f"close under prev low {ohlc[-2]['low']:.5f}"])
    return None


# ---------------------------------------------------------------- Stochastic family

def stoch_extreme_crossfade(ohlc, k_len=14, k_smooth=3, d_len=3,
                            oversold=20.0, overbought=80.0):
    """Catalog: Stoch_Extreme_CrossFade — %K in the extreme zone crossing %D."""
    tag = "mr_stoch_extreme_crossfade"
    closes = _series(ohlc, "close")
    highs = _series(ohlc, "high")
    lows = _series(ohlc, "low")
    if len(closes) < k_len + k_smooth + d_len + 1:
        return None
    k, d = _stochastic(highs, lows, closes, k_len, k_smooth, d_len)
    if k[-1] is None or d[-1] is None or k[-2] is None or d[-2] is None:
        return None
    if k[-1] < oversold and k[-2] <= d[-2] and k[-1] > d[-1]:
        return _signal("BUY", tag, _conf((oversold - k[-1]) / oversold),
                       [f"{tag}: %K {k[-1]:.1f} crossed above %D {d[-1]:.1f} below {oversold}"])
    if k[-1] > overbought and k[-2] >= d[-2] and k[-1] < d[-1]:
        return _signal("SELL", tag, _conf((k[-1] - overbought) / (100 - overbought)),
                       [f"{tag}: %K {k[-1]:.1f} crossed below %D {d[-1]:.1f} above {overbought}"])
    return None


def stochrsi_double_oscillator(ohlc, rsi_len=14, stoch_len=14, k_smooth=3,
                               d_len=3, os_level=0.2, ob_level=0.8,
                               rsi_filter_long=40.0, rsi_filter_short=60.0,
                               tail=40):
    """Catalog: StochRSI_Double_Oscillator — StochRSI %K cross in the extreme
    zone, gated by RSI(14) so it stays mean-reversion, not noise."""
    tag = "mr_stochrsi_double_oscillator"
    closes = _series(ohlc, "close")
    n = len(closes)
    if n < rsi_len + stoch_len + k_smooth + d_len + 1:
        return None
    start = max(rsi_len, n - max(tail, stoch_len + k_smooth + d_len + 2))
    rs = [_rsi(closes[:i + 1], rsi_len) for i in range(start, n)]
    rsi_now = rs[-1]
    raw = []
    for j in range(len(rs)):
        if j < stoch_len - 1:
            raw.append(None)
            continue
        win = rs[j - stoch_len + 1:j + 1]
        lo, hi = min(win), max(win)
        raw.append(0.5 if hi == lo else (rs[j] - lo) / (hi - lo))
    k_vals, d_vals = [], []
    for j in range(len(raw)):
        if j < stoch_len + k_smooth - 2 or raw[j] is None:
            k_vals.append(None)
        else:
            k_vals.append(sum(raw[j - k_smooth + 1:j + 1]) / k_smooth)
    for j in range(len(k_vals)):
        if j < stoch_len + k_smooth + d_len - 3 or k_vals[j] is None:
            d_vals.append(None)
        else:
            d_vals.append(sum(k_vals[j - d_len + 1:j + 1]) / d_len)
    if None in (k_vals[-1], k_vals[-2], d_vals[-1], d_vals[-2]):
        return None
    k1, k2, d1, d2 = k_vals[-2], k_vals[-1], d_vals[-2], d_vals[-1]
    if k2 < os_level and k1 <= d1 and k2 > d2 and rsi_now < rsi_filter_long:
        return _signal("BUY", tag, _conf((os_level - k2) / os_level),
                       [f"{tag}: StochRSI %K {k2:.2f} crossed %D {d2:.2f}, "
                        f"RSI({rsi_len}) {rsi_now:.1f} < {rsi_filter_long}"])
    if k2 > ob_level and k1 >= d1 and k2 < d2 and rsi_now > rsi_filter_short:
        return _signal("SELL", tag, _conf((k2 - ob_level) / (1 - ob_level)),
                       [f"{tag}: StochRSI %K {k2:.2f} crossed %D {d2:.2f}, "
                        f"RSI({rsi_len}) {rsi_now:.1f} > {rsi_filter_short}"])
    return None


# ---------------------------------------------------------------- Divergence family

def _rsi_map(closes, period, back):
    """RSI(period) at each bar index in the last `back` bars."""
    n = len(closes)
    start = max(period + 1, n - back)
    return {i: _rsi(closes[:i + 1], period) for i in range(start, n)}


def rsi_divergence_fade(ohlc, rsi_len=14, swing_window=5, max_divergence_bars=30,
                        rsi_long_max=40.0, rsi_short_min=60.0, freshness=20):
    """Catalog: RSI_Divergence_Fade — price lower swing low vs RSI higher low
    (ATR-buffered), entered only when RSI crosses its 3-bar SMA (momentum turn)."""
    tag = "mr_rsi_divergence_fade"
    closes = _series(ohlc, "close")
    highs = _series(ohlc, "high")
    lows = _series(ohlc, "low")
    if len(closes) < max_divergence_bars + 2 * swing_window + rsi_len:
        return None
    atr_v = _atr_at(highs, lows, closes, 14)
    if not atr_v:
        return None
    rmap = _rsi_map(closes, rsi_len, max_divergence_bars + 2 * swing_window + 5)
    rs = [rmap[i] for i in sorted(rmap)]
    if len(rs) < 5:
        return None
    rsma_prev = mean(rs[-4:-1])
    rsma_cur = mean(rs[-3:])
    cross_up = rs[-2] <= rsma_prev and rs[-1] > rsma_cur
    cross_down = rs[-2] >= rsma_prev and rs[-1] < rsma_cur

    if cross_up:
        pivots = _swing_lows(ohlc, swing_window)
        pivots = [(i, p) for i, p in pivots if i in rmap]
        if len(pivots) >= 2:
            (i1, p1), (i2, p2) = pivots[-2], pivots[-1]
            if (i2 - i1 <= max_divergence_bars and i2 >= len(ohlc) - freshness
                    and p2 < p1 - 0.2 * atr_v
                    and rmap[i2] > rmap[i1] and rmap[i2] < rsi_long_max):
                return _signal("BUY", tag, _conf((rsi_long_max - rmap[i2]) / rsi_long_max),
                               [f"{tag}: bullish divergence {p1:.5f}->{p2:.5f} vs RSI "
                                f"{rmap[i1]:.1f}->{rmap[i2]:.1f}, RSI crossed its 3-bar SMA"])
    if cross_down:
        pivots = _swing_highs(ohlc, swing_window)
        pivots = [(i, p) for i, p in pivots if i in rmap]
        if len(pivots) >= 2:
            (i1, p1), (i2, p2) = pivots[-2], pivots[-1]
            if (i2 - i1 <= max_divergence_bars and i2 >= len(ohlc) - freshness
                    and p2 > p1 + 0.2 * atr_v
                    and rmap[i2] < rmap[i1] and rmap[i2] > rsi_short_min):
                return _signal("SELL", tag, _conf((rmap[i2] - rsi_short_min) / (100 - rsi_short_min)),
                               [f"{tag}: bearish divergence {p1:.5f}->{p2:.5f} vs RSI "
                                f"{rmap[i1]:.1f}->{rmap[i2]:.1f}, RSI crossed its 3-bar SMA"])
    return None


def stoch_divergence_fade(ohlc, k_len=14, k_smooth=3, d_len=3, swing_window=5,
                          max_bars=30, d_long_max=25.0, d_short_min=75.0,
                          freshness=20):
    """Catalog: Stoch_Divergence_Fade — same pivot machinery as
    RSI_Divergence_Fade parameterized by the %D oscillator; trigger %K x %D."""
    tag = "mr_stoch_divergence_fade"
    closes = _series(ohlc, "close")
    highs = _series(ohlc, "high")
    lows = _series(ohlc, "low")
    if len(closes) < max_bars + 2 * swing_window + k_len + k_smooth + d_len:
        return None
    atr_v = _atr_at(highs, lows, closes, 14)
    if not atr_v:
        return None
    k, d = _stochastic(highs, lows, closes, k_len, k_smooth, d_len)
    if None in (k[-1], k[-2], d[-1], d[-2]):
        return None
    cross_up = k[-2] <= d[-2] and k[-1] > d[-1]
    cross_down = k[-2] >= d[-2] and k[-1] < d[-1]

    if cross_up:
        pivots = [(i, p) for i, p in _swing_lows(ohlc, swing_window) if d[i] is not None]
        if len(pivots) >= 2:
            (i1, p1), (i2, p2) = pivots[-2], pivots[-1]
            if (i2 - i1 <= max_bars and i2 >= len(ohlc) - freshness
                    and p2 < p1 - 0.2 * atr_v
                    and d[i2] > d[i1] and d[i2] < d_long_max):
                return _signal("BUY", tag, _conf((d_long_max - d[i2]) / d_long_max),
                               [f"{tag}: bullish divergence, %D {d[i1]:.1f}->{d[i2]:.1f}, %K x %D up"])
    if cross_down:
        pivots = [(i, p) for i, p in _swing_highs(ohlc, swing_window) if d[i] is not None]
        if len(pivots) >= 2:
            (i1, p1), (i2, p2) = pivots[-2], pivots[-1]
            if (i2 - i1 <= max_bars and i2 >= len(ohlc) - freshness
                    and p2 > p1 + 0.2 * atr_v
                    and d[i2] < d[i1] and d[i2] > d_short_min):
                return _signal("SELL", tag, _conf((d[i2] - d_short_min) / (100 - d_short_min)),
                               [f"{tag}: bearish divergence, %D {d[i1]:.1f}->{d[i2]:.1f}, %K x %D down"])
    return None


# ---------------------------------------------------------------- Statistical / band family

def _zscore_at(closes, end, ma_len):
    if end < ma_len:
        return None
    win = closes[end - ma_len:end]
    sd = pstdev(win)
    if sd <= 0:
        return None
    return (closes[end - 1] - mean(win)) / sd


def zscore_price_reversion(ohlc, ma_len=50, entry_z=2.0):
    """Catalog: ZScore_Price_Reversion — z beyond +/-2 with the essential
    tick-back condition (z turns back toward the mean) before entering."""
    tag = "mr_zscore_price_reversion"
    closes = _series(ohlc, "close")
    if len(closes) < ma_len + 1:
        return None
    z_prev = _zscore_at(closes, len(closes) - 1, ma_len)
    z_cur = _zscore_at(closes, len(closes), ma_len)
    if z_prev is None or z_cur is None:
        return None
    if z_cur < -entry_z and z_cur > z_prev:
        return _signal("BUY", tag, _conf((-z_cur - entry_z) / entry_z),
                       [f"{tag}: z {z_cur:.2f} < -{entry_z}, ticking up from {z_prev:.2f}"])
    if z_cur > entry_z and z_cur < z_prev:
        return _signal("SELL", tag, _conf((z_cur - entry_z) / entry_z),
                       [f"{tag}: z {z_cur:.2f} > +{entry_z}, ticking down from {z_prev:.2f}"])
    return None


def vwap_atr_deviation_fade(ohlc, k_atr=2.0, atr_len=14, min_session_bars=3):
    """Catalog: VWAP_ATR_Deviation_Fade — close beyond session VWAP +/- k*ATR;
    the cross-into-zone bar is the stateless form of 'first bar satisfying,
    then cooldown'. Needs ts + volume (OHLCV); resets 00:00 UTC."""
    tag = "mr_vwap_atr_deviation_fade"
    if len(ohlc) < atr_len + 2:
        return None
    start = _session_start_index(ohlc)
    if start is None:
        return None
    session = ohlc[start:]
    if len(session) < min_session_bars:
        return None
    vwap_now = _vwap(session)
    if vwap_now is None:
        return None
    vwap_prev = _vwap(session[:-1]) or vwap_now
    closes = _series(ohlc, "close")
    highs = _series(ohlc, "high")
    lows = _series(ohlc, "low")
    atr_now = _atr_at(highs, lows, closes, atr_len)
    if not atr_now:
        return None
    atr_prev = _atr_at(highs[:-1], lows[:-1], closes[:-1], atr_len) or atr_now
    dev_now = closes[-1] - vwap_now
    dev_prev = closes[-2] - vwap_prev
    if dev_now < -k_atr * atr_now and dev_prev >= -k_atr * atr_prev:
        return _signal("BUY", tag, _conf((-dev_now / atr_now - k_atr) / k_atr),
                       [f"{tag}: close {dev_now / atr_now:.1f} ATR below session VWAP "
                        f"{vwap_now:.5f}"])
    if dev_now > k_atr * atr_now and dev_prev <= k_atr * atr_prev:
        return _signal("SELL", tag, _conf((dev_now / atr_now - k_atr) / k_atr),
                       [f"{tag}: close {dev_now / atr_now:.1f} ATR above session VWAP "
                        f"{vwap_now:.5f}"])
    return None


def vwap_stdband_reversion(ohlc, band_mult=2.0, min_session_bars=6):
    """Catalog: VWAP_StdBand_Reversion — close beyond VWAP +/- mult*sigma, then
    a rejection candle closes back inside the band. sigma = std of session
    (typical price - VWAP). Needs ts + volume; resets 00:00 UTC."""
    tag = "mr_vwap_stdband_reversion"
    start = _session_start_index(ohlc)
    if start is None:
        return None
    session = ohlc[start:]
    if len(session) < min_session_bars + 1:
        return None

    def _band(bars):
        v = _vwap(bars)
        if v is None:
            return None
        devs = [((b["high"] + b["low"] + b["close"]) / 3.0) - v for b in bars]
        if len(devs) < 2:
            return None
        sd = pstdev(devs)
        if sd <= 0:
            return None
        return v, v + band_mult * sd, v - band_mult * sd

    cur = _band(session)
    prev = _band(session[:-1])
    if not cur or not prev:
        return None
    _, upper, lower = cur
    _, prev_upper, prev_lower = prev
    bar = ohlc[-1]
    prev_close = ohlc[-2]["close"]
    if prev_close < prev_lower and bar["close"] > lower and bar["close"] > bar["open"]:
        depth_pct = (prev_lower - prev_close) / prev_close * 100.0
        return _signal("BUY", tag, _conf(depth_pct * 10.0),
                       [f"{tag}: bullish close back above VWAP -{band_mult}sd band"])
    if prev_close > prev_upper and bar["close"] < upper and bar["close"] < bar["open"]:
        depth_pct = (prev_close - prev_upper) / prev_close * 100.0
        return _signal("SELL", tag, _conf(depth_pct * 10.0),
                       [f"{tag}: bearish close back below VWAP +{band_mult}sd band"])
    return None


def keltner_midline_reversion(ohlc, ema_len=20, atr_len=10, mult=2.0):
    """Catalog: Keltner_Midline_Reversion — close beyond the Keltner band, next
    bar closes back inside; target is the EMA midline (exit handled downstream)."""
    tag = "mr_keltner_midline_reversion"
    closes = _series(ohlc, "close")
    highs = _series(ohlc, "high")
    lows = _series(ohlc, "low")
    if len(closes) < max(ema_len, atr_len + 1) + 1:
        return None
    emas = _ema_all(closes, ema_len)
    if len(emas) < 2:
        return None
    atr_cur = _atr_at(highs, lows, closes, atr_len)
    atr_prev = _atr_at(highs[:-1], lows[:-1], closes[:-1], atr_len)
    if not atr_cur or not atr_prev:
        return None
    lower_prev = emas[-2] - mult * atr_prev
    upper_prev = emas[-2] + mult * atr_prev
    lower_cur = emas[-1] - mult * atr_cur
    upper_cur = emas[-1] + mult * atr_cur
    if closes[-2] < lower_prev and closes[-1] > lower_cur:
        depth = (lower_prev - closes[-2]) / atr_prev
        return _signal("BUY", tag, _conf(depth),
                       [f"{tag}: close back above lower Keltner "
                        f"({closes[-2]:.5f} -> {closes[-1]:.5f})"])
    if closes[-2] > upper_prev and closes[-1] < upper_cur:
        depth = (closes[-2] - upper_prev) / atr_prev
        return _signal("SELL", tag, _conf(depth),
                       [f"{tag}: close back below upper Keltner "
                        f"({closes[-2]:.5f} -> {closes[-1]:.5f})"])
    return None


# ---------------------------------------------------------------- IBS family

def ibs_classic_daily(ohlc, ibs_buy=0.2, ibs_sell=0.8):
    """Catalog: IBS_Classic_Daily — IBS=(close-low)/(high-low); <0.2 BUY, >0.8
    SELL at the close of the signal bar. Zero-range bar -> IBS 0.5 (no signal)."""
    tag = "mr_ibs_classic_daily"
    if len(ohlc) < 2:
        return None
    v = _ibs(ohlc[-1])
    if v < ibs_buy:
        return _signal("BUY", tag, _conf((ibs_buy - v) / ibs_buy),
                       [f"{tag}: IBS {v:.2f} < {ibs_buy}"])
    if v > ibs_sell:
        return _signal("SELL", tag, _conf((v - ibs_sell) / (1 - ibs_sell)),
                       [f"{tag}: IBS {v:.2f} > {ibs_sell}"])
    return None


def ibs_trendfiltered(ohlc, ibs_buy=0.2, ibs_sell=0.8, trend_sma=50,
                      vol_mult=1.2, vol_len=20):
    """Catalog: IBS_TrendFiltered — IBS extreme + SMA(50) trend filter; the long
    side also demands a high-volume capitulation close (needs OHLCV)."""
    tag = "mr_ibs_trendfiltered"
    if len(ohlc) < trend_sma + 1:
        return None
    closes = _series(ohlc, "close")
    trend = _sma(closes, trend_sma)
    if trend is None:
        return None
    vols = [b.get("volume") or 0.0 for b in ohlc]
    v_avg = _sma(vols, vol_len) if any(vols) else None
    v = _ibs(ohlc[-1])
    if (v < ibs_buy and closes[-1] > trend and v_avg
            and vols[-1] > vol_mult * v_avg):
        return _signal("BUY", tag, _conf((ibs_buy - v) / ibs_buy),
                       [f"{tag}: IBS {v:.2f} < {ibs_buy}, above SMA{trend_sma}, "
                        f"volume {vols[-1] / v_avg:.1f}x avg"])
    if v > ibs_sell and closes[-1] < trend:
        return _signal("SELL", tag, _conf((v - ibs_sell) / (1 - ibs_sell)),
                       [f"{tag}: IBS {v:.2f} > {ibs_sell}, below SMA{trend_sma}"])
    return None


# ---------------------------------------------------------------- Session / gap family

def overnight_session_gap_fade(ohlc, gap_threshold=0.003, session_open_utc=7):
    """Catalog: Overnight_Session_Gap_Fade (forex) — at the first bar of the
    new session, fade a gap vs the previous session close back to the fill."""
    tag = "mr_overnight_session_gap_fade"
    if len(ohlc) < 2:
        return None
    ts_cur = ohlc[-1].get("ts")
    ts_prev = ohlc[-2].get("ts")
    if ts_cur is None or ts_prev is None:
        return None
    open_mark = (session_open_utc * 3600) % SECONDS_PER_DAY
    if int(ts_cur) % SECONDS_PER_DAY != open_mark or int(ts_prev) % SECONDS_PER_DAY == open_mark:
        return None
    prev_close = ohlc[-2]["close"]
    if prev_close <= 0:
        return None
    gap = ohlc[-1]["open"] / prev_close - 1.0
    if gap <= -gap_threshold:
        return _signal("BUY", tag, _conf(-gap / gap_threshold - 1.0),
                       [f"{tag}: session gap {gap * 100:.2f}% at {session_open_utc}:00 UTC, fade to fill"])
    if gap >= gap_threshold:
        return _signal("SELL", tag, _conf(gap / gap_threshold - 1.0),
                       [f"{tag}: session gap +{gap * 100:.2f}% at {session_open_utc}:00 UTC, fade to fill"])
    return None


def forex_weekend_gap_fill(ohlc, gap_threshold=0.004, max_gap=0.015):
    """Catalog: Forex_Weekend_Gap_Fill — Monday open gaps vs Friday close;
    fade toward the Friday close. Gaps beyond max_gap are treated as
    news-driven continuation and skipped."""
    tag = "mr_forex_weekend_gap_fill"
    if len(ohlc) < 2:
        return None
    ts_cur = ohlc[-1].get("ts")
    ts_prev = ohlc[-2].get("ts")
    if ts_cur is None or ts_prev is None:
        return None
    if _utc_weekday(ts_cur) != 0 or _utc_weekday(ts_prev) != 4:
        return None
    prev_close = ohlc[-2]["close"]
    if prev_close <= 0:
        return None
    gap = ohlc[-1]["open"] / prev_close - 1.0
    if abs(gap) > max_gap:
        return None
    if gap <= -gap_threshold:
        return _signal("BUY", tag, _conf(-gap / gap_threshold - 1.0),
                       [f"{tag}: weekend gap {gap * 100:.2f}%, target Friday close {prev_close:.5f}"])
    if gap >= gap_threshold:
        return _signal("SELL", tag, _conf(gap / gap_threshold - 1.0),
                       [f"{tag}: weekend gap +{gap * 100:.2f}%, target Friday close {prev_close:.5f}"])
    return None


# ---------------------------------------------------------------- OU / stretch family

def ou_halflife_spread_reversion(ohlc, ma_len=20, ou_window=120, hl_min=5.0,
                                 hl_max=40.0, entry_z=2.0, add_z=3.0):
    """Catalog: OU_HalfLife_Spread_Reversion — SINGLE-SERIES PROXY per the
    catalog's data_needs note (S = price - MA(price,N) as a self-spread).
    AR(1) fit on S over ou_window bars; trade only when 5 <= half-life <= 40;
    z-window = 2*HL; z<-2 BUY (z<-3 = second-tranche zone), z>+2 SELL."""
    tag = "mr_ou_halflife_spread_reversion"
    closes = _series(ohlc, "close")
    if len(closes) < ou_window + ma_len + 5:
        return None
    spread = []
    for end in range(len(closes) - ou_window - 1, len(closes) + 1):
        m = _sma(closes[:end], ma_len)
        if m is None:
            return None
        spread.append(closes[end - 1] - m)
    x = spread[:-1]
    y = [spread[i] - spread[i - 1] for i in range(1, len(spread))]
    mx, my = mean(x), mean(y)
    var_x = sum((v - mx) ** 2 for v in x)
    if var_x <= 0:
        return None
    alpha = sum((x[i] - mx) * (y[i] - my) for i in range(len(x))) / var_x
    if alpha >= 0 or 1.0 + alpha <= 0:
        return None
    half_life = math.log(2) / abs(math.log(1.0 + alpha))
    if not (hl_min <= half_life <= hl_max):
        return None
    w = max(2, int(round(2 * half_life)))
    if len(spread) < w:
        return None
    win = spread[-w:]
    sd = pstdev(win)
    if sd <= 0:
        return None
    z = (spread[-1] - mean(win)) / sd
    if z < -entry_z:
        tranche = ", second-tranche zone" if z < -add_z else ""
        return _signal("BUY", tag, _conf((-z - entry_z) / entry_z),
                       [f"{tag}: self-spread z {z:.2f} < -{entry_z} "
                        f"(HL {half_life:.1f} bars, window {w}){tranche}"])
    if z > entry_z:
        tranche = ", second-tranche zone" if z > add_z else ""
        return _signal("SELL", tag, _conf((z - entry_z) / entry_z),
                       [f"{tag}: self-spread z {z:.2f} > +{entry_z} "
                        f"(HL {half_life:.1f} bars, window {w}){tranche}"])
    return None


def rubberband_sma_stretch(ohlc, sma_len=20, atr_len=14, stretch_atr=2.5):
    """Catalog: RubberBand_SMA_Stretch — price stretched N ATRs from SMA(20)
    snaps back; candle-direction trigger avoids catching the falling knife."""
    tag = "mr_rubberband_sma_stretch"
    closes = _series(ohlc, "close")
    highs = _series(ohlc, "high")
    lows = _series(ohlc, "low")
    if len(closes) < max(sma_len, atr_len + 1):
        return None
    m = _sma(closes, sma_len)
    a = _atr_at(highs, lows, closes, atr_len)
    if m is None or not a:
        return None
    stretch = (closes[-1] - m) / a
    bar = ohlc[-1]
    if stretch < -stretch_atr and bar["close"] > bar["open"]:
        return _signal("BUY", tag, _conf((-stretch - stretch_atr) / stretch_atr),
                       [f"{tag}: stretch {stretch:.1f} ATR below SMA{sma_len}, bullish bar"])
    if stretch > stretch_atr and bar["close"] < bar["open"]:
        return _signal("SELL", tag, _conf((stretch - stretch_atr) / stretch_atr),
                       [f"{tag}: stretch +{stretch:.1f} ATR above SMA{sma_len}, bearish bar"])
    return None


def rubberband_pct_from_ma(ohlc, ma_len=50, stretch_pct=0.05, rsi_len=2,
                           rsi_os=10.0, rsi_ob=90.0):
    """Catalog: RubberBand_Pct_From_MA — deep-percent stretch (>=5% from
    SMA50) plus an RSI(2) panic trigger."""
    tag = "mr_rubberband_pct_from_ma"
    closes = _series(ohlc, "close")
    if len(closes) < ma_len + rsi_len + 1:
        return None
    m = _sma(closes, ma_len)
    if not m:
        return None
    r = _rsi(closes, rsi_len)
    pct = closes[-1] / m - 1.0
    if pct <= -stretch_pct and r < rsi_os:
        return _signal("BUY", tag, _conf((-pct - stretch_pct) / stretch_pct),
                       [f"{tag}: {pct * 100:.1f}% below SMA{ma_len}, RSI({rsi_len}) {r:.1f}"])
    if pct >= stretch_pct and r > rsi_ob:
        return _signal("SELL", tag, _conf((pct - stretch_pct) / stretch_pct),
                       [f"{tag}: +{pct * 100:.1f}% above SMA{ma_len}, RSI({rsi_len}) {r:.1f}"])
    return None


# ---------------------------------------------------------------- Regime-gated / crypto-session

def _hurst(closes, lags=(2, 4, 8, 16)):
    """Lagged-variance Hurst estimate: H = 0.5 * slope of log Var(lag) vs
    log(lag). <0.5 mean-reverting, ~0.5 random walk, >0.5 trending."""
    xs, ys = [], []
    n = len(closes)
    for lag in lags:
        if n < lag * 2:
            continue
        diffs = [closes[i] - closes[i - lag] for i in range(lag, n)]
        if len(diffs) < 2:
            continue
        var = pstdev(diffs) ** 2
        if var <= 0:
            continue
        xs.append(math.log(lag))
        ys.append(math.log(var))
    if len(xs) < 2:
        return None
    mx, my = mean(xs), mean(ys)
    den = sum((v - mx) ** 2 for v in xs)
    if den <= 0:
        return None
    slope = sum((xs[i] - mx) * (ys[i] - my) for i in range(len(xs))) / den
    return max(0.0, min(1.0, slope / 2.0))


def hurst_gated_zscore(ohlc, hurst_window=100, hurst_max=0.45, smooth=10,
                       ma_len=40, entry_z=2.0):
    """Catalog: Hurst_Gated_ZScore — z-score reversion traded only when the
    smoothed Hurst exponent says the series is mean-reverting (H < 0.45)."""
    tag = "mr_hurst_gated_zscore"
    closes = _series(ohlc, "close")
    if len(closes) < hurst_window + smooth:
        return None
    estimates = []
    for end in range(len(closes) - smooth + 1, len(closes) + 1):
        h = _hurst(closes[end - hurst_window:end])
        if h is not None:
            estimates.append(h)
    if len(estimates) < 3:
        return None
    h_smoothed = _ema(estimates, min(smooth, len(estimates)))
    if h_smoothed is None or h_smoothed >= hurst_max:
        return None
    z = _zscore_at(closes, len(closes), ma_len)
    if z is None:
        return None
    if z < -entry_z:
        return _signal("BUY", tag, _conf((-z - entry_z) / entry_z),
                       [f"{tag}: H {h_smoothed:.2f} < {hurst_max}, z {z:.2f} < -{entry_z}"])
    if z > entry_z:
        return _signal("SELL", tag, _conf((z - entry_z) / entry_z),
                       [f"{tag}: H {h_smoothed:.2f} < {hurst_max}, z {z:.2f} > +{entry_z}"])
    return None


def overnight_crypto_session_reversion(ohlc, day_ret_thresh=0.04,
                                       ibs_long=0.3, ibs_short=0.7):
    """Catalog: Overnight_Crypto_Session_Reversion — on the bar closing at
    00:00 UTC, fade prior-24h moves of +/-4% when the day closes near its
    extreme (IBS confirm). Crypto 'session' = UTC day."""
    tag = "mr_overnight_crypto_session_reversion"
    if len(ohlc) < 25:
        return None
    ts_cur = ohlc[-1].get("ts")
    ts_prev = ohlc[-2].get("ts")
    if ts_cur is None or ts_prev is None:
        return None
    bar_secs = int(ts_cur) - int(ts_prev)
    if bar_secs <= 0 or (int(ts_cur) + bar_secs) % SECONDS_PER_DAY != 0:
        return None  # signal bar is the one CLOSING at UTC midnight
    ref_close = None
    for i in range(len(ohlc) - 1, -1, -1):
        if (ohlc[i].get("ts") or 0) <= int(ts_cur) - SECONDS_PER_DAY:
            ref_close = ohlc[i]["close"]
            break
    if ref_close is None:
        ref_close = _series(ohlc, "close")[-25]
    if not ref_close:
        return None
    day_ret = ohlc[-1]["close"] / ref_close - 1.0
    v = _ibs(ohlc[-1])
    if day_ret <= -day_ret_thresh and v < ibs_long:
        return _signal("BUY", tag, _conf((-day_ret - day_ret_thresh) / day_ret_thresh),
                       [f"{tag}: UTC day {day_ret * 100:.1f}%, IBS {v:.2f} — fade the day"])
    if day_ret >= day_ret_thresh and v > ibs_short:
        return _signal("SELL", tag, _conf((day_ret - day_ret_thresh) / day_ret_thresh),
                       [f"{tag}: UTC day +{day_ret * 100:.1f}%, IBS {v:.2f} — fade the day"])
    return None


# ---------------------------------------------------------------- registry
# (display_name, fn) tuples mirroring core.strategies.ALL_STRATEGIES so the
# integrator can splice this family in; the embedded "strategy" tag of every
# returned dict is the machine name used for per-strategy stats.

MEAN_REVERSION_STRATEGIES = [
    ("MR - RSI2 Connors Classic", rsi2_connors_classic),
    ("MR - RSI2 Connors Aggressive10", rsi2_connors_aggressive10),
    ("MR - RSI2 Triple Capitulation", rsi2_triple_capitulation),
    ("MR - RSI2 Scale-In TPS", rsi2_scalein_tps),
    ("MR - RSI14 Classic Fade", rsi14_classic_fade),
    ("MR - ConnorsRSI", connors_rsi_crsi),
    ("MR - Bollinger %b Reversal", bollinger_pctb_reversal),
    ("MR - Bollinger BandTouch WickFade", bollinger_bandtouch_wickfade),
    ("MR - Bollinger Midband Return", bollinger_midband_return),
    ("MR - Stoch Extreme CrossFade", stoch_extreme_crossfade),
    ("MR - StochRSI Double Oscillator", stochrsi_double_oscillator),
    ("MR - RSI Divergence Fade", rsi_divergence_fade),
    ("MR - Stoch Divergence Fade", stoch_divergence_fade),
    ("MR - ZScore Price Reversion", zscore_price_reversion),
    ("MR - VWAP ATR Deviation Fade", vwap_atr_deviation_fade),
    ("MR - VWAP StdBand Reversion", vwap_stdband_reversion),
    ("MR - Keltner Midline Reversion", keltner_midline_reversion),
    ("MR - IBS Classic Daily", ibs_classic_daily),
    ("MR - IBS TrendFiltered", ibs_trendfiltered),
    ("MR - Overnight Session Gap Fade", overnight_session_gap_fade),
    ("MR - Forex Weekend Gap Fill", forex_weekend_gap_fill),
    ("MR - OU HalfLife Spread Reversion", ou_halflife_spread_reversion),
    ("MR - RubberBand SMA Stretch", rubberband_sma_stretch),
    ("MR - RubberBand Pct From MA", rubberband_pct_from_ma),
    ("MR - Hurst Gated ZScore", hurst_gated_zscore),
    ("MR - Overnight Crypto Session Reversion", overnight_crypto_session_reversion),
]

SKIPPED_CATALOG_ENTRIES = {
    "Pairs_Cointegration_ZScore": "needs two synchronized symbols; no single-series proxy in catalog",
    "Perp_Basis_Reversion": "needs perp + spot series simultaneously; no OHLC proxy",
    "Funding_Rate_Extreme_Fade": "needs a funding-rate feed beyond OHLCV (parked per catalog note)",
}

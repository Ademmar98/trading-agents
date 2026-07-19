"""Breakout & Volatility strategy family (per-tag signals).

Implements all 29 OHLC/OHLCV-compatible entries from
research/breakout_volatility.md under the existing signal contract: each
function takes an OHLC bar list (dicts with open/high/low/close/volume/ts;
last bar CLOSED) and returns {"action", "confidence", "reasons", "strategy",
...} or None, exactly like core/strategies.py. Every signal dict carries its
own unique strategy tag so per-strategy stats stay attributable (same per-tag
pattern as core/swing.py and core/scalp15.py). SELL means "exit long / stay
flat" on the spot-only paper broker. Near-duplicate clusters (NR4/NR7/NRIB,
filtered Donchian variants) are implemented individually per the assignment —
dedupe happens downstream in the paper-cycle cluster accounting (catalog
implementation note #3).

Implemented (catalog order -> tag):
  #1  Donchian 20 (Turtle S1)              -> Breakout - Donchian 20 (Turtle S1)
  #2  Donchian 55 (Turtle S2)              -> Breakout - Donchian 55 (Turtle S2)
  #3  N-Day High + trend filter            -> Breakout - N-Day High Trend-Filtered
  #4  PDH/PDL                              -> Breakout - PDH/PDL
  #5  Weekly Opening Range                 -> Breakout - Weekly Opening Range
  #6  ORB-30                               -> Breakout - ORB-30
  #7  Crabel Stretch                       -> Breakout - Crabel Stretch
  #8  Asian Range (Tokyo compression)      -> Breakout - Asian Range
  #9  London Open first-hour               -> Breakout - London Open
  #10 New York Open pre-NY range           -> Breakout - NY Open
  #11 Daily Pivot R1/S1                    -> Breakout - Daily Pivot R1/S1
  #12 Camarilla H4/L4                      -> Breakout - Camarilla H4/L4
  #13 TTM Squeeze                          -> Volatility - TTM Squeeze
  #14 Bollinger Band Walk                  -> Volatility - Bollinger Band Walk
  #15 Keltner Channel Breakout             -> Breakout - Keltner Channel
  #16 ATR Volatility Expansion             -> Volatility - ATR Expansion
  #17 NR4                                  -> Breakout - NR4
  #18 NR7                                  -> Breakout - NR7
  #19 Inside Bar (mother-bar levels)       -> Breakout - Inside Bar (Mother)
  #20 NRIB                                 -> Breakout - NRIB
  #21 VCP (Minervini geometric core)       -> Breakout - VCP
  #22 Gap-and-Go                           -> Breakout - Gap and Go
  #23 CME/Weekend Gap Fill                 -> Volatility - Gap Fill
  #24 Swing Failure Pattern fade           -> Breakout - Swing Failure Fade
  #25 Turtle Soup                          -> Breakout - Turtle Soup
  #26 Breakout-Retest Continuation         -> Breakout - Retest Continuation
  #27 Volume-Confirmed Donchian            -> Breakout - Volume-Confirmed Donchian
  #28 Volatility Regime Gate               -> Breakout - Volatility Regime Gate
  #29 Chaikin Volatility Expansion         -> Volatility - Chaikin Expansion

Family-wide conventions (catalog header + implementation notes):
  - "Close above X" is confirmed on the last CLOSED bar with fresh-cross
    semantics (previous closed bar not yet beyond the level) so each break
    produces one attributable signal, matching the catalog convention.
  - Donchian channels always use PRIOR bars only (exclude the current bar) —
    catalog implementation note #1 (off-by-one changes every backtest).
  - Session strategies (#4-#12, #22, #23) read bar timestamps ('ts' unix
    seconds UTC, ISO 'date' as fallback). If timestamps are absent the
    session logic is skipped gracefully: the function returns None and never
    crashes (assignment requirement). Day boundaries are 00:00 UTC (crypto
    convention); the London/NY windows use the UTC-summer fixed hours noted
    in the catalog for 24/7 crypto, with the DST caveat documented per
    function (catalog note for #9: anchor on bar timestamps, not server
    clock).
  - Stateful catalog features simplified to stateless per-bar form (noted
    per function): Turtle S1 skip-winner filter, once-per-day/once-per-week
    order limits -> "first close beyond the level today/this week", ORB/Crabel
    stop orders -> close-confirmed triggers, #26 ARMED->RETESTING->TRIGGERED
    state machine -> windowed re-evaluation of the most recent breakout leg.
  - Daily-ATR references (ATR(14, 1d)) are computed on UTC-aggregated daily
    bars; when the fetched history holds too few days (< 3) the daily-ATR
    width filters are skipped and buffers fall back to execution-timeframe
    ATR(14) — documented fallback so strategies stay live on short paper-test
    histories while exact when data suffices.
  - Confidence mapping follows catalog note #5: breakout-bar close-strength,
    volume multiple vs SMA20, and ATR-percentile regime score where natural.
"""
from datetime import datetime, timedelta, timezone
from statistics import pstdev

from core.indicators import sma, ema_all

# ---------------------------------------------------------------------------
# generic helpers
# ---------------------------------------------------------------------------


def _closes(ohlc):
    return [b["close"] for b in ohlc]


def _highs(ohlc):
    return [b["high"] for b in ohlc]


def _lows(ohlc):
    return [b["low"] for b in ohlc]


def _vols(ohlc):
    return [float(b.get("volume", 0.0) or 0.0) for b in ohlc]


def _rng(bar):
    return bar["high"] - bar["low"]


def _close_pos(bar):
    """Close position within its bar: 1.0 = closed on the high, 0.0 = low."""
    r = _rng(bar)
    return (bar["close"] - bar["low"]) / r if r > 0 else 0.5


def _bar_dt(bar):
    """Defensive bar timestamp -> aware UTC datetime, or None.

    Primary source is the numeric 'ts' field (unix seconds; tolerates ms);
    falls back to parsing the ISO 'date' field. Session strategies stay
    silent when neither exists.
    """
    ts = bar.get("ts")
    if isinstance(ts, (int, float)) and ts > 0:
        if ts > 1e12:  # milliseconds slipped in
            ts = ts / 1000.0
        try:
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    date_s = bar.get("date")
    if isinstance(date_s, str):
        try:
            dt = datetime.fromisoformat(date_s.replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def _atr_series(ohlc, period=14):
    """Wilder ATR series aligned with bars; None for the first `period`."""
    n = len(ohlc)
    out = [None] * n
    if n < period + 1:
        return out
    trs = [0.0]
    for i in range(1, n):
        h, l, pc = ohlc[i]["high"], ohlc[i]["low"], ohlc[i - 1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    a = sum(trs[1:period + 1]) / period
    out[period] = a
    for i in range(period + 1, n):
        a = (a * (period - 1) + trs[i]) / period
        out[i] = a
    return out


def _atr_val(ohlc, period=14):
    s = _atr_series(ohlc, period)
    return s[-1] if s else None


def _adx(ohlc, period=14):
    """Wilder's ADX, fully smoothed (regime gates need the real series)."""
    if len(ohlc) < period * 2 + 1:
        return None
    trs, pdm, ndm = [], [], []
    for i in range(1, len(ohlc)):
        h, l, pc = ohlc[i]["high"], ohlc[i]["low"], ohlc[i - 1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
        up = ohlc[i]["high"] - ohlc[i - 1]["high"]
        dn = ohlc[i - 1]["low"] - ohlc[i]["low"]
        pdm.append(up if up > dn and up > 0 else 0.0)
        ndm.append(dn if dn > up and dn > 0 else 0.0)

    def _wilder(vals):
        s = sum(vals[:period])
        out = [s]
        for v in vals[period:]:
            s = s - s / period + v
            out.append(s)
        return out

    atr_s, pdm_s, ndm_s = _wilder(trs), _wilder(pdm), _wilder(ndm)
    dxs = []
    for a, p, n_ in zip(atr_s, pdm_s, ndm_s):
        if a == 0:
            dxs.append(0.0)
            continue
        di_p, di_n = p / a * 100, n_ / a * 100
        denom = di_p + di_n
        dxs.append(abs(di_p - di_n) / denom * 100 if denom else 0.0)
    if len(dxs) < period:
        return None
    adx = sum(dxs[:period]) / period
    for d in dxs[period:]:
        adx = (adx * (period - 1) + d) / period
    return adx


def _donchian(ohlc, period):
    """(highest high, lowest low) of the `period` bars BEFORE the last bar.

    Lookahead-free channel — catalog implementation note #1."""
    return _donchian_at(ohlc, period, len(ohlc) - 1)


def _donchian_at(ohlc, period, idx):
    """Channel of the `period` bars immediately before bar `idx`."""
    if idx < period or len(ohlc) < period + 1:
        return None, None
    window = ohlc[idx - period:idx]
    return max(b["high"] for b in window), min(b["low"] for b in window)


def _sig(action, tag, confidence, price, reasons):
    return {
        "action": action,
        "strategy": tag,
        "confidence": round(min(confidence, 0.95), 3),
        "price": price,
        "reasons": [f"{tag}: {r}" for r in reasons],
    }


def _strength_conf(base, bar, direction):
    """Catalog note #5: bump confidence when the trigger bar closes in the
    strong half of its range in the signal direction."""
    pos = _close_pos(bar)
    if direction == "BUY":
        return base + 0.10 * max(0.0, (pos - 0.5) * 2)
    return base + 0.10 * max(0.0, (0.5 - pos) * 2)


# ---------------------------------------------------------------------------
# session helpers (UTC day/week windows on bar timestamps)
# ---------------------------------------------------------------------------


def _split_days(ohlc):
    """Group consecutive bars by UTC calendar day. Returns a list of
    day-lists, or None when ANY bar lacks a usable timestamp — session logic
    is then skipped gracefully (assignment requirement)."""
    days = []
    for b in ohlc:
        dt = _bar_dt(b)
        if dt is None:
            return None
        if days and _bar_dt(days[-1][-1]).date() == dt.date():
            days[-1].append(b)
        else:
            days.append([b])
    return days


def _daily_ohlc(days):
    return [{
        "open": d[0]["open"],
        "high": max(b["high"] for b in d),
        "low": min(b["low"] for b in d),
        "close": d[-1]["close"],
        "volume": sum(float(b.get("volume", 0.0) or 0.0) for b in d),
    } for d in days]


def _daily_atr(daily, period=14):
    """ATR on aggregated daily bars (period shrinks to available history);
    None when fewer than 3 daily bars exist."""
    if len(daily) < 3:
        return None
    return _atr_val(daily, min(period, len(daily) - 1))


def _first_cross(bars, level, direction, start=0):
    """True when the LAST bar of `bars` is the first (from index `start`) to
    close beyond `level` — the stateless form of the catalog's once-per-day /
    one-fill-per-session order limits."""
    if direction == "BUY":
        if bars[-1]["close"] <= level:
            return False
        return all(b["close"] <= level for b in bars[start:-1])
    if bars[-1]["close"] >= level:
        return False
    return all(b["close"] >= level for b in bars[start:-1])


def _day_bounds(day_bars):
    """(midnight datetime of the day, list of bar datetimes) for a day group."""
    dts = [_bar_dt(b) for b in day_bars]
    midnight = dts[-1].replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight, dts


# ---------------------------------------------------------------------------
# #1 — Donchian 20-Bar Breakout (Turtle System 1)
# ---------------------------------------------------------------------------


def donchian_20_turtle_s1(ohlc, entry_len=20, n_atr=20):
    """Close beyond the prior 20-bar extreme (close-confirmed variant of the
    intraday touch — noted in catalog). The authentic skip-winner filter is
    stateful and stays OFF by default (catalog params/notes)."""
    tag = "Breakout - Donchian 20 (Turtle S1)"
    if len(ohlc) < entry_len + 5:
        return None
    dh, dl = _donchian(ohlc, entry_len)
    a = _atr_val(ohlc, n_atr)
    if dh is None or not a:
        return None
    last, prev = ohlc[-1], ohlc[-2]
    if last["close"] > dh and prev["close"] <= dh:
        return _sig("BUY", tag, _strength_conf(0.60, last, "BUY"), last["close"],
                    [f"close above prior {entry_len}-bar high {dh:.5f} (fresh break)",
                     f"N=ATR({n_atr}) {a:.5f}; advisory SL = entry - 2N, "
                     f"exit below 10-bar low; skip-winner filter off (stateful)"])
    if last["close"] < dl and prev["close"] >= dl:
        return _sig("SELL", tag, _strength_conf(0.60, last, "SELL"), last["close"],
                    [f"close below prior {entry_len}-bar low {dl:.5f} (fresh break)",
                     f"N=ATR({n_atr}) {a:.5f}; advisory SL = entry + 2N (spot: flat)"])
    return None


# ---------------------------------------------------------------------------
# #2 — Donchian 55-Bar Breakout (Turtle System 2)
# ---------------------------------------------------------------------------


def donchian_55_turtle_s2(ohlc, entry_len=55, n_atr=20):
    """Slower Turtle system; takes every signal (no skip filter by design)."""
    tag = "Breakout - Donchian 55 (Turtle S2)"
    if len(ohlc) < entry_len + 5:
        return None
    dh, dl = _donchian(ohlc, entry_len)
    a = _atr_val(ohlc, n_atr)
    if dh is None or not a:
        return None
    last, prev = ohlc[-1], ohlc[-2]
    if last["close"] > dh and prev["close"] <= dh:
        return _sig("BUY", tag, _strength_conf(0.60, last, "BUY"), last["close"],
                    [f"close above prior {entry_len}-bar high {dh:.5f}",
                     f"N=ATR({n_atr}) {a:.5f}; advisory SL = entry - 2N, "
                     f"exit beyond 20-bar opposite extreme"])
    if last["close"] < dl and prev["close"] >= dl:
        return _sig("SELL", tag, _strength_conf(0.60, last, "SELL"), last["close"],
                    [f"close below prior {entry_len}-bar low {dl:.5f}",
                     f"N=ATR({n_atr}) {a:.5f}; advisory SL = entry + 2N (spot: flat)"])
    return None


# ---------------------------------------------------------------------------
# #3 — N-Day High Breakout with Trend Filter (EMA200 + slope gate)
# ---------------------------------------------------------------------------


def nday_high_trend_filtered(ohlc, n=20, trend_ema=200, slope_lookback=10,
                             buffer_atr=0.1):
    """Donchian break gated by close vs EMA200 and EMA200 slope — the
    published fix for Donchian chop (catalog #3)."""
    tag = "Breakout - N-Day High Trend-Filtered"
    if len(ohlc) < trend_ema + slope_lookback + 2:
        return None
    closes = _closes(ohlc)
    dh, dl = _donchian(ohlc, n)
    a = _atr_val(ohlc, 14)
    ema_s = ema_all(closes, trend_ema)
    if dh is None or not a or len(ema_s) < slope_lookback + 1:
        return None
    rising = ema_s[-1] > ema_s[-1 - slope_lookback]
    falling = ema_s[-1] < ema_s[-1 - slope_lookback]
    last, prev = ohlc[-1], ohlc[-2]
    lvl_up, lvl_dn = dh + buffer_atr * a, dl - buffer_atr * a
    if (last["close"] > lvl_up and prev["close"] <= lvl_up
            and last["close"] > ema_s[-1] and rising):
        return _sig("BUY", tag, _strength_conf(0.65, last, "BUY"), last["close"],
                    [f"close > prior {n}-bar high + {buffer_atr}*ATR ({lvl_up:.5f})",
                     f"above rising EMA{trend_ema} ({ema_s[-1]:.5f}); "
                     f"advisory SL = entry - 2*ATR, TP = 3R"])
    if (last["close"] < lvl_dn and prev["close"] >= lvl_dn
            and last["close"] < ema_s[-1] and falling):
        return _sig("SELL", tag, _strength_conf(0.65, last, "SELL"), last["close"],
                    [f"close < prior {n}-bar low - {buffer_atr}*ATR ({lvl_dn:.5f})",
                     f"below falling EMA{trend_ema} ({ema_s[-1]:.5f}) (spot: flat)"])
    return None


# ---------------------------------------------------------------------------
# #4 — Previous Day High/Low Breakout (PDH/PDL), UTC day boundaries
# ---------------------------------------------------------------------------


def pdh_pdl_breakout(ohlc, buffer_atr=0.1, vol_mult=1.2):
    """Close beyond yesterday's high/low + buffer. Stateless once-per-day
    limit = first close beyond the level today. Crypto weekend caveat gate:
    trigger-bar volume >= 1.2*SMA(volume,20) when volume data exists."""
    tag = "Breakout - PDH/PDL"
    if len(ohlc) < 20:
        return None
    days = _split_days(ohlc)
    if not days or len(days) < 2:
        return None
    daily = _daily_ohlc(days)
    prev_day = daily[-2]
    today = days[-1]
    a = _daily_atr(daily) or _atr_val(ohlc, 14)  # short-history fallback
    if not a:
        return None
    last = ohlc[-1]
    vols = _vols(ohlc)
    vol_ok = True
    if sum(vols[-20:]) > 0:
        v_sma = sma(vols, 20)
        vol_ok = v_sma is not None and vols[-1] >= vol_mult * v_sma
    lvl_up = prev_day["high"] + buffer_atr * a
    lvl_dn = prev_day["low"] - buffer_atr * a
    if vol_ok and _first_cross(today, lvl_up, "BUY"):
        return _sig("BUY", tag, _strength_conf(0.60, last, "BUY"), last["close"],
                    [f"first close today above prev-day high {prev_day['high']:.5f} "
                     f"+ {buffer_atr}*ATR ({lvl_up:.5f})",
                     f"advisory SL = 1*ATR({a:.5f}) or prev-day mid, TP = 1.5R, "
                     f"flat by day end"])
    if vol_ok and _first_cross(today, lvl_dn, "SELL"):
        return _sig("SELL", tag, _strength_conf(0.60, last, "SELL"), last["close"],
                    [f"first close today below prev-day low {prev_day['low']:.5f} "
                     f"- {buffer_atr}*ATR ({lvl_dn:.5f}) (spot: flat)"])
    return None


# ---------------------------------------------------------------------------
# #5 — Weekly Opening Range Breakout (week opens Monday 00:00 UTC, crypto)
# ---------------------------------------------------------------------------


def weekly_opening_range(ohlc, range_hours=12, buffer_atr=0.2,
                         validity_days=3, max_range_atr=1.5):
    """Range = high/low of the first 12h of the UTC week; trade the break
    during the first 3 days. Weeks whose opening range already exceeds
    1.5*ATR are skipped (no compression, no edge)."""
    tag = "Breakout - Weekly Opening Range"
    if len(ohlc) < 20:
        return None
    days = _split_days(ohlc)
    if not days:
        return None
    last_dt = _bar_dt(ohlc[-1])
    monday = (last_dt - timedelta(days=last_dt.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0)
    week_bars = [b for d in days for b in d
                 if _bar_dt(b) >= monday]
    if not week_bars:
        return None
    range_end = monday + timedelta(hours=range_hours)
    range_bars = [b for b in week_bars if _bar_dt(b) < range_end]
    after = [b for b in week_bars if _bar_dt(b) >= range_end]
    if not range_bars or not after or after[-1] is not ohlc[-1]:
        return None
    if last_dt >= monday + timedelta(days=validity_days):
        return None  # setup cancelled after day 3
    rh = max(b["high"] for b in range_bars)
    rl = min(b["low"] for b in range_bars)
    a = _atr_val(ohlc, 14)
    if not a or rh - rl > max_range_atr * a:
        return None
    lvl_up, lvl_dn = rh + buffer_atr * a, rl - buffer_atr * a
    start = len(week_bars) - len(after)
    if _first_cross(week_bars, lvl_up, "BUY", start=start):
        return _sig("BUY", tag, _strength_conf(0.62, ohlc[-1], "BUY"), ohlc[-1]["close"],
                    [f"close above weekly opening range high {rh:.5f} "
                     f"+ {buffer_atr}*ATR ({lvl_up:.5f})",
                     f"range width {rh - rl:.5f} <= {max_range_atr}*ATR; advisory "
                     f"SL = range low, TP = 2R"])
    if _first_cross(week_bars, lvl_dn, "SELL", start=start):
        return _sig("SELL", tag, _strength_conf(0.62, ohlc[-1], "SELL"), ohlc[-1]["close"],
                    [f"close below weekly opening range low {rl:.5f} "
                     f"- {buffer_atr}*ATR ({lvl_dn:.5f}) (spot: flat)"])
    return None


# ---------------------------------------------------------------------------
# #6 — Opening Range Breakout (ORB-30, day open 00:00 UTC on crypto)
# ---------------------------------------------------------------------------


def orb_30(ohlc, range_minutes=30, buffer_atr=0.1, max_range_atr_d=1.0,
           min_range_atr_d=0.15):
    """Range = high/low of the first 30 min of the UTC day; break confirmed
    by a close beyond range + 0.1*ATR(execution TF). Width filters use
    ATR(1d) when enough days are loaded, else are skipped (short-history
    fallback, module docstring). Highly cost-sensitive (catalog note)."""
    tag = "Breakout - ORB-30"
    if len(ohlc) < 16:
        return None
    days = _split_days(ohlc)
    if not days:
        return None
    today = days[-1]
    midnight, dts = _day_bounds(today)
    range_end = midnight + timedelta(minutes=range_minutes)
    range_bars = [b for b, dt in zip(today, dts) if dt < range_end]
    after_idx = next((i for i, dt in enumerate(dts) if dt >= range_end), None)
    if not range_bars or after_idx is None:
        return None  # no bar past the opening range yet
    rh = max(b["high"] for b in range_bars)
    rl = min(b["low"] for b in range_bars)
    a_bar = _atr_val(ohlc, 14)
    if not a_bar:
        return None
    a_d = _daily_atr(_daily_ohlc(days)) if len(days) >= 3 else None
    if a_d:
        width = rh - rl
        if width > max_range_atr_d * a_d or width < min_range_atr_d * a_d:
            return None  # too wide = no compression; too narrow = noise
    lvl_up, lvl_dn = rh + buffer_atr * a_bar, rl - buffer_atr * a_bar
    if _first_cross(today, lvl_up, "BUY", start=after_idx):
        return _sig("BUY", tag, _strength_conf(0.58, ohlc[-1], "BUY"), ohlc[-1]["close"],
                    [f"close above {range_minutes}min opening range high {rh:.5f} "
                     f"+ {buffer_atr}*ATR ({lvl_up:.5f})",
                     "advisory SL = range midpoint or 1*ATR (wider), TP = 2R; "
                     "max 1 trade/direction/session"])
    if _first_cross(today, lvl_dn, "SELL", start=after_idx):
        return _sig("SELL", tag, _strength_conf(0.58, ohlc[-1], "SELL"), ohlc[-1]["close"],
                    [f"close below {range_minutes}min opening range low {rl:.5f} "
                     f"- {buffer_atr}*ATR ({lvl_dn:.5f}) (spot: flat)"])
    return None


# ---------------------------------------------------------------------------
# #7 — Crabel Opening Range + Stretch
# ---------------------------------------------------------------------------


def crabel_stretch(ohlc, stretch_len=10):
    """Stretch = SMA over past days of min(open - low, high - open); trigger
    at day-open +/- Stretch (stop orders -> close-confirmed here). Needs >= 4
    UTC days of bars or the stretch estimate is noise (defensive minimum)."""
    tag = "Breakout - Crabel Stretch"
    if len(ohlc) < 16:
        return None
    days = _split_days(ohlc)
    if not days or len(days) < 4:
        return None
    daily = _daily_ohlc(days)
    stretches = [min(d["open"] - d["low"], d["high"] - d["open"])
                 for d in daily[:-1]]
    stretches = stretches[-stretch_len:]
    if not stretches:
        return None
    stretch = sum(stretches) / len(stretches)
    if stretch <= 0:
        return None
    o = daily[-1]["open"]
    today = days[-1]
    lvl_up, lvl_dn = o + stretch, o - stretch
    if _first_cross(today, lvl_up, "BUY"):
        return _sig("BUY", tag, _strength_conf(0.58, ohlc[-1], "BUY"), ohlc[-1]["close"],
                    [f"close above day open {o:.5f} + Stretch {stretch:.5f} "
                     f"({lvl_up:.5f})",
                     "advisory SL = open - Stretch, TP = 1.5-2R or session close; "
                     "90min time stop (catalog)"])
    if _first_cross(today, lvl_dn, "SELL"):
        return _sig("SELL", tag, _strength_conf(0.58, ohlc[-1], "SELL"), ohlc[-1]["close"],
                    [f"close below day open {o:.5f} - Stretch {stretch:.5f} "
                     f"({lvl_dn:.5f}) (spot: flat)"])
    return None


# ---------------------------------------------------------------------------
# #8 — Asian Range Breakout (Tokyo compression, 00:00-07:00 UTC box)
# ---------------------------------------------------------------------------


def asian_range_breakout(ohlc, max_range_atr=0.6, buffer_atr=0.1,
                         trade_end_hour=12):
    """Asian box 00:00-07:00 UTC must be <= 0.6*ATR(1d) (the compression
    filter IS the edge); trade the break 07:00-12:00 UTC."""
    tag = "Breakout - Asian Range"
    if len(ohlc) < 16:
        return None
    days = _split_days(ohlc)
    if not days:
        return None
    today = days[-1]
    midnight, dts = _day_bounds(today)
    last_dt = dts[-1]
    range_end = midnight + timedelta(hours=7)
    trade_end = midnight + timedelta(hours=trade_end_hour)
    if not (range_end <= last_dt < trade_end):
        return None
    range_bars = [b for b, dt in zip(today, dts) if dt < range_end]
    after_idx = next((i for i, dt in enumerate(dts) if dt >= range_end), None)
    if not range_bars or after_idx is None or after_idx > len(today) - 1:
        return None
    rh = max(b["high"] for b in range_bars)
    rl = min(b["low"] for b in range_bars)
    a_bar = _atr_val(ohlc, 14)
    if not a_bar:
        return None
    a_d = _daily_atr(_daily_ohlc(days)) if len(days) >= 3 else None
    if a_d and rh - rl > max_range_atr * a_d:
        return None  # wide Asian range = the move already happened
    lvl_up, lvl_dn = rh + buffer_atr * a_bar, rl - buffer_atr * a_bar
    if _first_cross(today, lvl_up, "BUY", start=after_idx):
        return _sig("BUY", tag, _strength_conf(0.60, ohlc[-1], "BUY"), ohlc[-1]["close"],
                    [f"close above Asian range high {rh:.5f} + {buffer_atr}*ATR "
                     f"({lvl_up:.5f})",
                     f"box width {rh - rl:.5f} within compression cap; advisory "
                     f"SL = range low, TP = 1:1 projection or 1.5R"])
    if _first_cross(today, lvl_dn, "SELL", start=after_idx):
        return _sig("SELL", tag, _strength_conf(0.60, ohlc[-1], "SELL"), ohlc[-1]["close"],
                    [f"close below Asian range low {rl:.5f} - {buffer_atr}*ATR "
                     f"({lvl_dn:.5f}) (spot: flat)"])
    return None


# ---------------------------------------------------------------------------
# #9 — London Open Breakout (first-hour range after 07:00 UTC, crypto clock)
# ---------------------------------------------------------------------------


def london_open_breakout(ohlc, buffer_atr=0.1, trade_end_hour=13):
    """Range = first 60 min after 07:00 UTC (crypto/UTC-summer fixed window;
    forex DST handling noted in the catalog — anchor on bar timestamps).
    Direction gated by the daily floor pivot (bias filter)."""
    tag = "Breakout - London Open"
    if len(ohlc) < 16:
        return None
    days = _split_days(ohlc)
    if not days or len(days) < 2:
        return None
    today = days[-1]
    midnight, dts = _day_bounds(today)
    last_dt = dts[-1]
    range_start = midnight + timedelta(hours=7)
    range_end = midnight + timedelta(hours=8)
    trade_end = midnight + timedelta(hours=trade_end_hour)
    if not (range_end <= last_dt < trade_end):
        return None
    range_bars = [b for b, dt in zip(today, dts) if range_start <= dt < range_end]
    after_idx = next((i for i, dt in enumerate(dts) if dt >= range_end), None)
    if not range_bars or after_idx is None or after_idx > len(today) - 1:
        return None
    prev_day = _daily_ohlc(days)[-2]
    pivot = (prev_day["high"] + prev_day["low"] + prev_day["close"]) / 3
    rh = max(b["high"] for b in range_bars)
    rl = min(b["low"] for b in range_bars)
    a_bar = _atr_val(ohlc, 14)
    if not a_bar:
        return None
    last = ohlc[-1]
    lvl_up, lvl_dn = rh + buffer_atr * a_bar, rl - buffer_atr * a_bar
    if last["close"] > pivot and _first_cross(today, lvl_up, "BUY", start=after_idx):
        return _sig("BUY", tag, _strength_conf(0.60, last, "BUY"), last["close"],
                    [f"close above London first-hour high {rh:.5f} + buffer "
                     f"({lvl_up:.5f})",
                     f"above daily pivot {pivot:.5f} (bias filter); advisory "
                     f"SL = range low or 1*ATR (tighter), TP = 2R"])
    if last["close"] < pivot and _first_cross(today, lvl_dn, "SELL", start=after_idx):
        return _sig("SELL", tag, _strength_conf(0.60, last, "SELL"), last["close"],
                    [f"close below London first-hour low {rl:.5f} - buffer "
                     f"({lvl_dn:.5f}), under pivot {pivot:.5f} (spot: flat)"])
    return None


# ---------------------------------------------------------------------------
# #10 — New York Open Breakout (pre-NY range 12:00-13:30 UTC)
# ---------------------------------------------------------------------------


def ny_open_breakout(ohlc, buffer_atr=0.1, trade_end_hour=16):
    """Range = 12:00-13:30 UTC; trade 13:30-16:00 UTC only with the day's
    trend (close vs day open AND vs session VWAP; VWAP leg skipped when the
    feed carries no volume — defensive, noted)."""
    tag = "Breakout - NY Open"
    if len(ohlc) < 16:
        return None
    days = _split_days(ohlc)
    if not days:
        return None
    today = days[-1]
    midnight, dts = _day_bounds(today)
    last_dt = dts[-1]
    range_start = midnight + timedelta(hours=12)
    range_end = midnight + timedelta(hours=13, minutes=30)
    trade_end = midnight + timedelta(hours=trade_end_hour)
    if not (range_end <= last_dt < trade_end):
        return None
    range_bars = [b for b, dt in zip(today, dts) if range_start <= dt < range_end]
    after_idx = next((i for i, dt in enumerate(dts) if dt >= range_end), None)
    if not range_bars or after_idx is None or after_idx > len(today) - 1:
        return None
    rh = max(b["high"] for b in range_bars)
    rl = min(b["low"] for b in range_bars)
    a_bar = _atr_val(ohlc, 14)
    if not a_bar:
        return None
    day_open = today[0]["open"]
    last = ohlc[-1]
    vols = _vols(today)
    vwap = None
    if sum(vols) > 0:
        num = sum(((b["high"] + b["low"] + b["close"]) / 3) * v
                  for b, v in zip(today, vols))
        vwap = num / sum(vols)
    lvl_up, lvl_dn = rh + buffer_atr * a_bar, rl - buffer_atr * a_bar
    up_trend = last["close"] > day_open and (vwap is None or last["close"] > vwap)
    dn_trend = last["close"] < day_open and (vwap is None or last["close"] < vwap)
    if up_trend and _first_cross(today, lvl_up, "BUY", start=after_idx):
        return _sig("BUY", tag, _strength_conf(0.58, last, "BUY"), last["close"],
                    [f"close above pre-NY range high {rh:.5f} + buffer ({lvl_up:.5f})",
                     "day-trend aligned (above open/VWAP); advisory SL = 0.8*ATR "
                     "or range low, TP = 1.5R"])
    if dn_trend and _first_cross(today, lvl_dn, "SELL", start=after_idx):
        return _sig("SELL", tag, _strength_conf(0.58, last, "SELL"), last["close"],
                    [f"close below pre-NY range low {rl:.5f} - buffer ({lvl_dn:.5f}), "
                     "day-trend aligned down (spot: flat)"])
    return None


# ---------------------------------------------------------------------------
# #11 — Daily Pivot R1/S1 Breakout (floor-trader pivots, prior UTC day)
# ---------------------------------------------------------------------------


def daily_pivot_r1s1(ohlc, buffer_atr=0.05):
    """P/R1/S1 from the prior UTC day; continuation break of R1/S1 (fades
    are mean-reversion and deliberately excluded per catalog)."""
    tag = "Breakout - Daily Pivot R1/S1"
    if len(ohlc) < 16:
        return None
    days = _split_days(ohlc)
    if not days or len(days) < 2:
        return None
    prev_day = _daily_ohlc(days)[-2]
    p = (prev_day["high"] + prev_day["low"] + prev_day["close"]) / 3
    r1 = 2 * p - prev_day["low"]
    s1 = 2 * p - prev_day["high"]
    r2 = p + (prev_day["high"] - prev_day["low"])
    a = _atr_val(ohlc, 14)
    if not a:
        return None
    today = days[-1]
    lvl_up, lvl_dn = r1 + buffer_atr * a, s1 - buffer_atr * a
    if _first_cross(today, lvl_up, "BUY"):
        return _sig("BUY", tag, _strength_conf(0.58, ohlc[-1], "BUY"), ohlc[-1]["close"],
                    [f"close above daily R1 {r1:.5f} + {buffer_atr}*ATR ({lvl_up:.5f})",
                     f"advisory SL = pivot {p:.5f}, TP = R2 {r2:.5f}"])
    if _first_cross(today, lvl_dn, "SELL"):
        return _sig("SELL", tag, _strength_conf(0.58, ohlc[-1], "SELL"), ohlc[-1]["close"],
                    [f"close below daily S1 {s1:.5f} - {buffer_atr}*ATR ({lvl_dn:.5f})"
                     " (spot: flat)"])
    return None


# ---------------------------------------------------------------------------
# #12 — Camarilla H4/L4 Breakout (trend-day half of Nick Stott's levels)
# ---------------------------------------------------------------------------


def camarilla_h4l4(ohlc):
    """H4/L4 from prior-day H/L/C; rare breakout-day signal (a few per
    month). H3/L3 fade half is mean-reversion — excluded per catalog."""
    tag = "Breakout - Camarilla H4/L4"
    if len(ohlc) < 16:
        return None
    days = _split_days(ohlc)
    if not days or len(days) < 2:
        return None
    prev_day = _daily_ohlc(days)[-2]
    rng_d = prev_day["high"] - prev_day["low"]
    if rng_d <= 0:
        return None
    c = prev_day["close"]
    h4 = c + rng_d * 1.1 / 2
    l4 = c - rng_d * 1.1 / 2
    h3 = c + rng_d * 1.1 / 4
    l3 = c - rng_d * 1.1 / 4
    today = days[-1]
    if _first_cross(today, h4, "BUY"):
        return _sig("BUY", tag, _strength_conf(0.60, ohlc[-1], "BUY"), ohlc[-1]["close"],
                    [f"close above Camarilla H4 {h4:.5f} (breakout-day trigger)",
                     f"advisory SL = H3 {h3:.5f}, TP = H5 {c + rng_d * 1.1:.5f} or 2R"])
    if _first_cross(today, l4, "SELL"):
        return _sig("SELL", tag, _strength_conf(0.60, ohlc[-1], "SELL"), ohlc[-1]["close"],
                    [f"close below Camarilla L4 {l4:.5f} (spot: flat)",
                     f"advisory SL = L3 {l3:.5f}"])
    return None


# ---------------------------------------------------------------------------
# #13 — TTM Squeeze (Bollinger-in-Keltner compression release)
# ---------------------------------------------------------------------------


def _bb_kc_at(closes, atr_s, idx, bb_len, bb_mult, kc_len, kc_mult):
    """(bb_upper, bb_lower, kc_upper, kc_lower) on the bar ending at `idx`.
    KC midline is EMA(kc_len) — the Carter/LazyBear-standard form."""
    if idx + 1 < bb_len or idx + 1 < kc_len:
        return None
    win = closes[idx - bb_len + 1:idx + 1]
    mid = sum(win) / bb_len
    sd = pstdev(win)
    ema_s = ema_all(closes[:idx + 1], kc_len)
    a = atr_s[idx]
    if not ema_s or a is None:
        return None
    kc_mid = ema_s[-1]
    return (mid + bb_mult * sd, mid - bb_mult * sd,
            kc_mid + kc_mult * a, kc_mid - kc_mult * a)


def ttm_squeeze(ohlc, bb_len=20, bb_mult=2.0, kc_len=20, kc_mult=1.5,
                mom_len=20):
    """Squeeze ON = BB fully inside KC on the previous bar; FIRES on the
    first bar back outside; direction from the LazyBear-form momentum
    (close - midpoint of Donchian mid and SMA — catalog-sanctioned codable
    form)."""
    tag = "Volatility - TTM Squeeze"
    if len(ohlc) < bb_len + kc_len + 5:
        return None
    closes = _closes(ohlc)
    atr_s = _atr_series(ohlc, kc_len)
    prev = _bb_kc_at(closes, atr_s, len(ohlc) - 2, bb_len, bb_mult, kc_len, kc_mult)
    cur = _bb_kc_at(closes, atr_s, len(ohlc) - 1, bb_len, bb_mult, kc_len, kc_mult)
    if not prev or not cur:
        return None
    was_on = prev[0] < prev[2] and prev[1] > prev[3]
    now_on = cur[0] < cur[2] and cur[1] > cur[3]
    if not (was_on and not now_on):
        return None
    highs, lows = _highs(ohlc), _lows(ohlc)
    don_mid = (max(highs[-mom_len:]) + min(lows[-mom_len:])) / 2
    sma_mid = sma(closes, mom_len)
    if sma_mid is None:
        return None
    mom = closes[-1] - 0.5 * (don_mid + sma_mid)
    if mom > 0:
        return _sig("BUY", tag, _strength_conf(0.65, ohlc[-1], "BUY"), closes[-1],
                    [f"squeeze fired up (BB back outside KC), momentum {mom:+.5f}",
                     "advisory SL = 1.5*ATR, TP = 2.5R or momentum flip x2"])
    if mom < 0:
        return _sig("SELL", tag, _strength_conf(0.65, ohlc[-1], "SELL"), closes[-1],
                    [f"squeeze fired down, momentum {mom:+.5f} (spot: flat)"])
    return None


# ---------------------------------------------------------------------------
# #14 — Bollinger Band Walk (outside-band continuation; NOT the fade)
# ---------------------------------------------------------------------------


def bollinger_band_walk(ohlc, bb_len=20, bb_mult=2.0, bw_lookback=120,
                        bw_pct_min=30, bw_pct_max=85):
    """Close outside the band with bandwidth rising and its 120-bar
    percentile in [30, 85] — expanding but not climactic (the ceiling skips
    blowoff entries). Continuation reading, opposite of the codebase's
    mean-reversion detect_bollinger — kept as a separate strategy per
    catalog so the paper test arbitrates."""
    tag = "Volatility - Bollinger Band Walk"
    if len(ohlc) < bb_len + bw_lookback + 2:
        return None
    closes = _closes(ohlc)

    def _band(idx):
        win = closes[idx - bb_len + 1:idx + 1]
        mid = sum(win) / bb_len
        sd = pstdev(win)
        return mid + bb_mult * sd, mid - bb_mult * sd, mid

    def _bw(idx):
        ub, lb, mid = _band(idx)
        return (ub - lb) / mid if mid else None

    bw_now, bw_prev = _bw(len(ohlc) - 1), _bw(len(ohlc) - 2)
    if bw_now is None or bw_prev is None or bw_now <= bw_prev:
        return None
    hist = [_bw(i) for i in range(len(ohlc) - bw_lookback, len(ohlc))]
    hist = [b for b in hist if b is not None]
    if not hist:
        return None
    pct = 100.0 * sum(1 for b in hist if b <= bw_now) / len(hist)
    if not (bw_pct_min <= pct <= bw_pct_max):
        return None
    ub, lb, mid = _band(len(ohlc) - 1)
    ub_p, lb_p, _ = _band(len(ohlc) - 2)
    last, prev = ohlc[-1], ohlc[-2]
    if last["close"] > ub and prev["close"] <= ub_p:
        return _sig("BUY", tag, _strength_conf(0.62, last, "BUY"), last["close"],
                    [f"close above upper BB({bb_len},{bb_mult}) {ub:.5f}, "
                     f"bandwidth rising (pct {pct:.0f} in [{bw_pct_min},{bw_pct_max}])",
                     f"advisory exit on close back below midband {mid:.5f}; "
                     f"hard SL = 2*ATR"])
    if last["close"] < lb and prev["close"] >= lb_p:
        return _sig("SELL", tag, _strength_conf(0.62, last, "SELL"), last["close"],
                    [f"close below lower BB {lb:.5f}, bandwidth rising "
                     f"(pct {pct:.0f}) (spot: flat)"])
    return None


# ---------------------------------------------------------------------------
# #15 — Keltner Channel Breakout (EMA20 +/- 2*ATR(20), Raschke-modern form)
# ---------------------------------------------------------------------------


def keltner_breakout(ohlc, ema_len=20, atr_len=20, mult=2.0, adx_min=20):
    tag = "Breakout - Keltner Channel"
    if len(ohlc) < max(60, atr_len + ema_len + 2):
        return None
    closes = _closes(ohlc)
    ema_s = ema_all(closes, ema_len)
    atr_s = _atr_series(ohlc, atr_len)
    adx_v = _adx(ohlc, 14)
    if len(ema_s) < 2 or atr_s[-1] is None or atr_s[-2] is None:
        return None
    if adx_v is None or adx_v < adx_min:
        return None  # the ADX gate is essential in chop (catalog)
    ku = ema_s[-1] + mult * atr_s[-1]
    kl = ema_s[-1] - mult * atr_s[-1]
    ku_p = ema_s[-2] + mult * atr_s[-2]
    kl_p = ema_s[-2] - mult * atr_s[-2]
    last, prev = ohlc[-1], ohlc[-2]
    if last["close"] > ku and prev["close"] <= ku_p:
        return _sig("BUY", tag, _strength_conf(0.62, last, "BUY"), last["close"],
                    [f"close above Keltner EMA{ema_len}+{mult}*ATR ({ku:.5f}), "
                     f"ADX {adx_v:.0f} >= {adx_min}",
                     f"advisory SL = midline {ema_s[-1]:.5f} (auto-trails), TP = 2.5R"])
    if last["close"] < kl and prev["close"] >= kl_p:
        return _sig("SELL", tag, _strength_conf(0.62, last, "SELL"), last["close"],
                    [f"close below Keltner EMA{ema_len}-{mult}*ATR ({kl:.5f}), "
                     f"ADX {adx_v:.0f} >= {adx_min} (spot: flat)"])
    return None


# ---------------------------------------------------------------------------
# #16 — ATR Volatility Expansion Breakout (range break + vol surge)
# ---------------------------------------------------------------------------


def atr_expansion_breakout(ohlc, atr_len=14, atr_sma=50, surge_mult=1.5,
                           donchian_len=20):
    """ATR(14) > 1.5*SMA(ATR,50) concurrent with the Donchian break —
    Crabel's expansion principle; surge-without-break bars are filtered by
    requiring BOTH (catalog)."""
    tag = "Volatility - ATR Expansion"
    if len(ohlc) < atr_len + atr_sma + donchian_len:
        return None
    atr_s = [v for v in _atr_series(ohlc, atr_len) if v is not None]
    if len(atr_s) < atr_sma:
        return None
    a_now = atr_s[-1]
    a_base = sma(atr_s, atr_sma)
    if not a_base or a_now <= surge_mult * a_base:
        return None
    dh, dl = _donchian(ohlc, donchian_len)
    if dh is None:
        return None
    last, prev = ohlc[-1], ohlc[-2]
    surge_txt = f"ATR {a_now:.5f} > {surge_mult}*SMA{atr_sma} ({a_base:.5f})"
    if last["close"] > dh and prev["close"] <= dh:
        return _sig("BUY", tag, _strength_conf(0.64, last, "BUY"), last["close"],
                    [f"close above prior {donchian_len}-bar high {dh:.5f} with {surge_txt}",
                     "advisory SL = 2*ATR (surged), TP = 3R, chandelier after +2R"])
    if last["close"] < dl and prev["close"] >= dl:
        return _sig("SELL", tag, _strength_conf(0.64, last, "SELL"), last["close"],
                    [f"close below prior {donchian_len}-bar low {dl:.5f} with "
                     f"{surge_txt} (spot: flat)"])
    return None


# ---------------------------------------------------------------------------
# #17 / #18 — NR4 / NR7 (Crabel narrowest-range contractions)
# ---------------------------------------------------------------------------


def _nr_breakout(ohlc, lookback, buffer_atr, tag):
    """Bar T (= second-to-last) is the narrowest of the last `lookback` bars;
    the last bar closing beyond T's extreme + buffer is the close-confirmed
    form of the T+1 stop order. Cancel-after-1-day is inherent: the setup
    only exists on the bar right after T. Optional EMA20 trend filter left
    OFF (catalog default)."""
    if len(ohlc) < max(lookback + 2, 16):
        return None
    a = _atr_val(ohlc, 14)
    if not a:
        return None
    t = ohlc[-2]
    window = ohlc[-lookback - 1:-1]
    rng_t = _rng(t)
    if rng_t <= 0 or rng_t > min(_rng(b) for b in window[:-1]):
        return None
    last = ohlc[-1]
    lvl_up, lvl_dn = t["high"] + buffer_atr * a, t["low"] - buffer_atr * a
    if last["close"] > lvl_up:
        return _sig("BUY", tag, _strength_conf(0.62, last, "BUY"), last["close"],
                    [f"NR{lookback} bar (range {rng_t:.5f}) broken up: close > "
                     f"{t['high']:.5f} + buffer ({lvl_up:.5f})",
                     f"advisory SL = NR{lookback} low {t['low']:.5f}, TP = 2R"])
    if last["close"] < lvl_dn:
        return _sig("SELL", tag, _strength_conf(0.62, last, "SELL"), last["close"],
                    [f"NR{lookback} bar broken down: close < {t['low']:.5f} - buffer "
                     f"({lvl_dn:.5f}) (spot: flat)"])
    return None


def nr4_breakout(ohlc, lookback=4, buffer_atr=0.05):
    """Crabel NR4 — smallest reliable compression sample (catalog #17)."""
    return _nr_breakout(ohlc, lookback, buffer_atr, "Breakout - NR4")


def nr7_breakout(ohlc, lookback=7, buffer_atr=0.05):
    """Crabel NR7 via Connors/Raschke — rarer, bigger coil (catalog #18)."""
    return _nr_breakout(ohlc, lookback, buffer_atr, "Breakout - NR7")


# ---------------------------------------------------------------------------
# #19 — Inside Bar Breakout at MOTHER-bar levels (not the inside bar's)
# ---------------------------------------------------------------------------


def inside_bar_mother(ohlc, buffer_atr=0.05, validity_bars=3):
    """Mother-bar extremes define the range being resolved (Fuller/price-
    action consensus, catalog #19); the codebase's inside-bar-level
    detect_inside_bar is the weaker retail variant — kept separate so the
    paper test arbitrates. Stateless validity window: scan the last
    `validity_bars` bars for the most recent inside bar, fire only on the
    first close beyond the mother level since."""
    tag = "Breakout - Inside Bar (Mother)"
    if len(ohlc) < 18:
        return None
    a = _atr_val(ohlc, 14)
    if not a:
        return None
    last = ohlc[-1]
    for t_idx in range(len(ohlc) - 2, max(len(ohlc) - 2 - validity_bars, 0), -1):
        mother, inner = ohlc[t_idx - 1], ohlc[t_idx]
        if _rng(mother) <= 0:
            continue
        if not (inner["high"] <= mother["high"] and inner["low"] >= mother["low"]):
            continue
        lvl_up = mother["high"] + buffer_atr * a
        lvl_dn = mother["low"] - buffer_atr * a
        since = ohlc[t_idx + 1:-1]
        if last["close"] > lvl_up and all(b["close"] <= lvl_up for b in since):
            return _sig("BUY", tag, _strength_conf(0.62, last, "BUY"), last["close"],
                        [f"close above mother-bar high {mother['high']:.5f} + buffer "
                         f"({lvl_up:.5f}); inside bar {validity_bars} bars valid",
                         f"advisory SL = mother low {mother['low']:.5f} "
                         f"(midpoint + 1.5*ATR cap for wide mothers), TP = 2R"])
        if last["close"] < lvl_dn and all(b["close"] >= lvl_dn for b in since):
            return _sig("SELL", tag, _strength_conf(0.62, last, "SELL"), last["close"],
                        [f"close below mother-bar low {mother['low']:.5f} - buffer "
                         f"({lvl_dn:.5f}) (spot: flat)"])
        return None  # most recent inside-bar setup evaluated; older ones stale
    return None


# ---------------------------------------------------------------------------
# #20 — NRIB: narrow-range inside bar ("coil within a coil")
# ---------------------------------------------------------------------------


def nrib_breakout(ohlc, inside_vs_mother=0.5, narrow_lookback=4,
                  buffer_atr=0.05, validity_bars=2, max_mother_atr=2.0):
    """Inside bar whose range is <= 50% of the mother's AND the narrowest of
    the last 4 bars; mothers wider than 2*ATR are post-spike digestion, not
    compression (catalog note) — skipped."""
    tag = "Breakout - NRIB"
    if len(ohlc) < 18:
        return None
    a = _atr_val(ohlc, 14)
    if not a:
        return None
    last = ohlc[-1]
    for t_idx in range(len(ohlc) - 2, max(len(ohlc) - 2 - validity_bars, 1), -1):
        mother, inner = ohlc[t_idx - 1], ohlc[t_idx]
        rng_m, rng_i = _rng(mother), _rng(inner)
        if rng_m <= 0 or rng_i <= 0:
            continue
        if not (inner["high"] <= mother["high"] and inner["low"] >= mother["low"]):
            continue
        if rng_i > inside_vs_mother * rng_m or rng_m > max_mother_atr * a:
            return None
        lo4 = ohlc[max(0, t_idx - narrow_lookback + 1):t_idx + 1]
        if rng_i > min(_rng(b) for b in lo4[:-1]):
            return None
        lvl_up = mother["high"] + buffer_atr * a
        lvl_dn = mother["low"] - buffer_atr * a
        since = ohlc[t_idx + 1:-1]
        if last["close"] > lvl_up and all(b["close"] <= lvl_up for b in since):
            return _sig("BUY", tag, _strength_conf(0.66, last, "BUY"), last["close"],
                        [f"NRIB: inside range {rng_i:.5f} <= {inside_vs_mother}*mother "
                         f"{rng_m:.5f}, narrowest of {narrow_lookback}; close > "
                         f"{mother['high']:.5f} + buffer",
                         f"advisory SL = mother low {mother['low']:.5f}, TP = 2.5R"])
        if last["close"] < lvl_dn and all(b["close"] >= lvl_dn for b in since):
            return _sig("SELL", tag, _strength_conf(0.66, last, "SELL"), last["close"],
                        [f"NRIB broken down below mother low {mother['low']:.5f} "
                         f"(spot: flat)"])
        return None
    return None


# ---------------------------------------------------------------------------
# #21 — Volatility Contraction Pattern (VCP, Minervini geometric core)
# ---------------------------------------------------------------------------


def _zigzag(ohlc, window):
    """Confirmed swing highs/lows (window bars each side), time-ordered and
    alternating; equal-kind runs keep the more extreme point."""
    n = len(ohlc)
    pts = []
    for i in range(window, n - window):
        h, l = ohlc[i]["high"], ohlc[i]["low"]
        hi = all(h >= ohlc[i - j]["high"] and h >= ohlc[i + j]["high"]
                 for j in range(1, window + 1))
        lo = all(l <= ohlc[i - j]["low"] and l <= ohlc[i + j]["low"]
                 for j in range(1, window + 1))
        if hi:
            pts.append((i, h, "H"))
        if lo:
            pts.append((i, l, "L"))
    alt = []
    for p in pts:
        if alt and alt[-1][2] == p[2]:
            better = (p[2] == "H" and p[1] > alt[-1][1]) or \
                     (p[2] == "L" and p[1] < alt[-1][1])
            if better:
                alt[-1] = p
        else:
            alt.append(p)
    return alt


def vcp_minervini(ohlc, lookback=60, swing_window=5, contraction_ratio=0.6,
                  final_atr_mult=1.5, breakout_vol_mult=1.5):
    """Geometric core of Minervini's VCP (catalog #21): 2-3 successive
    pullbacks, each <= 0.6x the previous, the final one tight (<= 1.5*ATR),
    volume drying up into the last low; buy the break of the last
    contraction's high on >= 1.5x volume. Qualitative elements (fundamental
    leadership, 'tennis ball action') cannot be coded — per catalog, only
    the geometry is codified. Long-only by design (no published short
    variant). Regime gate: structure above a rising EMA50 (chop filter)."""
    tag = "Breakout - VCP"
    if len(ohlc) < lookback + 15:
        return None
    closes = _closes(ohlc)
    vols = _vols(ohlc)
    if sum(vols[-50:]) <= 0:
        return None  # OHLCV pattern — volume contraction is required
    ema_s = ema_all(closes, 50)
    if len(ema_s) < 11 or not (closes[-1] > ema_s[-1] > ema_s[-11]):
        return None
    a = _atr_val(ohlc, 14)
    if not a:
        return None
    zz = _zigzag(ohlc[-(lookback + swing_window + 1):], swing_window)
    if zz and zz[-1][2] == "H":
        zz = zz[:-1]  # final leg still rallying; use completed contractions
    # collect trailing (H, L) contraction pairs
    pairs = []
    i = len(zz) - 1
    while i - 1 >= 0 and zz[i][2] == "L" and zz[i - 1][2] == "H" and len(pairs) < 3:
        pairs.append((zz[i - 1], zz[i]))
        i -= 2
    if len(pairs) < 2:
        return None
    pairs.reverse()  # oldest first: (H1,L1), (H2,L2), (H3,L3)
    depths = [(h[1] - l[1]) / h[1] for h, l in pairs if h[1] > 0]
    if len(depths) != len(pairs):
        return None
    for k in range(1, len(depths)):
        if depths[k] > contraction_ratio * depths[k - 1]:
            return None
    h_last, l_last = pairs[-1]
    if h_last[1] - l_last[1] > final_atr_mult * a:
        return None  # final contraction not tight
    if not (pairs[0][0][1] >= pairs[-1][0][1]):
        return None  # pivot highs must not rise through the base
    # no undercut of the final low since it printed (invalidation rule)
    base_off = len(ohlc) - (lookback + swing_window + 1)
    last_low_idx = base_off + l_last[0]
    if any(b["low"] < l_last[1] for b in ohlc[last_low_idx + 1:]):
        return None
    # volume dry-up: SMA(vol,10) at final low < at first contraction high
    def _vsma(idx):
        lo, hi = max(0, idx - 9), idx + 1
        return sum(vols[lo:hi]) / (hi - lo)
    first_high_idx = base_off + pairs[0][0][0]
    if not (_vsma(last_low_idx) < _vsma(first_high_idx)):
        return None
    pivot = h_last[1]
    v_base = sma(vols, 50)
    if v_base is None or vols[-1] < breakout_vol_mult * v_base:
        return None
    last, prev = ohlc[-1], ohlc[-2]
    if last["close"] > pivot and prev["close"] <= pivot:
        return _sig("BUY", tag, _strength_conf(0.70, last, "BUY"), last["close"],
                    [f"VCP: {len(pairs)} contractions "
                     f"({'/'.join(f'{d:.1%}' for d in depths)}) tightening, close "
                     f"> pivot {pivot:.5f} on {vols[-1] / v_base:.1f}x vol",
                     f"advisory SL = final contraction low {l_last[1]:.5f}, TP = 3R"])
    return None


# ---------------------------------------------------------------------------
# #22 — Gap-and-Go (session gap + first-15m range continuation)
# ---------------------------------------------------------------------------


def gap_and_go(ohlc, gap_atr=0.5, range_minutes=15, max_gap_fill=0.5):
    """Day open gaps >= 0.5*ATR(1d); price holds the gap (no fill of > 50%);
    close beyond the first-15-minute range = go. The holds-above-open
    condition is the published filter separating go from fill (catalog)."""
    tag = "Breakout - Gap and Go"
    if len(ohlc) < 16:
        return None
    days = _split_days(ohlc)
    if not days or len(days) < 2:
        return None
    daily = _daily_ohlc(days)
    today = days[-1]
    midnight, dts = _day_bounds(today)
    a = _daily_atr(daily) or _atr_val(ohlc, 14)
    if not a:
        return None
    o = daily[-1]["open"]
    pc = daily[-2]["close"]
    gap = o - pc
    if abs(gap) < gap_atr * a:
        return None
    range_end = midnight + timedelta(minutes=range_minutes)
    early = [b for b, dt in zip(today, dts) if dt < range_end]
    after_idx = next((i for i, dt in enumerate(dts) if dt >= range_end), None)
    if not early or after_idx is None:
        return None
    rh = max(b["high"] for b in early)
    rl = min(b["low"] for b in early)
    if gap > 0:
        held = min(b["low"] for b in today) > o - max_gap_fill * gap
        if held and _first_cross(today, rh, "BUY", start=after_idx):
            return _sig("BUY", tag, _strength_conf(0.60, ohlc[-1], "BUY"),
                        ohlc[-1]["close"],
                        [f"gap up {gap:+.5f} (>= {gap_atr}*ATR) held; close above "
                         f"first-{range_minutes}min high {rh:.5f}",
                         f"advisory SL = first-{range_minutes}min low {rl:.5f}, "
                         f"TP = 2R, time exit at day end"])
    else:
        held = max(b["high"] for b in today) < o - max_gap_fill * gap
        if held and _first_cross(today, rl, "SELL", start=after_idx):
            return _sig("SELL", tag, _strength_conf(0.60, ohlc[-1], "SELL"),
                        ohlc[-1]["close"],
                        [f"gap down {gap:+.5f} held; close below first-"
                         f"{range_minutes}min low {rl:.5f} (spot: flat)"])
    return None


# ---------------------------------------------------------------------------
# #23 — CME / Weekend Gap Fill (fade toward the prior close; spot fallback
# uses UTC day boundaries per catalog data_needs note)
# ---------------------------------------------------------------------------


def gap_fill(ohlc, gap_atr=0.4, entry_window_minutes=60):
    """Gap between today's UTC open and the prior day's close >= 0.4*ATR:
    enter at market near the session open targeting the fill (prior close).
    Stateless single-shot form: only actionable during the first
    `entry_window_minutes` of the day while the gap is still unfilled."""
    tag = "Volatility - Gap Fill"
    if len(ohlc) < 16:
        return None
    days = _split_days(ohlc)
    if not days or len(days) < 2:
        return None
    daily = _daily_ohlc(days)
    today = days[-1]
    midnight, dts = _day_bounds(today)
    if dts[-1] >= midnight + timedelta(minutes=entry_window_minutes):
        return None
    a = _daily_atr(daily) or _atr_val(ohlc, 14)
    if not a:
        return None
    o = daily[-1]["open"]
    pc = daily[-2]["close"]
    last = ohlc[-1]
    if pc - o >= gap_atr * a and last["close"] < pc:
        return _sig("BUY", tag, 0.55, last["close"],
                    [f"gap down at day open ({o:.5f} vs prior close {pc:.5f}, "
                     f">= {gap_atr}*ATR); fading toward fill at {pc:.5f}",
                     "advisory TP = exact fill (prior close); SL = 0.5*gap beyond "
                     "entry; 48h time stop (catalog)"])
    if o - pc >= gap_atr * a and last["close"] > pc:
        return _sig("SELL", tag, 0.55, last["close"],
                    [f"gap up at day open ({o:.5f} vs prior close {pc:.5f}); "
                     f"fading toward fill at {pc:.5f} (spot: flat)"])
    return None


# ---------------------------------------------------------------------------
# #24 — Swing Failure Pattern (false-breakout fade; Wyckoff spring/upthrust)
# ---------------------------------------------------------------------------


def swing_failure_fade(ohlc, swing_window=5, lookback=20, adx_max=30):
    """Wick through a prior confirmed swing point, close back on the right
    side (same-bar reclaim — the cleanest codable form; 'next bar' variant
    noted). Regime gate: stand aside when ADX(14) > 30 — trend days blow
    through wick stops (catalog fix)."""
    tag = "Breakout - Swing Failure Fade"
    n = len(ohlc)
    if n < max(lookback + swing_window + 2, 31):
        return None
    adx_v = _adx(ohlc, 14)
    if adx_v is not None and adx_v > adx_max:
        return None
    last = ohlc[-1]
    lo_idx = None
    for i in range(n - 1 - swing_window, max(n - 1 - lookback, swing_window) - 1, -1):
        if all(ohlc[i]["low"] <= ohlc[i - j]["low"] and
               ohlc[i]["low"] <= ohlc[i + j]["low"]
               for j in range(1, swing_window + 1)):
            lo_idx = i
            break
    hi_idx = None
    for i in range(n - 1 - swing_window, max(n - 1 - lookback, swing_window) - 1, -1):
        if all(ohlc[i]["high"] >= ohlc[i - j]["high"] and
               ohlc[i]["high"] >= ohlc[i + j]["high"]
               for j in range(1, swing_window + 1)):
            hi_idx = i
            break
    if lo_idx is not None:
        level = ohlc[lo_idx]["low"]
        if last["low"] < level < last["close"]:
            return _sig("BUY", tag, _strength_conf(0.62, last, "BUY"), last["close"],
                        [f"failed breakdown: wicked {last['low']:.5f} through swing "
                         f"low {level:.5f}, closed back above",
                         "advisory SL = wick low - 0.1*ATR, TP = range far side "
                         "or 2R"])
    if hi_idx is not None:
        level = ohlc[hi_idx]["high"]
        if last["high"] > level > last["close"]:
            return _sig("SELL", tag, _strength_conf(0.62, last, "SELL"), last["close"],
                        [f"failed breakout: wicked {last['high']:.5f} through swing "
                         f"high {level:.5f}, closed back below (spot: flat)"])
    return None


# ---------------------------------------------------------------------------
# #25 — Turtle Soup (fade the fresh 20-bar break; Raschke/Connors)
# ---------------------------------------------------------------------------


def turtle_soup(ohlc, channel_len=20, fresh_level_bars=10):
    """Break of the prior 20-bar extreme that FAILS on the same bar (wick
    through, close back). Fresh-level clause: the level must not have been
    touched in the prior 10 bars (published rule). Same-bar-only form; the
    'or within the next bar' variant is noted but not coded."""
    tag = "Breakout - Turtle Soup"
    n = len(ohlc)
    if n < channel_len + fresh_level_bars + 2:
        return None
    highs, lows = _highs(ohlc), _lows(ohlc)
    lvl_dn = min(lows[-channel_len - 1:-1])
    lvl_up = max(highs[-channel_len - 1:-1])
    fresh_dn = all(l > lvl_dn for l in lows[-fresh_level_bars - 1:-1])
    fresh_up = all(h < lvl_up for h in highs[-fresh_level_bars - 1:-1])
    last = ohlc[-1]
    if fresh_dn and last["low"] < lvl_dn < last["close"]:
        return _sig("BUY", tag, _strength_conf(0.65, last, "BUY"), last["close"],
                    [f"fresh {channel_len}-bar low {lvl_dn:.5f} broke intrabar and "
                     f"failed (close {last['close']:.5f} back above)",
                     "advisory SL = 0.5*ATR beyond break extreme, TP = mid-channel "
                     "or EMA20, 5-bar time stop"])
    if fresh_up and last["high"] > lvl_up > last["close"]:
        return _sig("SELL", tag, _strength_conf(0.65, last, "SELL"), last["close"],
                    [f"fresh {channel_len}-bar high {lvl_up:.5f} broke intrabar and "
                     f"failed (spot: flat)"])
    return None


# ---------------------------------------------------------------------------
# #26 — Breakout-Retest Continuation (windowed state-machine evaluation)
# ---------------------------------------------------------------------------


def breakout_retest(ohlc, donchian_len=20, retest_window=5, touch_atr=0.3,
                    invalidation_atr=0.5, buffer_atr=0.1):
    """ARMED -> RETESTING -> TRIGGERED/VOID evaluated statelessly over the
    most recent breakout leg: the last bar is the FIRST close back beyond
    the broken level after a retest touch; any close more than
    0.5*ATR through the level voids the setup (catalog stage rules)."""
    tag = "Breakout - Retest Continuation"
    n = len(ohlc)
    if n < donchian_len + retest_window + 3:
        return None
    a = _atr_val(ohlc, 14)
    if not a:
        return None
    for b_idx in range(n - 2, max(n - 2 - retest_window, donchian_len) - 1, -1):
        dh, dl = _donchian_at(ohlc, donchian_len, b_idx)
        if dh is None:
            continue
        brk = ohlc[b_idx]
        direction = None
        level = None
        if brk["close"] > dh + buffer_atr * a:
            direction, level = "BUY", dh
        elif brk["close"] < dl - buffer_atr * a:
            direction, level = "SELL", dl
        if not direction:
            continue
        # scan the retest window after the breakout leg
        touch_idx = None
        void = False
        for k in range(b_idx + 1, min(b_idx + retest_window + 1, n)):
            if direction == "BUY":
                if ohlc[k]["close"] < level - invalidation_atr * a:
                    void = True
                    break
                if ohlc[k]["low"] <= level + touch_atr * a:
                    touch_idx = k
                    break
            else:
                if ohlc[k]["close"] > level + invalidation_atr * a:
                    void = True
                    break
                if ohlc[k]["high"] >= level - touch_atr * a:
                    touch_idx = k
                    break
        if void or touch_idx is None:
            return None  # most recent leg has no valid retest -> no signal
        last = ohlc[-1]
        mid = ohlc[touch_idx + 1:-1]
        if direction == "BUY":
            if last["close"] > level and all(b["close"] <= level for b in mid):
                return _sig("BUY", tag, _strength_conf(0.68, last, "BUY"),
                            last["close"],
                            [f"retest of broken {donchian_len}-bar high {level:.5f} "
                             f"held; first close back above",
                             f"advisory SL = retest low - 0.2*ATR, TP = 2.5R"])
        else:
            if last["close"] < level and all(b["close"] >= level for b in mid):
                return _sig("SELL", tag, _strength_conf(0.68, last, "SELL"),
                            last["close"],
                            [f"retest of broken {donchian_len}-bar low {level:.5f} "
                             f"held; first close back below (spot: flat)"])
        return None
    return None


# ---------------------------------------------------------------------------
# #27 — Volume-Confirmed Donchian Breakout
# ---------------------------------------------------------------------------


def volume_confirmed_donchian(ohlc, donchian_len=20, vol_sma=20,
                              vol_mult=1.5, close_strength=0.6):
    """Donchian break + trigger-bar volume >= 1.5*SMA(volume,20) + close in
    the strong 40% of the bar — the most-cited breakout-quality upgrade
    (catalog). Needs real volume; silent on volume-less feeds."""
    tag = "Breakout - Volume-Confirmed Donchian"
    if len(ohlc) < max(donchian_len, vol_sma) + 3:
        return None
    vols = _vols(ohlc)
    v_base = sma(vols, vol_sma)
    if v_base is None or v_base <= 0:
        return None
    dh, dl = _donchian(ohlc, donchian_len)
    if dh is None:
        return None
    last, prev = ohlc[-1], ohlc[-2]
    vol_ok = vols[-1] >= vol_mult * v_base
    pos = _close_pos(last)
    if (last["close"] > dh and prev["close"] <= dh and vol_ok
            and pos >= close_strength):
        return _sig("BUY", tag, 0.62 + 0.08 * min(vols[-1] / v_base - 1.0, 1.5),
                    last["close"],
                    [f"close above prior {donchian_len}-bar high {dh:.5f} on "
                     f"{vols[-1] / v_base:.1f}x volume, close-pos {pos:.2f}",
                     "advisory SL = 1.5*ATR, TP = 2.5R; trap-door exit if next "
                     "2 bars give back > 50% of trigger range"])
    if (last["close"] < dl and prev["close"] >= dl and vol_ok
            and pos <= 1.0 - close_strength):
        return _sig("SELL", tag, 0.62 + 0.08 * min(vols[-1] / v_base - 1.0, 1.5),
                    last["close"],
                    [f"close below prior {donchian_len}-bar low {dl:.5f} on "
                     f"{vols[-1] / v_base:.1f}x volume, close-pos {pos:.2f} "
                     f"(spot: flat)"])
    return None


# ---------------------------------------------------------------------------
# #28 — Volatility Regime Gate Breakout (ATR-percentile filtered Donchian)
# ---------------------------------------------------------------------------


def regime_gate_breakout(ohlc, atr_len=14, pct_lookback=100, pct_min=20,
                         pct_max=80, rise_lookback=10, donchian_len=20,
                         buffer_atr=0.1):
    """CTA-style vol-regime overlay as a standalone entry: ATR(14)/close
    percentile over 100 bars in [20, 80] (not dead, not blow-off) and rising
    vs 10 bars ago; only then does the Donchian break trigger."""
    tag = "Breakout - Volatility Regime Gate"
    if len(ohlc) < pct_lookback + atr_len + rise_lookback + 2:
        return None
    closes = _closes(ohlc)
    atr_s = _atr_series(ohlc, atr_len)
    atrp = [(a / c) if (a is not None and c > 0) else None
            for a, c in zip(atr_s, closes)]
    window = [v for v in atrp[-pct_lookback:] if v is not None]
    cur = atrp[-1]
    prev_ref = atrp[-1 - rise_lookback]
    if cur is None or prev_ref is None or len(window) < pct_lookback // 2:
        return None
    pct = 100.0 * sum(1 for v in window if v <= cur) / len(window)
    if not (pct_min <= pct <= pct_max) or cur <= prev_ref:
        return None
    dh, dl = _donchian(ohlc, donchian_len)
    a = atr_s[-1]
    if dh is None or a is None:
        return None
    last, prev = ohlc[-1], ohlc[-2]
    lvl_up, lvl_dn = dh + buffer_atr * a, dl - buffer_atr * a
    gate_txt = f"ATR% percentile {pct:.0f} in [{pct_min},{pct_max}] and rising"
    if last["close"] > lvl_up and prev["close"] <= lvl_up:
        return _sig("BUY", tag, _strength_conf(0.64, last, "BUY"), last["close"],
                    [f"close above prior {donchian_len}-bar high + buffer "
                     f"({lvl_up:.5f}); {gate_txt}",
                     "advisory SL = 2*ATR, TP = 3R; tighten to 1*ATR if pct > 90"])
    if last["close"] < lvl_dn and prev["close"] >= lvl_dn:
        return _sig("SELL", tag, _strength_conf(0.64, last, "SELL"), last["close"],
                    [f"close below prior {donchian_len}-bar low - buffer "
                     f"({lvl_dn:.5f}); {gate_txt} (spot: flat)"])
    return None


# ---------------------------------------------------------------------------
# #29 — Chaikin Volatility Expansion Breakout
# ---------------------------------------------------------------------------


def chaikin_expansion(ohlc, cv_ema=10, cv_lookback=10, cv_threshold=15,
                      donchian_len=20, trigger_window=3):
    """CV = ROC of EMA(H-L,10) over 10 bars, in %. Setup = CV cross above 0
    within the last `trigger_window` bars OR CV > 15; trigger = concurrent
    Donchian break (catalog)."""
    tag = "Volatility - Chaikin Expansion"
    if len(ohlc) < cv_ema + cv_lookback + donchian_len + 5:
        return None
    hl = [b["high"] - b["low"] for b in ohlc]
    e = ema_all(hl, cv_ema)
    off = len(hl) - len(e)  # bar index of e[0]
    cvs = {}
    for j in range(cv_lookback, len(e)):
        base = e[j - cv_lookback]
        if base > 0:
            cvs[off + j] = (e[j] - base) / base * 100.0
    last_i = len(ohlc) - 1
    if last_i not in cvs:
        return None
    crossed_recently = any(
        cvs.get(i - 1, 0.0) <= 0 < cvs[i]
        for i in range(max(last_i - trigger_window + 1, off + cv_lookback), last_i + 1)
        if i in cvs
    )
    strong = cvs[last_i] > cv_threshold
    if not (crossed_recently or strong):
        return None
    dh, dl = _donchian(ohlc, donchian_len)
    if dh is None:
        return None
    last, prev = ohlc[-1], ohlc[-2]
    cv_txt = f"CV {cvs[last_i]:+.1f}% ({'cross' if crossed_recently else 'strong'})"
    if last["close"] > dh and prev["close"] <= dh:
        return _sig("BUY", tag, _strength_conf(0.60, last, "BUY"), last["close"],
                    [f"close above prior {donchian_len}-bar high {dh:.5f} with {cv_txt}",
                     "advisory SL = 2*ATR, TP = 2.5R; exit if CV < 0 for 2 bars"])
    if last["close"] < dl and prev["close"] >= dl:
        return _sig("SELL", tag, _strength_conf(0.60, last, "SELL"), last["close"],
                    [f"close below prior {donchian_len}-bar low {dl:.5f} with "
                     f"{cv_txt} (spot: flat)"])
    return None


# ---------------------------------------------------------------------------
# registry (same shape as core.strategies.ALL_STRATEGIES)
# ---------------------------------------------------------------------------

STRATEGIES = [
    ("Breakout - Donchian 20 (Turtle S1)", donchian_20_turtle_s1),
    ("Breakout - Donchian 55 (Turtle S2)", donchian_55_turtle_s2),
    ("Breakout - N-Day High Trend-Filtered", nday_high_trend_filtered),
    ("Breakout - PDH/PDL", pdh_pdl_breakout),
    ("Breakout - Weekly Opening Range", weekly_opening_range),
    ("Breakout - ORB-30", orb_30),
    ("Breakout - Crabel Stretch", crabel_stretch),
    ("Breakout - Asian Range", asian_range_breakout),
    ("Breakout - London Open", london_open_breakout),
    ("Breakout - NY Open", ny_open_breakout),
    ("Breakout - Daily Pivot R1/S1", daily_pivot_r1s1),
    ("Breakout - Camarilla H4/L4", camarilla_h4l4),
    ("Volatility - TTM Squeeze", ttm_squeeze),
    ("Volatility - Bollinger Band Walk", bollinger_band_walk),
    ("Breakout - Keltner Channel", keltner_breakout),
    ("Volatility - ATR Expansion", atr_expansion_breakout),
    ("Breakout - NR4", nr4_breakout),
    ("Breakout - NR7", nr7_breakout),
    ("Breakout - Inside Bar (Mother)", inside_bar_mother),
    ("Breakout - NRIB", nrib_breakout),
    ("Breakout - VCP", vcp_minervini),
    ("Breakout - Gap and Go", gap_and_go),
    ("Volatility - Gap Fill", gap_fill),
    ("Breakout - Swing Failure Fade", swing_failure_fade),
    ("Breakout - Turtle Soup", turtle_soup),
    ("Breakout - Retest Continuation", breakout_retest),
    ("Breakout - Volume-Confirmed Donchian", volume_confirmed_donchian),
    ("Breakout - Volatility Regime Gate", regime_gate_breakout),
    ("Volatility - Chaikin Expansion", chaikin_expansion),
]

"""ICT / Smart-Money-Concepts & Price-Action strategy family (full published variants).

Source catalog: research/ict_smc_priceaction.md (27 entries). Each entry is a
per-tag signal function `fn(ohlc) -> None | {"action","confidence","reasons",
"strategy", ...}` following the existing detector contract in core/strategies.py
(scan_symbol ~L753: dict must carry action/confidence/reasons; we additionally
embed a unique "strategy" tag so per-strategy stats stay attributable, matching
the per-tag pattern used by core/scalp15.py and core/swing.py).

Candles are OHLC dict lists with fields {"date","open","high","low","close",
"volume","ts"} (see core/data_provider.py fetch_binance_klines). The last bar
is CLOSED. All detectors evaluate "is there an entry trigger on the last bar".

Audit evidence — why these replace (not duplicate) the simplified detectors in
core/strategies.py (we do NOT edit that file; the correct versions live here):
- detect_fvg (strategies.py L75): fires on any raw 3-bar gap with no
  displacement-candle requirement and no min-gap-size filter — catalog notes
  these two filters are "the documented edge preservers" and unfiltered
  versions "over-trade badly".
- detect_liquidity_sweep (L126): enters on the sweep candle itself with no
  close-position (top/bottom 50%) gate and no next-candle confirmation — the
  confirmation candle is "the published fix for the classic failure mode".
- detect_bos_choch (L146): fires on raw structure breaks "without
  sweep/displacement filters"; the sweep-before-CHoCH requirement is "the
  single biggest documented quality filter".
- detect_ote (L167): uses 61.8-79% with no leg-quality gates (min_leg_atr,
  displacement, unbroken-leg checks).
- detect_engulfing (L306): raw engulfing without the zone/structure gate that
  the catalog documents as the profitable variant ("the zone gate, which the
  current codebase's detect_engulfing lacks").

SKIPPED catalog entries:
- SMT Divergence (Correlated-Instrument Divergence): requires synchronized
  multi-symbol OHLC frames (BTC vs ETH legs at the same timestamps). The
  single-symbol `fn(ohlc)` contract cannot express it — flagged as a data-layer
  prerequisite per the catalog's coverage notes. Not implemented here.
- Session/killzone entries (Judas Swing, Power of Three, Silver Bullet,
  Killzone ORB) need 'ts' timestamps; they convert to US/Eastern via zoneinfo
  and SKIP GRACEFULLY (return None) when 'ts' is absent or unparseable.
"""
from datetime import datetime, timezone, timedelta

MIN_BARS = 40
FAMILY = "ict_smc_priceaction"

# ---------------------------------------------------------------------------
# Shared primitives (catalog "Conventions" section — implemented once, reused)
# ---------------------------------------------------------------------------


def _sma_series(values, period):
    out = [None] * len(values)
    run = 0.0
    for i, v in enumerate(values):
        run += v
        if i >= period:
            run -= values[i - period]
        if i >= period - 1:
            out[i] = run / period
    return out


def _atr_series(ohlc, period=14):
    """Wilder ATR aligned to bars (None until enough data). Catalog: ATR =
    Wilder ATR(14) on last closed bars unless stated."""
    n = len(ohlc)
    out = [None] * n
    if n < period + 1:
        return out
    trs = [0.0]
    for i in range(1, n):
        c, p = ohlc[i], ohlc[i - 1]
        trs.append(max(c["high"] - c["low"],
                       abs(c["high"] - p["close"]),
                       abs(c["low"] - p["close"])))
    a = sum(trs[1:period + 1]) / period
    out[period] = a
    for i in range(period + 1, n):
        a = (a * (period - 1) + trs[i]) / period
        out[i] = a
    return out


def _swing_points(ohlc, k=3):
    """Confirmed fractal swings, catalog convention: bar i is a swing high of
    order k if high[i] is STRICTLY greater than the k bars on either side; only
    confirmed swings (bar i+k closed) are returned to avoid lookahead."""
    highs, lows = [], []
    n = len(ohlc)
    for i in range(k, n - k):
        h, l = ohlc[i]["high"], ohlc[i]["low"]
        if all(h > ohlc[i - j]["high"] and h > ohlc[i + j]["high"]
               for j in range(1, k + 1)):
            highs.append((i, h))
        if all(l < ohlc[i - j]["low"] and l < ohlc[i + j]["low"]
               for j in range(1, k + 1)):
            lows.append((i, l))
    return highs, lows


def _ctx(ohlc):
    bodies = [abs(c["close"] - c["open"]) for c in ohlc]
    highs, lows = _swing_points(ohlc, 3)
    return {
        "n": len(ohlc),
        "atrs": _atr_series(ohlc),
        "bodies": bodies,
        "bsma": _sma_series(bodies, 20),
        "highs": highs,
        "lows": lows,
    }


def _is_displacement(ohlc, ctx, i, body_mult=1.5, range_mult=1.2):
    """Catalog: body >= 1.5 * SMA(|body|, 20) AND range >= 1.2 * ATR(14)."""
    if i < 1:
        return False
    b_avg, a = ctx["bsma"][i], ctx["atrs"][i]
    if not b_avg or not a:
        return False
    c = ohlc[i]
    return (abs(c["close"] - c["open"]) >= body_mult * b_avg
            and (c["high"] - c["low"]) >= range_mult * a)


def _close_pos(c):
    """Where the close sits inside its own range: 0 = low, 1 = high."""
    rng = c["high"] - c["low"]
    if rng <= 0:
        return 0.5
    return (c["close"] - c["low"]) / rng


def _find_fvgs(ohlc, ctx, min_gap_atr=0.15, displacement_only=False):
    """Catalog FVG: bullish at bar i when low[i] > high[i-2], zone =
    [high[i-2], low[i]]; bearish mirror zone = [high[i], low[i-2]]."""
    out = []
    for i in range(2, ctx["n"]):
        a = ctx["atrs"][i]
        if not a:
            continue
        if displacement_only and not _is_displacement(ohlc, ctx, i - 1):
            continue
        c, c2 = ohlc[i], ohlc[i - 2]
        if c["low"] > c2["high"] and (c["low"] - c2["high"]) >= min_gap_atr * a:
            out.append({"dir": "bull", "i": i,
                        "bottom": c2["high"], "top": c["low"]})
        elif c["high"] < c2["low"] and (c2["low"] - c["high"]) >= min_gap_atr * a:
            out.append({"dir": "bear", "i": i,
                        "bottom": c["high"], "top": c2["low"]})
    return out


def _et_hours(ohlc):
    """US/Eastern fractional hour per bar from 'ts' (epoch seconds, UTC).
    Returns None when any bar lacks a usable timestamp — session strategies
    then skip gracefully per assignment."""
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("America/New_York")
    except Exception:
        tz = timezone(timedelta(hours=-5))  # fixed EST fallback
    hours = []
    for c in ohlc:
        ts = c.get("ts", c.get("timestamp"))
        if ts is None:
            return None
        try:
            if isinstance(ts, (int, float)):
                dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            else:
                dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
            et = dt.astimezone(tz)
            hours.append(et.hour + et.minute / 60.0)
        except Exception:
            return None
    return hours


def _sig(action, tag, conf, reasons, **extra):
    s = {"action": action, "confidence": conf, "reasons": reasons,
         "strategy": tag, "family": FAMILY}
    s.update(extra)
    return s


# ---------------------------------------------------------------------------
# 1) Fair Value Gap Retracement Entry (catalog: FVG)
# ---------------------------------------------------------------------------

def strat_fvg_retrace(ohlc, displacement_body_mult=1.5, min_gap_atr=0.15,
                      max_wait_bars=20):
    """Bullish FVG after a DISPLACEMENT middle candle; enter on first retrace
    close inside the unfilled zone. Filters the existing detect_fvg lacks."""
    if len(ohlc) < MIN_BARS:
        return None
    ctx = _ctx(ohlc)
    n = ctx["n"]
    fvgs = _find_fvgs(ohlc, ctx, min_gap_atr, displacement_only=True)
    for f in reversed(fvgs):
        if n - 1 - f["i"] > max_wait_bars or f["i"] >= n - 1:
            continue
        if f["dir"] == "bull":
            # gap invalidated if any candle closed below the bottom edge
            if any(ohlc[j]["close"] < f["bottom"] for j in range(f["i"] + 1, n)):
                continue
            # first touch: no earlier post-formation close entered the zone
            if any(ohlc[j]["close"] <= f["top"] for j in range(f["i"] + 1, n - 1)):
                continue
            last = ohlc[-1]
            if f["bottom"] <= last["close"] <= f["top"]:
                return _sig("BUY", "ict_pa_fvg_retrace", 0.70, [
                    f"Bullish FVG {f['bottom']:.5f}-{f['top']:.5f} after displacement",
                    "first retrace close inside unfilled gap",
                ], zone_bottom=f["bottom"], zone_top=f["top"])
        else:
            if any(ohlc[j]["close"] > f["top"] for j in range(f["i"] + 1, n)):
                continue
            if any(ohlc[j]["close"] >= f["bottom"] for j in range(f["i"] + 1, n - 1)):
                continue
            last = ohlc[-1]
            if f["bottom"] <= last["close"] <= f["top"]:
                return _sig("SELL", "ict_pa_fvg_retrace", 0.70, [
                    f"Bearish FVG {f['bottom']:.5f}-{f['top']:.5f} after displacement",
                    "first retrace close inside unfilled gap",
                ], zone_bottom=f["bottom"], zone_top=f["top"])
    return None


# ---------------------------------------------------------------------------
# 2) Inversion FVG (IFVG)
# ---------------------------------------------------------------------------

def strat_ifvg(ohlc, min_gap_atr=0.15, retest_max_bars=15):
    """A failed gap flips polarity: bearish FVG CLOSED through to the upside
    becomes support; enter long on the first retest of the inverted zone that
    holds (close back above zone top, or rejection wick with close in the
    candle's upper 50%)."""
    if len(ohlc) < MIN_BARS:
        return None
    ctx = _ctx(ohlc)
    n = ctx["n"]
    for f in reversed(_find_fvgs(ohlc, ctx, min_gap_atr)):
        inv = None
        for j in range(f["i"] + 1, n):
            if f["dir"] == "bear" and ohlc[j]["close"] > f["top"]:
                inv, direction = j, "BUY"
                break
            if f["dir"] == "bull" and ohlc[j]["close"] < f["bottom"]:
                inv, direction = j, "SELL"
                break
        if inv is None or n - 1 - inv > retest_max_bars:
            continue
        # retest must be the first touch after inversion
        touched = False
        for k in range(inv + 1, n):
            c = ohlc[k]
            if direction == "BUY":
                if c["low"] <= f["top"]:
                    if k == n - 1 and (c["close"] > f["top"] or
                                       (c["low"] >= f["bottom"] and _close_pos(c) >= 0.5)):
                        touched = True
                    break
            else:
                if c["high"] >= f["bottom"]:
                    if k == n - 1 and (c["close"] < f["bottom"] or
                                       (c["high"] <= f["top"] and _close_pos(c) <= 0.5)):
                        touched = True
                    break
        if touched:
            return _sig(direction, "ict_pa_ifvg", 0.68, [
                f"Inverted {'bearish' if direction == 'BUY' else 'bullish'} FVG "
                f"{f['bottom']:.5f}-{f['top']:.5f} flipped polarity",
                "first retest of inverted zone held",
            ], zone_bottom=f["bottom"], zone_top=f["top"])
    return None


# ---------------------------------------------------------------------------
# 3) Order Block Retest (displacement-gated, unmitigated, first retest)
# ---------------------------------------------------------------------------

def strat_order_block_retest(ohlc, ob_lookback=8, max_zone_age_bars=100):
    """Bullish OB = LAST bearish candle before a bullish displacement that
    broke a confirmed swing high. Enter on first retest of the unmitigated
    zone ([low, high]). The displacement gate is mandatory per catalog."""
    if len(ohlc) < MIN_BARS:
        return None
    ctx = _ctx(ohlc)
    n = ctx["n"]
    start = max(2, n - 1 - max_zone_age_bars)
    for d in range(n - 3, start, -1):  # candidate displacement bar
        c = ohlc[d]
        bull = c["close"] > c["open"]
        if not _is_displacement(ohlc, ctx, d):
            continue
        prior_sh = [(i, p) for i, p in ctx["highs"] if i < d]
        prior_sl = [(i, p) for i, p in ctx["lows"] if i < d]
        if bull:
            if not prior_sh or c["close"] <= prior_sh[-1][1]:
                continue
        else:
            if not prior_sl or c["close"] >= prior_sl[-1][1]:
                continue
        ob = None
        for j in range(d - 1, max(d - 1 - ob_lookback, 0), -1):
            is_bear = ohlc[j]["close"] < ohlc[j]["open"]
            if bull and is_bear:
                ob = j
                break
            if not bull and ohlc[j]["close"] > ohlc[j]["open"]:
                ob = j
                break
        if ob is None:
            continue
        zbot, ztop = ohlc[ob]["low"], ohlc[ob]["high"]
        mid = (zbot + ztop) / 2.0
        if bull:
            if any(ohlc[j]["low"] < zbot for j in range(d + 1, n)):
                continue  # mitigated
            if any(ohlc[j]["low"] <= mid for j in range(d + 1, n - 1)):
                continue  # not the first retest
            last = ohlc[-1]
            if last["low"] <= mid and last["close"] > zbot:
                return _sig("BUY", "ict_pa_order_block", 0.65, [
                    f"Bullish OB {zbot:.5f}-{ztop:.5f} (displacement-gated)",
                    "first retest of unmitigated zone",
                ], zone_bottom=zbot, zone_top=ztop)
        else:
            if any(ohlc[j]["high"] > ztop for j in range(d + 1, n)):
                continue
            if any(ohlc[j]["high"] >= mid for j in range(d + 1, n - 1)):
                continue
            last = ohlc[-1]
            if last["high"] >= mid and last["close"] < ztop:
                return _sig("SELL", "ict_pa_order_block", 0.65, [
                    f"Bearish OB {zbot:.5f}-{ztop:.5f} (displacement-gated)",
                    "first retest of unmitigated zone",
                ], zone_bottom=zbot, zone_top=ztop)
    return None


# ---------------------------------------------------------------------------
# 4) Breaker Block (state machine: OB broken -> sweep -> displacement reclaim)
# ---------------------------------------------------------------------------

def _detect_breakers(ohlc, ctx, ob_lookback=8, sweep_window=10):
    """Return [{'dir','r','bottom','top'}] where r = index of the reclaim
    displacement bar. Bullish breaker: bearish OB forms (last bullish candle
    before a bearish displacement that broke a swing low), price closes DOWN
    through it, the down-leg sweeps an older swing low, then a bullish
    displacement closes back ABOVE the failed OB."""
    n = ctx["n"]
    out = []
    for d1 in range(max(3, n - 90), n - 5):
        c1 = ohlc[d1]
        if c1["close"] >= c1["open"] or not _is_displacement(ohlc, ctx, d1):
            continue
        prior_sl = [(i, p) for i, p in ctx["lows"] if i < d1]
        if not prior_sl or c1["close"] >= prior_sl[-1][1]:
            continue
        ob = None
        for j in range(d1 - 1, max(d1 - 1 - ob_lookback, 0), -1):
            if ohlc[j]["close"] > ohlc[j]["open"]:
                ob = j
                break
        if ob is None:
            continue
        zbot, ztop = ohlc[ob]["low"], ohlc[ob]["high"]
        b = None
        for j in range(d1 + 1, n - 3):
            if ohlc[j]["close"] < zbot:
                b = j
                break
        if b is None:
            continue
        s = None
        for j in range(b + 1, min(b + 1 + sweep_window, n - 2)):
            pre_sl = [(i, p) for i, p in ctx["lows"] if i < j - 3]
            if pre_sl and ohlc[j]["low"] < pre_sl[-1][1] and ohlc[j]["close"] > pre_sl[-1][1]:
                s = j
                break
        if s is None:
            continue
        for r in range(s + 1, n):
            cr = ohlc[r]
            if cr["close"] > cr["open"] and cr["close"] > ztop and \
                    _is_displacement(ohlc, ctx, r):
                out.append({"dir": "bull", "r": r, "bottom": zbot, "top": ztop})
                break
    # bearish mirror
    for d1 in range(max(3, n - 90), n - 5):
        c1 = ohlc[d1]
        if c1["close"] <= c1["open"] or not _is_displacement(ohlc, ctx, d1):
            continue
        prior_sh = [(i, p) for i, p in ctx["highs"] if i < d1]
        if not prior_sh or c1["close"] <= prior_sh[-1][1]:
            continue
        ob = None
        for j in range(d1 - 1, max(d1 - 1 - ob_lookback, 0), -1):
            if ohlc[j]["close"] < ohlc[j]["open"]:
                ob = j
                break
        if ob is None:
            continue
        zbot, ztop = ohlc[ob]["low"], ohlc[ob]["high"]
        b = None
        for j in range(d1 + 1, n - 3):
            if ohlc[j]["close"] > ztop:
                b = j
                break
        if b is None:
            continue
        s = None
        for j in range(b + 1, min(b + 1 + sweep_window, n - 2)):
            pre_sh = [(i, p) for i, p in ctx["highs"] if i < j - 3]
            if pre_sh and ohlc[j]["high"] > pre_sh[-1][1] and ohlc[j]["close"] < pre_sh[-1][1]:
                s = j
                break
        if s is None:
            continue
        for r in range(s + 1, n):
            cr = ohlc[r]
            if cr["close"] < cr["open"] and cr["close"] < zbot and \
                    _is_displacement(ohlc, ctx, r):
                out.append({"dir": "bear", "r": r, "bottom": zbot, "top": ztop})
                break
    return out


def strat_breaker_block(ohlc, max_retest_bars=30):
    """Failed OB + liquidity sweep + reclaim; enter on first retest of the
    breaker zone. Highest-documented-R:R SMC setup per catalog."""
    if len(ohlc) < MIN_BARS:
        return None
    ctx = _ctx(ohlc)
    n = ctx["n"]
    for br in reversed(_detect_breakers(ohlc, ctx)):
        if n - 1 - br["r"] > max_retest_bars or br["r"] >= n - 1:
            continue
        last = ohlc[-1]
        if br["dir"] == "bull":
            if any(ohlc[j]["low"] <= br["top"] for j in range(br["r"] + 1, n - 1)):
                continue
            if last["low"] <= br["top"] and last["close"] >= br["bottom"]:
                return _sig("BUY", "ict_pa_breaker", 0.75, [
                    f"Bullish breaker {br['bottom']:.5f}-{br['top']:.5f} "
                    "(failed OB + sweep + reclaim)",
                    "first retest of breaker zone",
                ], zone_bottom=br["bottom"], zone_top=br["top"])
        else:
            if any(ohlc[j]["high"] >= br["bottom"] for j in range(br["r"] + 1, n - 1)):
                continue
            if last["high"] >= br["bottom"] and last["close"] <= br["top"]:
                return _sig("SELL", "ict_pa_breaker", 0.75, [
                    f"Bearish breaker {br['bottom']:.5f}-{br['top']:.5f} "
                    "(failed OB + sweep + reclaim)",
                    "first retest of breaker zone",
                ], zone_bottom=br["bottom"], zone_top=br["top"])
    return None


# ---------------------------------------------------------------------------
# 5) Unicorn Setup (breaker + FVG overlap) — placed here, uses breaker+fvg
# ---------------------------------------------------------------------------

def strat_unicorn(ohlc, max_retest_bars=20):
    """Bullish breaker whose reclaim displacement also left a bullish FVG
    OVERLAPPING the breaker zone; entry = first retest of the intersection
    (must be >= 0.1*ATR wide)."""
    if len(ohlc) < MIN_BARS:
        return None
    ctx = _ctx(ohlc)
    n = ctx["n"]
    fvgs = _find_fvgs(ohlc, ctx)
    for br in reversed(_detect_breakers(ohlc, ctx)):
        if n - 1 - br["r"] > max_retest_bars:
            continue
        a = ctx["atrs"][br["r"]] or 0
        for f in fvgs:
            if f["dir"] != br["dir"] or not (br["r"] <= f["i"] <= br["r"] + 2):
                continue
            otop = min(br["top"], f["top"])
            obot = max(br["bottom"], f["bottom"])
            if otop - obot < 0.1 * a:
                continue
            last = ohlc[-1]
            if br["dir"] == "bull":
                if any(ohlc[j]["low"] <= otop for j in range(br["r"] + 1, n - 1)):
                    continue
                if last["low"] <= otop and last["close"] >= obot:
                    return _sig("BUY", "ict_pa_unicorn", 0.80, [
                        f"Unicorn: breaker+FVG overlap {obot:.5f}-{otop:.5f}",
                        "first retest of overlap zone",
                    ], zone_bottom=obot, zone_top=otop)
            else:
                if any(ohlc[j]["high"] >= obot for j in range(br["r"] + 1, n - 1)):
                    continue
                if last["high"] >= obot and last["close"] <= otop:
                    return _sig("SELL", "ict_pa_unicorn", 0.80, [
                        f"Unicorn: breaker+FVG overlap {obot:.5f}-{otop:.5f}",
                        "first retest of overlap zone",
                    ], zone_bottom=obot, zone_top=otop)
    return None


# ---------------------------------------------------------------------------
# 6) Mitigation Block (origin of a FAILED move, retested)
# ---------------------------------------------------------------------------

def strat_mitigation_block(ohlc, zone_lookback=30):
    """Bearish: a rally fails to break the prior swing high (lower high),
    sells off >= 1*ATR, and the origin bullish candle of that failed rally is
    retested from below -> SHORT. Long mirrors (failed down-move, higher low).
    Breaker requires price THROUGH the zone; mitigation requires the move OUT
    of the zone to fail at structure — that is the codable distinction."""
    if len(ohlc) < MIN_BARS:
        return None
    ctx = _ctx(ohlc)
    n = ctx["n"]
    highs, lows, atrs = ctx["highs"], ctx["lows"], ctx["atrs"]
    if len(highs) >= 2:
        (i1, h1), (i2, h2) = highs[-2], highs[-1]
        a = atrs[i2] or 0
        if h2 < h1 and n - 1 - i2 <= zone_lookback and a:
            declined = any(ohlc[j]["low"] < h2 - a for j in range(i2 + 1, n))
            origin = None
            for j in range(i2, max(i2 - 6, 0), -1):
                if ohlc[j]["close"] > ohlc[j]["open"]:
                    origin = j
                    break
            if declined and origin is not None:
                zbot, ztop = ohlc[origin]["low"], ohlc[origin]["high"]
                unbroken = not any(ohlc[j]["close"] > h2 for j in range(i2 + 1, n))
                last = ohlc[-1]
                if unbroken and last["high"] >= zbot and last["close"] <= ztop \
                        and last["low"] < zbot:
                    return _sig("SELL", "ict_pa_mitigation", 0.55, [
                        f"Bearish mitigation zone {zbot:.5f}-{ztop:.5f} "
                        f"(failed rally, LH {h2:.5f} < {h1:.5f})",
                        "zone retested from below",
                    ], zone_bottom=zbot, zone_top=ztop)
    if len(lows) >= 2:
        (i1, l1), (i2, l2) = lows[-2], lows[-1]
        a = atrs[i2] or 0
        if l2 > l1 and n - 1 - i2 <= zone_lookback and a:
            rallied = any(ohlc[j]["high"] > l2 + a for j in range(i2 + 1, n))
            origin = None
            for j in range(i2, max(i2 - 6, 0), -1):
                if ohlc[j]["close"] < ohlc[j]["open"]:
                    origin = j
                    break
            if rallied and origin is not None:
                zbot, ztop = ohlc[origin]["low"], ohlc[origin]["high"]
                unbroken = not any(ohlc[j]["close"] < l2 for j in range(i2 + 1, n))
                last = ohlc[-1]
                if unbroken and last["low"] <= ztop and last["close"] >= zbot \
                        and last["high"] > ztop:
                    return _sig("BUY", "ict_pa_mitigation", 0.55, [
                        f"Bullish mitigation zone {zbot:.5f}-{ztop:.5f} "
                        f"(failed down-move, HL {l2:.5f} > {l1:.5f})",
                        "zone retested from above",
                    ], zone_bottom=zbot, zone_top=ztop)
    return None


# ---------------------------------------------------------------------------
# 7) Liquidity Sweep Reversal (swing stop hunt, confirmation-candle variant)
# ---------------------------------------------------------------------------

def strat_liquidity_sweep_reversal(ohlc, swing_k=3, close_position=0.5):
    """Sweep of a confirmed swing low (wick beyond, close back above) closing
    in its top 50%, THEN the next candle closing above the sweep candle's
    high. The confirmation candle is the published fix vs entering on the
    sweep itself (audit: strategies.py L126 detect_liquidity_sweep)."""
    if len(ohlc) < MIN_BARS:
        return None
    ctx = _ctx(ohlc)
    n = ctx["n"]
    j, c = n - 2, n - 1  # sweep candle, confirmation candle
    sw = ohlc[j]
    conf = ohlc[c]
    for i, p in ctx["lows"]:
        if i >= j:
            continue
        if sw["low"] < p and sw["close"] > p and _close_pos(sw) >= close_position \
                and conf["close"] > sw["high"]:
            return _sig("BUY", "ict_pa_liquidity_sweep", 0.70, [
                f"Swept swing low {p:.5f} and reclaimed (close in top 50%)",
                "confirmation candle closed above sweep high",
            ], swept_level=p)
    for i, p in ctx["highs"]:
        if i >= j:
            continue
        if sw["high"] > p and sw["close"] < p and _close_pos(sw) <= close_position \
                and conf["close"] < sw["low"]:
            return _sig("SELL", "ict_pa_liquidity_sweep", 0.70, [
                f"Swept swing high {p:.5f} and rejected (close in bottom 50%)",
                "confirmation candle closed below sweep low",
            ], swept_level=p)
    return None


# ---------------------------------------------------------------------------
# 8) Equal Highs/Lows Liquidity Raid (Turtle Soup, Raschke 1995)
# ---------------------------------------------------------------------------

def strat_turtle_soup(ohlc, eq_tol_atr=0.1, min_separation=5,
                      max_penetration_atr=0.5, raid_bars=3):
    """Two confirmed swing lows within eq_tol*ATR, >= 5 bars apart; a SHALLOW
    raid below (< max_penetration*ATR) that closes back above within raid_bars
    -> BUY. The penetration cap is critical: deep penetration converts the
    raid into a breakout."""
    if len(ohlc) < MIN_BARS:
        return None
    ctx = _ctx(ohlc)
    n = ctx["n"]
    atr_end = ctx["atrs"][-1]
    if not atr_end:
        return None
    last = ohlc[-1]
    window_low = min(c["low"] for c in ohlc[n - raid_bars:])
    window_high = max(c["high"] for c in ohlc[n - raid_bars:])
    for k in range(len(ctx["lows"]) - 1):
        for m in range(k + 1, len(ctx["lows"])):
            (ia, la), (ib, lb) = ctx["lows"][k], ctx["lows"][m]
            if ib - ia < min_separation or ib >= n - 1:
                continue
            if abs(la - lb) > eq_tol_atr * atr_end:
                continue
            level = min(la, lb)
            pen = level - window_low
            if 0 < pen <= max_penetration_atr * atr_end \
                    and last["close"] > level \
                    and not any(ohlc[q]["close"] < level
                                for q in range(n - raid_bars, n)):
                return _sig("BUY", "ict_pa_turtle_soup", 0.65, [
                    f"Equal lows {la:.5f}/{lb:.5f} raided {pen:.5f} and reclaimed",
                    "shallow fast penetration (turtle soup)",
                ], level=level)
    for k in range(len(ctx["highs"]) - 1):
        for m in range(k + 1, len(ctx["highs"])):
            (ia, ha), (ib, hb) = ctx["highs"][k], ctx["highs"][m]
            if ib - ia < min_separation or ib >= n - 1:
                continue
            if abs(ha - hb) > eq_tol_atr * atr_end:
                continue
            level = max(ha, hb)
            pen = window_high - level
            if 0 < pen <= max_penetration_atr * atr_end \
                    and last["close"] < level \
                    and not any(ohlc[q]["close"] > level
                                for q in range(n - raid_bars, n)):
                return _sig("SELL", "ict_pa_turtle_soup", 0.65, [
                    f"Equal highs {ha:.5f}/{hb:.5f} raided {pen:.5f} and rejected",
                    "shallow fast penetration (turtle soup)",
                ], level=level)
    return None


# ---------------------------------------------------------------------------
# 9) Session High/Low Sweep + Reclaim (Judas Swing) — needs 'ts'
# ---------------------------------------------------------------------------

def strat_judas_swing(ohlc, killzone=(2, 5), sweep_max_bars=12, lookback=96):
    """During the London killzone (02:00-05:00 ET), price sweeps the Asian
    session low and closes back inside the Asian range -> BUY on the reclaim.
    Session hours per catalog (US/Eastern). Skips gracefully without 'ts'."""
    if len(ohlc) < MIN_BARS:
        return None
    hours = _et_hours(ohlc)
    if hours is None:
        return None
    n = len(ohlc)
    if not (killzone[0] <= hours[-1] < killzone[1]):
        return None
    # Asian range = bars with ET hour >= 20 or < 2, before this killzone
    kz_start = n - 1
    while kz_start > 0 and killzone[0] <= hours[kz_start - 1] < killzone[1]:
        kz_start -= 1
    asia = [i for i in range(max(0, kz_start - lookback), kz_start)
            if hours[i] >= 20 or hours[i] < 2]
    if not asia:
        return None
    asia_low = min(ohlc[i]["low"] for i in asia)
    asia_high = max(ohlc[i]["high"] for i in asia)
    last = ohlc[-1]
    recent = range(max(kz_start, n - sweep_max_bars), n)
    for j in recent:
        c = ohlc[j]
        if c["low"] < asia_low and c["close"] > asia_low and last["close"] > asia_low:
            return _sig("BUY", "ict_pa_judas_swing", 0.65, [
                f"Killzone sweep of Asian low {asia_low:.5f}, reclaimed",
                f"Judas swing long; Asian range top {asia_high:.5f}",
            ], swept_level=asia_low)
        if c["high"] > asia_high and c["close"] < asia_high and last["close"] < asia_high:
            return _sig("SELL", "ict_pa_judas_swing", 0.65, [
                f"Killzone sweep of Asian high {asia_high:.5f}, rejected",
                f"Judas swing short; Asian range bottom {asia_low:.5f}",
            ], swept_level=asia_high)
    return None


# ---------------------------------------------------------------------------
# 10) Break of Structure Continuation (BOS Pullback)
# ---------------------------------------------------------------------------

def strat_bos_pullback(ohlc, max_wait_bars=15):
    """Established uptrend (>= 2 consecutive HH and HL by confirmed swings);
    a DISPLACEMENT close above the most recent swing high (BOS); enter on the
    first pullback into the BOS candle's 50% body or its FVG, provided price
    has not closed below the last higher low. Does not fire on the first
    break of a fresh structure (that is CHoCH's job)."""
    if len(ohlc) < MIN_BARS:
        return None
    ctx = _ctx(ohlc)
    n = ctx["n"]
    fvgs = _find_fvgs(ohlc, ctx)
    for b in range(n - 2, max(2, n - 2 - max_wait_bars), -1):
        c = ohlc[b]
        bull = c["close"] > c["open"]
        if not _is_displacement(ohlc, ctx, b):
            continue
        pre_h = [(i, p) for i, p in ctx["highs"] if i < b]
        pre_l = [(i, p) for i, p in ctx["lows"] if i < b]
        if len(pre_h) < 2 or len(pre_l) < 2:
            continue
        if bull:
            if not (pre_h[-1][1] > pre_h[-2][1] and pre_l[-1][1] > pre_l[-2][1]):
                continue  # trend not yet established -> not BOS
            if c["close"] <= pre_h[-1][1]:
                continue
            hl = pre_l[-1][1]
            if any(ohlc[j]["close"] < hl for j in range(b + 1, n)):
                continue  # structure broken before fill
            body_mid = (c["open"] + c["close"]) / 2.0
            fvg_zone = next((f for f in fvgs if f["dir"] == "bull" and f["i"] == b), None)
            if any(ohlc[j]["low"] <= body_mid for j in range(b + 1, n - 1)):
                continue  # not the first pullback
            last = ohlc[-1]
            touched = last["low"] <= body_mid or (
                fvg_zone and last["low"] <= fvg_zone["top"])
            if touched and last["close"] > hl:
                return _sig("BUY", "ict_pa_bos_pullback", 0.65, [
                    f"BOS: displacement close above swing high {pre_h[-1][1]:.5f} "
                    "in established uptrend",
                    "first pullback into BOS 50%/FVG zone",
                ], broken_level=pre_h[-1][1])
        else:
            if not (pre_h[-1][1] < pre_h[-2][1] and pre_l[-1][1] < pre_l[-2][1]):
                continue
            if c["close"] >= pre_l[-1][1]:
                continue
            lh = pre_h[-1][1]
            if any(ohlc[j]["close"] > lh for j in range(b + 1, n)):
                continue
            body_mid = (c["open"] + c["close"]) / 2.0
            fvg_zone = next((f for f in fvgs if f["dir"] == "bear" and f["i"] == b), None)
            if any(ohlc[j]["high"] >= body_mid for j in range(b + 1, n - 1)):
                continue
            last = ohlc[-1]
            touched = last["high"] >= body_mid or (
                fvg_zone and last["high"] >= fvg_zone["bottom"])
            if touched and last["close"] < lh:
                return _sig("SELL", "ict_pa_bos_pullback", 0.65, [
                    f"BOS: displacement close below swing low {pre_l[-1][1]:.5f} "
                    "in established downtrend",
                    "first pullback into BOS 50%/FVG zone",
                ], broken_level=pre_l[-1][1])
    return None


# ---------------------------------------------------------------------------
# 11) CHoCH / Market Structure Shift Reversal (sweep-before-CHoCH gated)
# ---------------------------------------------------------------------------

def strat_choch_reversal(ohlc, retest_max_bars=15):
    """Downtrend (>= 2 LL/LH by swings BEFORE the sweep); price sweeps the
    last lower low, then rallies and CLOSES above the most recent lower high
    with displacement (CHoCH). Enter long on the first retracement that holds
    above the broken level. The sweep-before-CHoCH gate is the documented
    quality filter the existing detect_bos_choch (strategies.py L146) lacks."""
    if len(ohlc) < MIN_BARS:
        return None
    ctx = _ctx(ohlc)
    n = ctx["n"]
    last = ohlc[-1]
    for s in range(n - 3, max(4, n - 3 - 25), -1):  # sweep bar
        pre_h = [(i, p) for i, p in ctx["highs"] if i < s]
        pre_l = [(i, p) for i, p in ctx["lows"] if i < s]
        if len(pre_h) < 2 or len(pre_l) < 2:
            continue
        cs = ohlc[s]
        bull_rev = (pre_h[-1][1] < pre_h[-2][1] and pre_l[-1][1] < pre_l[-2][1]
                    and cs["low"] < pre_l[-1][1] and cs["close"] > pre_l[-1][1])
        bear_rev = (pre_h[-1][1] > pre_h[-2][1] and pre_l[-1][1] > pre_l[-2][1]
                    and cs["high"] > pre_h[-1][1] and cs["close"] < pre_h[-1][1])
        if not (bull_rev or bear_rev):
            continue
        level = pre_h[-1][1] if bull_rev else pre_l[-1][1]
        for r in range(s + 1, n - 1):  # CHoCH displacement bar
            cr = ohlc[r]
            if n - 1 - r > retest_max_bars:
                break
            if not _is_displacement(ohlc, ctx, r):
                continue
            if bull_rev and cr["close"] > level and cr["close"] > cr["open"]:
                if any(ohlc[j]["close"] < level for j in range(r + 1, n)):
                    break  # retest failed; level lost
                if any(ohlc[j]["low"] <= level for j in range(r + 1, n - 1)):
                    break  # not first retracement
                if last["low"] <= level and last["close"] > level:
                    return _sig("BUY", "ict_pa_choch_reversal", 0.65, [
                        f"CHoCH: swept {pre_l[-1][1]:.5f} then displacement close "
                        f"above LH {level:.5f}",
                        "first retracement holds above broken level",
                    ], broken_level=level)
            if bear_rev and cr["close"] < level and cr["close"] < cr["open"]:
                if any(ohlc[j]["close"] > level for j in range(r + 1, n)):
                    break
                if any(ohlc[j]["high"] >= level for j in range(r + 1, n - 1)):
                    break
                if last["high"] >= level and last["close"] < level:
                    return _sig("SELL", "ict_pa_choch_reversal", 0.65, [
                        f"CHoCH: swept {pre_h[-1][1]:.5f} then displacement close "
                        f"below HL {level:.5f}",
                        "first retracement holds below broken level",
                    ], broken_level=level)
    return None


# ---------------------------------------------------------------------------
# 12) Optimal Trade Entry (OTE) — leg-quality gated
# ---------------------------------------------------------------------------

def strat_ote(ohlc, fib_low=0.62, fib_high=0.79, min_leg_atr=1.5,
              max_wait_bars=25):
    """Bullish impulse leg (swing low -> swing high, range >= 1.5*ATR, with
    displacement, leg high unbroken, leg low unviolated); enter on retrace
    into the 0.62-0.79 zone. Reject legs whose retrace already exceeded 0.79
    (deep retraces statistically fail). Audit: strategies.py L167 detect_ote
    has no leg-quality gates."""
    if len(ohlc) < MIN_BARS:
        return None
    ctx = _ctx(ohlc)
    n = ctx["n"]
    last = ohlc[-1]
    for hi, hp in reversed(ctx["highs"]):
        if hi >= n - 1 or n - 1 - hi > max_wait_bars:
            continue
        a = ctx["atrs"][hi]
        if not a:
            continue
        prior_lows = [(li, lp) for li, lp in ctx["lows"] if li < hi]
        if not prior_lows:
            continue
        li, lp = prior_lows[-1]
        leg = hp - lp
        if leg < min_leg_atr * a:
            continue
        if not any(_is_displacement(ohlc, ctx, j) for j in range(li + 1, hi + 1)):
            continue
        if any(ohlc[j]["close"] > hp for j in range(hi + 1, n)):
            continue  # leg high broken
        if any(ohlc[j]["close"] < lp for j in range(hi + 1, n)):
            continue  # leg origin violated -> no edge per catalog
        z_top = hp - fib_low * leg
        z_bot = hp - fib_high * leg
        if any(ohlc[j]["close"] < z_bot for j in range(hi + 1, n - 1)):
            continue  # retrace exceeded 0.79 before entry
        if z_bot <= last["close"] <= z_top:
            return _sig("BUY", "ict_pa_ote", 0.65, [
                f"OTE long: leg {lp:.5f}->{hp:.5f} ({leg / a:.1f}x ATR, displacement)",
                f"retrace into {fib_low}-{fib_high} zone ({z_bot:.5f}-{z_top:.5f})",
            ], zone_bottom=z_bot, zone_top=z_top)
    for li, lp in reversed(ctx["lows"]):
        if li >= n - 1 or n - 1 - li > max_wait_bars:
            continue
        a = ctx["atrs"][li]
        if not a:
            continue
        prior_highs = [(hi, hp) for hi, hp in ctx["highs"] if hi < li]
        if not prior_highs:
            continue
        hi, hp = prior_highs[-1]
        leg = lp - hp
        if leg < min_leg_atr * a:
            continue
        if not any(_is_displacement(ohlc, ctx, j) for j in range(hi + 1, li + 1)):
            continue
        if any(ohlc[j]["close"] < lp for j in range(li + 1, n)):
            continue
        if any(ohlc[j]["close"] > hp for j in range(li + 1, n)):
            continue
        z_bot = lp + fib_low * leg
        z_top = lp + fib_high * leg
        if any(ohlc[j]["close"] > z_top for j in range(li + 1, n - 1)):
            continue
        if z_bot <= last["close"] <= z_top:
            return _sig("SELL", "ict_pa_ote", 0.65, [
                f"OTE short: leg {hp:.5f}->{lp:.5f} ({leg / a:.1f}x ATR, displacement)",
                f"retrace into {fib_low}-{fib_high} zone ({z_bot:.5f}-{z_top:.5f})",
            ], zone_bottom=z_bot, zone_top=z_top)
    return None


# ---------------------------------------------------------------------------
# 13) Premium/Discount Zone Filter (Equilibrium Model)
# ---------------------------------------------------------------------------

def _dealing_range(ctx, min_range_atr=2.0):
    """Most recent confirmed swing low -> swing high (or mirror) with range
    >= min_range_atr * ATR. Returns (low, high) or None."""
    if not ctx["highs"] or not ctx["lows"]:
        return None
    hi, hp = ctx["highs"][-1]
    li, lp = ctx["lows"][-1]
    lo, hi_p = (lp, hp) if hp > lp else (hp, lp)
    a = ctx["atrs"][-1]
    if not a or (hi_p - lo) < min_range_atr * a:
        return None
    return lo, hi_p


def premium_discount_gate(ohlc, price=None):
    """Gate companion for other entries (catalog: this is published as a
    FILTER): returns 'discount', 'premium', or None (equilibrium / no range).
    Longs only valid in discount, shorts only in premium."""
    if len(ohlc) < MIN_BARS:
        return None
    ctx = _ctx(ohlc)
    dr = _dealing_range(ctx)
    if not dr:
        return None
    lo, hi = dr
    eq = (lo + hi) / 2.0
    p = price if price is not None else ohlc[-1]["close"]
    return "discount" if p < eq else ("premium" if p > eq else None)


def strat_premium_discount(ohlc, deep_zone=0.2):
    """Standalone variant: fire when price enters the deepest discount
    quintile (<= 20% of the dealing range) AND prints a bullish rejection
    candle (close in top 40%); premium mirror. The gate form
    (premium_discount_gate) is the more robust deliverable per catalog."""
    if len(ohlc) < MIN_BARS:
        return None
    ctx = _ctx(ohlc)
    dr = _dealing_range(ctx)
    if not dr:
        return None
    lo, hi = dr
    rng = hi - lo
    last = ohlc[-1]
    if last["low"] <= lo + deep_zone * rng and _close_pos(last) >= 0.6:
        return _sig("BUY", "ict_pa_premium_discount", 0.60, [
            f"Deep discount (<= {deep_zone:.0%} of range {lo:.5f}-{hi:.5f})",
            "bullish rejection candle (close in top 40%)",
        ], range_low=lo, range_high=hi)
    if last["high"] >= hi - deep_zone * rng and _close_pos(last) <= 0.4:
        return _sig("SELL", "ict_pa_premium_discount", 0.60, [
            f"Deep premium (>= {1 - deep_zone:.0%} of range {lo:.5f}-{hi:.5f})",
            "bearish rejection candle (close in bottom 40%)",
        ], range_low=lo, range_high=hi)
    return None


# ---------------------------------------------------------------------------
# 14) Power of Three / AMD — needs 'ts'
# ---------------------------------------------------------------------------

def strat_power_of_three(ohlc, acc_hours=6, acc_range_atr=1.0,
                         manip_max_atr=0.75):
    """Anchor = 00:00 ET daily open. Accumulation = tight range (<= acc_range
    ATR) for the first acc_hours; manipulation = shallow break of the range
    low (<= manip_max ATR) closing back inside; distribution = displacement
    close above the manipulation leg's high -> BUY. Skips without 'ts'."""
    if len(ohlc) < MIN_BARS:
        return None
    hours = _et_hours(ohlc)
    if hours is None:
        return None
    n = len(ohlc)
    # find last midnight-ET boundary
    anchor = None
    for i in range(n - 1, 0, -1):
        if hours[i] < hours[i - 1] and hours[i] < 1:
            anchor = i
            break
    if anchor is None or n - 1 - anchor < acc_hours + 3:
        return None
    ctx = _ctx(ohlc)
    a = ctx["atrs"][-1]
    if not a:
        return None
    acc_end = min(anchor + acc_hours, n - 2)
    acc = ohlc[anchor:acc_end + 1]
    acc_lo = min(c["low"] for c in acc)
    acc_hi = max(c["high"] for c in acc)
    if acc_hi - acc_lo > acc_range_atr * a or acc_hi - acc_lo <= 0:
        return None
    manip = None
    for j in range(acc_end + 1, n - 1):
        c = ohlc[j]
        if c["low"] < acc_lo and (acc_lo - c["low"]) <= manip_max_atr * a \
                and c["close"] >= acc_lo:
            manip = j
            break
        if c["low"] < acc_lo - manip_max_atr * a:
            break  # too deep = real break, not manipulation
    if manip is None:
        return None
    manip_high = max(ohlc[j]["high"] for j in range(acc_end + 1, manip + 1))
    last = ohlc[-1]
    if last["close"] > manip_high and last["close"] > last["open"] \
            and _is_displacement(ohlc, ctx, n - 1):
        return _sig("BUY", "ict_pa_power_of_three", 0.60, [
            f"Po3/AMD: accumulation {acc_lo:.5f}-{acc_hi:.5f}, manipulation "
            f"sweep to {ohlc[manip]['low']:.5f}",
            "distribution: displacement close above manipulation high",
        ], acc_low=acc_lo, acc_high=acc_hi)
    # bearish mirror
    manip_s = None
    for j in range(acc_end + 1, n - 1):
        c = ohlc[j]
        if c["high"] > acc_hi and (c["high"] - acc_hi) <= manip_max_atr * a \
                and c["close"] <= acc_hi:
            manip_s = j
            break
        if c["high"] > acc_hi + manip_max_atr * a:
            break
    if manip_s is None:
        return None
    manip_low = min(ohlc[j]["low"] for j in range(acc_end + 1, manip_s + 1))
    if last["close"] < manip_low and last["close"] < last["open"] \
            and _is_displacement(ohlc, ctx, n - 1):
        return _sig("SELL", "ict_pa_power_of_three", 0.60, [
            f"Po3/AMD: accumulation {acc_lo:.5f}-{acc_hi:.5f}, manipulation "
            f"raid to {ohlc[manip_s]['high']:.5f}",
            "distribution: displacement close below manipulation low",
        ], acc_low=acc_lo, acc_high=acc_hi)
    return None


# ---------------------------------------------------------------------------
# 15) ICT Silver Bullet (time-window FVG) — needs 'ts'
# ---------------------------------------------------------------------------

def strat_silver_bullet(ohlc, windows=((3, 4), (10, 11), (14, 15))):
    """Inside a Silver Bullet window (03-04, 10-11, 14-15 ET), take the FIRST
    FVG in the window in the direction of the simple bias (close vs SMA50 as
    the single-series proxy for the 1h draw-on-liquidity gate — multi-TF bias
    needs a second frame, out of contract). Enter on the retrace into that
    FVG. Skips without 'ts'. Flag: most fee-sensitive entry in the catalog."""
    if len(ohlc) < MIN_BARS:
        return None
    hours = _et_hours(ohlc)
    if hours is None:
        return None
    n = len(ohlc)
    h_last = hours[-1]
    win = next((w for w in windows if w[0] <= h_last < w[1]), None)
    if win is None:
        return None
    win_start = n - 1
    while win_start > 0 and win[0] <= hours[win_start - 1] < win[1]:
        win_start -= 1
    closes = [c["close"] for c in ohlc]
    if len(closes) >= 50:
        sma50 = sum(closes[-50:]) / 50.0
        bias = "bull" if closes[-1] > sma50 else "bear"
    else:
        bias = "bull" if closes[-1] >= closes[0] else "bear"
    ctx = _ctx(ohlc)
    for f in _find_fvgs(ohlc, ctx):
        if f["i"] < win_start or f["dir"] != bias:
            continue
        last = ohlc[-1]
        if bias == "bull" and f["bottom"] <= last["close"] <= f["top"] \
                and last["low"] <= f["top"]:
            return _sig("BUY", "ict_pa_silver_bullet", 0.60, [
                f"Silver Bullet {win[0]:02d}:00-{win[1]:02d}:00 ET window",
                f"first bullish FVG {f['bottom']:.5f}-{f['top']:.5f} retrace",
            ], zone_bottom=f["bottom"], zone_top=f["top"])
        if bias == "bear" and f["bottom"] <= last["close"] <= f["top"] \
                and last["high"] >= f["bottom"]:
            return _sig("SELL", "ict_pa_silver_bullet", 0.60, [
                f"Silver Bullet {win[0]:02d}:00-{win[1]:02d}:00 ET window",
                f"first bearish FVG {f['bottom']:.5f}-{f['top']:.5f} retrace",
            ], zone_bottom=f["bottom"], zone_top=f["top"])
        break  # only the FIRST in-window FVG counts
    return None


# ---------------------------------------------------------------------------
# 16) Killzone Open-Range Breakout (London/NY) — needs 'ts'
# ---------------------------------------------------------------------------

def strat_killzone_orb(ohlc, killzone=(2, 5), max_range_atr=1.2):
    """Pre-killzone range (the 60 min before 02:00 ET approximated by the
    bars with ET hour in [1,2)); during the killzone take the first close
    above the pre-range high, one-directional day filter (no close beyond the
    opposite side first), and skip already-expanded days (pre-range >
    1.2*ATR). Skips without 'ts'."""
    if len(ohlc) < MIN_BARS:
        return None
    hours = _et_hours(ohlc)
    if hours is None:
        return None
    n = len(ohlc)
    if not (killzone[0] <= hours[-1] < killzone[1]):
        return None
    kz_start = n - 1
    while kz_start > 0 and killzone[0] <= hours[kz_start - 1] < killzone[1]:
        kz_start -= 1
    pre = [i for i in range(max(0, kz_start - 8), kz_start)
           if killzone[0] - 1 <= hours[i] < killzone[0]]
    if not pre:
        return None
    ctx = _ctx(ohlc)
    a = ctx["atrs"][-1]
    if not a:
        return None
    pre_hi = max(ohlc[i]["high"] for i in pre)
    pre_lo = min(ohlc[i]["low"] for i in pre)
    if pre_hi - pre_lo > max_range_atr * a or pre_hi <= pre_lo:
        return None
    kz = range(kz_start, n)
    broke_down = any(ohlc[j]["close"] < pre_lo for j in kz)
    broke_up = any(ohlc[j]["close"] > pre_hi for j in kz)
    last = ohlc[-1]
    if last["close"] > pre_hi and not broke_down and \
            not any(ohlc[j]["close"] > pre_hi for j in range(kz_start, n - 1)):
        return _sig("BUY", "ict_pa_killzone_orb", 0.60, [
            f"Killzone ORB: first close above pre-range high {pre_hi:.5f}",
            f"pre-range {pre_lo:.5f}-{pre_hi:.5f} ({(pre_hi - pre_lo) / a:.2f}x ATR)",
        ], pre_high=pre_hi, pre_low=pre_lo)
    if last["close"] < pre_lo and not broke_up and \
            not any(ohlc[j]["close"] < pre_lo for j in range(kz_start, n - 1)):
        return _sig("SELL", "ict_pa_killzone_orb", 0.60, [
            f"Killzone ORB: first close below pre-range low {pre_lo:.5f}",
            f"pre-range {pre_lo:.5f}-{pre_hi:.5f} ({(pre_hi - pre_lo) / a:.2f}x ATR)",
        ], pre_high=pre_hi, pre_low=pre_lo)
    return None


# ---------------------------------------------------------------------------
# 17) Bullish/Bearish Engulfing at Structure (zone-gated)
# ---------------------------------------------------------------------------

def strat_engulfing_structure(ohlc, body_mult=1.2, trend_sma=50):
    """Engulfing AFTER a decline, AT a demand zone / swing low / discount:
    current candle opens <= prior close and closes >= prior open with body
    >= 1.2x prior body. Raw engulfing is ~coin-flip (Bulkowski); the zone
    gate is the documented profitable variant and is what detect_engulfing
    (strategies.py L306) lacks."""
    if len(ohlc) < MIN_BARS:
        return None
    ctx = _ctx(ohlc)
    n = ctx["n"]
    if n < trend_sma + 2:
        return None
    prev, last = ohlc[n - 2], ohlc[n - 1]
    pb = abs(prev["close"] - prev["open"])
    lb = abs(last["close"] - last["open"])
    if pb <= 0 or lb < body_mult * pb:
        return None
    closes = [c["close"] for c in ohlc]
    sma50 = sum(closes[-trend_sma:]) / trend_sma
    a = ctx["atrs"][-1]
    if not a:
        return None
    lh_ll = (len(ctx["highs"]) >= 2 and len(ctx["lows"]) >= 2
             and ctx["highs"][-1][1] < ctx["highs"][-2][1]
             and ctx["lows"][-1][1] < ctx["lows"][-2][1])
    hh_hl = (len(ctx["highs"]) >= 2 and len(ctx["lows"]) >= 2
             and ctx["highs"][-1][1] > ctx["highs"][-2][1]
             and ctx["lows"][-1][1] > ctx["lows"][-2][1])
    near_swing_low = any(last["low"] <= p + 0.75 * a
                         for i, p in ctx["lows"] if i >= n - 25)
    near_swing_high = any(last["high"] >= p - 0.75 * a
                          for i, p in ctx["highs"] if i >= n - 25)
    gate = premium_discount_gate(ohlc)
    if (last["open"] <= prev["close"] and last["close"] >= prev["open"]
            and last["close"] > last["open"]):
        trend_ok = closes[-1] < sma50 or lh_ll
        zone_ok = near_swing_low or gate == "discount"
        if trend_ok and zone_ok:
            return _sig("BUY", "ict_pa_engulfing", 0.60, [
                "Bullish engulfing after decline (trend gate)",
                "at demand/swing-low/discount zone (zone gate)",
            ])
    if (last["open"] >= prev["close"] and last["close"] <= prev["open"]
            and last["close"] < last["open"]):
        trend_ok = closes[-1] > sma50 or hh_hl
        zone_ok = near_swing_high or gate == "premium"
        if trend_ok and zone_ok:
            return _sig("SELL", "ict_pa_engulfing", 0.60, [
                "Bearish engulfing after rally (trend gate)",
                "at supply/swing-high/premium zone (zone gate)",
            ])
    return None


# ---------------------------------------------------------------------------
# 18) Pin Bar / Hammer / Shooting Star Rejection (level-gated)
# ---------------------------------------------------------------------------

def strat_pin_bar_rejection(ohlc, wick_body_ratio=2.0, nose_max=0.3,
                            min_range_atr=1.0):
    """Hammer at support: lower wick >= 2x body, upper wick <= 0.3x body,
    range >= 1.0*ATR, low testing a swing low. Enter on break of the pin's
    high (higher-quality variant) or on the pin's close if it closes in its
    top 25%. The level requirement is NOT optional per catalog."""
    if len(ohlc) < MIN_BARS:
        return None
    ctx = _ctx(ohlc)
    n = ctx["n"]
    a = ctx["atrs"][-1]
    if not a:
        return None
    for p_idx in (n - 1, n - 2):
        if p_idx < 1:
            continue
        p = ohlc[p_idx]
        body = abs(p["close"] - p["open"])
        rng = p["high"] - p["low"]
        if body <= 0 or rng < min_range_atr * a:
            continue
        lower_wick = min(p["open"], p["close"]) - p["low"]
        upper_wick = p["high"] - max(p["open"], p["close"])
        hammer = (lower_wick >= wick_body_ratio * body
                  and upper_wick <= nose_max * body)
        star = (upper_wick >= wick_body_ratio * body
                and lower_wick <= nose_max * body)
        if hammer:
            at_level = any(p["low"] <= lp + 0.5 * a
                           for i, lp in ctx["lows"] if i < p_idx)
            if not at_level:
                continue
            if p_idx == n - 1 and _close_pos(p) >= 0.75:
                return _sig("BUY", "ict_pa_pin_bar", 0.58, [
                    f"Hammer at swing-low level (wick {lower_wick / body:.1f}x body)",
                    "close in top 25% of pin",
                ])
            if p_idx == n - 2 and ohlc[n - 1]["high"] > p["high"]:
                return _sig("BUY", "ict_pa_pin_bar", 0.58, [
                    f"Hammer at swing-low level (wick {lower_wick / body:.1f}x body)",
                    "entry on break of pin high",
                ])
        if star:
            at_level = any(p["high"] >= hp - 0.5 * a
                           for i, hp in ctx["highs"] if i < p_idx)
            if not at_level:
                continue
            if p_idx == n - 1 and _close_pos(p) <= 0.25:
                return _sig("SELL", "ict_pa_pin_bar", 0.58, [
                    f"Shooting star at swing-high level (wick {upper_wick / body:.1f}x body)",
                    "close in bottom 25% of pin",
                ])
            if p_idx == n - 2 and ohlc[n - 1]["low"] < p["low"]:
                return _sig("SELL", "ict_pa_pin_bar", 0.58, [
                    f"Shooting star at swing-high level (wick {upper_wick / body:.1f}x body)",
                    "entry on break of pin low",
                ])
    return None


# ---------------------------------------------------------------------------
# 19) Doji Reversal at Extremes (Dragonfly/Gravestone only)
# ---------------------------------------------------------------------------

def strat_doji_extreme(ohlc, body_pct=0.1, wick_pct=0.6, nose_pct=0.15,
                       prior_leg_bars=3):
    """Dragonfly at support after >= 3 consecutive bearish closes: body
    <= 10% of range, lower wick >= 60%, upper wick <= 15%; enter on the next
    candle breaking the doji high. Plain cross dojis are excluded on purpose
    (documented ~50%, no edge); the extended-prior-leg gate is mandatory."""
    if len(ohlc) < MIN_BARS:
        return None
    n = len(ohlc)
    d = n - 2
    if d < prior_leg_bars + 1:
        return None
    dj = ohlc[d]
    rng = dj["high"] - dj["low"]
    if rng <= 0:
        return None
    body = abs(dj["close"] - dj["open"])
    lower_wick = min(dj["open"], dj["close"]) - dj["low"]
    upper_wick = dj["high"] - max(dj["open"], dj["close"])
    trig = ohlc[n - 1]
    bears = all(ohlc[d - k]["close"] < ohlc[d - k]["open"]
                for k in range(1, prior_leg_bars + 1))
    bulls = all(ohlc[d - k]["close"] > ohlc[d - k]["open"]
                for k in range(1, prior_leg_bars + 1))
    if (body <= body_pct * rng and lower_wick >= wick_pct * rng
            and upper_wick <= nose_pct * rng and bears
            and trig["high"] > dj["high"]):
        return _sig("BUY", "ict_pa_doji_extreme", 0.55, [
            f"Dragonfly doji after {prior_leg_bars} bearish closes",
            "trigger: break of doji high",
        ])
    if (body <= body_pct * rng and upper_wick >= wick_pct * rng
            and lower_wick <= nose_pct * rng and bulls
            and trig["low"] < dj["low"]):
        return _sig("SELL", "ict_pa_doji_extreme", 0.55, [
            f"Gravestone doji after {prior_leg_bars} bullish closes",
            "trigger: break of doji low",
        ])
    return None


# ---------------------------------------------------------------------------
# 20) Morning Star / Evening Star (crypto no-gap codification)
# ---------------------------------------------------------------------------

def strat_morning_evening_star(ohlc, star_body_pct=0.4, third_close_pct=0.5):
    """Morning star: c1 bearish body >= 1.0x avg body; c2 small body
    (<= 0.4x c1) closing in the lower 25% of c1's range (crypto relaxation of
    the gap requirement — 24/7 markets rarely gap); c3 bullish close >= 50%
    into c1's body -> BUY on c3 close."""
    if len(ohlc) < MIN_BARS:
        return None
    ctx = _ctx(ohlc)
    n = ctx["n"]
    c1, c2, c3 = ohlc[n - 3], ohlc[n - 2], ohlc[n - 1]
    avg_body = ctx["bsma"][n - 3]
    if not avg_body:
        return None
    b1 = abs(c1["close"] - c1["open"])
    b2 = abs(c2["close"] - c2["open"])
    r1 = c1["high"] - c1["low"]
    if b1 < avg_body or r1 <= 0:
        return None
    if (c1["close"] < c1["open"] and b2 <= star_body_pct * b1
            and c2["close"] <= c1["low"] + 0.25 * r1
            and c3["close"] > c3["open"]
            and c3["close"] >= c1["close"] + third_close_pct * b1):
        return _sig("BUY", "ict_pa_morning_star", 0.60, [
            "Morning star: strong c1, stalled c2 (crypto no-gap), "
            "c3 closed >= 50% into c1 body",
        ])
    if (c1["close"] > c1["open"] and b2 <= star_body_pct * b1
            and c2["close"] >= c1["high"] - 0.25 * r1
            and c3["close"] < c3["open"]
            and c3["close"] <= c1["close"] - third_close_pct * b1):
        return _sig("SELL", "ict_pa_morning_star", 0.60, [
            "Evening star: strong c1, stalled c2 (crypto no-gap), "
            "c3 closed >= 50% into c1 body",
        ])
    return None


# ---------------------------------------------------------------------------
# 21) Three White Soldiers / Three Black Crows (extension-filtered)
# ---------------------------------------------------------------------------

def strat_three_soldiers_crows(ohlc, close_pct=0.3, min_pattern_atr=1.5):
    """Three consecutive bullish candles, each closing in its top 30%, each
    opening within the prior body, combined range >= 1.5*ATR, after a decline
    (prior 5-bar return < 0); skip when over-extended (close > SMA20 +
    2*ATR). Catalog: the edge is in the pullback entry; chase-buying candle 3
    in extended conditions has published negative expectancy."""
    if len(ohlc) < MIN_BARS + 10:
        return None
    ctx = _ctx(ohlc)
    n = ctx["n"]
    a = ctx["atrs"][-1]
    if not a:
        return None
    cs = ohlc[n - 3:]
    if any(c["high"] <= c["low"] for c in cs):
        return None
    opens_in_prior = all(
        min(cs[k - 1]["open"], cs[k - 1]["close"]) <= cs[k]["open"]
        <= max(cs[k - 1]["open"], cs[k - 1]["close"]) for k in (1, 2))
    span = max(c["high"] for c in cs) - min(c["low"] for c in cs)
    if not opens_in_prior or span < min_pattern_atr * a:
        return None
    closes = [c["close"] for c in ohlc]
    sma20 = sum(closes[-20:]) / 20.0
    prior_ret = closes[n - 4] - closes[max(0, n - 9)]
    soldiers = (all(c["close"] > c["open"] for c in cs)
                and all(_close_pos(c) >= 1 - close_pct for c in cs)
                and prior_ret < 0 and closes[-1] <= sma20 + 2.0 * a)
    if soldiers:
        return _sig("BUY", "ict_pa_three_soldiers", 0.58, [
            f"Three white soldiers after decline (span {span / a:.1f}x ATR)",
            "extension filter passed (not > SMA20 + 2*ATR)",
        ])
    crows = (all(c["close"] < c["open"] for c in cs)
             and all(_close_pos(c) <= close_pct for c in cs)
             and prior_ret > 0 and closes[-1] >= sma20 - 2.0 * a)
    if crows:
        return _sig("SELL", "ict_pa_three_soldiers", 0.58, [
            f"Three black crows after rally (span {span / a:.1f}x ATR)",
            "extension filter passed (not < SMA20 - 2*ATR)",
        ])
    return None


# ---------------------------------------------------------------------------
# 22) Harami (Inside-Body Reversal, level-gated; doji cross = same function)
# ---------------------------------------------------------------------------

def strat_harami(ohlc, mom_body_mult=1.2, prior_leg_bars=3):
    """After >= 3 bearish closes, c1 is a large bearish body (>= 1.2x avg);
    c2's body sits entirely inside c1's body and is bullish or small (doji
    cross variant included). Enter on the next candle breaking c2's high,
    only at support/discount. Harami targets are modest — early warning."""
    if len(ohlc) < MIN_BARS + prior_leg_bars + 3:
        return None
    ctx = _ctx(ohlc)
    n = ctx["n"]
    a = ctx["atrs"][-1]
    avg_body = ctx["bsma"][n - 3]
    if not a or not avg_body:
        return None
    c1, c2, trig = ohlc[n - 3], ohlc[n - 2], ohlc[n - 1]
    b1 = abs(c1["close"] - c1["open"])
    b2 = abs(c2["close"] - c2["open"])
    near_low = any(trig["low"] <= p + 1.0 * a for i, p in ctx["lows"] if i >= n - 25)
    near_high = any(trig["high"] >= p - 1.0 * a for i, p in ctx["highs"] if i >= n - 25)
    gate = premium_discount_gate(ohlc)
    bears = all(ohlc[n - 4 - k]["close"] < ohlc[n - 4 - k]["open"]
                for k in range(prior_leg_bars))
    bulls = all(ohlc[n - 4 - k]["close"] > ohlc[n - 4 - k]["open"]
                for k in range(prior_leg_bars))
    c2_inside = (min(c1["open"], c1["close"]) <= min(c2["open"], c2["close"])
                 and max(c2["open"], c2["close"]) <= max(c1["open"], c1["close"]))
    c2_small_or_cross = b2 <= 0.3 * b1 or b2 <= 0.1 * (c2["high"] - c2["low"] or 1)
    if (bears and c1["close"] < c1["open"] and b1 >= mom_body_mult * avg_body
            and c2_inside and (c2["close"] > c2["open"] or c2_small_or_cross)
            and trig["high"] > c2["high"] and (near_low or gate == "discount")):
        return _sig("BUY", "ict_pa_harami", 0.55, [
            f"Bullish harami after {prior_leg_bars} bearish closes, at support",
            "trigger: break of inside-bar high",
        ])
    if (bulls and c1["close"] > c1["open"] and b1 >= mom_body_mult * avg_body
            and c2_inside and (c2["close"] < c2["open"] or c2_small_or_cross)
            and trig["low"] < c2["low"] and (near_high or gate == "premium")):
        return _sig("SELL", "ict_pa_harami", 0.55, [
            f"Bearish harami after {prior_leg_bars} bullish closes, at resistance",
            "trigger: break of inside-bar low",
        ])
    return None


# ---------------------------------------------------------------------------
# 23) Tweezer Tops/Bottoms (double rejection at a level)
# ---------------------------------------------------------------------------

def strat_tweezer(ohlc, match_tol_atr=0.1):
    """Tweezer bottom: two consecutive candles with matching lows
    (<= 0.1*ATR) at a swing-low level, c1 bearish and c2 bullish; enter on
    c2's close if it closes in its top 50%. A 2-bar mini equal-lows event —
    mid-air tweezers without a level are random per catalog."""
    if len(ohlc) < MIN_BARS:
        return None
    ctx = _ctx(ohlc)
    n = ctx["n"]
    a = ctx["atrs"][-1]
    if not a:
        return None
    c1, c2 = ohlc[n - 2], ohlc[n - 1]
    if abs(c1["low"] - c2["low"]) <= match_tol_atr * a \
            and c1["close"] < c1["open"] and c2["close"] > c2["open"] \
            and _close_pos(c2) >= 0.5:
        if any(min(c1["low"], c2["low"]) <= p + 0.5 * a
               for i, p in ctx["lows"] if i < n - 2):
            return _sig("BUY", "ict_pa_tweezer", 0.55, [
                f"Tweezer bottom {c2['low']:.5f} at swing-low level",
                "bullish second candle closing in top 50%",
            ])
    if abs(c1["high"] - c2["high"]) <= match_tol_atr * a \
            and c1["close"] > c1["open"] and c2["close"] < c2["open"] \
            and _close_pos(c2) <= 0.5:
        if any(max(c1["high"], c2["high"]) >= p - 0.5 * a
               for i, p in ctx["highs"] if i < n - 2):
            return _sig("SELL", "ict_pa_tweezer", 0.55, [
                f"Tweezer top {c2['high']:.5f} at swing-high level",
                "bearish second candle closing in bottom 50%",
            ])
    return None


# ---------------------------------------------------------------------------
# 24) Inside Bar Breakout (volatility compression, trend-side only)
# ---------------------------------------------------------------------------

def strat_inside_bar_breakout(ohlc, compress_ratio=0.6, min_mother_atr=1.0,
                              trend_sma=50, cancel_bars=3):
    """Mother bar range >= 1.0*ATR, inside bar range <= 0.6x mother, fully
    inside; in an uptrend (close > SMA50) enter on the break of the mother
    high within cancel_bars. Counter-trend breaks are ~random per catalog —
    the trend-side-only requirement is the documented fix."""
    if len(ohlc) < MIN_BARS + trend_sma:
        return None
    ctx = _ctx(ohlc)
    n = ctx["n"]
    a = ctx["atrs"][-1]
    if not a:
        return None
    closes = [c["close"] for c in ohlc]
    sma50 = sum(closes[-trend_sma:]) / trend_sma
    last = ohlc[-1]
    for j in range(n - 2, max(1, n - 2 - cancel_bars), -1):
        mother, inside = ohlc[j - 1], ohlc[j]
        m_rng = mother["high"] - mother["low"]
        i_rng = inside["high"] - inside["low"]
        if m_rng < min_mother_atr * a:
            continue
        if not (inside["high"] < mother["high"] and inside["low"] > mother["low"]
                and i_rng <= compress_ratio * m_rng):
            continue
        if closes[-1] > sma50 and last["close"] > mother["high"] \
                and not any(ohlc[q]["close"] > mother["high"]
                            for q in range(j + 1, n - 1)):
            return _sig("BUY", "ict_pa_inside_bar", 0.58, [
                f"Inside-bar compression ({i_rng / m_rng:.2f}x mother) in uptrend",
                f"break of mother high {mother['high']:.5f}",
            ])
        if closes[-1] < sma50 and last["close"] < mother["low"] \
                and not any(ohlc[q]["close"] < mother["low"]
                            for q in range(j + 1, n - 1)):
            return _sig("SELL", "ict_pa_inside_bar", 0.58, [
                f"Inside-bar compression ({i_rng / m_rng:.2f}x mother) in downtrend",
                f"break of mother low {mother['low']:.5f}",
            ])
    return None


# ---------------------------------------------------------------------------
# 25) S/R Flip (polarity principle: broken level, first retest holds)
# ---------------------------------------------------------------------------

def strat_sr_flip(ohlc, min_touches=2, touch_tol_atr=0.3, max_retest_bars=30):
    """A resistance level tested >= 2x (swing highs clustered within
    0.3*ATR) broken by a DISPLACEMENT close; enter long on the FIRST retest
    from above that holds. Oldest principle in the catalog (Edwards & Magee
    polarity); retests > 30 bars after the break lose polarity meaning."""
    if len(ohlc) < MIN_BARS:
        return None
    ctx = _ctx(ohlc)
    n = ctx["n"]
    a = ctx["atrs"][-1]
    if not a:
        return None
    last = ohlc[-1]

    def clusters(points):
        out = []
        used = set()
        for x in range(len(points)):
            if x in used:
                continue
            grp = [points[x]]
            for y in range(x + 1, len(points)):
                if abs(points[y][1] - points[x][1]) <= touch_tol_atr * a:
                    grp.append(points[y])
                    used.add(y)
            if len(grp) >= min_touches:
                lvl = sum(p for _, p in grp) / len(grp)
                out.append((max(i for i, _ in grp), lvl))
        return out

    for lvl_idx, lvl in clusters(ctx["highs"]):
        for b in range(n - 2, max(lvl_idx, n - 2 - max_retest_bars), -1):
            if not _is_displacement(ohlc, ctx, b):
                continue
            if not (ohlc[b]["close"] > lvl and ohlc[b - 1]["close"] <= lvl):
                continue
            if any(ohlc[q]["low"] <= lvl for q in range(b + 1, n - 1)):
                break  # already retested
            if last["low"] <= lvl and last["close"] > lvl:
                return _sig("BUY", "ict_pa_sr_flip", 0.62, [
                    f"S/R flip: resistance {lvl:.5f} ({min_touches}+ touches) "
                    "broken by displacement",
                    "first retest from above holds",
                ], level=lvl)
    for lvl_idx, lvl in clusters(ctx["lows"]):
        for b in range(n - 2, max(lvl_idx, n - 2 - max_retest_bars), -1):
            if not _is_displacement(ohlc, ctx, b):
                continue
            if not (ohlc[b]["close"] < lvl and ohlc[b - 1]["close"] >= lvl):
                continue
            if any(ohlc[q]["high"] >= lvl for q in range(b + 1, n - 1)):
                break
            if last["high"] >= lvl and last["close"] < lvl:
                return _sig("SELL", "ict_pa_sr_flip", 0.62, [
                    f"S/R flip: support {lvl:.5f} ({min_touches}+ touches) "
                    "broken by displacement",
                    "first retest from below holds",
                ], level=lvl)
    return None


# ---------------------------------------------------------------------------
# 26) Supply/Demand Zone First Retest (RBR/DBD, Seiden framework)
# ---------------------------------------------------------------------------

def strat_supply_demand_zone(ohlc, base_max_atr=0.8, leg_min_atr=1.5,
                             max_zone_age_bars=100):
    """Demand zone = tight base (>= 2 consecutive candles spanning
    <= 0.8*ATR) followed by a >= 1.5*ATR rally leg WITH displacement
    (rally-base-rally). Enter at the FIRST untested return to the zone.
    Freshness (first retest only) and departure strength (displacement) are
    the two documented edge carriers."""
    if len(ohlc) < MIN_BARS:
        return None
    ctx = _ctx(ohlc)
    n = ctx["n"]
    last = ohlc[-1]
    start = max(0, n - max_zone_age_bars)
    for b in range(n - 6, start, -1):
        a = ctx["atrs"][b]
        if not a:
            continue
        base_lo = min(ohlc[b]["low"], ohlc[b + 1]["low"])
        base_hi = max(ohlc[b]["high"], ohlc[b + 1]["high"])
        if base_hi - base_lo > base_max_atr * a:
            continue
        # rally-base-rally (demand): rally leg of >= leg_min_atr*ATR with
        # displacement within the next 10 bars
        leg_peak = None
        for j in range(b + 2, min(b + 12, n - 1)):
            if ohlc[j]["high"] >= base_hi + leg_min_atr * a and any(
                    _is_displacement(ohlc, ctx, q) for q in range(b + 2, j + 1)):
                leg_peak = j
        if leg_peak is not None \
                and not any(ohlc[q]["low"] <= base_hi
                            for q in range(leg_peak + 1, n - 1)) \
                and last["low"] <= base_hi and last["close"] >= base_lo:
            return _sig("BUY", "ict_pa_supply_demand", 0.60, [
                f"RBR demand zone {base_lo:.5f}-{base_hi:.5f}, "
                f"departure >= {leg_min_atr}x ATR with displacement",
                "first untested return to zone",
            ], zone_bottom=base_lo, zone_top=base_hi)
        # drop-base-drop (supply) mirror — checked independently per base
        leg_trough = None
        for j in range(b + 2, min(b + 12, n - 1)):
            if ohlc[j]["low"] <= base_lo - leg_min_atr * a and any(
                    _is_displacement(ohlc, ctx, q) for q in range(b + 2, j + 1)):
                leg_trough = j
        if leg_trough is not None \
                and not any(ohlc[q]["high"] >= base_lo
                            for q in range(leg_trough + 1, n - 1)) \
                and last["high"] >= base_lo and last["close"] <= base_hi:
            return _sig("SELL", "ict_pa_supply_demand", 0.60, [
                f"DBD supply zone {base_lo:.5f}-{base_hi:.5f}, "
                f"departure >= {leg_min_atr}x ATR with displacement",
                "first untested return to zone",
            ], zone_bottom=base_lo, zone_top=base_hi)
    return None


# ---------------------------------------------------------------------------
# Registry + family scan (per-tag signals; attribution preserved)
# ---------------------------------------------------------------------------
# NOTE: SMT Divergence (catalog entry) is deliberately absent — it needs
# synchronized multi-symbol frames, which the single-series contract cannot
# express (see module docstring).

ICT_SMC_PRICEACTION_STRATEGIES = [
    ("ict_pa_fvg_retrace", strat_fvg_retrace),
    ("ict_pa_ifvg", strat_ifvg),
    ("ict_pa_order_block", strat_order_block_retest),
    ("ict_pa_breaker", strat_breaker_block),
    ("ict_pa_mitigation", strat_mitigation_block),
    ("ict_pa_liquidity_sweep", strat_liquidity_sweep_reversal),
    ("ict_pa_turtle_soup", strat_turtle_soup),
    ("ict_pa_judas_swing", strat_judas_swing),
    ("ict_pa_bos_pullback", strat_bos_pullback),
    ("ict_pa_choch_reversal", strat_choch_reversal),
    ("ict_pa_ote", strat_ote),
    ("ict_pa_premium_discount", strat_premium_discount),
    ("ict_pa_unicorn", strat_unicorn),
    ("ict_pa_power_of_three", strat_power_of_three),
    ("ict_pa_silver_bullet", strat_silver_bullet),
    ("ict_pa_killzone_orb", strat_killzone_orb),
    ("ict_pa_engulfing", strat_engulfing_structure),
    ("ict_pa_pin_bar", strat_pin_bar_rejection),
    ("ict_pa_doji_extreme", strat_doji_extreme),
    ("ict_pa_morning_star", strat_morning_evening_star),
    ("ict_pa_three_soldiers", strat_three_soldiers_crows),
    ("ict_pa_harami", strat_harami),
    ("ict_pa_tweezer", strat_tweezer),
    ("ict_pa_inside_bar", strat_inside_bar_breakout),
    ("ict_pa_sr_flip", strat_sr_flip),
    ("ict_pa_supply_demand", strat_supply_demand_zone),
]

# Backwards-friendly alias
STRATEGIES = ICT_SMC_PRICEACTION_STRATEGIES


def scan_ict_smc_priceaction(ohlc):
    """Run the whole family on one symbol's OHLC list. Returns a LIST of
    per-tag signal dicts (not combined — the learning loop needs each
    strategy's stats attributable, per the per-tag pattern in scalp15/swing).
    Individual strategy failures are isolated like scan_symbol does."""
    import logging
    log = logging.getLogger("strats.ict_smc_priceaction")
    signals = []
    for tag, fn in ICT_SMC_PRICEACTION_STRATEGIES:
        try:
            sig = fn(ohlc)
            if sig:
                sig.setdefault("strategy", tag)
                signals.append(sig)
        except Exception as e:
            log.warning("Strategy %s: %s", tag, e)
    return signals

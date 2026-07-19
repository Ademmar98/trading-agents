"""Quant/Statistical & Forex-Specific strategy family (per-tag signals).

Implements the OHLC/OHLCV-compatible, single-symbol entries from
research/quant_forex_specific.md (30-entry catalog) under the existing
signal contract: each function takes an OHLC bar list (dicts with
open/high/low/close/volume/ts; last bar CLOSED) and returns
{"action", "confidence", "reasons", "strategy", ...} or None, exactly like
core/strategies.py. Every signal dict carries its own unique strategy tag so
per-strategy stats stay attributable (same per-tag pattern as core/swing.py
and core/scalp15.py). SELL means "exit long / stay flat" on the spot-only
paper broker (catalog global adaptation note #1).

Implemented (catalog # -> tag):
  #4  TSMOM classic 12-month          -> Quant - TSMOM Classic
  #5  Crypto TSMOM multi-lookback     -> Quant - TSMOM Ensemble
  #10 OU mean reversion (half-life)   -> Quant - OU Mean Reversion
  #14 Classic range grid              -> Quant - Range Grid
  #15 Infinity grid (vol harvest)     -> Quant - Infinity Grid
  #18 Day-of-week / weekend effect    -> Quant - Weekend Drift
  #19 Turn-of-month effect            -> Quant - Turn of Month
  #20 Intraday time-of-day            -> Quant - Time of Day
  #21 Tokyo range / London breakout   -> Quant - London Breakout
  #22 London-NY overlap ORB           -> Quant - Overlap ORB
  #23 NY close / fix mean reversion   -> Quant - NY Close Reversion
  #25 Post-event drift (OHLC proxy)   -> Quant - Post-Event Drift
  #26 ADX regime switching (meta)     -> Quant - ADX Regime Switch
  #27 Volatility percentile regime    -> Quant - Vol Percentile Regime
  #28 Hurst exponent regime filter    -> Quant - Hurst Regime

Skipped catalog entries (exact blocker):
  #1  FX Carry Trade (single pair)  - needs central-bank policy-rate feed
      (data_needs: OHLC + rates); forex-native, no crypto spot adaptation.
  #2  FX Carry Basket (G10 rank)    - multi-symbol + rate/forward-point feed.
  #3  Carry-to-Risk ranking         - multi-symbol + rates feed.
  #6  Cross-Sectional Momentum      - multi-symbol (top-20 universe ranking).
  #7  Dual Momentum (Antonacci)     - multi-symbol (relative ranking step).
  #8  Cointegration Pairs (Engle-Granger) - multi-symbol (two legs).
  #9  Kalman Filter Pairs           - multi-symbol (two legs).
  #11 Funding-Rate Arbitrage        - needs perp simulation + funding feed
      (infra dependency flagged in catalog notes).
  #12 Triangular Arbitrage          - CONCEPT ONLY: needs L2 orderbook, tick
      data, atomic multi-leg execution; cannot be evaluated on OHLC bars.
  #13 Spot-Futures Basis Convergence - needs dated-futures prices
      (multi-symbol + futures simulation).
  #16 DCA Periodic Accumulation     - NOT A TRADING EDGE; catalog marks it
      benchmark-only / do-not-build as a signal.
  #17 Martingale Sizing             - DANGEROUS, DO NOT IMPLEMENT (gambler's
      ruin; catalog excludes it from the implementation backlog).
  #24 Event-Driven Volatility Straddle - CONCEPT ONLY: needs economic/event
      calendar feed + event-window slippage model (not in stack).

Overlays (NOT signals — noted per assignment, not in STRATEGIES):
  #29 Volatility Targeting  - sizing transform (position = base x
      target_vol / realized_vol, capped). Provided here as the helper
      vol_target_multiplier() so any strategy's signal can be sized through
      it for the equal-risk paper-cycle comparison (catalog note #3).
  #30 Risk Parity / ERC     - portfolio-level capital allocation across
      strategies/assets; needs per-strategy or multi-symbol return series,
      so it cannot live in a single-symbol signal function. Deferred to the
      portfolio layer for the post-cycle "keep the winners" phase.

Catalog pre-deployment mandates intentionally left to the operator (stated
in the catalog, not encodable in a stateless signal): #18/#20 require
trailing-180d re-estimation with Bonferroni multiple-testing control before
enabling; #22 should cross-reference an economic calendar on 12:30 UTC US
release days; #23 must avoid month-end fix days (encoded as a skip).
Stateful catalog features simplified to stateless per-bar form (noted per
function): grid re-arm, ORB "first trigger only", regime hysteresis
persistence, and Hurst 2-estimate confirmation.
"""
import calendar
import math
from datetime import datetime, timezone
from statistics import pstdev

from core.indicators import sma, ema_all, rsi, atr

MIN_BARS = 60


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _closes(ohlc):
    return [b["close"] for b in ohlc]


def _highs(ohlc):
    return [b["high"] for b in ohlc]


def _lows(ohlc):
    return [b["low"] for b in ohlc]


def _bar_dt(bar):
    """Defensive bar timestamp -> aware UTC datetime, or None.

    Primary source is the numeric 'ts' field (unix seconds; tolerates ms);
    falls back to parsing the ISO 'date' field. Seasonality/session
    strategies stay silent when neither exists.
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


def _log_returns(closes):
    return [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))
            if closes[i - 1] > 0 and closes[i] > 0]


def _zscore(values, window):
    if len(values) < window:
        return None
    chunk = values[-window:]
    mu = sum(chunk) / window
    sd = pstdev(chunk)
    if sd == 0:
        return None
    return (values[-1] - mu) / sd


def _adx(ohlc, period=14):
    """Wilder's ADX, fully smoothed (strategies.py's _adx_single is a
    single-DX approximation; regime gates need the real series)."""
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
    for a, p, n in zip(atr_s, pdm_s, ndm_s):
        if a == 0:
            dxs.append(0.0)
            continue
        di_p, di_n = p / a * 100, n / a * 100
        denom = di_p + di_n
        dxs.append(abs(di_p - di_n) / denom * 100 if denom else 0.0)
    if len(dxs) < period:
        return None
    adx = sum(dxs[:period]) / period
    for d in dxs[period:]:
        adx = (adx * (period - 1) + d) / period
    return adx


def _donchian(ohlc, period):
    """(highest high, lowest low) of the `period` bars BEFORE the last bar."""
    if len(ohlc) < period + 1:
        return None, None
    window = ohlc[-period - 1:-1]
    return max(b["high"] for b in window), min(b["low"] for b in window)


def _sig(action, tag, confidence, price, reasons):
    return {
        "action": action,
        "strategy": tag,
        "confidence": confidence,
        "price": price,
        "reasons": [f"{tag}: {r}" for r in reasons],
    }


# ---------------------------------------------------------------------------
# #4 — Time-Series Momentum, classic 12-month (Moskowitz/Ooi/Pedersen 2012)
# ---------------------------------------------------------------------------

def tsmom_classic(ohlc, lookback=252):
    """sign(trailing `lookback`-bar total return). Vol-scaling from the
    catalog is a sizing concern, handled by the #29 overlay downstream."""
    tag = "Quant - TSMOM Classic"
    if len(ohlc) <= lookback:
        return None
    closes = _closes(ohlc)
    base = closes[-1 - lookback]
    if base <= 0:
        return None
    r = closes[-1] / base - 1.0
    if r > 0:
        return _sig("BUY", tag, 0.55, closes[-1],
                    [f"{lookback}-bar return {r:+.1%} > 0"])
    if r < 0:
        return _sig("SELL", tag, 0.55, closes[-1],
                    [f"{lookback}-bar return {r:+.1%} < 0 (spot: stay flat)"])
    return None


# ---------------------------------------------------------------------------
# #5 — Crypto TSMOM, multi-lookback ensemble (Hurst/Ooi/Pedersen blend)
# ---------------------------------------------------------------------------

def tsmom_ensemble(ohlc, lookbacks=(30, 90, 180)):
    """Ensemble score = mean of sign(trailing return) over the lookbacks.
    Long when score >= +1/3, short/flat-signal when <= -1/3."""
    tag = "Quant - TSMOM Ensemble"
    longest = max(lookbacks)
    if len(ohlc) <= longest:
        return None
    closes = _closes(ohlc)
    signs = []
    for lb in lookbacks:
        base = closes[-1 - lb]
        if base <= 0:
            return None
        r = closes[-1] / base - 1.0
        signs.append(1 if r > 0 else (-1 if r < 0 else 0))
    score = sum(signs) / len(signs)
    if score >= 1 / 3:
        return _sig("BUY", tag, 0.55, closes[-1],
                    [f"ensemble score {score:+.2f} over {lookbacks}"])
    if score <= -1 / 3:
        return _sig("SELL", tag, 0.55, closes[-1],
                    [f"ensemble score {score:+.2f} over {lookbacks} (spot: flat)"])
    return None


# ---------------------------------------------------------------------------
# #10 — Ornstein-Uhlenbeck mean reversion, half-life timed (Avellaneda & Lee)
# ---------------------------------------------------------------------------

def _ou_fit(x):
    """OLS of dx on x_{t-1}: dx = a + b*x + e. Returns
    (a, b, t_stat_b, sigma_eps) or None."""
    n = len(x) - 1
    if n < 10:
        return None
    xs = x[:-1]
    ys = [x[i + 1] - x[i] for i in range(n)]
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((v - mx) ** 2 for v in xs)
    if sxx == 0:
        return None
    sxy = sum((xs[i] - mx) * (ys[i] - my) for i in range(n))
    b = sxy / sxx
    a = my - b * mx
    resid = [ys[i] - (a + b * xs[i]) for i in range(n)]
    s2 = sum(r * r for r in resid) / (n - 2)
    se_b = math.sqrt(s2 / sxx) if s2 > 0 else 0.0
    t_stat = b / se_b if se_b > 0 else 0.0
    return a, b, t_stat, math.sqrt(s2)


def ou_mean_reversion(ohlc, sma_period=200, fit_window=120, entry_mult=1.5,
                      halflife_min=3, halflife_max=60, b_tstat=2.0):
    """Single-asset OU variant: x_t = log(close / SMA(sma_period)); AR(1)
    must show b < 0 with |t| > 2 and half-life in [3, 60] bars. Enter long
    below mu - 1.5*sigma_eq, short/flat above mu + 1.5*sigma_eq."""
    tag = "Quant - OU Mean Reversion"
    if len(ohlc) < sma_period + fit_window:
        return None
    closes = _closes(ohlc)
    x = []
    for i in range(sma_period, len(closes)):
        m = sum(closes[i - sma_period:i]) / sma_period
        if m <= 0 or closes[i] <= 0:
            return None
        x.append(math.log(closes[i] / m))
    fit = _ou_fit(x[-fit_window - 1:-1])  # formation window; current bar is the trade candidate
    if not fit:
        return None
    a, b, t_stat, sigma_eps = fit
    if b >= 0 or abs(t_stat) < b_tstat or not (-1 < b < 0):
        return None
    half_life = -math.log(2) / math.log(1 + b)
    if not (halflife_min <= half_life <= halflife_max):
        return None
    mu = -a / b
    denom = 1 - (1 + b) ** 2
    if denom <= 0:
        return None
    sigma_eq = sigma_eps / math.sqrt(denom)
    cur = x[-1]
    if cur < mu - entry_mult * sigma_eq:
        return _sig("BUY", tag, 0.6, closes[-1],
                    [f"x {cur:.4f} < mu {mu:.4f} - {entry_mult}*sigma_eq "
                     f"{sigma_eq:.4f} (half-life {half_life:.0f} bars)"])
    if cur > mu + entry_mult * sigma_eq:
        return _sig("SELL", tag, 0.6, closes[-1],
                    [f"x {cur:.4f} > mu {mu:.4f} + {entry_mult}*sigma_eq "
                     f"{sigma_eq:.4f} (spot: flat)"])
    return None


# ---------------------------------------------------------------------------
# #14 — Classic range grid (signal form)
# ---------------------------------------------------------------------------

def range_grid(ohlc, channel=30, grids=20, width_avg=90, width_filter=1.5,
               breakout_atr_mult=2.0):
    """Grid-armed signal: 30-bar Donchian range valid (width <= 1.5x its
    90-bar average, price not broken out by > 2xATR). BUY in the lower half
    of the range (grid bid zone), SELL above mid (paired grid offers).
    Stateless proxy: per-fill pairing and breakout re-arm are executor
    concerns, noted in the module docstring."""
    tag = "Quant - Range Grid"
    if len(ohlc) < channel + width_avg:
        return None
    highs, lows, closes = _highs(ohlc), _lows(ohlc), _closes(ohlc)
    p_high = max(highs[-channel - 1:-1])
    p_low = min(lows[-channel - 1:-1])
    width = p_high - p_low
    if width <= 0:
        return None
    widths = []
    for end in range(len(ohlc) - width_avg, len(ohlc) - 1):
        w = max(highs[end - channel:end]) - min(lows[end - channel:end])
        widths.append(w)
    avg_width = sum(widths) / len(widths) if widths else width
    if avg_width > 0 and width > width_filter * avg_width:
        return None  # range too wide = trending, grid disabled
    atr_v = atr(highs, lows, closes)
    price = closes[-1]
    if atr_v and (price > p_high + breakout_atr_mult * atr_v or
                  price < p_low - breakout_atr_mult * atr_v):
        return None  # global stop: price left the range, grid killed
    mid = (p_high + p_low) / 2
    step = width / grids
    if p_low <= price < mid:
        return _sig("BUY", tag, 0.5, price,
                    [f"price in grid bid zone ({grids} x {step:.5f} steps "
                     f"above range low {p_low:.5f})"])
    if mid < price <= p_high:
        return _sig("SELL", tag, 0.5, price,
                    [f"price in grid offer zone below range high {p_high:.5f}"])
    return None


# ---------------------------------------------------------------------------
# #15 — Infinity grid, long-biased volatility harvesting (signal form)
# ---------------------------------------------------------------------------

def infinity_grid(ohlc, step_pct=0.01, vol_lookback=20, vol_ref=100,
                  vol_pause_mult=2.0):
    """Percent-step harvester, stateless proxy for the anchored grid: BUY
    when the last bar dropped >= step_pct (crossed a grid step down), SELL
    when it rose >= step_pct (harvest a step up). Circuit breaker from the
    catalog: pause buying when short realized vol > vol_pause_mult x its own
    reference median (crash regime — no catching falling knives)."""
    tag = "Quant - Infinity Grid"
    if len(ohlc) < vol_ref + vol_lookback + 2:
        return None
    closes = _closes(ohlc)
    rets = _log_returns(closes)
    vols = [pstdev(rets[i - vol_lookback:i])
            for i in range(vol_lookback, len(rets) + 1)]
    cur_vol = vols[-1]
    ref = sorted(vols[-vol_ref:])[len(vols[-vol_ref:]) // 2] if vols else 0
    last_ret = closes[-1] / closes[-2] - 1.0 if closes[-2] > 0 else 0.0
    crash_regime = ref > 0 and cur_vol > vol_pause_mult * ref
    if last_ret <= -step_pct:
        if crash_regime:
            return None  # vol circuit breaker: buying paused
        return _sig("BUY", tag, 0.5, closes[-1],
                    [f"bar return {last_ret:+.2%} crossed -{step_pct:.1%} grid step"])
    if last_ret >= step_pct:
        return _sig("SELL", tag, 0.5, closes[-1],
                    [f"bar return {last_ret:+.2%} crossed +{step_pct:.1%} grid step "
                     f"(harvest trim)"])
    return None


# ---------------------------------------------------------------------------
# #18 — Day-of-week / weekend effect (codified Fri 20:00 -> Sun 20:00 UTC)
# ---------------------------------------------------------------------------

def weekend_drift(ohlc, entry_weekday=4, entry_hour=20,
                  exit_weekday=6, exit_hour=20):
    """Weekend drift: BUY Friday 20:00 UTC, exit Sunday 20:00 UTC. Daily
    bars (midnight timestamps) trigger on the matching weekday. The
    catalog's trailing-180d re-estimation with Bonferroni control is a
    pre-enable mandate (module docstring), not a per-bar check."""
    tag = "Quant - Weekend Drift"
    dt = _bar_dt(ohlc[-1])
    if dt is None:
        return None
    midnight = dt.hour == 0 and dt.minute == 0  # daily-bar tolerance
    if dt.weekday() == entry_weekday and (dt.hour >= entry_hour or midnight):
        return _sig("BUY", tag, 0.5, ohlc[-1]["close"],
                    [f"weekend window open ({dt.strftime('%a %H:%M')} UTC)"])
    if dt.weekday() == exit_weekday and (dt.hour >= exit_hour or midnight):
        return _sig("SELL", tag, 0.5, ohlc[-1]["close"],
                    [f"weekend window close ({dt.strftime('%a %H:%M')} UTC)"])
    return None


# ---------------------------------------------------------------------------
# #19 — Turn-of-month effect (Lakonishok & Smidt; McConnell & Xu)
# ---------------------------------------------------------------------------

def turn_of_month(ohlc, hold_days=3):
    """Enter on the last calendar day of the month (00:00 UTC bar), hold
    through day `hold_days` of the new month (time-based exit downstream)."""
    tag = "Quant - Turn of Month"
    dt = _bar_dt(ohlc[-1])
    if dt is None:
        return None
    last_day = calendar.monthrange(dt.year, dt.month)[1]
    if dt.day == last_day and dt.hour == 0 and dt.minute == 0:
        return _sig("BUY", tag, 0.5, ohlc[-1]["close"],
                    [f"last calendar day of {dt.strftime('%Y-%m')}, "
                     f"hold {hold_days} days"])
    return None


# ---------------------------------------------------------------------------
# #20 — Intraday time-of-day seasonality (default 13:00-21:00 UTC block)
# ---------------------------------------------------------------------------

def time_of_day(ohlc, start_hour=13, end_hour=21):
    """Long the documented US-hours return block: BUY at the `start_hour`
    bar, flat at the `end_hour` bar. The catalog's top-quartile hour-block
    re-estimation on owner data is a pre-enable mandate (module docstring)."""
    tag = "Quant - Time of Day"
    dt = _bar_dt(ohlc[-1])
    if dt is None:
        return None
    if dt.hour == start_hour:
        return _sig("BUY", tag, 0.5, ohlc[-1]["close"],
                    [f"session block {start_hour:02d}:00-{end_hour:02d}:00 UTC open"])
    if dt.hour == end_hour:
        return _sig("SELL", tag, 0.5, ohlc[-1]["close"],
                    [f"session block {start_hour:02d}:00-{end_hour:02d}:00 UTC close"])
    return None


# ---------------------------------------------------------------------------
# intraday session helpers (#21-#23): group bars by UTC day
# ---------------------------------------------------------------------------

def _bars_by_day(ohlc):
    """[(date, [bars...])] in order; None if any bar lacks a usable ts."""
    days = []
    for b in ohlc:
        dt = _bar_dt(b)
        if dt is None:
            return None
        if days and days[-1][0] == dt.date():
            days[-1][1].append((dt, b))
        else:
            days.append((dt.date(), [(dt, b)]))
    return days


# ---------------------------------------------------------------------------
# #21 — Tokyo range / London breakout (canonical filter set)
# ---------------------------------------------------------------------------

def tokyo_london_breakout(ohlc, asian_end_hour=7, buffer_atr_mult=0.1,
                          range_filter=1.2, adx_max=30, flat_hour=16,
                          avg_days=20):
    """Asian range = 00:00-07:00 UTC high/low of the current day. After
    07:00, BUY on a bar close above range high + 0.1xATR(14); mirror SELL
    below range low. Filters: range height <= 1.2x its 20-day average and
    prior-day ADX(14) <= 30. One-trade-per-day and the 16:00 UTC time exit
    are executor concerns (stateless note in module docstring)."""
    tag = "Quant - London Breakout"
    days = _bars_by_day(ohlc)
    if not days or len(days) < 3:
        return None
    today = days[-1][1]
    dt_now, last = today[-1]
    if not (asian_end_hour <= dt_now.hour < flat_hour):
        return None
    asian = [b for dt, b in today if dt.hour < asian_end_hour]
    if not asian:
        return None
    r_high = max(b["high"] for b in asian)
    r_low = min(b["low"] for b in asian)
    height = r_high - r_low
    if height <= 0:
        return None
    prior_heights = []
    for _, bars in days[-avg_days - 1:-1]:
        a = [b for dt, b in bars if dt.hour < asian_end_hour]
        if a:
            prior_heights.append(max(b["high"] for b in a) - min(b["low"] for b in a))
    avg_height = sum(prior_heights) / len(prior_heights) if prior_heights else height
    if avg_height > 0 and height > range_filter * avg_height:
        return None  # Asian session already moved — skip the day
    prior_bars = [b for _, bars in days[:-1] for _, b in bars]
    adx_v = _adx(prior_bars) if len(prior_bars) >= 30 else None
    if adx_v is not None and adx_v > adx_max:
        return None  # trending regime — breakout continuation unreliable
    highs, lows, closes = _highs(ohlc), _lows(ohlc), _closes(ohlc)
    atr_v = atr(highs, lows, closes)
    if not atr_v:
        return None
    buffer = buffer_atr_mult * atr_v
    price = closes[-1]
    if price > r_high + buffer:
        return _sig("BUY", tag, 0.6, price,
                    [f"close above Asian range high {r_high:.5f} + buffer "
                     f"(range {height:.5f}, ADX {adx_v:.0f})" if adx_v is not None
                     else f"close above Asian range high {r_high:.5f} + buffer"])
    if price < r_low - buffer:
        return _sig("SELL", tag, 0.6, price,
                    [f"close below Asian range low {r_low:.5f} - buffer (spot: flat)"])
    return None


# ---------------------------------------------------------------------------
# #22 — London-NY overlap opening-range breakout (12:00-16:00 UTC)
# ---------------------------------------------------------------------------

def overlap_orb(ohlc, or_start_hour=12, or_minutes=30, buffer_atr_mult=0.1,
                flat_hour=15, flat_minute=45):
    """Opening range = 12:00-12:30 UTC high/low of the current day; trade
    only inside the overlap. BUY on close above OR high + 0.1xATR(14),
    mirror SELL below OR low. Hard-flat 15:45 UTC and news-day skips are
    executor/calendar concerns (module docstring)."""
    tag = "Quant - Overlap ORB"
    days = _bars_by_day(ohlc)
    if not days:
        return None
    today = days[-1][1]
    dt_now, _ = today[-1]
    or_end_minutes = or_start_hour * 60 + or_minutes
    now_minutes = dt_now.hour * 60 + dt_now.minute
    if not (or_end_minutes <= now_minutes < flat_hour * 60 + flat_minute):
        return None
    or_bars = [b for dt, b in today
               if or_start_hour * 60 <= dt.hour * 60 + dt.minute < or_end_minutes]
    if not or_bars:
        return None
    or_high = max(b["high"] for b in or_bars)
    or_low = min(b["low"] for b in or_bars)
    if or_high <= or_low:
        return None
    highs, lows, closes = _highs(ohlc), _lows(ohlc), _closes(ohlc)
    atr_v = atr(highs, lows, closes)
    if not atr_v:
        return None
    buffer = buffer_atr_mult * atr_v
    price = closes[-1]
    if price > or_high + buffer:
        return _sig("BUY", tag, 0.6, price,
                    [f"close above overlap OR high {or_high:.5f} + buffer"])
    if price < or_low - buffer:
        return _sig("SELL", tag, 0.6, price,
                    [f"close below overlap OR low {or_low:.5f} - buffer (spot: flat)"])
    return None


# ---------------------------------------------------------------------------
# #23 — NY close / fix mean reversion (fade the >1.2x daily-ATR close move)
# ---------------------------------------------------------------------------

def ny_close_reversion(ohlc, entry_hour=21, trigger_mult=1.2, atr_days=20,
                       london_hour=7):
    """At the 21:00 UTC bar, fade extreme intraday moves: move from the
    London-open proxy (07:00 UTC bar open) beyond +/-1.2x the 20-day average
    daily true range -> enter for overnight reversion. Month-end days are
    skipped per the catalog (fix flows are directional — do not fade)."""
    tag = "Quant - NY Close Reversion"
    days = _bars_by_day(ohlc)
    if not days or len(days) < atr_days + 1:
        return None
    today = days[-1][1]
    dt_now, _ = today[-1]
    if dt_now.hour != entry_hour:
        return None
    if dt_now.day == calendar.monthrange(dt_now.year, dt_now.month)[1]:
        return None  # month-end fix flows: do not fade
    london_open = None
    for dt, b in today:
        if dt.hour == london_hour:
            london_open = b["open"]
            break
    if london_open is None:
        london_open = today[0][1]["open"]
    daily_trs = []
    prev_close = None
    for _, bars in days[:-1]:
        day_high = max(b["high"] for _, b in bars)
        day_low = min(b["low"] for _, b in bars)
        if prev_close is not None:
            daily_trs.append(max(day_high - day_low,
                                 abs(day_high - prev_close),
                                 abs(day_low - prev_close)))
        prev_close = bars[-1][1]["close"]
    if not daily_trs:
        return None
    avg_tr = sum(daily_trs[-atr_days:]) / min(len(daily_trs), atr_days)
    if avg_tr <= 0:
        return None
    move = _closes(ohlc)[-1] - london_open
    if move < -trigger_mult * avg_tr:
        return _sig("BUY", tag, 0.55, _closes(ohlc)[-1],
                    [f"intraday move {move:.5f} < -{trigger_mult}x avg daily TR "
                     f"{avg_tr:.5f} — fade for overnight reversion"])
    if move > trigger_mult * avg_tr:
        return _sig("SELL", tag, 0.55, _closes(ohlc)[-1],
                    [f"intraday move +{move:.5f} > {trigger_mult}x avg daily TR "
                     f"{avg_tr:.5f} — fade (spot: exit longs)"])
    return None


# ---------------------------------------------------------------------------
# #25 — Post-event drift, OHLC-only proxy (PEAD analog)
# ---------------------------------------------------------------------------

def post_event_drift(ohlc, range_mult=4.0, atr_period=20, decile=0.10):
    """"News day" proxy from the catalog: last bar's true range >
    range_mult x ATR(20) AND close in the top decile of the bar's range ->
    BUY (hold ~3 days, time exit downstream). Bottom decile -> SELL/flat."""
    tag = "Quant - Post-Event Drift"
    if len(ohlc) < atr_period + 2:
        return None
    highs, lows, closes = _highs(ohlc), _lows(ohlc), _closes(ohlc)
    atr_v = atr(highs[:-1], lows[:-1], closes[:-1], period=atr_period)
    if not atr_v or atr_v <= 0:
        return None
    last = ohlc[-1]
    bar_range = last["high"] - last["low"]
    if bar_range <= 0:
        return None
    tr = max(bar_range, abs(last["high"] - closes[-2]),
             abs(last["low"] - closes[-2]))
    if tr <= range_mult * atr_v:
        return None
    location = (last["close"] - last["low"]) / bar_range
    if location >= 1 - decile:
        return _sig("BUY", tag, 0.6, last["close"],
                    [f"surprise range {tr / atr_v:.1f}x ATR, closed top decile "
                     f"({location:.0%}) — positive news day proxy"])
    if location <= decile:
        return _sig("SELL", tag, 0.6, last["close"],
                    [f"surprise range {tr / atr_v:.1f}x ATR, closed bottom decile "
                     f"({location:.0%}) — negative news day proxy (spot: flat)"])
    return None


# ---------------------------------------------------------------------------
# #26 — ADX regime switching meta-strategy (Wilder gate, hysteresis noted)
# ---------------------------------------------------------------------------

def adx_regime_switch(ohlc, adx_period=14, trend_enter=26, mr_thresh=20,
                      donchian=20, rsi_period=14, z_window=20):
    """ADX >= trend_enter -> TREND mode: Donchian(20) breakout entries.
    ADX < mr_thresh -> MEAN-REVERSION mode: RSI(14) < 30 or z-score < -2
    entries (mirror for shorts). Between the bands -> flat. The catalog's
    26-in/23-out hysteresis persistence is stateful; this stateless form
    uses the entry bands directly (module docstring)."""
    tag = "Quant - ADX Regime Switch"
    if len(ohlc) < max(adx_period * 2 + 1, donchian + 1, z_window + 1):
        return None
    adx_v = _adx(ohlc, adx_period)
    if adx_v is None:
        return None
    closes = _closes(ohlc)
    price = closes[-1]
    if adx_v >= trend_enter:
        d_high, d_low = _donchian(ohlc, donchian)
        if d_high is None:
            return None
        if price > d_high:
            return _sig("BUY", tag, 0.6, price,
                        [f"TREND mode (ADX {adx_v:.0f}), close above "
                         f"Donchian({donchian}) high {d_high:.5f}"])
        if price < d_low:
            return _sig("SELL", tag, 0.6, price,
                        [f"TREND mode (ADX {adx_v:.0f}), close below "
                         f"Donchian({donchian}) low {d_low:.5f} (spot: flat)"])
        return None
    if adx_v < mr_thresh:
        rsi_v = rsi(closes, rsi_period)
        z = _zscore(closes, z_window)
        if rsi_v < 30 or (z is not None and z < -2):
            return _sig("BUY", tag, 0.55, price,
                        [f"MR mode (ADX {adx_v:.0f}), RSI {rsi_v:.0f} / "
                         f"z {z if z is not None else 0:+.1f} fade-up"])
        if rsi_v > 70 or (z is not None and z > 2):
            return _sig("SELL", tag, 0.55, price,
                        [f"MR mode (ADX {adx_v:.0f}), RSI {rsi_v:.0f} / "
                         f"z {z if z is not None else 0:+.1f} fade-down (spot: flat)"])
    return None


# ---------------------------------------------------------------------------
# #27 — Volatility percentile regime switching
# ---------------------------------------------------------------------------

def vol_percentile_regime(ohlc, vol_lookback=20, pct_window=252,
                          compression_pct=20.0, crisis_pct=80.0, donchian=20):
    """20-bar realized vol percentile vs the trailing `pct_window`. Below
    the 20th percentile -> COMPRESSION mode: Donchian(20) breakout entries
    (vol expansion expected). 20-80 -> NORMAL: this meta stays silent and
    lets the default strategy set run. Above 80 -> CRISIS: no new entries."""
    tag = "Quant - Vol Percentile Regime"
    if len(ohlc) < pct_window + vol_lookback + 2:
        return None
    closes = _closes(ohlc)
    rets = _log_returns(closes)
    vols = [pstdev(rets[i - vol_lookback:i])
            for i in range(vol_lookback, len(rets) + 1)]
    window = vols[-pct_window:]
    cur = vols[-1]
    pct = sum(1 for v in window if v < cur) / len(window) * 100
    if pct >= crisis_pct or pct > compression_pct:
        return None  # CRISIS: no new entries; NORMAL: defer to default set
    d_high, d_low = _donchian(ohlc, donchian)
    if d_high is None:
        return None
    price = closes[-1]
    if price > d_high:
        return _sig("BUY", tag, 0.6, price,
                    [f"COMPRESSION mode (vol pct {pct:.0f}), close above "
                     f"Donchian({donchian}) high {d_high:.5f}"])
    if price < d_low:
        return _sig("SELL", tag, 0.6, price,
                    [f"COMPRESSION mode (vol pct {pct:.0f}), close below "
                     f"Donchian({donchian}) low {d_low:.5f} (spot: flat)"])
    return None


# ---------------------------------------------------------------------------
# #28 — Hurst exponent regime filter (experimental heuristic)
# ---------------------------------------------------------------------------

def _hurst(closes, lags=(2, 4, 8, 16)):
    """Variance-of-lags Hurst estimate on log prices: Var(x_{t+lag} - x_t)
    scales as lag^(2H). Research-grade DFA/wavelet estimators are preferred
    by the catalog; this is the dependency-free fallback (honest status:
    promising heuristic, paper-cycle experiment)."""
    x = [math.log(c) for c in closes if c > 0]
    if len(x) < max(lags) + 20:
        return None
    pts = []
    for lag in lags:
        diffs = [x[i] - x[i - lag] for i in range(lag, len(x))]
        if len(diffs) < 10:
            return None
        var = sum((d - sum(diffs) / len(diffs)) ** 2 for d in diffs) / len(diffs)
        if var <= 0:
            return None
        pts.append((math.log(lag), math.log(var)))
    mx = sum(p[0] for p in pts) / len(pts)
    my = sum(p[1] for p in pts) / len(pts)
    sxx = sum((p[0] - mx) ** 2 for p in pts)
    if sxx == 0:
        return None
    slope = sum((p[0] - mx) * (p[1] - my) for p in pts) / sxx
    return slope / 2.0


def hurst_regime(ohlc, window=100, trend_min=0.55, mr_max=0.45, donchian=20,
                 z_window=20):
    """H over the last `window` bars: > 0.55 -> trend module (Donchian
    breakout), < 0.45 -> mean-reversion module (z-score fade vs SMA(20)),
    0.45-0.55 -> near-random-walk: flat. The catalog's 2-consecutive-
    estimate confirmation is stateful and noted in the module docstring."""
    tag = "Quant - Hurst Regime"
    if len(ohlc) < window:
        return None
    closes = _closes(ohlc)
    h = _hurst(closes[-window:])
    if h is None:
        return None
    price = closes[-1]
    if h > trend_min:
        d_high, d_low = _donchian(ohlc, donchian)
        if d_high is None:
            return None
        if price > d_high:
            return _sig("BUY", tag, 0.55, price,
                        [f"persistent regime (H {h:.2f}), Donchian({donchian}) breakup"])
        if price < d_low:
            return _sig("SELL", tag, 0.55, price,
                        [f"persistent regime (H {h:.2f}), Donchian({donchian}) "
                         f"breakdown (spot: flat)"])
        return None
    if h < mr_max:
        mu = sma(closes, z_window)
        z = _zscore(closes, z_window)
        if z is not None and z < -2:
            return _sig("BUY", tag, 0.55, price,
                        [f"anti-persistent regime (H {h:.2f}), z {z:+.1f} vs "
                         f"SMA({z_window}) fade-up"])
        if z is not None and z > 2:
            return _sig("SELL", tag, 0.55, price,
                        [f"anti-persistent regime (H {h:.2f}), z {z:+.1f} vs "
                         f"SMA({z_window}) fade-down (spot: flat)"])
    return None


# ---------------------------------------------------------------------------
# #29 — Volatility targeting OVERLAY (not a signal; not in STRATEGIES)
# ---------------------------------------------------------------------------

def vol_target_multiplier(ohlc, target_vol_ann, vol_lookback=20,
                          periods_per_year=365, cap_lo=0.25, cap_hi=2.0):
    """Sizing transform from catalog #29: multiplier = target_vol /
    realized_vol(20d, annualized), capped to [cap_lo, cap_hi]. Apply to any
    strategy's base size so the paper cycle is an equal-risk comparison
    (Moreira & Muir 2017). Returns None when vol cannot be estimated.
    OVERLAY ONLY — emits no entry/exit signals."""
    if len(ohlc) < vol_lookback + 2:
        return None
    rets = _log_returns(_closes(ohlc))
    if len(rets) < vol_lookback:
        return None
    sigma_bar = pstdev(rets[-vol_lookback:])
    sigma_ann = sigma_bar * math.sqrt(periods_per_year)
    if sigma_ann <= 0:
        return None
    mult = target_vol_ann / sigma_ann
    return max(cap_lo, min(cap_hi, mult))


# ---------------------------------------------------------------------------
# registry (same shape as core.strategies.ALL_STRATEGIES)
# ---------------------------------------------------------------------------

STRATEGIES = [
    ("Quant - TSMOM Classic", tsmom_classic),
    ("Quant - TSMOM Ensemble", tsmom_ensemble),
    ("Quant - OU Mean Reversion", ou_mean_reversion),
    ("Quant - Range Grid", range_grid),
    ("Quant - Infinity Grid", infinity_grid),
    ("Quant - Weekend Drift", weekend_drift),
    ("Quant - Turn of Month", turn_of_month),
    ("Quant - Time of Day", time_of_day),
    ("Quant - London Breakout", tokyo_london_breakout),
    ("Quant - Overlap ORB", overlap_orb),
    ("Quant - NY Close Reversion", ny_close_reversion),
    ("Quant - Post-Event Drift", post_event_drift),
    ("Quant - ADX Regime Switch", adx_regime_switch),
    ("Quant - Vol Percentile Regime", vol_percentile_regime),
    ("Quant - Hurst Regime", hurst_regime),
]

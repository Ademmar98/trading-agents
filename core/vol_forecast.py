"""GARCH(1,1) 1-day-ahead volatility forecast → position-size throttle.

Answers "how much", never "which way". Feeds a vol-target throttle that
sizes DOWN when tomorrow's forecast volatility is high and NEVER up (hard cap
1.0x — spot-only / halal). Validated on the firm's own strategy: throttling
cut BTC/ETH max-drawdown 11–16 points and roughly halved worst-month losses
for a small Sharpe gain (research: garch vol-target test, 2026-07).

Pure numpy — no arch/scipy dependency. GARCH(1,1) is fit by variance
targeting (ω pinned to the sample variance) plus a small (α, β) grid search
maximising the Gaussian likelihood; this tracks the reference `arch` fit to
within a couple of percent on crypto majors, which is well inside the
throttle's tolerance. Per-symbol forecasts are cached with a TTL because
daily-bar vol moves slowly and only the few symbols that reach execution are
ever fit.
"""
import time

import numpy as np

_CACHE = {}   # symbol -> (epoch_ts, forecast_vol_ann_pct)

# (α, β) search grid — α+β<1 (stationary); β high = persistent vol (crypto).
_ALPHAS = np.array([0.02, 0.04, 0.06, 0.08, 0.10, 0.13, 0.16, 0.20])
_BETAS = np.array([0.70, 0.75, 0.80, 0.84, 0.88, 0.90, 0.93, 0.96])


def garch11_forecast_daily(returns):
    """1-day-ahead conditional stdev (same units as `returns`) from a
    variance-targeted GARCH(1,1). Returns None if the series is too short or
    degenerate."""
    r = np.asarray(returns, dtype=float)
    r = r[np.isfinite(r)]
    if len(r) < 100:
        return None
    r = r - r.mean()
    uncond = float(np.mean(r ** 2))
    if uncond <= 0:
        return None
    r2 = r ** 2

    best_nll, best = np.inf, None
    for a in _ALPHAS:
        for b in _BETAS:
            if a + b >= 0.999:
                continue
            omega = (1.0 - a - b) * uncond
            s2 = uncond
            nll = 0.0
            for t in range(len(r)):
                # s2 is Var(t) formed from info up to t-1
                if s2 <= 1e-18:
                    nll = np.inf
                    break
                nll += np.log(s2) + r2[t] / s2
                s2 = omega + a * r2[t] + b * s2
            if nll < best_nll:
                best_nll, best = nll, (omega, a, b, s2)
    if best is None:
        return None
    omega, a, b, s2_next = best
    # s2_next is already Var(T+1): ω + α·r²(T) + β·Var(T)
    return float(np.sqrt(max(s2_next, 1e-18)))


def _forecast_from_closes(closes, periods_per_year=365):
    px = np.asarray([c for c in closes if c and c > 0], dtype=float)
    if len(px) < 120:
        return None
    rets = np.diff(px) / px[:-1]
    daily = garch11_forecast_daily(rets)
    if daily is None or not np.isfinite(daily):
        return None
    return float(daily * np.sqrt(periods_per_year) * 100.0)   # annualized %


def forecast_vol_ann(symbol, closes=None, ttl=21600, periods_per_year=365,
                     fetch=True):
    """Cached 1-day-ahead annualized vol forecast (%) for `symbol`.

    Pass `closes` to forecast from a series in hand; otherwise the daily
    series is fetched (once per TTL) via the firm's data provider. Returns
    None when no forecast is possible (caller should then apply no throttle).
    """
    now = time.time()
    hit = _CACHE.get(symbol)
    if hit and now - hit[0] < ttl:
        return hit[1]
    if closes is None and fetch:
        try:
            from core.data_provider import fetch_ohlc
            bars = fetch_ohlc(symbol, interval="1d", limit=520) or []
            closes = [b.get("close") for b in bars]
        except Exception:
            closes = None
    v = _forecast_from_closes(closes or [], periods_per_year) if closes else None
    if v is not None and v > 0:
        _CACHE[symbol] = (now, v)
    return v


def vol_throttle(symbol, target_vol_ann, floor=0.25, closes=None,
                 ttl=21600, periods_per_year=365, fetch=True):
    """Position-size multiplier in [floor, 1.0]. Sizes DOWN in high-vol
    regimes, never up (1.0 cap = no leverage). Fails safe to 1.0 (no
    throttle) when no forecast is available — the firm's other caps still
    bound the trade."""
    if target_vol_ann is None or target_vol_ann <= 0:
        return 1.0
    v = forecast_vol_ann(symbol, closes=closes, ttl=ttl,
                         periods_per_year=periods_per_year, fetch=fetch)
    if not v or v <= 0:
        return 1.0
    return float(np.clip(target_vol_ann / v, floor, 1.0))


def clear_cache():
    _CACHE.clear()

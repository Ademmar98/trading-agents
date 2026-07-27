"""
core/vol_sizing.py — Module 4: Dynamic Volatility-Adjusted Position Sizing
==========================================================================
Replaces static / linear position sizing with an ATR + realized-volatility
model that CONTRACTS allocation *exponentially* as volatility rises above a
calm baseline, backed by hard circuit breakers.

Halal / spot invariants (enforced structurally):
  * The volatility multiplier is bounded to (floor, 1.0] — it can only ever
    SHRINK a position, never lever it up. No shorting, no margin.
  * Sizing is risk-based (constant 1R) so dollar risk is consistent across
    trades regardless of stop width — the exact discipline the 2026-07-23
    meta-analysis showed was missing (positive R, negative dollars).

Why exponential, not linear
---------------------------
A linear throttle (size ∝ target/vol) de-risks too slowly in a genuine vol
spike — the regime where single trades do the most damage. Exponential decay
collapses size fast once vol clears the baseline, then flattens, so ordinary
noise barely moves size but a 2–3× vol blow-out cuts it hard.

Core math
---------
    base_qty  = (equity · risk_frac) / |entry − stop|          # constant-1R
    vol_ratio = current_vol / baseline_vol                      # 1.0 == calm
    size_mult = clip( exp(−k · max(0, vol_ratio − 1)), floor, 1.0 )
    final_qty = min( base_qty · size_mult , equity · max_notional_frac / entry )

Parameters
----------
    risk_frac ........ equity fraction risked per trade at baseline vol (e.g. 0.01 = 1 %)
    k ................ contraction steepness; higher ⇒ shrink faster above baseline
    floor ............ minimum vol multiplier (never size to zero on vol alone)
    baseline_vol ..... "normal" volatility (e.g. 30-period average ATR%)
    max_notional_frac  hard per-position notional cap as a fraction of equity

Dependencies: numpy (+ pandas only for the indicator helpers). The sizing and
breaker logic are pure Python so every rule is trivially unit-testable.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional, Sequence, Tuple

import numpy as np

try:                                    # pandas only for the OHLC indicator helpers
    import pandas as pd
    _HAVE_PD = True
except Exception:                       # pragma: no cover
    _HAVE_PD = False


# ─────────────────────────── indicator helpers ───────────────────────────
def atr(high: Sequence[float], low: Sequence[float], close: Sequence[float],
        period: int = 14) -> np.ndarray:
    """Wilder Average True Range (absolute price units).

    TR_t = max(high−low, |high−prev_close|, |low−prev_close|); ATR is the
    Wilder-smoothed (EMA α=1/period) TR. Returns an array aligned to the input;
    leading values are warm-up estimates, not NaN, so callers can index [-1]."""
    h = np.asarray(high, float); l = np.asarray(low, float); c = np.asarray(close, float)
    if len(c) == 0:
        return np.array([])
    pc = np.roll(c, 1); pc[0] = c[0]
    tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
    if _HAVE_PD:
        return pd.Series(tr).ewm(alpha=1.0 / period, adjust=False).mean().to_numpy()
    # numpy fallback: iterative Wilder smoothing
    out = np.empty_like(tr); out[0] = tr[0]
    a = 1.0 / period
    for i in range(1, len(tr)):
        out[i] = out[i - 1] + a * (tr[i] - out[i - 1])
    return out


def atr_pct_baseline(high, low, close, atr_period: int = 14,
                     baseline_period: int = 30) -> Tuple[Optional[float], Optional[float]]:
    """Return (current_atr_pct, baseline_atr_pct) using the LAST CLOSED bar.

    current  = ATR/price at the latest bar, in percent.
    baseline = simple mean of the last `baseline_period` ATR% values ("normal").
    Returns (None, None) when there isn't enough data — callers then skip the
    throttle (the notional cap still bounds the trade). Anti-lookahead: uses only
    completed bars, so it is safe to call at decision time on closed candles."""
    c = np.asarray(close, float)
    if len(c) < max(atr_period, baseline_period) + 1 or c[-1] <= 0:
        return None, None
    a = atr(high, low, c, atr_period)
    atr_pct = a / np.where(c == 0, np.nan, c) * 100.0
    cur = float(atr_pct[-1])
    window = atr_pct[-baseline_period:]
    window = window[np.isfinite(window)]
    if window.size == 0 or not math.isfinite(cur):
        return None, None
    return cur, float(window.mean())


def realized_vol(close: Sequence[float], period: int = 20) -> Optional[float]:
    """Standard deviation of log returns over the last `period` bars (per-bar,
    not annualized). Returns None if the series is too short. A robust,
    orderflow-free volatility read that complements ATR."""
    c = np.asarray(close, float)
    if len(c) < period + 1 or np.any(c[-(period + 1):] <= 0):
        return None
    rets = np.diff(np.log(c[-(period + 1):]))
    return float(np.std(rets, ddof=1)) if rets.size > 1 else None


# ─────────────────────────── sizing core ───────────────────────────
def vol_multiplier(current_vol: Optional[float], baseline_vol: Optional[float],
                   k: float = 1.5, floor: float = 0.25) -> float:
    """Exponential volatility contraction multiplier in (floor, 1.0].

        ratio = current_vol / baseline_vol
        mult  = clip( exp(−k · max(0, ratio − 1)), floor, 1.0 )

    At/below baseline (ratio ≤ 1) ⇒ 1.0 (full size). Above baseline it decays
    exponentially, floored so a position is never zeroed on volatility alone
    (that is the circuit breaker's job). Never exceeds 1.0 — spot, no leverage.
    Missing/invalid vol ⇒ 1.0 (fail-open; the notional cap still bounds size)."""
    if (current_vol is None or baseline_vol is None or baseline_vol <= 0
            or current_vol < 0 or not math.isfinite(current_vol)
            or not math.isfinite(baseline_vol)):
        return 1.0
    ratio = current_vol / baseline_vol
    mult = math.exp(-k * max(0.0, ratio - 1.0))
    return max(floor, min(1.0, mult))


def risk_based_qty(equity: float, risk_frac: float, entry: float, stop: float) -> float:
    """Constant-1R base quantity: (equity · risk_frac) / |entry − stop|.
    A stop-out then loses exactly `risk_frac` of equity, whatever the stop width.
    Returns 0.0 on any degenerate input (non-positive equity or zero stop dist)."""
    if equity <= 0 or risk_frac <= 0 or entry <= 0:
        return 0.0
    dist = abs(entry - stop)
    if dist <= 0 or not math.isfinite(dist):
        return 0.0
    return (equity * risk_frac) / dist


def vol_scaled_qty(equity: float, risk_frac: float, entry: float, stop: float,
                   current_vol: Optional[float], baseline_vol: Optional[float],
                   k: float = 1.5, floor: float = 0.25,
                   max_notional_frac: float = 0.10) -> dict:
    """Full sizing pipeline → dict with the final qty and a full audit trail.

    Steps: constant-1R base → exponential vol contraction → hard notional cap.
    The returned dict exposes every intermediate so the decision is auditable
    (the meta-analysis lesson: realized per-trade risk must be verifiable)."""
    base = risk_based_qty(equity, risk_frac, entry, stop)
    mult = vol_multiplier(current_vol, baseline_vol, k, floor)
    qty = base * mult
    capped = False
    if entry > 0 and max_notional_frac > 0 and qty > 0:
        cap_qty = (equity * max_notional_frac) / entry
        if qty > cap_qty:
            qty = cap_qty
            capped = True
    qty = max(0.0, round(qty, 8))
    realized_risk = qty * abs(entry - stop)
    return {
        "qty": qty,
        "base_qty": round(base, 8),
        "vol_mult": round(mult, 4),
        "notional_capped": capped,
        "notional": round(qty * entry, 2),
        "realized_risk_usd": round(realized_risk, 2),
        "realized_risk_pct": round(realized_risk / equity * 100, 4) if equity > 0 else 0.0,
    }


# ─────────────────────────── circuit breakers ───────────────────────────
@dataclass
class CircuitBreaker:
    """Hard defensive gates checked BEFORE any new entry. Every threshold is a
    fraction of equity so it scales with the book automatically (no $-value that
    silently goes stale when capital changes — a bug this firm has hit before).

        daily_dd_cap ....... halt new entries once the day is down this fraction
        max_open_exposure .. total open notional may not exceed this × equity
        streak_loss_cap .... halt after a losing streak of this fraction of equity
    """
    daily_dd_cap: float = 0.03          # -3% on the day → pause
    max_open_exposure: float = 0.60     # ≤ 60% of equity deployed at once
    streak_loss_cap: float = 0.02       # -2% loss streak → pause

    def check_new_entry(self, equity: float, day_start_equity: float,
                        open_notional: float, streak_loss_usd: float = 0.0,
                        candidate_notional: float = 0.0) -> Tuple[bool, list]:
        """Return (allowed, reasons). allowed=False means PAUSE — do not enter.
        `streak_loss_usd` is the running loss of the current losing streak
        (positive number). All inputs are explicit so this is pure & testable."""
        reasons = []
        if equity <= 0:
            return False, ["equity is zero/negative — emergency pause"]
        if day_start_equity > 0:
            dd = (day_start_equity - equity) / day_start_equity
            if dd >= self.daily_dd_cap:
                reasons.append(f"daily drawdown {dd:.1%} ≥ {self.daily_dd_cap:.1%} cap")
        if (open_notional + candidate_notional) / equity > self.max_open_exposure:
            reasons.append(
                f"open exposure {(open_notional + candidate_notional) / equity:.0%} "
                f"> {self.max_open_exposure:.0%} cap")
        if streak_loss_usd > 0 and streak_loss_usd / equity >= self.streak_loss_cap:
            reasons.append(
                f"loss streak {streak_loss_usd / equity:.1%} ≥ {self.streak_loss_cap:.1%} cap")
        return (len(reasons) == 0, reasons)


if __name__ == "__main__":  # self-check: python core/vol_sizing.py
    # constant-1R: risk 1% of $10k over a $5 stop -> 20 units, loses exactly $100
    r = vol_scaled_qty(10_000, 0.01, 100, 95, current_vol=1.0, baseline_vol=1.0,
                       max_notional_frac=1.0)
    assert abs(r["qty"] - 20) < 1e-6 and abs(r["realized_risk_usd"] - 100) < 1e-6, r
    assert risk_based_qty(10_000, 0.01, 100, 100) == 0.0          # zero stop dist
    # exponential multiplier: calm=1.0, shrinks above baseline, never >1, floored
    assert vol_multiplier(1.0, 1.0) == 1.0
    assert vol_multiplier(0.5, 1.0) == 1.0                        # below baseline
    assert 0 < vol_multiplier(2.0, 1.0, k=1.5, floor=0.05) < 1.0
    assert vol_multiplier(2.0, 1.0, floor=0.25) == 0.25          # floor holds
    assert vol_multiplier(None, 1.0) == 1.0                       # missing data fails open
    # notional cap binds when risk-based size wants more than the cap
    c = vol_scaled_qty(10_000, 0.05, 100, 99, 1.0, 1.0, max_notional_frac=0.10)
    assert c["notional_capped"] and abs(c["qty"] - 10) < 1e-6, c
    # circuit breakers
    cb = CircuitBreaker()
    assert cb.check_new_entry(9_600, 10_000, 0)[0] is False        # -4% day >= 3% cap
    assert cb.check_new_entry(10_000, 10_000, 7_000)[0] is False   # 70% exposure > 60%
    assert cb.check_new_entry(10_000, 10_000, 3_000)[0] is True    # all clear
    print("vol_sizing self-check OK")

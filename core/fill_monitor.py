"""Limit-fill & microstructure diagnostics for the maker-limit entry path.

Answers, on the firm's ACTUAL resting limits (core.pending_orders), the three
questions the execution objective asks:

  1. Fill-rate efficiency — filled vs expired vs cancelled.
  2. Time-to-fill        — created_at -> filled_at (seconds; the firm runs a
                           ~1-minute REST cycle, so this is cycle-scale, not
                           the microsecond scale a live ccxt.pro feed gives).
  3. Adverse selection   — price move 1m / 5m AFTER a buy fills. Positive =
                           price bounced (good entry); negative = it kept
                           dumping (caught a falling knife).

Plus two circuit breakers:
  - spread-expansion kill-switch: skip placement when the bid/ask spread blows
    out vs its rolling average (liquidity vacuum);
  - adverse-selection throttle: widen the ATR offset after a run of knife fills.

Everything is measured at the cadence the paper/REST architecture truly
supports — no fabricated microsecond latency or 10-second adverse scores.
"""
import time
from collections import deque
from datetime import datetime, timezone

from config import TRADE_FEE_PCT
from core.database import execute, fetchall, fetchone

MAKER_FEE_PCT = 0.02          # post-only maker fee target
TAKER_FEE_PCT = TRADE_FEE_PCT  # what a market order pays instead

# In-memory rolling spread per symbol (EWMA) for the kill-switch. Rebuilds
# quickly after a restart; a kill-switch does not need persistence.
_SPREAD_EWMA = {}
_SPREAD_ALPHA = 0.05
SPREAD_KILL_MULT = 3.0        # halt placement if spread > this x rolling avg

# Adverse-selection throttle state.
_THROTTLE_UNTIL = 0.0
THROTTLE_WINDOW_S = 900       # 15 min
THROTTLE_WIDEN = 1.5          # widen k by 50%
ADVERSE_DD_PCT = -0.5         # a fill is "adverse" if 1m move < -0.5%
ADVERSE_STREAK = 3


def _parse(ts):
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts).replace(" ", "T"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


# ── circuit breaker 1: spread-expansion kill-switch ──
def observe_spread(symbol, bid, ask):
    """Feed a bid/ask observation; returns current spread %."""
    if not bid or not ask or ask < bid:
        return None
    mid = (bid + ask) / 2
    spread = (ask - bid) / mid * 100 if mid else 0
    prev = _SPREAD_EWMA.get(symbol)
    _SPREAD_EWMA[symbol] = spread if prev is None else _SPREAD_ALPHA * spread + (1 - _SPREAD_ALPHA) * prev
    return spread


def spread_ok(symbol, bid, ask):
    """False = liquidity vacuum, skip placing a passive quote."""
    spread = observe_spread(symbol, bid, ask)
    avg = _SPREAD_EWMA.get(symbol)
    if spread is None or avg is None or avg <= 0:
        return True
    return spread <= avg * SPREAD_KILL_MULT


# ── circuit breaker 2: adverse-selection throttle ──
def adverse_throttle_active(now=None):
    return (now or time.time()) < _THROTTLE_UNTIL


def offset_mult(base_k, now=None):
    """Widen the ATR offset while the adverse-selection throttle is active."""
    return base_k * THROTTLE_WIDEN if adverse_throttle_active(now) else base_k


def _maybe_trip_throttle(now=None):
    """Trip the throttle if the last ADVERSE_STREAK scored fills were all
    knives (1m move below ADVERSE_DD_PCT)."""
    global _THROTTLE_UNTIL
    rows = fetchall(
        "SELECT adverse_1m FROM pending_orders WHERE status='filled' "
        "AND adverse_1m IS NOT NULL ORDER BY id DESC LIMIT ?", [ADVERSE_STREAK])
    if len(rows) == ADVERSE_STREAK and all(
            (r["adverse_1m"] or 0) * 100 < ADVERSE_DD_PCT for r in rows):
        _THROTTLE_UNTIL = (now or time.time()) + THROTTLE_WINDOW_S


# ── module 2: adverse-selection scoring (run each maintenance cycle) ──
def score_adverse_selection(prices, now=None):
    """For filled limits, record the 1m and 5m post-fill price move. Positive
    = price rose after the buy (good); negative = kept falling (knife)."""
    now = now or time.time()
    scored_any = False
    rows = fetchall(
        "SELECT id, symbol, fill_price, filled_at, scored FROM pending_orders "
        "WHERE status='filled' AND scored < 2 AND fill_price > 0")
    for r in rows:
        ft = _parse(r["filled_at"])
        if ft is None:
            continue
        age = now - ft
        pd = prices.get(r["symbol"], {})
        price = pd.get("price") if isinstance(pd, dict) else pd
        if not price:
            continue
        move = price / r["fill_price"] - 1
        if r["scored"] < 1 and age >= 60:
            execute("UPDATE pending_orders SET adverse_1m=?, scored=1 WHERE id=?",
                    [round(move, 6), r["id"]])
            scored_any = True
        elif r["scored"] < 2 and age >= 300:
            execute("UPDATE pending_orders SET adverse_5m=?, scored=2 WHERE id=?",
                    [round(move, 6), r["id"]])
            scored_any = True
    if scored_any:
        _maybe_trip_throttle(now)


# ── module 1 helper: cancel/replace stale quotes on drift ──
def cancel_drifted(prices, drift_mult=1.5):
    """Cancel resting limits the market has run away from by > drift_mult x ATR
    (price now far ABOVE the limit and unlikely to come back this cycle).
    Returns the count cancelled; the next scan re-places at a fresh distance."""
    cancelled = 0
    for row in fetchall("SELECT * FROM pending_orders WHERE status='pending'"):
        po = dict(row)
        atr = po.get("atr") or 0
        if atr <= 0:
            continue
        pd = prices.get(po["symbol"], {})
        price = pd.get("price") if isinstance(pd, dict) else pd
        if not price:
            continue
        if price - po["limit_price"] > drift_mult * atr:
            execute("UPDATE pending_orders SET status='cancelled' WHERE id=?", [po["id"]])
            cancelled += 1
    return cancelled


# ── spread saved on a filled order ──
def spread_saved_usd(po):
    """$ saved vs a taker market order: maker-vs-taker fee + price improvement
    (we bought below the reference/ask). Only meaningful for filled orders."""
    ref = po.get("ref_price") or 0
    fill = po.get("fill_price") or po.get("limit_price") or 0
    qty = po.get("quantity") or 0
    if fill <= 0 or qty <= 0:
        return 0.0
    fee_saved = qty * fill * (TAKER_FEE_PCT - MAKER_FEE_PCT) / 100.0
    price_improve = qty * max(0.0, ref - fill) if ref else 0.0
    return round(fee_saved + price_improve, 4)


# ── module 2: diagnostics dashboard ──
def diagnostics(days=7):
    """Per-symbol execution metrics over the window + a totals row."""
    since = f"datetime('now', '-{int(days)} days')"
    rows = fetchall(f"SELECT * FROM pending_orders WHERE created_at >= {since}")
    by = {}
    for row in rows:
        po = dict(row)
        s = by.setdefault(po["symbol"], {"quotes": 0, "filled": 0, "expired": 0,
                                         "cancelled": 0, "ttf": [], "adv1": [],
                                         "saved": 0.0})
        s["quotes"] += 1
        st = po["status"]
        if st == "filled":
            s["filled"] += 1
            ct, ft = _parse(po["created_at"]), _parse(po["filled_at"])
            if ct and ft and ft >= ct:
                s["ttf"].append(ft - ct)
            if po.get("adverse_1m") is not None:
                s["adv1"].append(po["adverse_1m"] * 100)
            s["saved"] += spread_saved_usd(po)
        elif st == "expired":
            s["expired"] += 1
        elif st == "cancelled":
            s["cancelled"] += 1
    out = []
    tot = {"quotes": 0, "filled": 0, "ttf": [], "adv1": [], "saved": 0.0}
    for sym, s in sorted(by.items()):
        fr = s["filled"] / s["quotes"] * 100 if s["quotes"] else 0
        out.append({
            "symbol": sym, "total_quotes": s["quotes"],
            "fill_rate_pct": round(fr, 1),
            "filled": s["filled"], "expired": s["expired"], "cancelled": s["cancelled"],
            "avg_time_to_fill_s": round(sum(s["ttf"]) / len(s["ttf"]), 1) if s["ttf"] else None,
            "adverse_1m_pct": round(sum(s["adv1"]) / len(s["adv1"]), 3) if s["adv1"] else None,
            "net_spread_saved_usd": round(s["saved"], 2),
        })
        tot["quotes"] += s["quotes"]; tot["filled"] += s["filled"]
        tot["ttf"] += s["ttf"]; tot["adv1"] += s["adv1"]; tot["saved"] += s["saved"]
    totals = {
        "total_quotes": tot["quotes"],
        "fill_rate_pct": round(tot["filled"] / tot["quotes"] * 100, 1) if tot["quotes"] else 0,
        "avg_time_to_fill_s": round(sum(tot["ttf"]) / len(tot["ttf"]), 1) if tot["ttf"] else None,
        "adverse_1m_pct": round(sum(tot["adv1"]) / len(tot["adv1"]), 3) if tot["adv1"] else None,
        "net_spread_saved_usd": round(tot["saved"], 2),
        "throttle_active": adverse_throttle_active(),
    }
    return {"per_symbol": out, "totals": totals, "window_days": days}

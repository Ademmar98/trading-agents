"""Correlated-selloff defenses (post-mortem 2026-07-12).

Six altcoin longs opened as "independent" positions and all stopped out on
one BTC dip during the Asian session. Four small, unit-testable functions
close the structural holes; compliance/execution/pricing call them before
opening or sizing anything.
"""
from datetime import datetime, timezone

from config import (
    CORRELATION_GROUPS, SESSION_RISK_MULTS, MACRO_DIP_PCT,
    MIN_SL_PCT, MAX_SL_PCT,
)


def symbol_group(symbol):
    for group, members in CORRELATION_GROUPS.items():
        if symbol in members:
            return group
    return None


def count_group_positions(symbol, open_symbols):
    """How many already-held symbols share this symbol's correlation group.
    open_symbols: iterable of open-position symbols (plus any approved this
    cycle — the caller accumulates)."""
    group = symbol_group(symbol)
    if not group:
        return 0
    members = set(CORRELATION_GROUPS[group])
    return sum(1 for s in open_symbols if s in members)


def session_risk_mult(now=None):
    """Risk multiplier by liquidity session (UTC): Asian 00-08 x0.5,
    European 08-14 x0.8, US overlap 14-22 x1.0, late-US 22-24 x0.5."""
    mults = [float(x) for x in SESSION_RISK_MULTS.split(",")]
    while len(mults) < 4:
        mults.append(1.0)
    hour = (now or datetime.now(timezone.utc)).hour
    if hour < 8:
        return mults[0]
    if hour < 14:
        return mults[1]
    if hour < 22:
        return mults[2]
    return mults[3]


def macro_dip_alert(cluster, bellwether_moves):
    """True when the asset class's bellwether dropped more than MACRO_DIP_PCT
    in the last ~30 minutes (as computed by the analyst into the scan)."""
    if not bellwether_moves:
        return False
    move = bellwether_moves.get(cluster)
    if move is None:
        return False
    return move <= -MACRO_DIP_PCT


def vol_aware_stop_loss(atr_pct, sl_mult):
    """ATR-calibrated stop distance in percent: ATR x multiplier first,
    floored at MIN_SL_PCT (noise floor), capped at MAX_SL_PCT only as a
    sanity ceiling for absurd volatility readings. Previously the cap acted
    as the routine placement — every stop landed at exactly the cap."""
    if not atr_pct or atr_pct <= 0:
        return None
    return max(MIN_SL_PCT, min(atr_pct * sl_mult, MAX_SL_PCT))


# ── Per-symbol concentration guards (post-mortem 2026-07-23) ──
# The meta-analysis of 77 live trades found risk-normalised expectancy was
# POSITIVE (+0.63R) yet dollars were NEGATIVE (-$8.59/trade): one alt (UNI)
# was traded 13 times and became 100%+ of the net loss. Per-trade risk was
# already capped; the hole was SEQUENTIAL concentration — re-entering the same
# losing name over and over. These two gates close it.

def recent_stopout_cooldown(symbol, cooldown_min, now=None):
    """True when `symbol` had a losing STOP-OUT close within the last
    cooldown_min minutes — blocks the machine-gun re-entry that let one name
    dominate the book. Only stop-driven losses trigger it (a winner or a
    take-profit resets the clock). cooldown_min <= 0 disables the gate."""
    if not cooldown_min or cooldown_min <= 0:
        return False
    from core.database import fetchone
    row = fetchone("SELECT pnl, reason, closed_at FROM trades "
                   "WHERE symbol = ? ORDER BY id DESC LIMIT 1", [symbol])
    if not row or not row["closed_at"]:
        return False
    if (row["pnl"] or 0) > 0:
        return False                                   # last trade won
    if "stop" not in (row["reason"] or "").lower():
        return False                                   # only after a stop-out
    try:
        raw = str(row["closed_at"]).replace(" ", "T").replace("Z", "+00:00")
        closed = datetime.fromisoformat(raw)
        if closed.tzinfo is None:
            closed = closed.replace(tzinfo=timezone.utc)
    except (ValueError, AttributeError):
        return False
    age_min = ((now or datetime.now(timezone.utc)) - closed).total_seconds() / 60.0
    return 0 <= age_min < cooldown_min


def symbol_daily_loss(symbol, day=None):
    """Realized loss (returned as a POSITIVE dollar figure) for `symbol` on the
    given UTC day (default today). 0.0 if the symbol is net-flat or profitable.
    Used to pause a single name once it has bled its daily budget."""
    from core.database import fetchone
    day = day or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    row = fetchone("SELECT SUM(pnl) AS pnl FROM trades "
                   "WHERE symbol = ? AND date(closed_at) = ?", [symbol, day])
    pnl = (row["pnl"] if row and row["pnl"] is not None else 0.0) or 0.0
    return -pnl if pnl < 0 else 0.0

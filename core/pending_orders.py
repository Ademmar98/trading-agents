"""Resting BUY-limit orders.

When price is extended above session VWAP, paying up at market means buying
the local top — instead the trader rests a limit at VWAP and lets price come
back. Unfilled limits expire after LIMIT_ORDER_TTL_MIN; a limit whose symbol
acquires a position through another path is cancelled.

Fill realism: a limit is NOT filled on a mere touch of its price — at the
touch the queue ahead of you is rarely cleared. It fills only once the market
trades THROUGH the limit by LIMIT_FILL_THROUGH_PCT.
"""
from datetime import datetime, timedelta, timezone

import config
from config import LIMIT_ORDER_TTL_MIN
from core.database import execute, fetchall

# How far (percent) the check price must trade through the limit before the
# resting order counts as filled. getattr fallback: config.py predates this.
LIMIT_FILL_THROUGH_PCT = float(getattr(config, "LIMIT_FILL_THROUGH_PCT", 0.05))


def place_limit(symbol, limit_price, quantity, sl=0, tp=0, strategy="",
                ttl_min=LIMIT_ORDER_TTL_MIN):
    expires = (datetime.now(timezone.utc) + timedelta(minutes=ttl_min)).isoformat()
    cur = execute("""
        INSERT INTO pending_orders (symbol, side, limit_price, quantity,
                                    stop_loss, take_profit, strategy, expires_at)
        VALUES (?, 'BUY', ?, ?, ?, ?, ?, ?)
    """, [symbol, limit_price, quantity, sl, tp, strategy, expires])
    return cur.lastrowid


def open_pending(symbol=None):
    if symbol:
        return [dict(r) for r in fetchall(
            "SELECT * FROM pending_orders WHERE status='pending' AND symbol=?", [symbol])]
    return [dict(r) for r in fetchall(
        "SELECT * FROM pending_orders WHERE status='pending'")]


def check_fills(prices, has_position):
    """Fill limits the market has touched, expire stale ones, cancel those
    whose symbol got a position through another path. Returns fill dicts —
    the caller routes them to the broker/PositionManager."""
    now = datetime.now(timezone.utc).isoformat()
    fills = []
    for po in open_pending():
        if has_position(po["symbol"]):
            execute("UPDATE pending_orders SET status='cancelled' WHERE id=?", [po["id"]])
            continue
        if po.get("expires_at") and now > po["expires_at"]:
            execute("UPDATE pending_orders SET status='expired' WHERE id=?", [po["id"]])
            continue
        pd = prices.get(po["symbol"], {})
        price = pd.get("price") if isinstance(pd, dict) else pd
        if not price:
            continue
        # BUY limit fills only when the market trades THROUGH it, not on a
        # touch — price must come below limit by LIMIT_FILL_THROUGH_PCT.
        # The fill itself is still booked at the limit price (or better),
        # which is what a real resting limit would get.
        through_price = po["limit_price"] * (1 - LIMIT_FILL_THROUGH_PCT / 100)
        if price <= through_price:
            execute("UPDATE pending_orders SET status='filled', filled_at=? WHERE id=?",
                    [now, po["id"]])
            fills.append(po)
    return fills

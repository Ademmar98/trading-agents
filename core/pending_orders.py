"""Resting BUY-limit orders.

When price is extended above session VWAP, paying up at market means buying
the local top — instead the trader rests a limit at VWAP and lets price come
back. Unfilled limits expire after LIMIT_ORDER_TTL_MIN; a limit whose symbol
acquires a position through another path is cancelled.
"""
from datetime import datetime, timedelta, timezone

from config import LIMIT_ORDER_TTL_MIN
from core.database import execute, fetchall


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
        if price <= po["limit_price"]:  # BUY limit touched
            execute("UPDATE pending_orders SET status='filled', filled_at=? WHERE id=?",
                    [now, po["id"]])
            fills.append(po)
    return fills

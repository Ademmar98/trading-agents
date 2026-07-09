from datetime import datetime, timezone

from config import (
    TRAILING_STOP_PCT, TRAILING_ACTIVATION_PCT, BREAKEVEN_ENABLED,
    BREAKEVEN_ACTIVATION_PCT, TRADE_FEE_PCT,
    PARTIAL_TP_ENABLED, PARTIAL_TP_R, PARTIAL_TP_FRACTION,
    TRAILING_ACTIVATION_R, TRAILING_STOP_R,
)
from core.database import execute, fetchone, fetchall, init_db


class PositionManager:
    def __init__(self):
        init_db()

    def get_open_positions(self):
        rows = fetchall("SELECT * FROM positions WHERE status='open' ORDER BY opened_at DESC")
        return [dict(r) for r in rows]

    def has_position(self, symbol):
        r = fetchone("SELECT id FROM positions WHERE symbol=? AND status='open'", [symbol])
        return r is not None

    def open_position(self, symbol, side, qty, price, sl=0, tp=0, strategy=""):
        # initial_risk freezes the entry-time SL distance (1R) so exits stay
        # R-based even after breakeven/partial moves the live stop to entry.
        initial_risk = abs(price - sl) if sl else 0
        cur = execute("""
            INSERT INTO positions (symbol, side, quantity, entry_price, current_price, stop_loss, take_profit, peak_price, strategy, initial_risk)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, [symbol, side.upper(), qty, price, price, sl, tp, price, strategy, initial_risk])
        return cur.lastrowid

    @staticmethod
    def _net_pnl(side, entry, exit_price, qty):
        """Realized pnl net of round-trip fees, plus pct. This figure feeds
        strategy_stats and the unprofitable-strategy filter, which must not
        count fee-losing trades as wins."""
        if side == "BUY":
            pnl = (exit_price - entry) * qty
        else:
            pnl = (entry - exit_price) * qty
        pnl -= (entry + exit_price) * qty * (TRADE_FEE_PCT / 100.0)
        denom = entry * qty
        pnl_pct = (pnl / denom) * 100 if denom else 0
        return pnl, pnl_pct

    def close_position(self, position_id, exit_price, reason="manual"):
        row = fetchone("SELECT * FROM positions WHERE id=?", [position_id])
        if not row or row["status"] == "closed":
            return None
        pos = dict(row)
        pnl, pnl_pct = self._net_pnl(pos["side"], pos["entry_price"], exit_price, pos["quantity"])
        now = datetime.now(timezone.utc).isoformat()
        execute("""
            UPDATE positions SET status='closed', closed_at=?, current_price=?, pnl=?, pnl_pct=?, updated_at=?
            WHERE id=?
        """, [now, exit_price, round(pnl, 2), round(pnl_pct, 2), now, position_id])
        execute("""
            INSERT INTO trades (position_id, symbol, side, qty, entry_price, exit_price, pnl, pnl_pct, reason, opened_at, strategy)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, [position_id, pos["symbol"], pos["side"], pos["quantity"],
              pos["entry_price"], exit_price, round(pnl, 2), round(pnl_pct, 2),
              reason, pos["opened_at"], pos.get("strategy", "")])
        return {"symbol": pos["symbol"], "side": pos["side"], "qty": pos["quantity"],
                "entry_price": pos["entry_price"], "exit_price": exit_price,
                "pnl": round(pnl, 2), "pnl_pct": round(pnl_pct, 2), "reason": reason}

    def close_partial(self, position_id, exit_price, fraction, reason="partial_tp"):
        """Close a fraction of the position and move the stop to breakeven.

        The remainder rides as a risk-free runner: banked profit is locked in,
        and the worst case for the rest of the trade is roughly breakeven.
        """
        row = fetchone("SELECT * FROM positions WHERE id=?", [position_id])
        if not row or row["status"] == "closed":
            return None
        pos = dict(row)
        close_qty = round(pos["quantity"] * fraction, 8)
        remaining = round(pos["quantity"] - close_qty, 8)
        if close_qty <= 0 or remaining <= 0:
            return None
        pnl, pnl_pct = self._net_pnl(pos["side"], pos["entry_price"], exit_price, close_qty)
        now = datetime.now(timezone.utc).isoformat()
        # Rewrite the row's unrealized pnl for the reduced quantity too, or it
        # keeps showing the full-size figure until the next price tick.
        if pos["side"] == "BUY":
            rem_pnl = (exit_price - pos["entry_price"]) * remaining
        else:
            rem_pnl = (pos["entry_price"] - exit_price) * remaining
        rem_pct = (rem_pnl / (pos["entry_price"] * remaining)) * 100 if pos["entry_price"] * remaining else 0
        execute("""
            UPDATE positions SET quantity=?, stop_loss=?, partial_taken=1,
                   current_price=?, pnl=?, pnl_pct=?, updated_at=?
            WHERE id=? AND status='open'
        """, [remaining, pos["entry_price"], exit_price,
              round(rem_pnl, 2), round(rem_pct, 2), now, position_id])
        execute("""
            INSERT INTO trades (position_id, symbol, side, qty, entry_price, exit_price, pnl, pnl_pct, reason, opened_at, strategy)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, [position_id, pos["symbol"], pos["side"], close_qty,
              pos["entry_price"], exit_price, round(pnl, 2), round(pnl_pct, 2),
              reason, pos["opened_at"], pos.get("strategy", "")])
        return {"symbol": pos["symbol"], "side": pos["side"], "qty": close_qty,
                "entry_price": pos["entry_price"], "exit_price": exit_price,
                "pnl": round(pnl, 2), "pnl_pct": round(pnl_pct, 2), "reason": reason}

    def update_prices(self, prices):
        positions = self.get_open_positions()
        triggered = []
        for pos in positions:
            price_data = prices.get(pos["symbol"], {})
            price = price_data.get("price") if isinstance(price_data, dict) else price_data
            if not price:
                continue
            if pos["side"] == "BUY":
                pnl = (price - pos["entry_price"]) * pos["quantity"]
                pnl_pct = (price - pos["entry_price"]) / pos["entry_price"] * 100
            else:
                pnl = (pos["entry_price"] - price) * pos["quantity"]
                pnl_pct = (pos["entry_price"] - price) / pos["entry_price"] * 100

            # Peak = most favorable price seen (highest for longs, lowest for shorts).
            # Rows from before the peak_price column default to 0 -> seed with price.
            peak = pos.get("peak_price") or price
            peak = max(peak, price) if pos["side"] == "BUY" else min(peak, price)
            execute("""
                UPDATE positions SET current_price=?, pnl=?, pnl_pct=?, peak_price=?, updated_at=datetime('now')
                WHERE id=? AND status='open'
            """, [price, round(pnl, 2), round(pnl_pct, 2), peak, pos["id"]])

            # Scaled exit: at PARTIAL_TP_R x initial risk in profit, bank
            # PARTIAL_TP_FRACTION of the position and let the rest run from
            # breakeven — winners pay for the losers instead of being clipped.
            initial_risk = pos.get("initial_risk") or 0
            if (PARTIAL_TP_ENABLED and initial_risk > 0 and pos["stop_loss"]
                    and not pos.get("partial_taken")):
                if pos["side"] == "BUY":
                    partial_hit = price >= pos["entry_price"] + initial_risk * PARTIAL_TP_R
                else:
                    partial_hit = price <= pos["entry_price"] - initial_risk * PARTIAL_TP_R
                if partial_hit:
                    result = self.close_partial(pos["id"], price, PARTIAL_TP_FRACTION)
                    if result:
                        triggered.append(result)
                        pos["quantity"] = round(pos["quantity"] - result["qty"], 8)
                        pos["stop_loss"] = pos["entry_price"]
                        pos["partial_taken"] = 1

            # Breakeven: move stop_loss to entry_price to lock in a risk-free trade.
            # Activates at the lesser of:
            #   - BREAKEVEN_ACTIVATION_PCT% of the TP distance from entry
            #   - 1x the initial SL distance (safeguard floor)
            if BREAKEVEN_ENABLED and pos["stop_loss"] and pos["entry_price"]:
                sl_dist = abs(pos["entry_price"] - pos["stop_loss"])
                tp_dist = abs(pos["take_profit"] - pos["entry_price"]) if pos.get("take_profit") else 0
                tp_activation = tp_dist * (BREAKEVEN_ACTIVATION_PCT / 100) if tp_dist > 0 else float("inf")
                activation_dist = min(tp_activation, sl_dist)
                if pos["side"] == "BUY":
                    if price >= pos["entry_price"] + activation_dist and pos["stop_loss"] < pos["entry_price"]:
                        execute("""
                            UPDATE positions SET stop_loss=?, updated_at=datetime('now')
                            WHERE id=? AND status='open'
                        """, [pos["entry_price"], pos["id"]])
                        pos["stop_loss"] = pos["entry_price"]
                else:
                    if price <= pos["entry_price"] - activation_dist and pos["stop_loss"] > pos["entry_price"]:
                        execute("""
                            UPDATE positions SET stop_loss=?, updated_at=datetime('now')
                            WHERE id=? AND status='open'
                        """, [pos["entry_price"], pos["id"]])
                        pos["stop_loss"] = pos["entry_price"]

            if pos["stop_loss"] and (
                (pos["side"] == "BUY" and price <= pos["stop_loss"]) or
                (pos["side"] == "SELL" and price >= pos["stop_loss"])
            ):
                result = self.close_position(pos["id"], price, reason="stop_loss")
                if result:
                    triggered.append(result)
            elif pos["take_profit"] and (
                (pos["side"] == "BUY" and price >= pos["take_profit"]) or
                (pos["side"] == "SELL" and price <= pos["take_profit"])
            ):
                result = self.close_position(pos["id"], price, reason="take_profit")
                if result:
                    triggered.append(result)
            elif self._trailing_stop_hit(pos["side"], pos["entry_price"], peak, price,
                                         pos.get("initial_risk") or 0):
                result = self.close_position(pos["id"], price, reason="trailing_stop")
                if result:
                    triggered.append(result)
        return triggered

    @staticmethod
    def _trailing_stop_hit(side, entry, peak, price, initial_risk=0):
        """Exit when a winner gives back too much from its peak.

        R-based when the position's initial risk is known: arm after
        TRAILING_ACTIVATION_R x risk of profit and give back TRAILING_STOP_R x
        risk from the peak. Sized off the same distance a loser can cost, the
        trail no longer clips winners at a fraction of a full stop-loss.
        Falls back to percent-of-price for legacy rows without initial_risk.
        """
        if not entry or not peak:
            return False
        if initial_risk > 0:
            # TRAILING_STOP_PCT <= 0 has always been the trailing off-switch;
            # honor it (and TRAILING_STOP_R <= 0) on the R-based path too.
            if TRAILING_STOP_PCT <= 0 or TRAILING_STOP_R <= 0:
                return False
            if side == "BUY":
                if peak - entry < initial_risk * TRAILING_ACTIVATION_R:
                    return False
                return price <= peak - initial_risk * TRAILING_STOP_R
            if entry - peak < initial_risk * TRAILING_ACTIVATION_R:
                return False
            return price >= peak + initial_risk * TRAILING_STOP_R
        if TRAILING_STOP_PCT <= 0:
            return False
        if side == "BUY":
            run_up_pct = (peak - entry) / entry * 100
            if run_up_pct < TRAILING_ACTIVATION_PCT:
                return False
            return price <= peak * (1 - TRAILING_STOP_PCT / 100)
        run_down_pct = (entry - peak) / entry * 100
        if run_down_pct < TRAILING_ACTIVATION_PCT:
            return False
        return price >= peak * (1 + TRAILING_STOP_PCT / 100)

    def get_positions_summary(self):
        positions = self.get_open_positions()
        total_pnl = sum(p["pnl"] for p in positions)
        return {"count": len(positions), "total_pnl": round(total_pnl, 2), "positions": positions}

    def get_recent_trades(self, limit=10):
        rows = fetchall("SELECT * FROM trades ORDER BY closed_at DESC LIMIT ?", [limit])
        return [dict(r) for r in rows]

    def filter_new_signals(self, opportunities):
        return [o for o in opportunities if not self.has_position(o["symbol"])]

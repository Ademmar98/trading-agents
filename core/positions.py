from datetime import datetime, timezone
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

    def open_position(self, symbol, side, qty, price, sl=0, tp=0):
        execute("""
            INSERT INTO positions (symbol, side, quantity, entry_price, current_price, stop_loss, take_profit)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, [symbol, side.upper(), qty, price, price, sl, tp])
        pos_id = fetchone("SELECT last_insert_rowid()")[0]
        return pos_id

    def close_position(self, position_id, exit_price, reason="manual"):
        row = fetchone("SELECT * FROM positions WHERE id=?", [position_id])
        if not row or row["status"] == "closed":
            return None
        pos = dict(row)
        if pos["side"] == "BUY":
            pnl = (exit_price - pos["entry_price"]) * pos["quantity"]
        else:
            pnl = (pos["entry_price"] - exit_price) * pos["quantity"]
        pnl_pct = (pnl / (pos["entry_price"] * pos["quantity"])) * 100 if pos["entry_price"] * pos["quantity"] else 0
        now = datetime.now(timezone.utc).isoformat()
        execute("""
            UPDATE positions SET status='closed', closed_at=?, current_price=?, pnl=?, pnl_pct=?, updated_at=?
            WHERE id=?
        """, [now, exit_price, round(pnl, 2), round(pnl_pct, 2), now, position_id])
        execute("""
            INSERT INTO trades (position_id, symbol, side, qty, entry_price, exit_price, pnl, pnl_pct, reason, opened_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, [position_id, pos["symbol"], pos["side"], pos["quantity"],
              pos["entry_price"], exit_price, round(pnl, 2), round(pnl_pct, 2),
              reason, pos["opened_at"]])
        return {"symbol": pos["symbol"], "side": pos["side"], "pnl": round(pnl, 2), "pnl_pct": round(pnl_pct, 2), "reason": reason}

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
            execute("""
                UPDATE positions SET current_price=?, pnl=?, pnl_pct=?, updated_at=datetime('now')
                WHERE id=? AND status='open'
            """, [price, round(pnl, 2), round(pnl_pct, 2), pos["id"]])
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
        return triggered

    def get_positions_summary(self):
        positions = self.get_open_positions()
        total_pnl = sum(p["pnl"] for p in positions)
        return {"count": len(positions), "total_pnl": round(total_pnl, 2), "positions": positions}

    def get_recent_trades(self, limit=10):
        rows = fetchall("SELECT * FROM trades ORDER BY closed_at DESC LIMIT ?", [limit])
        return [dict(r) for r in rows]

    def filter_new_signals(self, opportunities):
        return [o for o in opportunities if not self.has_position(o["symbol"])]

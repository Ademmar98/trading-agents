from datetime import datetime, timezone

import config
from config import (
    TRAILING_STOP_PCT, TRAILING_ACTIVATION_PCT, BREAKEVEN_ENABLED,
    BREAKEVEN_ACTIVATION_PCT, BREAKEVEN_BUFFER_PCT, TRADE_FEE_PCT,
    PARTIAL_TP_ENABLED, PARTIAL_TP_R, PARTIAL_TP_FRACTION,
    TRAILING_ACTIVATION_R, TRAILING_STOP_R,
)
from core.database import execute, fetchone, fetchall, init_db

# ── Execution-realism knobs (getattr fallbacks: config.py predates them) ──
# Adverse fill slippage per side, percent. A stop-market order never books
# the exact trigger price — it fills at the stop or worse (audit 2026-07:
# stop-losses used to close at the last trade price, i.e. BETTER than the
# stop whenever the check price had gapped through it... or at the stop with
# zero gap/slippage cost otherwise).
SLIPPAGE_PCT = float(getattr(config, "SLIPPAGE_PCT", 0.05))
# Minimum holding period before ANY exit (SL/TP/partial/trailing) may fill.
# Audit 2026-07: positions were observed closing +1.1% 19 seconds after
# open — the "move" was already inside the entry bar and got booked as
# profit. No exit fills less than this many bars (of the trading timeframe)
# after entry.
MIN_HOLD_BARS = float(getattr(config, "MIN_HOLD_BARS", 1))
_TRADING_TIMEFRAME = getattr(config, "TRADING_TIMEFRAME", "5m")

_TF_UNIT_SECONDS = {"m": 60, "h": 3600, "d": 86400}


def _bar_seconds():
    try:
        return int(_TRADING_TIMEFRAME[:-1]) * _TF_UNIT_SECONDS[_TRADING_TIMEFRAME[-1]]
    except (ValueError, KeyError, IndexError):
        return 300  # unparseable label -> the firm's 5m default


def _opened_timestamp(pos):
    """opened_at is stored as SQLite datetime('now') (UTC, naive) — parse
    tolerantly and treat naive as UTC. None = unknown (legacy row): allow
    exits rather than freeze the position forever."""
    raw = pos.get("opened_at") or ""
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        try:
            dt = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


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

    def adjust_levels(self, position_id, new_sl=None, new_tp=None):
        """Steward adjustments with a hard invariant: risk may only shrink.
        SL can move toward price (tighten), never away; TP may move either
        way (extending a target adds no risk)."""
        row = fetchone("SELECT * FROM positions WHERE id=? AND status='open'", [position_id])
        if not row:
            return False
        pos = dict(row)
        updates, params = [], []
        if new_sl is not None and pos["stop_loss"]:
            tighter = (new_sl > pos["stop_loss"]) if pos["side"] == "BUY" else (new_sl < pos["stop_loss"])
            if tighter:
                updates.append("stop_loss=?")
                params.append(new_sl)
        if new_tp is not None and new_tp > 0:
            updates.append("take_profit=?")
            params.append(new_tp)
        if not updates:
            return False
        params.append(position_id)
        execute(f"UPDATE positions SET {', '.join(updates)}, updated_at=datetime('now') "
                "WHERE id=? AND status='open'", params)
        return True

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
        # Runner stop goes to entry + buffer (same convention as breakeven):
        # parked at exact entry, a stopped runner still loses the round-trip fee.
        if pos["side"] == "BUY":
            runner_sl = pos["entry_price"] * (1 + BREAKEVEN_BUFFER_PCT / 100)
            rem_pnl = (exit_price - pos["entry_price"]) * remaining
        else:
            runner_sl = pos["entry_price"] * (1 - BREAKEVEN_BUFFER_PCT / 100)
            rem_pnl = (pos["entry_price"] - exit_price) * remaining
        rem_pct = (rem_pnl / (pos["entry_price"] * remaining)) * 100 if pos["entry_price"] * remaining else 0
        execute("""
            UPDATE positions SET quantity=?, stop_loss=?, partial_taken=1,
                   current_price=?, pnl=?, pnl_pct=?, updated_at=?
            WHERE id=? AND status='open'
        """, [remaining, runner_sl, exit_price,
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
        now_ts = datetime.now(timezone.utc).timestamp()
        min_hold_s = MIN_HOLD_BARS * _bar_seconds()
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

            # Minimum-hold gate: no exit fills less than MIN_HOLD_BARS bars
            # after entry (price/peak/breakeven bookkeeping above still runs,
            # so the trail sees an honest peak history). Rows without a
            # parseable opened_at are treated as long-open.
            opened_ts = _opened_timestamp(pos)
            exits_allowed = not (min_hold_s > 0 and opened_ts is not None
                                 and now_ts - opened_ts < min_hold_s)

            # Scaled exit: at PARTIAL_TP_R x initial risk in profit, bank
            # PARTIAL_TP_FRACTION of the position and let the rest run from
            # breakeven — winners pay for the losers instead of being clipped.
            initial_risk = pos.get("initial_risk") or 0
            if (exits_allowed and PARTIAL_TP_ENABLED and initial_risk > 0 and pos["stop_loss"]
                    and not pos.get("partial_taken")):
                if pos["side"] == "BUY":
                    partial_price = pos["entry_price"] + initial_risk * PARTIAL_TP_R
                    partial_hit = price >= partial_price
                else:
                    partial_price = pos["entry_price"] - initial_risk * PARTIAL_TP_R
                    partial_hit = price <= partial_price
                if partial_hit:
                    # Fill AT the target like any resting limit — the check
                    # price can overshoot it (same rule as the full TP below).
                    result = self.close_partial(pos["id"], partial_price, PARTIAL_TP_FRACTION)
                    if result:
                        triggered.append(result)
                        pos["quantity"] = round(pos["quantity"] - result["qty"], 8)
                        buffer = 1 + BREAKEVEN_BUFFER_PCT / 100 if pos["side"] == "BUY" else 1 - BREAKEVEN_BUFFER_PCT / 100
                        pos["stop_loss"] = pos["entry_price"] * buffer
                        pos["partial_taken"] = 1

            # Breakeven: move stop_loss to entry + buffer at 1:1 risk-to-reward,
            # then lock it there — no further trailing — so the trade has room
            # to hit TP without getting choked on micro-pullbacks.
            if BREAKEVEN_ENABLED and pos["stop_loss"] and pos["entry_price"]:
                sl_dist = abs(pos["entry_price"] - pos["stop_loss"])
                activation_dist = sl_dist * (BREAKEVEN_ACTIVATION_PCT / 100)
                if pos["side"] == "BUY":
                    if price >= pos["entry_price"] + activation_dist and pos["stop_loss"] < pos["entry_price"]:
                        sl_buffer = pos["entry_price"] * (1 + BREAKEVEN_BUFFER_PCT / 100)
                        execute("""
                            UPDATE positions SET stop_loss=?, updated_at=datetime('now')
                            WHERE id=? AND status='open'
                        """, [sl_buffer, pos["id"]])
                        pos["stop_loss"] = sl_buffer
                else:
                    if price <= pos["entry_price"] - activation_dist and pos["stop_loss"] > pos["entry_price"]:
                        sl_buffer = pos["entry_price"] * (1 - BREAKEVEN_BUFFER_PCT / 100)
                        execute("""
                            UPDATE positions SET stop_loss=?, updated_at=datetime('now')
                            WHERE id=? AND status='open'
                        """, [sl_buffer, pos["id"]])
                        pos["stop_loss"] = sl_buffer

            if exits_allowed and pos["stop_loss"] and (
                (pos["side"] == "BUY" and price <= pos["stop_loss"]) or
                (pos["side"] == "SELL" and price >= pos["stop_loss"])
            ):
                # Stop-market fill: never better than the stop. If the check
                # price gapped through, the fill is the (worse) traded price;
                # adverse slippage applies on top either way. Previously the
                # exit booked `price` — better than the stop exactly when the
                # market had already gapped past it.
                if pos["side"] == "BUY":
                    sl_fill = min(price, pos["stop_loss"]) * (1 - SLIPPAGE_PCT / 100)
                else:
                    sl_fill = max(price, pos["stop_loss"]) * (1 + SLIPPAGE_PCT / 100)
                result = self.close_position(pos["id"], sl_fill, reason="stop_loss")
                if result:
                    triggered.append(result)
            elif exits_allowed and pos["take_profit"] and (
                (pos["side"] == "BUY" and price >= pos["take_profit"]) or
                (pos["side"] == "SELL" and price <= pos["take_profit"])
            ):
                # A resting limit at the TP fills AT the limit price, only
                # because the check price actually reached it — never at the
                # (better) overshoot price. Previously closing at `price`
                # booked better-than-limit fills on every overshoot.
                result = self.close_position(pos["id"], pos["take_profit"], reason="take_profit")
                if result:
                    triggered.append(result)
            elif self._trailing_stop_hit(pos["side"], pos["entry_price"], peak, price,
                                         pos.get("initial_risk") or 0):
                # Once SL has been moved to (or past) entry — whether by
                # breakeven, partial TP, or the R-trail itself — stop
                # trailing so the runner has room to breathe.
                sl_past_entry = (
                    (pos["side"] == "BUY" and pos["stop_loss"] >= pos["entry_price"]) or
                    (pos["side"] == "SELL" and pos["stop_loss"] <= pos["entry_price"])
                )
                if exits_allowed and not sl_past_entry:
                    # The trail executes as a stop-market order too: adverse
                    # slippage on the check price, never a free exact fill.
                    if pos["side"] == "BUY":
                        trail_fill = price * (1 - SLIPPAGE_PCT / 100)
                    else:
                        trail_fill = price * (1 + SLIPPAGE_PCT / 100)
                    result = self.close_position(pos["id"], trail_fill, reason="trailing_stop")
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

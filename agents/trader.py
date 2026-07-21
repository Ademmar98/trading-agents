import time

from config import BROKER_TYPE, BINANCE_API_KEY, BINANCE_API_SECRET, BINANCE_USE_TESTNET
from agents.base_agent import BaseAgent
from core.broker import PaperBroker
from core.binance_broker import BinanceBroker
from core.positions import PositionManager
from core.database import update_plan_status
from core import pending_orders
from config import USE_LIMIT_ENTRIES, LIMIT_ENTRY_EXT_PCT, LIMIT_ENTRY_ATR_MULT

# Reject fills when the market has run this far past the planned entry —
# the plan's SL/TP geometry no longer holds.
MAX_PRICE_DRIFT_PCT = 1.5


class Trader(BaseAgent):
    name = "trader"

    def __init__(self):
        super().__init__()
        if BROKER_TYPE == "binance":
            self.broker = BinanceBroker(BINANCE_API_KEY, BINANCE_API_SECRET, BINANCE_USE_TESTNET)
        else:
            self.broker = PaperBroker()
        self.pos_mgr = PositionManager()

    def run(self):
        self.log("Checking execution-approved trades")
        execution = self.memory.read("orders", "execution_plan") or {}
        analysis = self.memory.read("analyses", "market_scan") or {}
        all_analyses = analysis.get("all_analyses", {}) or {}
        all_prices = {s: d.get("price", 0) for s, d in all_analyses.items() if isinstance(d, dict)}
        orders_executed = []

        if execution.get("status") == "halted":
            self.log("Trading skipped: execution plan halted")
            self.memory.write("orders", "trade_log", {
                "orders": orders_executed,
                "status": "halted",
                "timestamp": time.time(),
            })
            return orders_executed

        orders = execution.get("orders", []) or []
        if not orders:
            self.log("No execution-approved orders available")
            self.memory.write("orders", "trade_log", {
                "orders": orders_executed,
                "status": "skipped",
                "timestamp": time.time(),
            })
            return orders_executed

        for planned in orders:
            if not planned.get("execution_ok", False):
                continue
            symbol = planned["symbol"]
            action = planned.get("action", "BUY")
            qty = planned.get("qty", 0)
            price = planned.get("price", 0)
            sl_price = planned.get("stop_loss", 0)
            tp_price = planned.get("take_profit", 0)

            if qty <= 0 or price <= 0:
                continue

            if self.pos_mgr.has_position(symbol):
                self.log(f"Skipping {symbol}: position already open")
                continue

            # Execute at the real market price: the planned entry is a pullback
            # target the market may never trade at, and paper-filling there
            # books a cost basis reality wouldn't allow.
            market_price = all_prices.get(symbol) or price
            drift_pct = abs(market_price - price) / price * 100
            if drift_pct > MAX_PRICE_DRIFT_PCT:
                self.log(f"Skipping {symbol}: market {drift_pct:.2f}% away from planned entry")
                continue

            # Real fills cross the spread: pay the ask on BUY, hit the bid on
            # SELL. Filling at mid overstates results by half the spread per leg.
            quote = all_analyses.get(symbol, {}) if isinstance(all_analyses.get(symbol), dict) else {}
            if action == "BUY":
                fill_ref = quote.get("ask") or market_price
            else:
                fill_ref = quote.get("bid") or market_price

            # Study-validated limit entry (research/limit_exec_2026_07): rest a
            # BUY limit LIMIT_ENTRY_ATR_MULT x ATR below price on EVERY buy —
            # best per-decision execution (maker fee + better fills). SL/TP
            # shift down by the same offset so the risk geometry is preserved
            # from the actual (lower) fill. Takes precedence over the VWAP
            # heuristic; the pending-order loop fills-through or expires it.
            atr_v = planned.get("atr") or (planned.get("indicators") or {}).get("atr") or 0
            if (USE_LIMIT_ENTRIES and action == "BUY" and LIMIT_ENTRY_ATR_MULT > 0
                    and atr_v > 0 and not pending_orders.open_pending(symbol)):
                limit_px = round(market_price - LIMIT_ENTRY_ATR_MULT * atr_v, 8)
                if limit_px > 0:
                    off = market_price - limit_px
                    pending_orders.place_limit(
                        symbol, limit_px, qty,
                        sl=round(sl_price - off, 8) if sl_price else 0,
                        tp=round(tp_price - off, 8) if tp_price else 0,
                        strategy="|".join(planned.get("strategies") or [""]))
                    self.log(f"BUY {symbol}: resting limit @ ${limit_px:g} "
                             f"({LIMIT_ENTRY_ATR_MULT}xATR below ${market_price:g})")
                    continue

            # Extended above session VWAP? (legacy heuristic, used when
            # LIMIT_ENTRY_ATR_MULT=0.) Rest a BUY limit at VWAP instead of
            # buying the local top; the pending-order loop fills or expires it.
            vwap_v = quote.get("vwap")
            if (USE_LIMIT_ENTRIES and action == "BUY" and vwap_v
                    and market_price > vwap_v * (1 + LIMIT_ENTRY_EXT_PCT / 100)
                    and not pending_orders.open_pending(symbol)):
                pending_orders.place_limit(
                    symbol, vwap_v, qty, sl=sl_price, tp=tp_price,
                    # All contributors to the signal, pipe-joined (see below).
                    strategy="|".join(planned.get("strategies") or [""]))
                self.log(f"BUY {symbol}: extended {((market_price/vwap_v)-1)*100:.2f}% over VWAP — resting limit @ ${vwap_v}")
                continue

            order = self.broker.place_order(symbol, action, qty, fill_ref, sl=sl_price, tp=tp_price)
            orders_executed.append(order)
            plan_id = planned.get("plan_id")
            strategies = planned.get("strategies") or []
            # Tag the position with EVERY strategy that contributed to the
            # winning combined signal (pipe-joined — one position per symbol
            # stays intact; the auditor/analytics split on "|" to score each
            # contributor). Previously only strategies[0] was recorded, so
            # co-contributors never accumulated a track record.
            strategy = "|".join(strategies)
            fill_price = order.get("price") or fill_ref
            fill_qty = order.get("quantity") or qty
            if order.get("status") == "filled":
                if plan_id:
                    update_plan_status(plan_id, "executed")
                self.pos_mgr.open_position(symbol, action, fill_qty, fill_price, sl=sl_price, tp=tp_price, strategy=strategy)
                self.notifier.on_trade({
                    "symbol": symbol, "side": action, "qty": fill_qty,
                    "price": fill_price, "stop_loss": sl_price, "take_profit": tp_price,
                    "status": "filled",
                })
            status = "FILLED" if order.get("status") == "filled" else "REJECTED"
            self.log(
                f"{action} {symbol} x{fill_qty:g} @ ${fill_price:.5f} "
                f"SL=${sl_price:.5f} TP=${tp_price:.5f} ({status})"
            )

        self.memory.write("orders", "trade_log", {
            "orders": orders_executed,
            "status": "completed",
            "timestamp": time.time(),
        })
        return orders_executed

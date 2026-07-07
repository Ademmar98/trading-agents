import time

from config import BROKER_TYPE, BINANCE_API_KEY, BINANCE_API_SECRET, BINANCE_USE_TESTNET, MT5_LOGIN, MT5_PASSWORD, MT5_SERVER
from agents.base_agent import BaseAgent
from core.broker import PaperBroker
from core.binance_broker import BinanceBroker
from core.mt5_broker import MetaQuotesBroker
from core.positions import PositionManager
from core.database import update_plan_status


class Trader(BaseAgent):
    name = "trader"

    def __init__(self):
        super().__init__()
        if BROKER_TYPE == "mt5":
            self.broker = MetaQuotesBroker(MT5_LOGIN, MT5_PASSWORD, MT5_SERVER)
        elif BROKER_TYPE == "binance":
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

            order = self.broker.place_order(symbol, action, qty, price, sl=sl_price, tp=tp_price)
            orders_executed.append(order)
            plan_id = planned.get("plan_id")
            strategies = planned.get("strategies") or []
            strategy = strategies[0] if strategies else ""
            if order.get("status") == "filled":
                if plan_id:
                    update_plan_status(plan_id, "executed")
                self.pos_mgr.open_position(symbol, action, qty, price, sl=sl_price, tp=tp_price, strategy=strategy)
                self.notifier.on_trade({
                    "symbol": symbol, "side": action, "qty": qty,
                    "price": price, "stop_loss": sl_price, "take_profit": tp_price,
                    "status": "filled",
                })
            status = "FILLED" if order.get("status") == "filled" else "REJECTED"
            self.log(
                f"{action} {qty} {symbol} @ ${price:.5f} "
                f"SL=${sl_price:.5f} TP=${tp_price:.5f} ({status})"
            )

        self.memory.write("orders", "trade_log", {
            "orders": orders_executed,
            "status": "completed",
            "timestamp": time.time(),
        })
        return orders_executed

import json
import time
import os
from datetime import datetime, timezone

from config import DATA_DIR, STOP_LOSS_PCT

MT5_AVAILABLE = False
try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    pass


class MetaQuotesBroker:
    def __init__(self, login=None, password=None, server=None):
        self.login = login or int(os.getenv("MT5_LOGIN", "0"))
        self.password = password or os.getenv("MT5_PASSWORD", "")
        self.server = server or os.getenv("MT5_SERVER", "")
        self.connected = False
        self.orders_dir = DATA_DIR / "orders"
        self.orders_dir.mkdir(parents=True, exist_ok=True)
        self._use_mt5 = False
        self._connect()

    def _connect(self):
        if not MT5_AVAILABLE:
            return
        init = mt5.initialize()
        if not init:
            return
        if self.login and self.password:
            logged = mt5.login(self.login, password=self.password, server=self.server)
            if logged:
                self.connected = True
                self._use_mt5 = True
                info = mt5.account_info()
                if info:
                    self._log(f"Connected to MT5: {info.name}, balance ${info.balance:.2f}")
            else:
                err = mt5.last_error()
                self._log(f"MT5 login failed: {err}")
        else:
            info = mt5.account_info()
            if info:
                self.connected = True
                self._use_mt5 = True
                self._log(f"MT5 terminal active: {info.name}, balance ${info.balance:.2f}")

    def _log(self, msg):
        from core.memory import SharedMemory
        try:
            SharedMemory().log("mt5", msg)
        except Exception:
            pass

    def place_order(self, symbol, side, quantity, price, order_type="market", sl=0, tp=0):
        order = {
            "symbol": symbol, "side": side.upper(), "quantity": quantity,
            "price": price, "type": order_type,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "id": str(int(time.time() * 1000)),
            "broker": "mt5",
        }
        if sl:
            order["stop_loss"] = sl
        if tp:
            order["take_profit"] = tp

        if self._use_mt5 and self.connected:
            import MetaTrader5 as mt5
            order_type_mt5 = mt5.ORDER_TYPE_BUY if side.upper() == "BUY" else mt5.ORDER_TYPE_SELL
            # convert units to MT5 lots (1 lot = 100000 base units for forex)
            try:
                si = mt5.symbol_info(symbol)
                if si:
                    lot_size = si.trade_contract_size or 100000
                    lot_qty = round(quantity / lot_size, 2)
                    lot_qty = max(si.volume_min, min(si.volume_max, lot_qty))
                    lot_qty = round(lot_qty / si.volume_step) * si.volume_step
                else:
                    lot_qty = round(quantity / 100000, 2)
            except Exception:
                lot_qty = round(quantity / 100000, 2)

            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": symbol,
                "volume": lot_qty,
                "type": order_type_mt5,
                "price": price,
                "deviation": 10,
                "magic": 234000,
                "comment": "trading-agents",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_IOC,
            }
            result = mt5.order_send(request)
            if result and result.retcode == mt5.TRADE_RETCODE_DONE:
                order["status"] = "filled"
                order["mt5_order"] = result.order
                order["broker"] = "mt5_live"
                self._log(f"MT5 order filled: {side} {lot_qty}L {symbol} @ {price}")
                # Mirror the live fill into the local ledger so cash/equity stay real
                from core.portfolio import load_portfolio, save_portfolio, apply_fill
                p = load_portfolio()
                apply_fill(p, symbol, side, quantity, price)
                p.trades.append(order)
                save_portfolio(p)
            else:
                code = result.retcode if result else "no_result"
                order["status"] = "rejected"
                order["reason"] = f"mt5_error_{code}_lot_{lot_qty}"
                self._log(f"MT5 order rejected ({code}) — fallback to paper: {side} {quantity}u {symbol}")
                from core.broker import PaperBroker
                pb = PaperBroker()
                order = pb.place_order(symbol, side, quantity, price, order_type, sl=sl, tp=tp)
                order["broker"] = "mt5_paper"
        else:
            from core.broker import PaperBroker
            pb = PaperBroker()
            order = pb.place_order(symbol, side, quantity, price, order_type, sl=sl, tp=tp)
            order["broker"] = "mt5_paper"

        self._save_order(order)
        return order

    def _save_order(self, order):
        f = self.orders_dir / f"{order['id']}.json"
        f.write_text(json.dumps(order, indent=2))

    def get_account_info(self):
        if self._use_mt5 and self.connected:
            import MetaTrader5 as mt5
            info = mt5.account_info()
            if info:
                return {
                    "balance": info.balance,
                    "equity": info.equity,
                    "margin": info.margin,
                    "margin_free": info.margin_free,
                    "margin_level": info.margin_level,
                    "name": info.name,
                    "server": info.server,
                    "currency": info.currency,
                    "leverage": info.leverage,
                }
        return None

    def check_stop_losses(self, prices):
        if self._use_mt5 and self.connected:
            import MetaTrader5 as mt5
            positions = mt5.positions_get()
            triggered = []
            if positions:
                for pos in positions:
                    price = prices.get(pos.symbol, {}).get("price", 0)
                    if not price:
                        continue
                    pnl_pct = (price - pos.price_open) / pos.price_open * 100
                    if pnl_pct <= -STOP_LOSS_PCT:
                        order = self.place_order(pos.symbol, "SELL", pos.volume, price)
                        order["trigger"] = "stop_loss"
                        triggered.append(order)
            return triggered
        # Paper exits are handled by PositionManager, not the broker
        return []

    def get_status(self):
        base = {"broker": "mt5", "connected": self.connected, "mode": "live" if (self._use_mt5 and self.connected) else "paper_fallback"}
        if self._use_mt5 and self.connected:
            info = self.get_account_info()
            if info:
                base.update(info)
        return base

    def shutdown(self):
        if self._use_mt5 and self.connected:
            try:
                import MetaTrader5 as mt5
                mt5.shutdown()
            except Exception:
                pass

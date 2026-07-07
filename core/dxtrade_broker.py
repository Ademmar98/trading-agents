import json
import time
from uuid import uuid4
from datetime import datetime, timezone

import requests

from config import DATA_DIR


class DXTradeBroker:
    def __init__(self, api_url="", username="", password="", domain="default"):
        self.api_url = api_url.rstrip("/")
        self.username = username
        self.password = password
        self.domain = domain
        self.session_token = None
        self.session_expires = 0
        self._account = None
        self.connected = False
        self._use_live = bool(api_url and username and password)
        self.orders_dir = DATA_DIR / "orders"
        self.orders_dir.mkdir(parents=True, exist_ok=True)
        if self._use_live:
            self._login()

    def _log(self, msg):
        from core.memory import SharedMemory
        try:
            SharedMemory().log("dxtrade", msg)
        except Exception:
            pass

    def _request(self, method, path, json_data=None):
        url = f"{self.api_url}{path}"
        headers = {"Content-Type": "application/json"}
        if self.session_token:
            headers["Authorization"] = f"DXAPI {self.session_token}"
        try:
            r = requests.request(method, url, headers=headers,
                                 json=json_data, timeout=15)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 429:
                time.sleep(1)
                r = requests.request(method, url, headers=headers,
                                     json=json_data, timeout=15)
                if r.status_code == 200:
                    return r.json()
            err_body = ""
            try:
                err_body = r.json()
            except Exception:
                err_body = r.text[:200]
            self._log(f"DXtrade API {method} {path} -> {r.status_code}: {err_body}")
            return {"error": str(r.status_code), "detail": err_body}
        except Exception as e:
            self._log(f"DXtrade API {method} {path} error: {e}")
            return {"error": str(e)}

    def _login(self):
        data = {"username": self.username, "domain": self.domain, "password": self.password}
        result = self._request("POST", "/login", json_data=data)
        if result.get("sessionToken"):
            self.session_token = result["sessionToken"]
            self.session_expires = time.time() + (result.get("timeout", 3600) * 60)
            self.connected = True
            self._log(f"Logged in as {self.username}")
            self._load_account()
            return True
        self._log(f"Login failed: {result}")
        self.connected = False
        return False

    def _ensure_session(self):
        if not self._use_live:
            return False
        if self.session_token and time.time() < self.session_expires - 60:
            return True
        if self.session_token:
            self._log("Session expired, re-authenticating")
        return self._login()

    def _load_account(self):
        result = self._request("GET", f"/users/{self.username}")
        details = (result.get("userDetails") or [None])[0]
        if details and details.get("accounts"):
            self._account = details["accounts"][0]["account"]
            self._log(f"Using account: {self._account}")
        return self._account

    def place_order(self, symbol, side, quantity, price, order_type="market", sl=0, tp=0):
        order = {
            "symbol": symbol,
            "side": side.upper(),
            "quantity": quantity,
            "price": price,
            "type": order_type,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "id": str(int(time.time() * 1000)),
            "broker": "dxtrade",
        }
        if sl:
            order["stop_loss"] = sl
        if tp:
            order["take_profit"] = tp

        if not self._ensure_session():
            return self._paper_fallback(order, "not connected")

        order_code = f"bot_{uuid4().hex[:12]}"
        payload = {
            "orderCode": order_code,
            "type": "MARKET",
            "instrument": symbol,
            "quantity": quantity,
            "side": side.upper(),
            "tif": "GTC",
        }
        result = self._request("POST", f"/accounts/{self._account}/orders", json_data=payload)
        if result.get("error"):
            return self._paper_fallback(order, result.get("detail", result["error"]))

        position_code = result.get("orderId")
        if position_code:
            order["status"] = "filled"
            order["dxtrade_order"] = position_code
            order["broker"] = "dxtrade_live"

            if tp:
                self._place_tp_sl(symbol, quantity, side, tp, position_code, is_tp=True)
            if sl:
                self._place_tp_sl(symbol, quantity, side, sl, position_code, is_tp=False)

            from core.portfolio import load_portfolio, save_portfolio, apply_fill
            p = load_portfolio()
            apply_fill(p, symbol, side, quantity, price)
            p.trades.append(order)
            save_portfolio(p)

            self._log(f"DXtrade filled: {side} {quantity} {symbol} (id={position_code})")
        else:
            return self._paper_fallback(order, f"no orderId in response: {result}")

        self._save_order(order)
        return order

    def _place_tp_sl(self, symbol, quantity, side, price, position_code, is_tp=True):
        order_type = "LIMIT" if is_tp else "STOP"
        close_side = "SELL" if side.upper() == "BUY" else "BUY"
        payload = {
            "orderCode": f"bot_{uuid4().hex[:12]}",
            "type": order_type,
            "instrument": symbol,
            "quantity": quantity,
            "side": close_side,
            "positionEffect": "CLOSE",
            "positionCode": position_code,
            "tif": "GTC",
        }
        if is_tp:
            payload["limitPrice"] = price
        else:
            payload["stopPrice"] = price
        label = "TP" if is_tp else "SL"
        result = self._request("POST", f"/accounts/{self._account}/orders", json_data=payload)
        if result.get("orderId"):
            self._log(f"DXtrade {label} set: {price} on {position_code}")
        else:
            self._log(f"DXtrade {label} failed: {result}")
        return result

    def _paper_fallback(self, order, reason):
        order["status"] = "rejected"
        order["reason"] = str(reason)
        self._log(f"DXtrade fallback to paper: {order.get('side')} {order.get('quantity')} {order.get('symbol')} ({reason})")
        from core.broker import PaperBroker
        pb = PaperBroker()
        fallback = pb.place_order(
            order["symbol"], order["side"], order["quantity"],
            order["price"], order.get("type", "market"),
            sl=order.get("stop_loss", 0), tp=order.get("take_profit", 0)
        )
        fallback["broker"] = "dxtrade_paper"
        self._save_order(fallback)
        return fallback

    def get_positions(self):
        if not self._ensure_session():
            return []
        result = self._request("GET", f"/accounts/{self._account}/positions")
        return result.get("positions", [])

    def check_stop_losses(self, prices):
        if not self._use_live:
            from core.broker import PaperBroker
            return PaperBroker().check_stop_losses(prices)
        positions = self.get_positions()
        triggered = []
        for pos in positions:
            sym = pos.get("symbol")
            price = prices.get(sym, {}).get("price", 0)
            if not price:
                continue
            pos_side = pos.get("side", "").upper()
            entry = pos.get("openPrice", 0)
            if not entry:
                continue
            qty = pos.get("quantity", 0)
            if pos_side == "BUY":
                pnl_pct = (price - entry) / entry * 100
                close_side = "SELL"
            else:
                pnl_pct = (entry - price) / entry * 100
                close_side = "BUY"
            from config import STOP_LOSS_PCT
            if pnl_pct <= -STOP_LOSS_PCT:
                order = self.place_order(sym, close_side, qty, price)
                order["trigger"] = "stop_loss"
                triggered.append(order)
        return triggered

    def get_account_info(self):
        if not self._ensure_session():
            return None
        result = self._request("GET", f"/accounts/{self._account}")
        return result

    def get_status(self):
        if not self._use_live or not self._ensure_session():
            from core.portfolio import load_portfolio
            p = load_portfolio()
            return {
                "cash": round(p.cash, 2),
                "positions_value": round(p.positions_value, 2),
                "equity": round(p.equity, 2),
                "total_pnl": round(p.total_pnl, 2),
                "total_pnl_pct": round(p.total_pnl_pct, 2),
                "exposure_pct": round(p.exposure_pct, 2),
                "positions_count": len(p.positions),
                "connected": self.connected,
                "mode": "paper_fallback" if self._use_live else "paper",
                "broker": "dxtrade",
            }

        result = self._request("GET", f"/accounts/{self._account}")
        if result.get("error"):
            from core.portfolio import load_portfolio
            p = load_portfolio()
            return {
                "cash": round(p.cash, 2),
                "positions_value": round(p.positions_value, 2),
                "equity": round(p.equity, 2),
                "total_pnl": round(p.total_pnl, 2),
                "total_pnl_pct": round(p.total_pnl_pct, 2),
                "exposure_pct": round(p.exposure_pct, 2),
                "positions_count": len(p.positions),
                "connected": self.connected,
                "mode": "paper_fallback",
                "broker": "dxtrade",
            }

        balance = result.get("balance", 0)
        equity = result.get("equity", result.get("balance", 0))
        margin = result.get("marginUsed", 0)
        pnl = result.get("unrealizedPnl", 0)
        positions = result.get("positions", []) or result.get("openPositions", []) or []
        # Fall back to local portfolio for position count accuracy
        from core.portfolio import load_portfolio
        p = load_portfolio()
        return {
            "cash": round(float(balance) - float(margin), 2),
            "positions_value": round(float(equity) - float(balance) + float(margin), 2),
            "equity": round(float(equity), 2),
            "total_pnl": round(float(pnl), 2),
            "total_pnl_pct": round((float(pnl) / float(balance) * 100) if float(balance) else 0, 2),
            "exposure_pct": round((float(margin) / float(equity) * 100) if float(equity) else 0, 2),
            "positions_count": len(positions),
            "connected": self.connected,
            "mode": "live",
            "broker": "dxtrade",
        }

    def _save_order(self, order):
        f = self.orders_dir / f"{order['id']}.json"
        f.write_text(json.dumps(order, indent=2))

    def shutdown(self):
        self.session_token = None
        self.connected = False

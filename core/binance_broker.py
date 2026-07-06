import json
import time
import hmac
import hashlib
import requests
from datetime import datetime, timezone
from urllib.parse import urlencode

from config import DATA_DIR

BINANCE_API = "https://api.binance.com"
BINANCE_TESTNET = "https://testnet.binance.vision"


class BinanceBroker:
    def __init__(self, api_key="", api_secret="", testnet=True):
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = BINANCE_TESTNET if testnet else BINANCE_API
        self.connected = False
        self._use_live = bool(api_key and api_secret)
        self.orders_dir = DATA_DIR / "orders"
        self.orders_dir.mkdir(parents=True, exist_ok=True)
        self._test()

    def _test(self):
        if not self._use_live:
            return
        try:
            r = requests.get(f"{self.base_url}/api/v3/ping", timeout=5)
            if r.status_code == 200:
                self.connected = True
                self._log("Binance testnet connected")
        except Exception as e:
            self._log(f"Binance connection failed: {e}")

    def _log(self, msg):
        from core.memory import SharedMemory
        try:
            SharedMemory().log("binance", msg)
        except Exception:
            pass

    def _sign(self, params):
        params["timestamp"] = int(time.time() * 1000)
        query = urlencode(params)
        signature = hmac.new(self.api_secret.encode(), query.encode(), hashlib.sha256).hexdigest()
        params["signature"] = signature
        return params

    def _request(self, method, path, signed=False, params=None):
        url = f"{self.base_url}{path}"
        headers = {"X-MBX-APIKEY": self.api_key} if self._use_live else {}
        if signed and self._use_live:
            params = self._sign(params or {})
        try:
            r = requests.request(method, url, headers=headers, params=params, timeout=10)
            return r.json()
        except Exception as e:
            return {"error": str(e)}

    def _to_binance_symbol(self, symbol):
        s = symbol.replace("/", "").upper()
        if s == "BTCUSD":
            return "BTCUSDT"
        if s == "ETHUSD":
            return "ETHUSDT"
        if s == "SOLUSD":
            return "SOLUSDT"
        if s.endswith("USD") and not s.endswith("USDT"):
            return s + "T"
        return s

    def _from_binance_symbol(self, symbol):
        if symbol.endswith("USDT"):
            return symbol[:-4] + "/USD"
        if symbol.endswith("BUSD"):
            return symbol[:-4] + "/USD"
        return symbol

    def place_order(self, symbol, side, quantity, price, order_type="market", sl=0, tp=0):
        bsym = self._to_binance_symbol(symbol)
        order = {
            "symbol": symbol, "side": side.upper(), "quantity": quantity,
            "price": price, "type": order_type,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "id": str(int(time.time() * 1000)),
            "broker": "binance",
        }
        if sl:
            order["stop_loss"] = sl
        if tp:
            order["take_profit"] = tp

        if self._use_live and self.connected:
            rounded = self._round_lot(bsym, quantity)
            params = {
                "symbol": bsym,
                "side": side.upper(),
                "type": "MARKET",
                "quoteOrderQty": round(quantity * price, 2),
                "newOrderRespType": "FULL",
            }
            result = self._request("POST", "/api/v3/order", signed=True, params=params)
            if result.get("orderId"):
                order["status"] = "filled"
                order["binance_order"] = result["orderId"]
                order["broker"] = "binance_live"
                fills = result.get("fills", [])
                if fills:
                    avg_price = sum(float(f["price"]) * float(f["qty"]) for f in fills) / sum(float(f["qty"]) for f in fills)
                    order["price"] = round(avg_price, 6)
                    order["quantity"] = sum(float(f["qty"]) for f in fills)
                self._log(f"Binance filled: {side} {bsym} qty={order['quantity']}")
            else:
                order["status"] = "rejected"
                order["reason"] = str(result.get("msg", result.get("error", "unknown")))
                self._log(f"Binance rejected ({order['reason']}) — fallback to paper: {side} {quantity} {symbol}")
                from core.broker import PaperBroker
                pb = PaperBroker()
                order = pb.place_order(symbol, side, quantity, price, order_type, sl=sl, tp=tp)
                order["broker"] = "binance_paper"
        else:
            from core.broker import PaperBroker
            pb = PaperBroker()
            order = pb.place_order(symbol, side, quantity, price, order_type, sl=sl, tp=tp)
            order["broker"] = "binance_paper"

        self._save_order(order)
        return order

    def _round_lot(self, symbol, quantity):
        info = self._get_symbol_info(symbol)
        if info:
            step = float(info.get("lotSize", {}).get("stepSize", "0.00000100"))
            if step > 0:
                return round(quantity // step * step, 8)
        return round(quantity, 6)

    def _get_symbol_info(self, symbol):
        exchange = self._request("GET", "/api/v3/exchangeInfo")
        if "symbols" in exchange:
            for s in exchange["symbols"]:
                if s["symbol"] == symbol:
                    filters = {f["filterType"]: f for f in s.get("filters", [])}
                    return filters
        return None

    def _save_order(self, order):
        f = self.orders_dir / f"{order['id']}.json"
        f.write_text(json.dumps(order, indent=2))

    def get_account_info(self):
        if not self._use_live:
            return {"balance": 0, "equity": 0, "name": "binance_paper"}
        result = self._request("GET", "/api/v3/account", signed=True)
        if "balances" in result:
            usdt = next((b for b in result["balances"] if b["asset"] == "USDT"), {})
            free = float(usdt.get("free", 0))
            locked = float(usdt.get("locked", 0))
            return {
                "balance": free + locked,
                "equity": free + locked,
                "free": free,
                "name": "Binance Testnet",
                "currency": "USDT",
            }
        return None

    def fetch_prices(self, symbols=None):
        symbols = symbols or []
        result = {}
        bsyms = [self._to_binance_symbol(s) for s in symbols]
        try:
            r = requests.get(f"{self.base_url}/api/v3/ticker/24hr", timeout=10)
            data = r.json() if isinstance(r.json(), list) else []
            tickers = {t["symbol"]: t for t in data}
            for sym, bsym in zip(symbols, bsyms):
                t = tickers.get(bsym)
                if t:
                    result[sym] = {
                        "price": float(t["lastPrice"]),
                        "change_24h": float(t["priceChangePercent"]),
                        "volume_24h": float(t["quoteVolume"]),
                        "type": "crypto",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "bid": float(t.get("bidPrice", 0)),
                        "ask": float(t.get("askPrice", 0)),
                    }
        except Exception:
            pass
        return result

    def get_klines(self, symbol, interval="1d", limit=100):
        bsym = self._to_binance_symbol(symbol)
        try:
            r = requests.get(f"{self.base_url}/api/v3/klines", params={
                "symbol": bsym, "interval": interval, "limit": limit
            }, timeout=10)
            data = r.json()
            ohlc = []
            for k in data:
                ohlc.append({
                    "date": datetime.fromtimestamp(k[0] / 1000, tz=timezone.utc).isoformat(),
                    "open": float(k[1]), "high": float(k[2]), "low": float(k[3]),
                    "close": float(k[4]), "volume": float(k[5]), "ts": k[0] // 1000
                })
            return ohlc
        except Exception:
            return []

    def check_stop_losses(self, prices):
        from core.broker import PaperBroker
        pb = PaperBroker()
        return pb.check_stop_losses(prices)

    def get_status(self):
        info = self.get_account_info()
        base = {"broker": "binance", "connected": self.connected,
                "mode": "live" if (self._use_live and self.connected) else "paper_fallback"}
        if info:
            base.update(info)
        return base

    def shutdown(self):
        pass

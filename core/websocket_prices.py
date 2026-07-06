import threading
import time
from datetime import datetime, timezone

import requests

from config import WATCHED_SYMBOLS

LIVE_PRICES = {}
_lock = threading.Lock()
_running = False
_thread = None


def _to_binance_symbol(symbol):
    s = symbol.replace("/", "").upper()
    if s.endswith("USD") and not s.endswith("USDT"):
        return s + "T"
    return s


def update_price(symbol, price, bid=0, ask=0, change=0, volume=0):
    with _lock:
        LIVE_PRICES[symbol] = {
            "price": price,
            "bid": bid,
            "ask": ask,
            "change_24h": change,
            "volume_24h": volume,
            "type": "crypto",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }


def get_price(symbol):
    with _lock:
        return LIVE_PRICES.get(symbol)


def get_all_prices():
    with _lock:
        return dict(LIVE_PRICES)


def _poll_loop():
    symbols = [s.strip() for s in WATCHED_SYMBOLS if s.strip()]
    bsyms = [_to_binance_symbol(s).lower() for s in symbols]

    while _running:
        try:
            r = requests.get("https://api.binance.com/api/v3/ticker/24hr", timeout=5)
            data = r.json() if isinstance(r.json(), list) else []
            tickers = {t["symbol"]: t for t in data}
            for sym, bsym in zip(symbols, bsyms):
                t = tickers.get(bsym.upper())
                if t:
                    update_price(
                        sym,
                        float(t["lastPrice"]),
                        bid=float(t.get("bidPrice", 0)),
                        ask=float(t.get("askPrice", 0)),
                        change=float(t.get("priceChangePercent", 0)),
                        volume=float(t.get("quoteVolume", 0)),
                    )
        except Exception:
            pass
        for _ in range(10):
            if not _running:
                break
            time.sleep(0.2)


def start(testnet=True):
    global _running, _thread
    if _running:
        return
    _running = True
    _thread = threading.Thread(target=_poll_loop, daemon=True, name="price-poll")
    _thread.start()


def stop():
    global _running
    _running = False

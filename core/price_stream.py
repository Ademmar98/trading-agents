import json
import time
import threading
from datetime import datetime, timezone

from config import WATCHED_SYMBOLS, ALPACA_API_KEY, ALPACA_SECRET_KEY
from core.memory import SharedMemory

_STREAM_THREAD = None
_RUNNING = False


def _binance_stream(symbols):
    import asyncio
    try:
        import websockets
    except ImportError:
        return
    streams = []
    for sym in symbols:
        bsym = sym.replace("/", "").lower() + "@trade"
        streams.append(bsym)
    url = f"wss://stream.binance.com:9443/ws/{'/'.join(streams)}" if len(streams) <= 200 else None
    if not url:
        return

    async def _listen():
        async with websockets.connect(url, ping_interval=30) as ws:
            while _RUNNING:
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=30)
                    data = json.loads(msg)
                    sym = data.get("s", "")
                    price = float(data.get("p", 0))
                    ts = data.get("T", 0)
                    if sym and price:
                        _update_price(sym, price, ts)
                except asyncio.TimeoutError:
                    await ws.ping()
                except Exception:
                    pass

    try:
        asyncio.run(_listen())
    except Exception:
        pass


def _update_price(raw_symbol, price, ts_ms):
    sym = raw_symbol.replace("USDT", "/USD")
    mem = SharedMemory()
    stream = mem.read("reports", "price_stream") or {"prices": {}, "timestamp": 0}
    stream["prices"][sym] = {"price": price, "timestamp": ts_ms / 1000 if ts_ms else time.time()}
    stream["_updated"] = time.time()
    mem.write("reports", "price_stream", stream)


def start(symbols=None):
    global _STREAM_THREAD, _RUNNING
    if _STREAM_THREAD and _STREAM_THREAD.is_alive():
        return
    symbols = symbols or [s for s in WATCHED_SYMBOLS if "/" in s]
    _RUNNING = True
    _STREAM_THREAD = threading.Thread(
        target=_binance_stream, args=(symbols,), daemon=True, name="price-stream"
    )
    _STREAM_THREAD.start()


def stop():
    global _RUNNING
    _RUNNING = False


def get_latest_prices():
    mem = SharedMemory()
    stream = mem.read("reports", "price_stream")
    if stream and time.time() - stream.get("_updated", 0) < 120:
        return stream.get("prices", {})
    return {}

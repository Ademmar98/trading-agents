import time
from datetime import datetime, timezone

import requests

from config import (
    WATCHED_SYMBOLS,
    ALPACA_API_KEY, ALPACA_SECRET_KEY,
    BROKER_TYPE,
)

BINANCE_BASE = "https://api.binance.com"
BINANCE_TESTNET = "https://testnet.binance.vision"
BINANCE_INTERVALS = {"1m", "5m", "15m", "30m", "1h", "4h", "1d", "1w"}
ALPACA_AVAILABLE = bool(ALPACA_API_KEY and ALPACA_SECRET_KEY)

_ALPACA_CLIENT = None


def _get_alpaca():
    global _ALPACA_CLIENT
    if _ALPACA_CLIENT is None and ALPACA_AVAILABLE:
        try:
            from alpaca.data import CryptoHistoricalDataClient
            _ALPACA_CLIENT = {
                "crypto": CryptoHistoricalDataClient(ALPACA_API_KEY, ALPACA_SECRET_KEY),
            }
        except ImportError:
            pass
    return _ALPACA_CLIENT


def _to_binance_symbol(symbol):
    s = symbol.replace("/", "").upper()
    if s.endswith("USD") and not s.endswith("USDT"):
        return s + "T"
    return s


def _from_binance_symbol(bsym):
    sym = bsym.replace("USDT", "/USD").replace("BUSD", "/USD")
    if sym.endswith("/USD"):
        return sym
    return bsym


def fetch_binance_usdt_pairs(testnet=True):
    url = f"{BINANCE_TESTNET if testnet else BINANCE_BASE}/api/v3/exchangeInfo"
    try:
        r = requests.get(url, timeout=15)
        data = r.json()
        symbols = data.get("symbols", [])
        usdt = []
        for s in symbols:
            if s.get("quoteAsset") == "USDT" and s.get("status") == "TRADING":
                usdt.append(_from_binance_symbol(s["symbol"]))
        return sorted(usdt)
    except Exception:
        return []


def _is_crypto(symbol):
    return "/" in symbol


def fetch_binance_klines(symbol, interval="1d", limit=100):
    bsym = _to_binance_symbol(symbol)
    try:
        params = {"symbol": bsym, "interval": interval, "limit": limit}
        r = requests.get(f"{BINANCE_BASE}/api/v3/klines", params=params, timeout=15)
        data = r.json()
        if not isinstance(data, list):
            return []
        return [{
            "date": datetime.fromtimestamp(k[0] / 1000, tz=timezone.utc).isoformat(),
            "open": float(k[1]), "high": float(k[2]), "low": float(k[3]),
            "close": float(k[4]), "volume": float(k[5]), "ts": k[0] // 1000,
        } for k in data]
    except Exception:
        return []


# Crypto.com Exchange public API — keyless, exchange-grade crypto data.
# Fallback for Binance klines where Binance is geo-blocked (e.g. US VPS).
_CRYPTOCOM_TIMEFRAMES = {"1m": "M1", "5m": "M5", "15m": "M15", "30m": "M30",
                         "1h": "H1", "4h": "H4", "1d": "D1"}


def fetch_cryptocom_ohlc(symbol, interval="1d", limit=100):
    tf = _CRYPTOCOM_TIMEFRAMES.get(interval)
    if not tf or not _is_crypto(symbol):
        return []
    inst = symbol.replace("/", "_")  # BTC/USD -> BTC_USD (spot naming)
    try:
        r = requests.get(
            "https://api.crypto.com/exchange/v1/public/get-candlestick",
            params={"instrument_name": inst, "timeframe": tf, "count": min(limit, 300)},
            timeout=15,
        )
        bars = ((r.json().get("result") or {}).get("data")) or []
        return [{
            "date": datetime.fromtimestamp(b["t"] / 1000, tz=timezone.utc).isoformat(),
            "open": float(b["o"]), "high": float(b["h"]),
            "low": float(b["l"]), "close": float(b["c"]),
            "volume": float(b.get("v") or 0), "ts": b["t"] // 1000,
        } for b in bars[-limit:]]
    except Exception:
        return []


def fetch_alpaca_bars(symbol, interval="1d", limit=100):
    client = _get_alpaca()
    if not client or not _is_crypto(symbol):
        return None
    try:
        from alpaca.data.requests import CryptoBarsRequest
        from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
        tf_map = {
            "1m": TimeFrame(1, TimeFrameUnit.Minute),
            "5m": TimeFrame(5, TimeFrameUnit.Minute),
            "15m": TimeFrame(15, TimeFrameUnit.Minute),
            "1h": TimeFrame(1, TimeFrameUnit.Hour),
            "4h": TimeFrame(4, TimeFrameUnit.Hour),
            "1d": TimeFrame(1, TimeFrameUnit.Day),
        }
        tf = tf_map.get(interval, TimeFrame(1, TimeFrameUnit.Day))
        req = CryptoBarsRequest(symbol_or_symbols=[symbol.replace("/", "")], timeframe=tf, limit=limit)
        bars = client["crypto"].get_crypto_bars(req)
        df = bars.df
        if df.empty:
            return []
        ohlc = []
        for idx, row in df.iterrows():
            ts = idx[1] if isinstance(idx, tuple) else idx
            if isinstance(ts, datetime):
                ts_epoch = int(ts.timestamp())
            else:
                ts_epoch = int(time.mktime(ts.timetuple())) if hasattr(ts, "timetuple") else int(ts)
            ohlc.append({
                "date": ts.isoformat() if isinstance(ts, datetime) else str(ts),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": float(row["volume"]),
                "ts": ts_epoch,
            })
        return ohlc
    except Exception:
        return None


def fetch_ohlc(symbol, interval="1d", limit=100):
    if not _is_crypto(symbol):
        return []
    alpaca_result = fetch_alpaca_bars(symbol, interval, limit) if ALPACA_AVAILABLE else None
    if alpaca_result:
        return alpaca_result
    result = fetch_binance_klines(symbol, interval, limit)
    # Crypto.com Exchange as keyless second source when Binance fails/geo-blocks
    if not result:
        result = fetch_cryptocom_ohlc(symbol, interval, limit)
    return result


def fetch_current_price(symbol):
    if not _is_crypto(symbol):
        return 0
    bsym = _to_binance_symbol(symbol)
    try:
        r = requests.get(f"{BINANCE_BASE}/api/v3/ticker/price", params={"symbol": bsym}, timeout=10)
        return float(r.json()["price"])
    except Exception:
        pass
    cg_id = symbol.split("/")[0].lower()
    try:
        r = requests.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={"ids": cg_id, "vs_currencies": "usd"},
            timeout=10,
        )
        return r.json().get(cg_id, {}).get("usd", 0)
    except Exception:
        pass
    return 0


def fetch_prices(symbols=None):
    symbols = symbols or WATCHED_SYMBOLS
    crypto = [s for s in symbols if _is_crypto(s)]
    if not crypto:
        return {}
    if BROKER_TYPE == "binance" or not ALPACA_AVAILABLE:
        return _fetch_binance_tickers(crypto)
    return _fetch_combined_crypto_prices(crypto)


def _fetch_binance_tickers(symbols):
    try:
        r = requests.get(f"{BINANCE_BASE}/api/v3/ticker/24hr", timeout=10)
        data = r.json() if isinstance(r.json(), list) else []
        tickers = {t["symbol"]: t for t in data}
        result = {}
        for sym in symbols:
            bsym = _to_binance_symbol(sym)
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
        return result
    except Exception:
        return {}


def _fetch_combined_crypto_prices(symbols):
    result = {}
    alpaca = _get_alpaca()
    if alpaca:
        try:
            from alpaca.data.requests import CryptoLatestQuoteRequest
            req = CryptoLatestQuoteRequest(symbol_or_symbols=[s.replace("/", "") for s in symbols])
            quotes = alpaca["crypto"].get_crypto_latest_quote(req)
            for sym in symbols:
                q = quotes.get(sym.replace("/", ""))
                if q:
                    price = (q.ask_price + q.bid_price) / 2 if q.ask_price and q.bid_price else (q.ask_price or q.bid_price)
                    result[sym] = {
                        "price": float(price) if price else 0,
                        "change_24h": 0,
                        "volume_24h": 0,
                        "type": "crypto",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "bid": float(q.bid_price) if q.bid_price else 0,
                        "ask": float(q.ask_price) if q.ask_price else 0,
                    }
        except Exception:
            pass
    remaining = [s for s in symbols if s not in result]
    if remaining:
        result.update(_fetch_binance_tickers(remaining))
    return result

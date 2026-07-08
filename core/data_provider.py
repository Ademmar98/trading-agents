import time
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

from config import (
    WATCHED_SYMBOLS, BINANCE_API_KEY, BINANCE_API_SECRET,
    ALPACA_API_KEY, ALPACA_SECRET_KEY, ALPACA_PAPER,
    BROKER_TYPE,
)

BINANCE_BASE = "https://api.binance.com"
BINANCE_INTERVALS = {"1m", "5m", "15m", "30m", "1h", "4h", "1d", "1w"}
ALPACA_AVAILABLE = bool(ALPACA_API_KEY and ALPACA_SECRET_KEY)

_ALPACA_CLIENT = None


def _get_alpaca():
    global _ALPACA_CLIENT
    if _ALPACA_CLIENT is None and ALPACA_AVAILABLE:
        try:
            from alpaca.data import StockHistoricalDataClient
            from alpaca.data import CryptoHistoricalDataClient
            _ALPACA_CLIENT = {
                "stock": StockHistoricalDataClient(ALPACA_API_KEY, ALPACA_SECRET_KEY),
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


def _is_crypto(symbol):
    return "/" in symbol


def _is_stock(symbol):
    return "/" not in symbol and symbol.isalpha() and len(symbol) <= 5


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


def fetch_yahoo_ohlc(symbol, interval="1d", days=100):
    if _is_crypto(symbol):
        yahoo_sym = symbol.replace("/", "-")
    else:
        yahoo_sym = symbol
        if _is_stock(symbol):
            yahoo_sym = symbol
    range_map = {"1m": "1d", "5m": "5d", "15m": "1mo", "1h": "3mo", "4h": "6mo", "1d": f"{days}d"}
    yahoo_interval = interval
    if interval == "4h":
        yahoo_interval = "1h"
    y_range = range_map.get(interval, f"{days}d")
    try:
        r = requests.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{yahoo_sym}",
            params={"range": y_range, "interval": yahoo_interval},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=15,
        )
        data = r.json()
        result = data.get("chart", {}).get("result", [{}])[0]
        quotes = result.get("indicators", {}).get("quote", [{}])[0]
        timestamps = result.get("timestamp", [])
        ohlc = []
        for i, ts in enumerate(timestamps):
            ohlc.append({
                "date": datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(),
                "open": quotes.get("open", [None])[i] if quotes.get("open") else None,
                "high": quotes.get("high", [None])[i] if quotes.get("high") else None,
                "low": quotes.get("low", [None])[i] if quotes.get("low") else None,
                "close": quotes.get("close", [None])[i] if quotes.get("close") else None,
                "volume": quotes.get("volume", [0])[i] if quotes.get("volume") else 0,
                "ts": ts,
            })
        return [c for c in ohlc if c["close"] is not None]
    except Exception:
        return []


def fetch_alpaca_bars(symbol, interval="1d", limit=100):
    client = _get_alpaca()
    if not client:
        return None
    try:
        from alpaca.data.requests import StockBarsRequest, CryptoBarsRequest
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
        if _is_crypto(symbol):
            req = CryptoBarsRequest(symbol_or_symbols=[symbol.replace("/", "")], timeframe=tf, limit=limit)
            bars = client["crypto"].get_crypto_bars(req)
        else:
            req = StockBarsRequest(symbol_or_symbols=[symbol], timeframe=tf, limit=limit, feed="iex")
            bars = client["stock"].get_stock_bars(req)
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
    if _is_crypto(symbol):
        if BROKER_TYPE == "binance":
            return fetch_binance_klines(symbol, interval, limit)
        alpaca_result = fetch_alpaca_bars(symbol, interval, limit) if ALPACA_AVAILABLE else None
        if alpaca_result:
            return alpaca_result
        if interval == "1d":
            return fetch_binance_klines(symbol, interval, limit)
        return None
    if _is_stock(symbol):
        alpaca_result = fetch_alpaca_bars(symbol, interval, limit) if ALPACA_AVAILABLE else None
        if alpaca_result:
            return alpaca_result
        days = limit
        if interval == "1d":
            days = limit
        elif interval == "1h":
            days = limit
        return fetch_yahoo_ohlc(symbol, interval, days=days)
    return fetch_yahoo_ohlc(symbol, interval, days=limit)


def fetch_current_price(symbol):
    if _is_crypto(symbol):
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
    elif _is_stock(symbol):
        try:
            r = requests.get(
                f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
                params={"range": "1d", "interval": "1d"},
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=10,
            )
            meta = r.json().get("chart", {}).get("result", [{}])[0].get("meta", {})
            return meta.get("regularMarketPrice", 0)
        except Exception:
            pass
    return 0


def fetch_prices(symbols=None):
    symbols = symbols or WATCHED_SYMBOLS
    result = {}
    crypto = [s for s in symbols if _is_crypto(s)]
    stocks = [s for s in symbols if _is_stock(s)]
    if crypto:
        if BROKER_TYPE == "binance" or not ALPACA_AVAILABLE:
            result.update(_fetch_binance_tickers(crypto))
        else:
            result.update(_fetch_combined_crypto_prices(crypto))
    if stocks:
        if ALPACA_AVAILABLE:
            result.update(_fetch_alpaca_stock_prices(stocks))
        else:
            result.update(_fetch_yahoo_stock_prices(stocks))
    others = [s for s in symbols if s not in result and s not in crypto and s not in stocks]
    for sym in others:
        if _is_crypto(sym):
            continue
        result[sym] = {"price": fetch_current_price(sym), "change_24h": 0, "volume_24h": 0, "type": "other"}
    return result


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


def _fetch_alpaca_stock_prices(symbols):
    client = _get_alpaca()
    if not client:
        return {}
    try:
        from alpaca.data.requests import StockLatestQuoteRequest
        req = StockLatestQuoteRequest(symbol_or_symbols=symbols, feed="iex")
        quotes = client["stock"].get_stock_latest_quote(req)
        result = {}
        for sym in symbols:
            q = quotes.get(sym)
            if q:
                price = (q.ask_price + q.bid_price) / 2 if q.ask_price and q.bid_price else (q.ask_price or q.bid_price)
                result[sym] = {
                    "price": float(price) if price else 0,
                    "change_24h": 0,
                    "volume_24h": 0,
                    "type": "stock",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "bid": float(q.bid_price) if q.bid_price else 0,
                    "ask": float(q.ask_price) if q.ask_price else 0,
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


def _fetch_yahoo_stock_prices(symbols):
    result = {}
    for sym in symbols:
        try:
            r = requests.get(
                f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}",
                params={"range": "1d", "interval": "1d"},
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=10,
            )
            meta = r.json().get("chart", {}).get("result", [{}])[0].get("meta", {})
            price = meta.get("regularMarketPrice", 0)
            prev_close = meta.get("chartPreviousClose", 0)
            change_pct = ((price - prev_close) / prev_close * 100) if prev_close else 0
            result[sym] = {
                "price": price,
                "change_24h": change_pct,
                "volume_24h": meta.get("regularMarketVolume", 0),
                "type": "stock",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        except Exception:
            pass
    return result

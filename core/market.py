import time
from datetime import datetime, timezone

import requests

from config import WATCHED_SYMBOLS, BROKER_TYPE

MT5_AVAILABLE = False
try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    pass


MAX_CACHE_SIZE = 200


def classify_symbol(symbol):
    """crypto (BASE/QUOTE), forex (6-letter pairs incl. metals XAUUSD), or stock."""
    if "/" in symbol:
        return "crypto"
    if symbol.isalpha() and len(symbol) == 6:
        return "forex"
    return "stock"


def is_market_open(symbol, now=None):
    """Whether the symbol's market currently trades.

    Crypto is 24/7. Stocks follow NYSE regular hours (9:30-16:00 ET, Mon-Fri).
    Forex/metals trade 24/5: closed Fri 21:00 UTC through Sun 22:00 UTC.
    Outside these windows quote APIs return the stale last close, and trading
    on a frozen price would be fiction.
    """
    kind = classify_symbol(symbol)
    if kind == "crypto":
        return True
    now = now or datetime.now(timezone.utc)
    if kind == "stock":
        try:
            from zoneinfo import ZoneInfo
            ny = now.astimezone(ZoneInfo("America/New_York"))
            if ny.weekday() >= 5:
                return False
            minutes = ny.hour * 60 + ny.minute
            return 9 * 60 + 30 <= minutes < 16 * 60
        except Exception:
            # No tz database: approximate with EDT (UTC-4)
            if now.weekday() >= 5:
                return False
            minutes = now.hour * 60 + now.minute
            return 13 * 60 + 30 <= minutes < 20 * 60
    # forex/metals
    wd = now.weekday()
    if wd == 5:
        return False
    if wd == 4 and now.hour >= 21:
        return False
    if wd == 6 and now.hour < 22:
        return False
    return True


class MarketData:
    def __init__(self):
        self.cache = {}
        self.cache_ttl = 60
        self._mt5_init = False

    def _ensure_mt5(self):
        if not MT5_AVAILABLE or self._mt5_init:
            return self._mt5_init
        try:
            self._mt5_init = mt5.initialize()
        except Exception:
            self._mt5_init = False
        return self._mt5_init

    def _get_cached(self, key, ttl=None):
        ttl = ttl or self.cache_ttl
        if key in self.cache:
            ts, val = self.cache[key]
            if time.time() - ts < ttl:
                return val
        return None

    def _set_cache(self, key, val):
        self.cache[key] = (time.time(), val)
        if len(self.cache) > MAX_CACHE_SIZE:
            oldest = sorted(self.cache.keys(), key=lambda k: self.cache[k][0])[:len(self.cache) - MAX_CACHE_SIZE]
            for k in oldest:
                del self.cache[k]

    def clear_cache(self):
        self.cache.clear()

    def fetch_prices(self, symbols=None):
        symbols = symbols or WATCHED_SYMBOLS
        crypto = [s for s in symbols if "/" in s]
        stocks = [s for s in symbols if "/" not in s and s.isalpha()]
        forex = [s for s in symbols if "/" not in s and s.isalpha() and len(s) == 6]
        result = {}

        if crypto:
            result.update(self._fetch_crypto_prices(crypto))
        if forex:
            result.update(self._fetch_forex_prices(forex))
        remaining = [s for s in stocks if s not in result]
        if remaining:
            result.update(self._fetch_stock_prices(remaining))
        return result

    def _fetch_forex_prices(self, symbols):
        cached = self._get_cached("forex")
        if cached:
            return cached
        result = {}
        if self._ensure_mt5():
            for sym in symbols:
                try:
                    tick = mt5.symbol_info_tick(sym)
                    if tick:
                        result[sym] = {
                            "price": tick.ask if tick.ask else tick.bid,
                            "change_24h": 0,
                            "volume_24h": tick.volume or 0,
                            "type": "forex",
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                            "bid": tick.bid,
                            "ask": tick.ask
                        }
                except Exception:
                    pass
        # fallback to Yahoo Finance (metals map to futures proxies, forex to =X)
        from core.data_provider import _yahoo_symbol
        for sym in symbols:
            if sym not in result:
                try:
                    r = requests.get(
                        f"https://query1.finance.yahoo.com/v8/finance/chart/{_yahoo_symbol(sym)}",
                        params={"range": "1d", "interval": "1d"},
                        headers={"User-Agent": "Mozilla/5.0"},
                        timeout=10
                    )
                    d = r.json()
                    meta = d.get("chart", {}).get("result", [{}])[0].get("meta", {})
                    price = meta.get("regularMarketPrice", 0)
                    result[sym] = {
                        "price": price,
                        "change_24h": 0,
                        "volume_24h": meta.get("regularMarketVolume", 0),
                        "type": "forex",
                        "timestamp": datetime.now(timezone.utc).isoformat()
                    }
                except Exception:
                    pass
        self._set_cache("forex", result)
        return result

    def _fetch_crypto_prices(self, symbols):
        if BROKER_TYPE == "binance":
            return self._fetch_crypto_from_binance(symbols)
        return self._fetch_crypto_from_coingecko(symbols)

    def _to_binance_symbol(self, symbol):
        s = symbol.replace("/", "").upper()
        if s.endswith("USD") and not s.endswith("USDT"):
            return s + "T"
        return s

    def _fetch_crypto_from_binance(self, symbols):
        cached = self._get_cached("crypto_binance")
        if cached:
            return cached
        bsyms = [self._to_binance_symbol(s) for s in symbols]
        result = {}
        try:
            r = requests.get("https://api.binance.com/api/v3/ticker/24hr", timeout=10)
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
            self._set_cache("crypto_binance", result)
        except Exception:
            pass
        return result

    def _fetch_crypto_from_coingecko(self, symbols):
        cached = self._get_cached("crypto")
        if cached:
            return cached
        ticker_to_id = {
            "BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana",
            "AAVE": "aave", "LINK": "chainlink", "AVAX": "avalanche-2",
            "BNB": "binancecoin", "DOGE": "dogecoin", "XRP": "ripple",
            "ADA": "cardano", "DOT": "polkadot", "MATIC": "matic-network",
            "UNI": "uniswap", "ATOM": "cosmos", "LTC": "litecoin",
            "BCH": "bitcoin-cash", "TRX": "tron",
        }
        ids = []
        mapping = {}
        for s in symbols:
            ticker = s.split("/")[0].upper()
            cg_id = ticker_to_id.get(ticker, ticker.lower())
            mapping[cg_id] = s
            ids.append(cg_id)
        ids_str = ",".join(ids)
        try:
            r = requests.get(
                f"https://api.coingecko.com/api/v3/simple/price",
                params={"ids": ids_str, "vs_currencies": "usd",
                        "include_24hr_change": "true",
                        "include_24hr_vol": "true"},
                timeout=10
            )
            data = r.json()
            result = {}
            for cid, info in data.items():
                sym = mapping.get(cid)
                if sym:
                    result[sym] = {
                        "price": info.get("usd", 0),
                        "change_24h": info.get("usd_24h_change") or 0,
                        "volume_24h": info.get("usd_24h_vol") or 0,
                        "type": "crypto",
                        "timestamp": datetime.now(timezone.utc).isoformat()
                    }
            self._set_cache("crypto", result)
            return result
        except Exception as e:
            return {}

    def _fetch_stock_prices(self, symbols):
        cached = self._get_cached("stocks")
        if cached:
            return cached
        try:
            symbols_str = ",".join(symbols)
            r = requests.get(
                f"https://query1.finance.yahoo.com/v8/finance/chart/{symbols[0]}",
                params={"range": "1d", "interval": "1d"},
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=10
            )
            result = {}
            for sym in symbols:
                try:
                    r2 = requests.get(
                        f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}",
                        params={"range": "1d", "interval": "1d"},
                        headers={"User-Agent": "Mozilla/5.0"},
                        timeout=10
                    )
                    d = r2.json()
                    meta = d.get("chart", {}).get("result", [{}])[0].get("meta", {})
                    price = meta.get("regularMarketPrice", 0)
                    prev_close = meta.get("chartPreviousClose", 0)
                    change_pct = ((price - prev_close) / prev_close * 100) if prev_close else 0
                    result[sym] = {
                        "price": price,
                        "change_24h": change_pct,
                        "volume_24h": meta.get("regularMarketVolume", 0),
                        "type": "stock",
                        "timestamp": datetime.now(timezone.utc).isoformat()
                    }
                except Exception:
                    pass
            self._set_cache("stocks", result)
            return result
        except Exception as e:
            return {}

    def get_historical(self, symbol, days=30):
        cache_key = f"hist_{symbol}_{days}"
        cached = self._get_cached(cache_key, ttl=300)
        if cached:
            return cached
        # Use MT5 for forex symbols
        if symbol.isalpha() and len(symbol) == 6 and self._ensure_mt5():
            try:
                rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_D1, 0, days)
                if rates is not None and len(rates) > 0:
                    prices = []
                    for r in rates:
                        prices.append({
                            "date": datetime.fromtimestamp(r[0], tz=timezone.utc).isoformat(),
                            "close": r[4],  # close
                            "volume": r[5]   # volume
                        })
                    self._set_cache(cache_key, prices)
                    return prices
            except Exception:
                pass
        try:
            from core.data_provider import _yahoo_symbol
            yahoo_sym = _yahoo_symbol(symbol)
            r = requests.get(
                f"https://query1.finance.yahoo.com/v8/finance/chart/{yahoo_sym}",
                params={"range": f"{days}d", "interval": "1d"},
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=10
            )
            data = r.json()
            result = data.get("chart", {}).get("result", [{}])[0]
            quotes = result.get("indicators", {}).get("quote", [{}])[0]
            timestamps = result.get("timestamp", [])
            closes = quotes.get("close", [])
            volumes = quotes.get("volume", [])
            prices = []
            for i, ts in enumerate(timestamps):
                if i < len(closes) and closes[i]:
                    prices.append({
                        "date": datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(),
                        "close": closes[i],
                        "volume": volumes[i] if i < len(volumes) else 0
                    })
            self._set_cache(cache_key, prices)
            return prices
        except Exception:
            return []

    def get_ohlc(self, symbol, days=100, interval="1d"):
        cache_key = f"ohlc_{symbol}_{interval}_{days}"
        cached = self._get_cached(cache_key, ttl=300)
        if cached:
            return cached
        from core.data_provider import fetch_ohlc
        ohlc = fetch_ohlc(symbol, interval=interval, limit=days + 50)
        if ohlc and len(ohlc) >= 2:
            self._set_cache(cache_key, ohlc)
            return ohlc
        return []

    def compute_indicators(self, prices):
        if not prices or len(prices) < 20:
            return {}
        from core.indicators import compute_all
        return compute_all(prices)

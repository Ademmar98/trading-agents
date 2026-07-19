import time
from datetime import datetime, timezone

from config import WATCHED_SYMBOLS, BROKER_TYPE

MAX_CACHE_SIZE = 200


def classify_symbol(symbol):
    """Crypto-only firm: every watched symbol is a spot crypto pair.

    Kept as a function (compliance groups positions by cluster) but there is
    just one cluster now — 'crypto'.
    """
    return "crypto"


def is_market_open(symbol, now=None):
    """Crypto trades 24/7 — always open. Kept so callers that gate on market
    hours keep working after stocks/metals were removed."""
    return True


class MarketData:
    def __init__(self):
        self.cache = {}
        self.cache_ttl = 60

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
        if not crypto:
            return {}
        return self._fetch_crypto_prices(crypto)

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
        import requests
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
        import requests
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
                "https://api.coingecko.com/api/v3/simple/price",
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
        except Exception:
            return {}

    def get_historical(self, symbol, days=30):
        cache_key = f"hist_{symbol}_{days}"
        cached = self._get_cached(cache_key, ttl=300)
        if cached:
            return cached
        # Crypto history from daily klines via the data provider chain
        from core.data_provider import fetch_ohlc
        ohlc = fetch_ohlc(symbol, interval="1d", limit=days)
        prices = [{"date": c["date"], "close": c["close"], "volume": c.get("volume", 0)}
                  for c in (ohlc or [])]
        if prices:
            self._set_cache(cache_key, prices)
        return prices

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

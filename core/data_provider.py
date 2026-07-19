import logging
import time
from datetime import datetime, timezone

import requests

from config import (
    WATCHED_SYMBOLS,
    ALPACA_API_KEY, ALPACA_SECRET_KEY,
    BROKER_TYPE,
)

_log = logging.getLogger("data_provider")

BINANCE_BASE = "https://api.binance.com"
BINANCE_TESTNET = "https://testnet.binance.vision"
BINANCE_INTERVALS = {"1m", "5m", "15m", "30m", "1h", "4h", "1d", "1w"}

# Bar duration per interval, milliseconds. Used to decide whether a bar is
# closed yet and to detect gaps between consecutive bars.
_INTERVAL_MS = {
    "1m": 60_000, "5m": 300_000, "15m": 900_000, "30m": 1_800_000,
    "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000, "1w": 604_800_000,
}
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


def _ensure_ts(bars):
    """Guarantee every candle dict carries 'ts' (unix seconds, UTC) so
    session-based strategies can rely on it. Backward compatible: derives
    'ts' from the ISO 'date' field when a source omits it."""
    for b in bars:
        if b.get("ts") is None and b.get("date"):
            try:
                b["ts"] = int(datetime.fromisoformat(
                    str(b["date"]).replace("Z", "+00:00")).timestamp())
            except (ValueError, TypeError):
                pass
    return bars


def _check_bar_gaps(symbol, interval, bars):
    """Contiguity check: consecutive bar timestamps must differ by exactly
    one interval. Gaps skew every downstream indicator, so log them loudly
    (the bars are still returned — missing history is not fabricated)."""
    step = (_INTERVAL_MS.get(interval) or 0) // 1000
    if not step or len(bars) < 2:
        return
    ordered = sorted(bars, key=lambda b: b["ts"])
    gaps = 0
    for prev, cur in zip(ordered, ordered[1:]):
        dt = cur["ts"] - prev["ts"]
        if dt != step:
            gaps += 1
            _log.warning("bar gap %s %s: %s -> %s (delta %ds, expected %ds)",
                         symbol, interval, prev.get("date"), cur.get("date"),
                         dt, step)
    if gaps:
        _log.warning("%s %s: %d gap(s) across %d bars",
                     symbol, interval, gaps, len(ordered))


def fetch_binance_klines(symbol, interval="1d", limit=100):
    """Closed bars only, up to the requested depth.

    Two audit findings fixed here: (1) Binance appends the still-FORMING bar
    as the last kline — every strategy reading bars[-1] was trading on a
    repainting candle; any kline whose close_time is not yet in the past is
    dropped. (2) The API caps one call at 1000 klines, so requests deeper
    than that were silently truncated; we paginate backwards via endTime
    until the requested depth is filled or history runs out.
    """
    bsym = _to_binance_symbol(symbol)
    try:
        now_ms = int(time.time() * 1000)
        raw = []
        remaining = max(1, limit)
        end_time = None
        while remaining > 0:
            page_size = min(remaining, 1000)  # Binance hard cap per call
            params = {"symbol": bsym, "interval": interval, "limit": page_size}
            if end_time is not None:
                params["endTime"] = end_time
            r = requests.get(f"{BINANCE_BASE}/api/v3/klines", params=params, timeout=15)
            data = r.json()
            if not isinstance(data, list):
                return []
            if not data:
                break
            raw = data + raw
            if len(data) < page_size:
                break  # history ran out
            remaining -= len(data)
            end_time = data[0][0] - 1  # walk one page further into the past
        bars = []
        seen = set()
        for k in raw:
            if k[6] >= now_ms:
                continue  # still forming — never hand a repainting bar downstream
            ts = k[0] // 1000
            if ts in seen:
                continue
            seen.add(ts)
            bars.append({
                "date": datetime.fromtimestamp(k[0] / 1000, tz=timezone.utc).isoformat(),
                "open": float(k[1]), "high": float(k[2]), "low": float(k[3]),
                "close": float(k[4]), "volume": float(k[5]), "ts": ts,
            })
        bars.sort(key=lambda b: b["ts"])
        bars = bars[-limit:]
        _log.info("binance klines %s %s: requested %d, received %d closed bars",
                  symbol, interval, limit, len(bars))
        _check_bar_gaps(symbol, interval, bars)
        return _ensure_ts(bars)
    except Exception:
        return []


# Crypto.com Exchange public API — keyless, exchange-grade crypto data.
# Fallback for Binance klines where Binance is geo-blocked (e.g. US VPS).
_CRYPTOCOM_TIMEFRAMES = {"1m": "M1", "5m": "M5", "15m": "M15", "30m": "M30",
                         "1h": "H1", "4h": "H4", "1d": "D1"}


def fetch_cryptocom_ohlc(symbol, interval="1d", limit=100):
    """Keyless fallback with the same guarantees as fetch_binance_klines:
    closed bars only (the venue includes the forming bar in its response)
    and real depth — one call is capped at 300 bars, so deeper requests
    paginate backwards via end_ts."""
    tf = _CRYPTOCOM_TIMEFRAMES.get(interval)
    if not tf or not _is_crypto(symbol):
        return []
    inst = symbol.replace("/", "_")  # BTC/USD -> BTC_USD (spot naming)
    interval_ms = _INTERVAL_MS.get(interval)
    try:
        now_ms = int(time.time() * 1000)
        raw = []
        seen = set()
        end_ts = None
        remaining = max(1, limit)
        while remaining > 0:
            page_size = min(remaining, 300)  # venue hard cap per call
            params = {"instrument_name": inst, "timeframe": tf, "count": page_size}
            if end_ts is not None:
                params["end_ts"] = end_ts
            r = requests.get(
                "https://api.crypto.com/exchange/v1/public/get-candlestick",
                params=params,
                timeout=15,
            )
            page = ((r.json().get("result") or {}).get("data")) or []
            if not page:
                break
            new = 0
            oldest = None
            for b in page:
                t = int(b["t"])
                if t in seen:
                    continue
                seen.add(t)
                raw.append(b)
                new += 1
                oldest = t if oldest is None else min(oldest, t)
            if new == 0:
                break  # no progress (e.g. end_ts ignored) — stop paginating
            remaining -= new
            if len(page) < page_size:
                break  # history ran out
            end_ts = oldest - 1  # walk one page further into the past
        bars = []
        for b in raw:
            t = int(b["t"])
            if interval_ms is not None and t + interval_ms > now_ms:
                continue  # still forming — never hand a repainting bar downstream
            bars.append({
                "date": datetime.fromtimestamp(t / 1000, tz=timezone.utc).isoformat(),
                "open": float(b["o"]), "high": float(b["h"]),
                "low": float(b["l"]), "close": float(b["c"]),
                "volume": float(b.get("v") or 0), "ts": t // 1000,
            })
        bars.sort(key=lambda b: b["ts"])
        bars = bars[-limit:]
        _log.info("cryptocom ohlc %s %s: requested %d, received %d closed bars",
                  symbol, interval, limit, len(bars))
        _check_bar_gaps(symbol, interval, bars)
        return _ensure_ts(bars)
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
        step_s = (_INTERVAL_MS.get(interval) or 0) // 1000
        now_s = int(time.time())
        ohlc = []
        for idx, row in df.iterrows():
            ts = idx[1] if isinstance(idx, tuple) else idx
            if isinstance(ts, datetime):
                ts_epoch = int(ts.timestamp())
            else:
                ts_epoch = int(time.mktime(ts.timetuple())) if hasattr(ts, "timetuple") else int(ts)
            if step_s and ts_epoch + step_s > now_s:
                continue  # still forming — never hand a repainting bar downstream
            ohlc.append({
                "date": ts.isoformat() if isinstance(ts, datetime) else str(ts),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": float(row["volume"]),
                "ts": ts_epoch,
            })
        _check_bar_gaps(symbol, interval, ohlc)
        return _ensure_ts(ohlc)
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

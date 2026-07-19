"""Market microstructure signals: session VWAP, order-book imbalance, and
funding rate. Everything fails open (returns None) — these enrich decisions,
they never block the pipeline.

Order book and funding come from Crypto.com Exchange's keyless public API
(Binance is geo-blocked on the production VPS). Liquidation heatmaps and
footprint data need paid feeds (e.g. Coinglass) and are intentionally
absent until a key exists.
"""
import requests

from core.pricing import round_sig

_CC_BASE = "https://api.crypto.com/exchange/v1/public"


def _cc_instrument(symbol, perp=False):
    base = symbol.replace("/", "_")           # BTC/USD -> BTC_USD (spot)
    if perp:
        return symbol.replace("/", "").replace("USD", "USD-PERP")
    return base


def vwap(bars):
    """Volume-weighted average price over the given bars (typical price x
    volume). Falls back to a plain average of typical prices when the feed
    carries no volume (metals/forex)."""
    if not bars:
        return None
    pv = 0.0
    vol = 0.0
    tp_sum = 0.0
    for b in bars:
        try:
            typical = (b["high"] + b["low"] + b["close"]) / 3.0
        except (KeyError, TypeError):
            return None
        v = float(b.get("volume") or 0)
        pv += typical * v
        vol += v
        tp_sum += typical
    if vol > 0:
        return round_sig(pv / vol)
    return round_sig(tp_sum / len(bars))


def book_imbalance(symbol, depth=20):
    """Bid/ask volume imbalance in [-1, 1] from the top of the order book.
    Positive = bid-heavy (buy-side liquidity support). None on any failure."""
    try:
        r = requests.get(f"{_CC_BASE}/get-book",
                         params={"instrument_name": _cc_instrument(symbol),
                                 "depth": depth},
                         timeout=10)
        data = (r.json().get("result") or {}).get("data") or []
        if not data:
            return None
        book = data[0]
        bid_vol = sum(float(level[1]) for level in (book.get("bids") or []))
        ask_vol = sum(float(level[1]) for level in (book.get("asks") or []))
        total = bid_vol + ask_vol
        if total <= 0:
            return None
        return round((bid_vol - ask_vol) / total, 4)
    except Exception:
        return None


def funding_rate(symbol):
    """Latest funding rate of the symbol's perpetual (positioning signal:
    strongly positive funding = crowded longs). None on any failure."""
    try:
        r = requests.get(f"{_CC_BASE}/get-valuations",
                         params={"instrument_name": _cc_instrument(symbol, perp=True),
                                 "valuation_type": "funding_rate", "count": 1},
                         timeout=10)
        data = (r.json().get("result") or {}).get("data") or []
        if not data:
            return None
        return float(data[0].get("v"))
    except Exception:
        return None

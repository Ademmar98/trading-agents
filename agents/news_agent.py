"""News agent: keyless RSS market-news scanner feeding the other agents.

Pulls headlines from public RSS feeds (no API keys), scores each with a
keyword sentiment lexicon, and maps them to watched symbols. Writes
reports/news_scan consumed by:
- the analyst (confidence nudges on BUY candidates),
- the open-trade steward (bearish news tightens stops),
- the HeadTrader (memo context).

Self-throttled to NEWS_INTERVAL_MIN. Any failure degrades to a no-op —
news enriches decisions, it never blocks the pipeline.
"""
import re
import time
import xml.etree.ElementTree as ET

import requests

from config import WATCHED_SYMBOLS, NEWS_AGENT_ENABLED, NEWS_INTERVAL_MIN
from agents.base_agent import BaseAgent
from core.database import get_meta, set_meta

FEEDS = [
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "https://cointelegraph.com/rss",
    "https://finance.yahoo.com/news/rssindex",
]

BULLISH = {
    "surge", "surges", "soar", "soars", "rally", "rallies", "record", "high",
    "approval", "approve", "approved", "adoption", "bullish", "gain", "gains",
    "breakout", "upgrade", "upgraded", "partnership", "etf inflow", "inflows",
    "beats", "buyback", "all-time",
}
BEARISH = {
    "crash", "crashes", "plunge", "plunges", "hack", "hacked", "exploit",
    "ban", "bans", "banned", "lawsuit", "sue", "sued", "sec charges",
    "selloff", "sell-off", "bearish", "dump", "liquidation", "liquidations",
    "outflow", "outflows", "downgrade", "downgraded", "bankruptcy", "misses",
    "recession", "default",
}

# Symbol -> search terms in headlines
_ASSET_TERMS = {
    "BTC/USD": ["bitcoin", "btc"], "ETH/USD": ["ethereum", "eth "],
    "SOL/USD": ["solana"], "BNB/USD": ["bnb", "binance coin"],
    "XRP/USD": ["xrp", "ripple"], "ADA/USD": ["cardano"],
    "DOGE/USD": ["dogecoin", "doge"], "DOT/USD": ["polkadot"],
    "AVAX/USD": ["avalanche"], "LINK/USD": ["chainlink"],
    "UNI/USD": ["uniswap"], "ATOM/USD": ["cosmos"], "LTC/USD": ["litecoin"],
    "BCH/USD": ["bitcoin cash"], "TRX/USD": ["tron"], "AAVE/USD": ["aave"],
    "AAPL": ["apple"], "MSFT": ["microsoft"], "NVDA": ["nvidia"],
    "TSLA": ["tesla"], "AMZN": ["amazon"], "GOOGL": ["google", "alphabet"],
    "META": ["meta platforms", "facebook"],
    "XAUUSD": ["gold"], "XAGUSD": ["silver"],
}


def _score_text(text):
    t = text.lower()
    pos = sum(1 for w in BULLISH if w in t)
    neg = sum(1 for w in BEARISH if w in t)
    if pos == neg:
        return 0.0
    return round((pos - neg) / max(pos + neg, 1), 2)


def _fetch_feed(url):
    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=12)
        root = ET.fromstring(r.content)
        items = []
        for item in root.iter("item"):
            title = (item.findtext("title") or "").strip()
            if title:
                items.append(re.sub(r"\s+", " ", title))
        return items[:40]
    except Exception:
        return []


class NewsAgent(BaseAgent):
    name = "news"

    def run(self):
        if not NEWS_AGENT_ENABLED:
            return None
        now = time.time()
        last = float(get_meta("news_last_run", "0") or 0)
        if now - last < NEWS_INTERVAL_MIN * 60:
            return None
        set_meta("news_last_run", str(now))

        headlines = []
        for url in FEEDS:
            headlines.extend(_fetch_feed(url))
        if not headlines:
            self.log("No headlines fetched")
            return None

        symbols = {}
        overall_scores = []
        for h in headlines:
            score = _score_text(h)
            overall_scores.append(score)
            hl = h.lower()
            for sym in WATCHED_SYMBOLS:
                terms = _ASSET_TERMS.get(sym)
                if not terms:
                    continue
                if any(term in hl for term in terms):
                    s = symbols.setdefault(sym, {"score": 0.0, "n": 0, "headlines": []})
                    s["score"] += score
                    s["n"] += 1
                    if len(s["headlines"]) < 3:
                        s["headlines"].append(h[:140])
        for sym, s in symbols.items():
            s["score"] = round(max(-1.0, min(1.0, s["score"] / s["n"])), 2)

        report = {
            "symbols": symbols,
            "overall": round(sum(overall_scores) / len(overall_scores), 3),
            "headline_count": len(headlines),
            "timestamp": now,
        }
        self.memory.write("reports", "news_scan", report)
        tagged = len(symbols)
        self.log(f"{len(headlines)} headlines, {tagged} symbols tagged, market tone {report['overall']:+.2f}")
        if tagged:
            movers = sorted(symbols.items(), key=lambda kv: abs(kv[1]["score"]), reverse=True)[:3]
            self.notifier.on_agent_action(
                "news", " | ".join(f"{s} {d['score']:+.1f}" for s, d in movers))
        return report

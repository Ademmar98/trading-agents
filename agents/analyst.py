import time

from config import WATCHED_SYMBOLS
from agents.base_agent import BaseAgent
from core.market import MarketData
from core.strategies import scan_symbol
from core.multiframe import analyze_symbol_multiframe
from core.database import get_unprofitable_strategies
from core.pricing import compute_pricing


class ResearchAnalyst(BaseAgent):
    name = "analyst"

    def __init__(self):
        super().__init__()
        self.market = MarketData()

    def run(self):
        self.log("Fetching market data and analyzing symbols")
        prices = self.market.fetch_prices()
        if not prices:
            self.memory.write("analyses", "market_scan", {
                "error": "No market data available",
                "timestamp": time.time(),
            })
            self.log("WARNING: No market data received")
            return

        analyses = {}
        opportunities = []
        regime_scan = self.memory.read("analyses", "regime_scan") or {}
        symbol_regimes = regime_scan.get("symbols", {})
        for symbol, data in prices.items():
            ohlc = self.market.get_ohlc(symbol, days=100)
            hist = self.market.get_historical(symbol)
            indicators = self.market.compute_indicators(hist)

            mtf_signal = analyze_symbol_multiframe(symbol)

            regime = symbol_regimes.get(symbol, {}).get("regime") if symbol_regimes else None
            bad_strats = get_unprofitable_strategies()
            signals = scan_symbol(ohlc, regime=regime, exclude_strategies=bad_strats) if ohlc and len(ohlc) >= 30 else []

            analyses[symbol] = {
                "price": data["price"],
                "change_24h": data["change_24h"],
                "volume_24h": data["volume_24h"],
                "type": data.get("type", "unknown"),
                "bid": data.get("bid"),
                "ask": data.get("ask"),
                **indicators,
                "signals": signals,
                "mtf_signal": mtf_signal,
            }
            seen_actions = set()
            for sig in signals:
                action = sig["action"]
                if action in seen_actions:
                    continue
                seen_actions.add(action)
                confidence = sig["confidence"]
                reasons = list(sig["reasons"])
                if mtf_signal and mtf_signal.get("action") == action:
                    confidence = max(confidence, mtf_signal["confidence"])
                    reasons = mtf_signal["reasons"] + reasons

                opp = {
                    "symbol": symbol,
                    "action": action,
                    "confidence": min(confidence, 0.95),
                    "price": data["price"],
                    "reasons": reasons[:5],
                    "strategies": sig["strategies"],
                    "regime": regime,
                    "multi_timeframe": mtf_signal is not None,
                    "indicators": {
                        "trend": indicators.get("trend", "neutral"),
                        "rsi": indicators.get("rsi_14", 50),
                        "volatility": indicators.get("volatility", 0),
                        "atr": indicators.get("atr", 0),
                    }
                }

                pricing = compute_pricing(symbol, action, data["price"], {**data, **indicators}, regime, indicators.get("atr", 0))
                opp.update(pricing)
                opportunities.append(opp)

        opportunities.sort(key=lambda o: o["confidence"], reverse=True)
        summary = f"Analyzed {len(analyses)} symbols, found {len(opportunities)} opportunities"

        pricing_map = {o["symbol"]: o for o in opportunities}

        self.memory.write("analyses", "market_scan", {
            "summary": summary,
            "opportunities": opportunities,
            "all_analyses": analyses,
            "timestamp": time.time(),
        })
        self.memory.write("decisions", "pricing", {
            "pricing_map": pricing_map,
            "timestamp": time.time(),
        })
        self.log(summary)
        if opportunities:
            top = opportunities[0]
            self.notifier.on_agent_action(
                "analyst", f"{len(opportunities)} signals | top: {top['action']} {top['symbol']} SL={top['sl_pct']:.1f}% TP={top['tp_pct']:.1f}%"
            )
        return analyses

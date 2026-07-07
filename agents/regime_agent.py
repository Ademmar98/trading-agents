import time

from config import WATCHED_SYMBOLS
from agents.base_agent import BaseAgent
from core.market import MarketData
from core.regime import detect_regime


class RegimeAgent(BaseAgent):
    name = "regime"

    def __init__(self):
        super().__init__()
        self.market = MarketData()

    def run(self):
        self.log("Detecting market regimes")
        regimes = {}
        counts = {}
        for symbol in WATCHED_SYMBOLS:
            ohlc = self.market.get_ohlc(symbol, days=100)
            if not ohlc or len(ohlc) < 60:
                result = {"regime": "unknown", "confidence_multiplier": 0.85, "size_multiplier": 0.75, "notes": ["Insufficient OHLC history"]}
            else:
                detected = detect_regime(ohlc)
                result = detected if isinstance(detected, dict) else {"regime": detected or "unknown"}
                regime = result.get("regime", "unknown")
                if regime == "trending_up":
                    result.update({"favored_action": "BUY", "confidence_multiplier": 1.10, "size_multiplier": 1.00})
                elif regime == "trending_down":
                    result.update({"favored_action": "SELL", "confidence_multiplier": 1.10, "size_multiplier": 0.80})
                elif regime == "volatile":
                    result.update({"confidence_multiplier": 0.85, "size_multiplier": 0.50})
                elif regime == "ranging":
                    result.update({"confidence_multiplier": 0.95, "size_multiplier": 0.70})
                else:
                    result.update({"confidence_multiplier": 0.85, "size_multiplier": 0.60})
            regimes[symbol] = result
            counts[result.get("regime", "unknown")] = counts.get(result.get("regime", "unknown"), 0) + 1

        report = {"symbols": regimes, "summary": counts, "timestamp": time.time()}
        self.memory.write("analyses", "regime_scan", report)
        self.log(f"Regime scan complete: {counts}")
        dominant = max(counts, key=counts.get) if counts else "unknown"
        found_volatile = any(r.get("regime") == "volatile" for r in regimes.values() if isinstance(r, dict))
        if found_volatile:
            self.notifier.on_agent_action("regime", f"VOLATILE detected | dominant: {dominant}")
        return report

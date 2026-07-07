import statistics
import time
from datetime import datetime, timezone

from agents.base_agent import BaseAgent


def fetch_fear_greed():
    try:
        import requests
        r = requests.get("https://api.alternative.me/fng/", timeout=10)
        data = r.json()
        if "data" in data and data["data"]:
            entry = data["data"][0]
            return {
                "value": int(entry.get("value", 50)),
                "classification": entry.get("value_classification", "Neutral"),
                "timestamp": entry.get("timestamp"),
            }
    except Exception:
        pass
    return None


class SentimentAgent(BaseAgent):
    name = "sentiment"

    def run(self):
        self.log("Scoring market sentiment from price breadth and volume")
        analysis = self.memory.read("analyses", "market_scan") or {}
        all_analyses = analysis.get("all_analyses", {}) or {}

        fng = fetch_fear_greed()
        fng_value = fng["value"] if fng else 50
        changes = [d.get("change_24h", 0) for d in all_analyses.values() if isinstance(d, dict)]
        avg_change = statistics.mean(changes) if changes else 0
        positive_breadth = sum(1 for c in changes if c > 0) / len(changes) if changes else 0

        fg_adjust = (fng_value - 50) / 5
        if avg_change <= -4 or positive_breadth < 0.25:
            market_mood = "risk_off"
        elif avg_change >= 3 and positive_breadth > 0.65:
            market_mood = "risk_on"
        else:
            market_mood = "neutral"

        symbols = {}
        for symbol, data in all_analyses.items():
            change = data.get("change_24h", 0) if isinstance(data, dict) else 0
            volume = data.get("volume_24h", 0) if isinstance(data, dict) else 0
            score = 50 + max(min(change * 4, 30), -30) + fg_adjust
            if market_mood == "risk_on":
                score += 8
            elif market_mood == "risk_off":
                score -= 12
            score = max(0, min(100, score))

            if score >= 65:
                label = "bullish"
                confidence_multiplier = 1.10
                size_multiplier = 1.00
            elif score <= 35:
                label = "bearish"
                confidence_multiplier = 0.85
                size_multiplier = 0.65
            else:
                label = "neutral"
                confidence_multiplier = 1.00
                size_multiplier = 0.85

            symbols[symbol] = {
                "label": label,
                "score": round(score, 1),
                "change_24h": change,
                "volume_24h": volume,
                "confidence_multiplier": confidence_multiplier,
                "size_multiplier": size_multiplier,
                "block_buy": market_mood == "risk_off" and change < -8,
            }

        report = {
            "market_mood": market_mood,
            "avg_change_24h": round(avg_change, 2),
            "positive_breadth": round(positive_breadth, 2),
            "symbols": symbols,
            "fear_greed": fng or {"value": 50, "classification": "Neutral"},
            "timestamp": time.time(),
        }
        self.memory.write("analyses", "sentiment_scan", report)
        fng_info = f"F&G {fng_value}" if fng else ""
        self.log(f"Sentiment: {market_mood}, breadth {positive_breadth:.0%}, avg {avg_change:+.2f}% {fng_info}")
        if market_mood != "neutral":
            self.notifier.on_agent_action("sentiment", f"mood: {market_mood} | breadth {positive_breadth:.0%} avg {avg_change:+.1f}%")
        return report

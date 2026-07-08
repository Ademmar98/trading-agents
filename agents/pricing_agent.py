import time

from agents.base_agent import BaseAgent
from core.pricing import compute_pricing


class PricingAgent(BaseAgent):
    name = "pricing"

    def run(self):
        self.log("Computing dynamic entry, stop-loss, and take-profit prices")
        analysis = self.memory.read("analyses", "market_scan") or {}
        opportunities = analysis.get("opportunities", []) or []
        all_analyses = analysis.get("all_analyses", {}) or {}
        regimes = self.memory.read("analyses", "regime_scan") or {}
        regime_symbols = regimes.get("symbols", {}) or {}

        pricing_map = {}
        for opp in opportunities:
            symbol = opp.get("symbol")
            action = opp.get("action", "BUY")
            price = opp.get("price") or 0
            data = (all_analyses.get(symbol) or {}) if isinstance(all_analyses, dict) else {}
            data_price = data.get("price") or data.get("current_price") or 0
            price = price or data_price
            if price <= 0:
                continue

            reg = regime_symbols.get(symbol, {})
            regime = reg.get("regime")

            vol = data.get("volatility") or (opp.get("indicators", {}) or {}).get("volatility") or 2.0
            atr_val = data.get("atr") or (opp.get("indicators", {}) or {}).get("atr") or 0
            merged_data = {**data, "volatility": vol, "atr": atr_val}

            pricing = compute_pricing(symbol, action, price, merged_data, regime, atr_val)
            pricing_map[symbol] = pricing

        report = {
            "pricing_map": pricing_map,
            "timestamp": time.time(),
        }
        self.memory.write("decisions", "pricing", report)
        self.log(f"Pricing computed for {len(pricing_map)} symbols")
        return report

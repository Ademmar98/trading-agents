import time

from config import MAX_POSITION_SIZE_PCT
from agents.base_agent import BaseAgent
from core.portfolio import load_portfolio


class PortfolioManagerAgent(BaseAgent):
    name = "portfolio_manager"

    def run(self):
        self.log("Optimizing approved opportunities for portfolio context")
        portfolio = load_portfolio()
        risk = self.memory.read("decisions", "risk_assessment") or {}
        sentiment = self.memory.read("analyses", "sentiment_scan") or {}
        regimes = self.memory.read("analyses", "regime_scan") or {}
        opportunities = risk.get("approved_opportunities", []) or []

        adjusted = []
        notes = []
        crypto_exposure = portfolio.exposure_pct
        for opp in opportunities:
            item = {**opp}
            symbol = item.get("symbol")
            action = item.get("action", "BUY")
            confidence = item.get("confidence", 0)
            max_qty = item.get("max_qty", 0)
            reasons = list(item.get("reasons", []))

            sent = (sentiment.get("symbols", {}) or {}).get(symbol, {})
            reg = (regimes.get("symbols", {}) or {}).get(symbol, {})

            confidence *= sent.get("confidence_multiplier", 1.0)
            max_qty *= sent.get("size_multiplier", 1.0)
            confidence *= reg.get("confidence_multiplier", 1.0)
            max_qty *= reg.get("size_multiplier", 1.0)

            if sent.get("block_buy") and action == "BUY":
                item["risk_ok"] = False
                reasons.append("Sentiment guard blocked BUY during sharp selloff")
            favored = reg.get("favored_action")
            if favored and favored != action:
                confidence *= 0.80
                max_qty *= 0.70
                reasons.append(f"Regime prefers {favored}, reduced conviction")
            if crypto_exposure > 60:
                max_qty *= 0.50
                reasons.append("Portfolio crypto exposure high, reduced size")
            if symbol in portfolio.positions:
                item["risk_ok"] = False
                reasons.append("Existing portfolio position already open")

            item["confidence"] = round(min(confidence, 0.95), 4)
            item["max_qty"] = round(max_qty, 8)
            item["portfolio_notes"] = reasons[-4:]
            item["max_position_pct"] = MAX_POSITION_SIZE_PCT
            item["reasons"] = reasons
            adjusted.append(item)

        adjusted.sort(key=lambda o: o.get("confidence", 0), reverse=True)
        report = {
            "approved_opportunities": adjusted,
            "portfolio_exposure_pct": round(portfolio.exposure_pct, 2),
            "cash": round(portfolio.cash, 2),
            "positions": len(portfolio.positions),
            "notes": notes,
            "timestamp": time.time(),
        }
        self.memory.write("decisions", "portfolio_plan", report)
        self.log(f"Portfolio plan: {len([o for o in adjusted if o.get('risk_ok', False)])} candidates")
        return report

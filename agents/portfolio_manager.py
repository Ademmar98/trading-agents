import time

from config import MAX_POSITION_SIZE_PCT
from agents.base_agent import BaseAgent
from core.portfolio import load_portfolio
from core.database import fetchall


class PortfolioManagerAgent(BaseAgent):
    name = "portfolio_manager"

    def run(self):
        self.log("Optimizing approved opportunities for portfolio context")
        portfolio = load_portfolio()
        sizing = self.memory.read("decisions", "position_sizing") or {}
        sentiment = self.memory.read("analyses", "sentiment_scan") or {}
        regimes = self.memory.read("analyses", "regime_scan") or {}
        opportunities = sizing.get("sized_opportunities", []) or []
        strategy_weights = self._load_strategy_weights()

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

            for strat_name in item.get("strategies", []):
                weight = strategy_weights.get(strat_name, 1.0)
                if weight < 1.0:
                    confidence *= weight
                    reasons.append(f"Strategy weight {strat_name}: {weight:.2f}")

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

    @staticmethod
    def _load_strategy_weights():
        rows = fetchall("SELECT * FROM strategy_stats ORDER BY pnl DESC")
        if not rows:
            return {}
        stats = [dict(r) for r in rows]
        best_pnl = stats[0]["pnl"] or 0.01
        weights = {}
        for s in stats:
            ratio = max(s["pnl"] / best_pnl, -0.5)
            wr = s.get("win_rate", 50) / 100.0
            if s["trades"] < 3:
                weights[s["strategy"]] = 1.0
            elif ratio < 0:
                weights[s["strategy"]] = 0.50
            else:
                weights[s["strategy"]] = round(0.5 + 0.5 * ratio, 2)
        return weights

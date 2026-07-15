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
        # The HeadTrader LLM memo is READ-ONLY (dashboard/Telegram). It used to
        # multiply per-strategy confidence here; removed 2026-07-15 — nothing an
        # LLM writes may touch sizing, ranking, or routing (AUDIT.md sec. 7).

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

            sent_conf = sent.get("confidence_multiplier", 1.0)
            reg_conf = reg.get("confidence_multiplier", 1.0)
            combined_mult = 1.0 + (sent_conf - 1.0) + (reg_conf - 1.0)
            confidence *= max(combined_mult, 0.5)
            max_qty *= sent.get("size_multiplier", 1.0) * reg.get("size_multiplier", 1.0)

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
        approved = [o for o in adjusted if o.get("risk_ok", False)]
        self.log(f"Portfolio plan: {len(approved)} candidates")
        if approved:
            top = approved[0]
            self.notifier.on_agent_action(
                "portfolio", f"approved {len(approved)} | top: {top['action']} {top['symbol']} ({top['confidence']:.0%})"
            )
        elif adjusted:
            self.notifier.on_agent_action("portfolio", f"0 approved — all {len(adjusted)} candidates blocked")
        return report

    @staticmethod
    def _load_strategy_weights():
        rows = fetchall("SELECT * FROM strategy_stats ORDER BY pnl DESC")
        if not rows:
            return {}
        stats = [dict(r) for r in rows]
        best_pnl = stats[0]["pnl"] or 0
        weights = {}
        for s in stats:
            if s["trades"] < 3:
                weights[s["strategy"]] = 1.0
            elif s["pnl"] < 0:
                # Always penalize losers — dividing by a negative best_pnl
                # would rank the worst strategy highest.
                weights[s["strategy"]] = 0.50
            elif best_pnl > 0:
                weights[s["strategy"]] = round(0.5 + 0.5 * (s["pnl"] / best_pnl), 2)
            else:
                weights[s["strategy"]] = 1.0
        return weights

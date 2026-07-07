import time

from config import MAX_POSITION_SIZE_PCT, MAX_PORTFOLIO_RISK_PCT, LEVERAGE_ENABLED
from agents.base_agent import BaseAgent
from core.portfolio import load_portfolio
from core.memory import SharedMemory


class RiskManager(BaseAgent):
    name = "risk_manager"

    def run(self):
        self.log("Evaluating portfolio risk (spot-only, no leverage)")
        portfolio = load_portfolio()
        analysis = self.memory.read("analyses", "market_scan")
        opportunities = (analysis or {}).get("opportunities", [])

        risks = []
        if LEVERAGE_ENABLED:
            risks.append("LEVERAGE IS ENABLED — spot-only mode recommended")
        exposure = portfolio.exposure_pct
        risk_verdict = "low"
        max_trade_size = portfolio.cash * (MAX_POSITION_SIZE_PCT / 100)

        if exposure > 80:
            risks.append("CRITICAL: Portfolio over-exposed")
            risk_verdict = "high_risk"
        elif exposure > 60:
            risks.append("HIGH: Portfolio exposure high")
            risk_verdict = "moderate_risk"

        concentration = 0
        if portfolio.positions:
            max_pos = max(p.current_price * p.quantity
                         for p in portfolio.positions.values())
            if portfolio.equity > 0:
                concentration = (max_pos / portfolio.equity) * 100
                if concentration > MAX_POSITION_SIZE_PCT:
                    risks.append(f"WARNING: Position concentration {concentration:.0f}%")

        filtered = []
        for opp in opportunities:
            sym = opp["symbol"]
            pos = portfolio.positions.get(sym)
            current_exposure = (pos.current_price * pos.quantity / portfolio.equity * 100
                              ) if pos and portfolio.equity > 0 else 0

            adjusted = {**opp}
            if current_exposure + MAX_POSITION_SIZE_PCT > 100:
                adjusted["max_qty"] = 0
                adjusted["risk_ok"] = False
                risks.append(f"SKIPPED {sym}: would exceed max exposure")
            else:
                max_cost = min(
                    max_trade_size,
                    portfolio.cash * ((MAX_POSITION_SIZE_PCT - current_exposure) / 100)
                )
                adjusted["max_qty"] = round(max_cost / opp["price"], 6) if opp["price"] > 0 else 0
                adjusted["risk_ok"] = True
            filtered.append(adjusted)

        pnl = portfolio.total_pnl_pct
        if pnl < -MAX_PORTFOLIO_RISK_PCT:
            risk_verdict = "critical"
            risks.append(f"EMERGENCY: Portfolio down {pnl:.1f}%")

        report = {
            "verdict": risk_verdict,
            "exposure_pct": round(exposure, 2),
            "concentration_pct": round(concentration, 2),
            "max_trade_size": round(max_trade_size, 2),
            "risks": risks,
            "approved_opportunities": filtered,
            "timestamp": time.time(),
        }
        self.memory.write("decisions", "risk_assessment", report)
        self.log(f"Risk verdict: {risk_verdict}, {len(risks)} warnings")
        if risk_verdict in ("high_risk", "critical"):
            self.notifier.on_agent_action("risk_manager", f"verdict={risk_verdict} | exposure {exposure:.0f}% | {len(risks)} warnings")
        return report

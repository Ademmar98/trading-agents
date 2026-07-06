import time

from config import BROKER_TYPE, LEVERAGE_ENABLED, MAX_PORTFOLIO_RISK_PCT
from agents.base_agent import BaseAgent
from core.portfolio import load_portfolio

MIN_CONFIDENCE = 0.55
MAX_TRADES_PER_CYCLE = 3


class ComplianceAgent(BaseAgent):
    name = "compliance"

    def run(self):
        self.log("Running safety and compliance gate")
        portfolio = load_portfolio()
        risk = self.memory.read("decisions", "risk_assessment") or {}
        plan = self.memory.read("decisions", "portfolio_plan") or {}
        candidates = plan.get("approved_opportunities", []) or []

        halted = False
        blockers = []
        warnings = []
        if LEVERAGE_ENABLED:
            halted = True
            blockers.append("Leverage enabled; system is configured for spot-only trading")
        if risk.get("verdict") == "critical":
            halted = True
            blockers.append("Risk verdict is critical")
        if portfolio.total_pnl_pct < -MAX_PORTFOLIO_RISK_PCT:
            halted = True
            blockers.append(f"Portfolio drawdown {portfolio.total_pnl_pct:.2f}% exceeds risk limit")
        if BROKER_TYPE not in {"paper", "binance", "mt5", "alpaca"}:
            halted = True
            blockers.append(f"Unknown broker type: {BROKER_TYPE}")

        approved = []
        rejected = []
        for opp in candidates:
            reasons = []
            if halted:
                reasons.append("Global safety halt")
            if not opp.get("risk_ok", False):
                reasons.append("Risk flag is false")
            if opp.get("confidence", 0) < MIN_CONFIDENCE:
                reasons.append("Confidence below compliance threshold")
            if opp.get("price", 0) <= 0 or opp.get("max_qty", 0) <= 0:
                reasons.append("Invalid price or quantity")
            if reasons:
                rejected.append({**opp, "compliance_reasons": reasons})
            else:
                approved.append({**opp, "compliance_ok": True})

        approved = approved[:MAX_TRADES_PER_CYCLE]
        report = {
            "halted": halted,
            "blockers": blockers,
            "warnings": warnings,
            "approved_opportunities": approved,
            "rejected_opportunities": rejected,
            "timestamp": time.time(),
        }
        self.memory.write("decisions", "compliance_gate", report)
        self.log(f"Compliance: {len(approved)} approved, {len(rejected)} rejected, halted={halted}")
        return report

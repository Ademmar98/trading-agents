import json
import time
from datetime import datetime, timezone

from config import WATCHED_SYMBOLS, CLAUDE_MODEL, ANTHROPIC_API_KEY
from agents.base_agent import BaseAgent
from core.memory import SharedMemory
from core.portfolio import load_portfolio


class Orchestrator(BaseAgent):
    name = "orchestrator"

    def run(self):
        self.log("Starting orchestration cycle")
        portfolio = load_portfolio()
        prices = self.memory.read("analyses", "market_scan")
        risk_report = self.memory.read("decisions", "risk_assessment")
        audit = self.memory.read("reports", "audit")

        state = {
            "cycle_time": datetime.now(timezone.utc).isoformat(),
            "portfolio": {
                "cash": portfolio.cash,
                "equity": portfolio.equity,
                "positions": len(portfolio.positions),
                "pnl_pct": portfolio.total_pnl_pct,
            },
            "symbols_watching": WATCHED_SYMBOLS,
            "last_analysis": prices.get("summary", {}) if prices else {},
            "risk_status": risk_report.get("verdict", "unknown") if risk_report else "none",
            "recent_performance": audit.get("summary", {}) if audit else {},
        }

        self.memory.write("decisions", "orchestrator_plan", state)
        self.log(f"Cycle planned. Equity: ${portfolio.equity:.2f}, "
                 f"Positions: {len(portfolio.positions)}")

        instructions = []
        if portfolio.equity == 0 or len(portfolio.positions) == 0:
            instructions.append("ANALYST: Scan watched symbols for opportunities")
        if risk_report and risk_report.get("verdict") == "high_risk":
            instructions.append("RISK_MANAGER: Review and reduce exposure")
        if audit and audit.get("needs_rebalance", False):
            instructions.append("TRADER: Rebalance portfolio")

        if not instructions:
            instructions.append("ANALYST: Continue monitoring")
            instructions.append("AUDITOR: Review recent performance")

        self.memory.write("decisions", "instructions", {
            "instructions": instructions,
            "timestamp": time.time(),
        })
        return state

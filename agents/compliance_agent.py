import time

from config import BROKER_TYPE, LEVERAGE_ENABLED, MAX_PORTFOLIO_RISK_PCT, DAILY_LOSS_LIMIT_PCT, MAX_CONSECUTIVE_LOSSES, MAX_TRADES_PER_DAY
from agents.base_agent import BaseAgent
from core.portfolio import load_portfolio
from core.positions import PositionManager
from core.equity import daily_loss_pct
from core.database import fetchall, fetchone
from core.market import is_market_open

MIN_CONFIDENCE = 0.55
MAX_TRADES_PER_CYCLE = 3


class ComplianceAgent(BaseAgent):
    name = "compliance"

    def __init__(self):
        super().__init__()
        self._pos_mgr = PositionManager()

    def run(self):
        self.log("Running safety and compliance gate")
        portfolio = load_portfolio()
        risk = self.memory.read("decisions", "risk_assessment") or {}
        plan = self.memory.read("decisions", "portfolio_plan") or {}
        candidates = plan.get("approved_opportunities", []) or []

        halted = False
        blockers = []
        warnings = []
        health = self.memory.read("reports", "health") or {}
        if health.get("halted"):
            halted = True
            blockers.extend(health.get("issues", []))
        if LEVERAGE_ENABLED:
            halted = True
            blockers.append("Leverage enabled; system is configured for spot-only trading")
        if risk.get("verdict") == "critical":
            halted = True
            blockers.append("Risk verdict is critical")
        if portfolio.total_pnl_pct < -MAX_PORTFOLIO_RISK_PCT:
            halted = True
            blockers.append(f"Portfolio drawdown {portfolio.total_pnl_pct:.2f}% exceeds risk limit")
        day_pnl = daily_loss_pct()
        if day_pnl < -DAILY_LOSS_LIMIT_PCT:
            halted = True
            blockers.append(f"Daily loss {day_pnl:.2f}% breached the {DAILY_LOSS_LIMIT_PCT}% circuit breaker — no new trades today")
        recent = fetchall("SELECT pnl FROM trades ORDER BY closed_at DESC LIMIT ?", [MAX_CONSECUTIVE_LOSSES])
        consecutive_losses = 0
        for row in recent:
            if row["pnl"] < 0:
                consecutive_losses += 1
            else:
                break
        if consecutive_losses >= MAX_CONSECUTIVE_LOSSES:
            halted = True
            blockers.append(f"{consecutive_losses} consecutive losses hit the {MAX_CONSECUTIVE_LOSSES} limit — trading halted")

        if BROKER_TYPE not in {"paper", "binance", "mt5", "alpaca", "dxtrade"}:
            halted = True
            blockers.append(f"Unknown broker type: {BROKER_TYPE}")

        # Trade-frequency cap: overtrading is a consistent loss pattern, and
        # every extra round trip costs fees. Exits are unaffected — this only
        # gates new entries.
        opened_today = fetchone(
            "SELECT COUNT(*) AS c FROM positions WHERE opened_at >= date('now')"
        )
        opened_today = opened_today["c"] if opened_today else 0
        entries_left_today = max(0, MAX_TRADES_PER_DAY - opened_today)
        if entries_left_today == 0:
            warnings.append(
                f"Daily trade cap reached ({opened_today}/{MAX_TRADES_PER_DAY}) — no new entries until tomorrow")

        approved = []
        rejected = []
        for opp in candidates:
            reasons = []
            if halted:
                reasons.append("Global safety halt")
            if not opp.get("risk_ok", False):
                reasons.append("Risk flag is false")
            if opp.get("action", "BUY") == "SELL":
                held = portfolio.positions.get(opp.get("symbol"))
                if not held or held.quantity <= 0:
                    reasons.append("Spot-only: SELL without holdings would open a short")
            if opp.get("confidence", 0) < MIN_CONFIDENCE:
                reasons.append("Confidence below compliance threshold")
            if not is_market_open(opp.get("symbol", "")):
                reasons.append("Market closed for this symbol")
            if opp.get("price", 0) <= 0 or opp.get("max_qty", 0) <= 0:
                reasons.append("Invalid price or quantity")
            if self._pos_mgr.has_position(opp.get("symbol", "")):
                reasons.append("Position already open")
            if reasons:
                rejected.append({**opp, "compliance_reasons": reasons})
            else:
                approved.append({**opp, "compliance_ok": True})

        approved = approved[:min(MAX_TRADES_PER_CYCLE, entries_left_today)]
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
        if halted:
            self.notifier.on_agent_action("compliance", f"HALTED — {'; '.join(blockers[:2])}")
        elif rejected:
            self.notifier.on_agent_action("compliance", f"{len(approved)} approved, {len(rejected)} rejected")
        return report

import time

from config import (
    BROKER_TYPE, LEVERAGE_ENABLED, MAX_PORTFOLIO_RISK_PCT, DAILY_LOSS_LIMIT_PCT,
    MAX_CONSECUTIVE_LOSSES, MAX_TRADES_PER_DAY, MAX_TRADES_PER_HOUR,
    MAX_OPEN_RISK_PCT, MAX_POSITIONS_PER_CLUSTER, MAX_GROSS_LEVERAGE,
    MAX_GROUP_POSITIONS, GROUP_OVERRIDE_CONF, MACRO_DIP_OVERRIDE_CONF,
)
from core.risk import count_group_positions, macro_dip_alert
from agents.base_agent import BaseAgent
from core.portfolio import load_portfolio
from core.positions import PositionManager
from core.equity import daily_loss_pct
from core.database import fetchall, fetchone
from core.market import is_market_open, classify_symbol


def _position_open_risk(p):
    """Dollar risk a position still carries: distance to its stop x quantity.
    A stop at/past entry (breakeven runner) carries zero risk; a position
    without a stop is assumed to risk its full initial 1R distance."""
    sl = p.get("stop_loss") or 0
    if not sl:
        return (p.get("initial_risk") or 0) * p["quantity"]
    if p["side"] == "BUY":
        return max(0.0, p["entry_price"] - sl) * p["quantity"]
    return max(0.0, sl - p["entry_price"]) * p["quantity"]

MIN_CONFIDENCE = 0.55
MAX_TRADES_PER_CYCLE = 999999


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

        # Optional trade-frequency caps (0 = unlimited). Exits are unaffected —
        # these only gate new entries.
        entries_left = None
        if MAX_TRADES_PER_DAY > 0:
            row = fetchone("SELECT COUNT(*) AS c FROM positions WHERE opened_at >= date('now')")
            opened_today = row["c"] if row else 0
            entries_left = max(0, MAX_TRADES_PER_DAY - opened_today)
            if entries_left == 0:
                warnings.append(
                    f"Daily trade cap reached ({opened_today}/{MAX_TRADES_PER_DAY}) — no new entries until tomorrow")
        if MAX_TRADES_PER_HOUR > 0:
            row = fetchone("SELECT COUNT(*) AS c FROM positions WHERE opened_at >= datetime('now', '-1 hour')")
            opened_hour = row["c"] if row else 0
            hour_left = max(0, MAX_TRADES_PER_HOUR - opened_hour)
            if hour_left == 0 and (entries_left is None or entries_left > 0):
                warnings.append(
                    f"Hourly pacing cap reached ({opened_hour}/{MAX_TRADES_PER_HOUR}) — resumes within the hour")
            entries_left = hour_left if entries_left is None else min(entries_left, hour_left)

        # Portfolio heat + cluster concentration, computed once per cycle.
        # New approvals this cycle aren't re-counted (MAX_TRADES_PER_CYCLE
        # bounds the overshoot to a couple of positions).
        open_positions = self._pos_mgr.get_open_positions()
        equity = portfolio.equity or 1
        heat_pct = sum(_position_open_risk(p) for p in open_positions) / equity * 100
        heat_full = MAX_OPEN_RISK_PCT > 0 and heat_pct >= MAX_OPEN_RISK_PCT
        if heat_full:
            warnings.append(
                f"Portfolio heat {heat_pct:.1f}% >= {MAX_OPEN_RISK_PCT}% cap — new entries paused until risk unwinds")
        cluster_counts = {}
        for p in open_positions:
            k = classify_symbol(p["symbol"])
            cluster_counts[k] = cluster_counts.get(k, 0) + 1

        # No-leverage policy (halal requirement): total open notional must
        # stay within equity x MAX_GROSS_LEVERAGE — strict cash-only trading.
        # Approvals within this cycle count toward the budget immediately.
        gross_notional = sum(
            (p.get("current_price") or p.get("entry_price") or 0) * p["quantity"]
            for p in open_positions
        )
        approved_notional = 0.0

        # Correlated-selloff defenses: positions in the same correlation
        # group move as one asset — count them together (open + approved
        # this cycle); and read the analyst's bellwether momentum so a whole
        # class pauses mid-dip.
        scan = self.memory.read("analyses", "market_scan") or {}
        bellwether_moves = scan.get("bellwether_moves") or {}
        held_symbols = [p["symbol"] for p in open_positions]

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
            if heat_full:
                reasons.append(f"Portfolio heat {heat_pct:.1f}% at cap")
            if MAX_POSITIONS_PER_CLUSTER > 0:
                cluster = classify_symbol(opp.get("symbol", ""))
                if cluster_counts.get(cluster, 0) >= MAX_POSITIONS_PER_CLUSTER:
                    reasons.append(
                        f"Cluster '{cluster}' already holds {cluster_counts[cluster]} positions (cap {MAX_POSITIONS_PER_CLUSTER})")
            group_n = count_group_positions(opp.get("symbol", ""), held_symbols)
            if (MAX_GROUP_POSITIONS > 0 and group_n >= MAX_GROUP_POSITIONS
                    and opp.get("confidence", 0) < GROUP_OVERRIDE_CONF):
                reasons.append(
                    f"Correlation group already holds {group_n} positions "
                    f"(cap {MAX_GROUP_POSITIONS}; override needs conf >= {GROUP_OVERRIDE_CONF:g})")
            cluster = classify_symbol(opp.get("symbol", ""))
            if (macro_dip_alert(cluster, bellwether_moves)
                    and opp.get("confidence", 0) < MACRO_DIP_OVERRIDE_CONF):
                reasons.append(
                    f"Macro dip interlock: {cluster} bellwether "
                    f"{bellwether_moves.get(cluster)}% in 30m — new entries paused")
            candidate_notional = (opp.get("price") or 0) * (opp.get("max_qty") or 0)
            if MAX_GROSS_LEVERAGE > 0 and (
                gross_notional + approved_notional + candidate_notional
                > portfolio.equity * MAX_GROSS_LEVERAGE
            ):
                reasons.append(
                    f"No-leverage policy: gross notional would exceed {MAX_GROSS_LEVERAGE:g}x equity")
            if opp.get("price", 0) <= 0 or opp.get("max_qty", 0) <= 0:
                reasons.append("Invalid price or quantity")
            if self._pos_mgr.has_position(opp.get("symbol", "")):
                reasons.append("Position already open")
            if reasons:
                rejected.append({**opp, "compliance_reasons": reasons})
            else:
                approved.append({**opp, "compliance_ok": True})
                approved_notional += candidate_notional
                held_symbols.append(opp.get("symbol", ""))  # counts toward group caps this cycle

        cycle_cap = MAX_TRADES_PER_CYCLE if entries_left is None else min(MAX_TRADES_PER_CYCLE, entries_left)
        approved = approved[:cycle_cap]
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

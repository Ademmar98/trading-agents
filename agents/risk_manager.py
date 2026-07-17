import time

from config import MAX_POSITION_SIZE_PCT, MAX_PORTFOLIO_RISK_PCT, LEVERAGE_ENABLED, MAX_PAIR_CORRELATION
from agents.base_agent import BaseAgent
from core.correlation import pearson
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
                remaining_capacity = portfolio.equity * max(0, (MAX_POSITION_SIZE_PCT - current_exposure) / 100)
                max_cost = min(max_trade_size, remaining_capacity)
                adjusted["max_qty"] = round(max_cost / opp["price"], 6) if opp["price"] > 0 else 0
                adjusted["risk_ok"] = True
                # Correlation gate: candidates moving in lockstep with an
                # open position add beta, not diversification. Halving size
                # still let the same cluster through the door (post-mortem
                # 2026-07-12: six correlated alts stopped together in one
                # dip), so past the threshold the entry is BLOCKED outright.
                corr_hit = self._max_correlation(sym, analysis, portfolio)
                if corr_hit:
                    other, corr = corr_hit
                    adjusted["max_qty"] = 0
                    adjusted["risk_ok"] = False
                    risks.append(
                        f"SKIPPED {sym}: {corr:.2f} correlated with open {other} "
                        f"— blocked (beta, not diversification)")
            filtered.append(adjusted)

        pnl = portfolio.total_pnl_pct
        if pnl < -MAX_PORTFOLIO_RISK_PCT:
            risk_verdict = "critical"
            risks.append(f"EMERGENCY: Portfolio down {pnl:.1f}%")

        report = {
            "verdict": risk_verdict,
            "correlation_threshold": MAX_PAIR_CORRELATION,
            "exposure_pct": round(exposure, 2),
            "concentration_pct": round(concentration, 2),
            "max_trade_size": round(max_trade_size, 2),
            "risks": risks,
            "approved_opportunities": filtered,
            "timestamp": time.time(),
        }
        self.memory.write("decisions", "risk_assessment", report)
        self.log(f"Risk verdict: {risk_verdict}, {len(risks)} warnings")
        return self._finish(report, risk_verdict, exposure, risks)

    @staticmethod
    def _max_correlation(sym, analysis, portfolio):
        """Highest 30d-return correlation between the candidate and any open
        position, computed from the analyst's shared-memory scan (no
        fetching here). Returns (other_symbol, corr) past the threshold,
        else None. Fails open on missing data."""
        if MAX_PAIR_CORRELATION <= 0 or not portfolio.positions:
            return None
        all_analyses = (analysis or {}).get("all_analyses", {}) or {}
        mine = (all_analyses.get(sym) or {}).get("returns_30d") or []
        if len(mine) < 10:
            return None
        worst = None
        for other in portfolio.positions:
            if other == sym:
                continue
            theirs = (all_analyses.get(other) or {}).get("returns_30d") or []
            corr = pearson(mine, theirs)
            if corr is not None and abs(corr) >= MAX_PAIR_CORRELATION:
                if worst is None or abs(corr) > abs(worst[1]):
                    worst = (other, corr)
        return worst

    def _finish(self, report, risk_verdict, exposure, risks):
        if risk_verdict in ("high_risk", "critical"):
            self.notifier.on_agent_action("risk_manager", f"verdict={risk_verdict} | exposure {exposure:.0f}% | {len(risks)} warnings")
        return report

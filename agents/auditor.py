import time

from agents.base_agent import BaseAgent
from core.portfolio import load_portfolio
from core.memory import SharedMemory
from core.analytics import compute_analytics, get_analytics


class Auditor(BaseAgent):
    name = "auditor"

    def run(self):
        self.log("Auditing performance and suggesting improvements")
        portfolio = load_portfolio()
        trade_log = self.memory.read_latest("orders")
        logs = self.memory.get_recent_logs(50)
        analytics = compute_analytics()

        total_trades = len(portfolio.trades)
        winning_trades = sum(1 for t in portfolio.trades
                            if t.get("realized_pnl", 0) > 0)
        losing_trades = sum(1 for t in portfolio.trades
                           if t.get("realized_pnl", 0) < 0)
        win_rate = (winning_trades / total_trades * 100
                   ) if total_trades > 0 else 0

        suggestions = []
        needs_rebalance = False

        if portfolio.exposure_pct < 10 and portfolio.cash > 100:
            suggestions.append("Low exposure — consider deploying more capital")
        if win_rate < 40 and total_trades > 5:
            suggestions.append("Win rate below 40% — tighten entry criteria")
        if portfolio.total_pnl_pct < -5:
            suggestions.append("Drawdown exceeds 5% — review risk parameters")
            needs_rebalance = True
        if portfolio.positions and portfolio.exposure_pct > 80:
            suggestions.append("Over-exposed — reduce positions")
            needs_rebalance = True
        if analytics.get("sharpe_ratio", 0) < 0.5 and analytics["total_trades"] > 5:
            suggestions.append(f"Sharpe {analytics['sharpe_ratio']} — improve risk-adjusted returns")
        if analytics.get("profit_factor") and analytics["profit_factor"] < 1.2 and analytics["total_trades"] > 5:
            suggestions.append(f"Profit factor {analytics['profit_factor']} — cut losers faster")

        agent_health = {}
        for entry in logs:
            agent = entry.get("agent", "unknown")
            if agent not in agent_health:
                agent_health[agent] = {"messages": 0, "last_seen": 0}
            agent_health[agent]["messages"] += 1
            agent_health[agent]["last_seen"] = entry.get("time", 0)

        report = {
            "summary": {
                "total_trades": total_trades,
                "win_rate": round(win_rate, 1),
                "total_pnl": round(portfolio.total_pnl, 2),
                "total_pnl_pct": round(portfolio.total_pnl_pct, 2),
                "current_exposure": round(portfolio.exposure_pct, 1),
                "positions": len(portfolio.positions),
                "analytics": {
                    "sharpe": analytics["sharpe_ratio"],
                    "profit_factor": analytics["profit_factor"],
                    "max_drawdown": analytics["max_drawdown"],
                    "expectancy": analytics["expectancy"],
                },
            },
            "suggestions": suggestions,
            "needs_rebalance": needs_rebalance,
            "agent_activity": agent_health,
            "timestamp": time.time(),
        }

        self.memory.write("reports", "audit", report)
        self.log(f"Audit: {total_trades} trades, {win_rate:.0f}% win rate, "
                 f"{len(suggestions)} suggestions")
        return report

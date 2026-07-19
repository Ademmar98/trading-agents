import time

from config import TRADING_INTERVAL_MINUTES
from agents.base_agent import BaseAgent
from core.portfolio import load_portfolio
from core.memory import SharedMemory
from core import websocket_prices


class HealthMonitor(BaseAgent):
    name = "health"

    def run(self):
        self.log("Checking system health")
        issues = []
        warnings = []
        halted = False

        analysis = self.memory.read("analyses", "market_scan") or {}
        ts = analysis.get("timestamp", 0)
        stale_seconds = (TRADING_INTERVAL_MINUTES * 60) * 2
        # Liveness means PROGRESS, not latency. A 400+-symbol scan lawfully
        # takes longer than the nominal interval, and health runs before the
        # analyst each cycle — so the last scan's wall-clock age exceeds
        # 2x interval on every healthy-but-slow cycle. Halting on age alone
        # froze all entries for 38h straight (every cycle: scan ~150s old vs
        # 120s threshold). Halt only when the scanner stops producing NEW
        # scans past a hard ceiling; a slow-but-advancing scan just warns.
        prev_report = self.memory.read("reports", "health") or {}
        prev_scan_ts = prev_report.get("last_scan_ts", 0)
        age = time.time() - ts if ts else 0
        hard_ceiling = max(TRADING_INTERVAL_MINUTES * 60 * 10, 600)
        if ts and ts == prev_scan_ts and age > hard_ceiling:
            issues.append(f"market_scan stalled ({int(age)}s old, no new scan since last check)")
            halted = True
        elif ts and age > stale_seconds:
            warnings.append(f"market_scan slow ({int(age)}s old — cycle exceeds the "
                            f"{TRADING_INTERVAL_MINUTES}m interval)")

        errors = self.memory.get_recent_errors(10)
        recent_errors = [e for e in errors if time.time() - e.get("time", 0) < stale_seconds]
        if len(recent_errors) > 5:
            issues.append(f"{len(recent_errors)} errors in last cycle")
            halted = True
        elif len(recent_errors) > 2:
            warnings.append(f"{len(recent_errors)} errors recently")

        prices = websocket_prices.get_all_prices()
        if not prices:
            warnings.append("WebSocket price feed empty")

        portfolio = load_portfolio()
        if portfolio.cash <= 0 and not portfolio.positions:
            warnings.append("No cash and no positions — possible data issue")

        agents_log = self.memory.get_recent_logs(30)
        agent_counts = {}
        for entry in agents_log:
            agent_counts[entry.get("agent", "?")] = agent_counts.get(entry.get("agent", "?"), 0) + 1
        all_agents = {"orchestrator", "analyst", "sentiment", "regime", "risk_manager",
                      "portfolio_manager", "compliance", "execution", "trader", "auditor"}
        missing = all_agents - set(agent_counts.keys())
        if missing:
            warnings.append(f"Agents with no output: {', '.join(sorted(missing))}")

        status = "halted" if halted else "degraded" if issues else ("warning" if warnings else "ok")
        report = {
            "status": status,
            "halted": halted,
            "issues": issues,
            "warnings": warnings,
            "errors_last_cycle": len(recent_errors),
            "agents_active": len(agent_counts),
            "price_feed_alive": bool(prices),
            "last_scan_ts": ts,
            "timestamp": time.time(),
        }
        self.memory.write("reports", "health", report)
        msg = f"Health: {status}"
        if issues:
            msg += f", {len(issues)} issues"
        if warnings:
            msg += f", {len(warnings)} warnings"
        self.log(msg)
        if status != "ok":
            self.notifier.on_agent_action("health", f"status={status} | {len(issues)} issues {len(warnings)} warnings")
        return report

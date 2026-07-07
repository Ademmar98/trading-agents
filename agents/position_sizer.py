import time

from agents.base_agent import BaseAgent
from core.database import fetchall


class PositionSizer(BaseAgent):
    name = "position_sizer"

    def run(self):
        self.log("Applying Kelly Criterion and volatility-adjusted sizing")
        risk = self.memory.read("decisions", "risk_assessment") or {}
        regimes = self.memory.read("analyses", "regime_scan") or {}
        opportunities = risk.get("approved_opportunities", []) or []

        kelly_pct = self._kelly_fraction()

        sized = []
        for opp in opportunities:
            item = {**opp}
            symbol = opp.get("symbol")
            max_qty = opp.get("max_qty", 0)
            price = opp.get("price", 0)
            action = opp.get("action", "BUY")

            size_mult = kelly_pct / 25.0 if kelly_pct else 1.0
            size_mult = max(0.25, min(size_mult, 1.0))

            reg = (regimes.get("symbols", {}) or {}).get(symbol, {})
            vol = reg.get("volatility", 0)
            if vol > 6:
                size_mult *= 0.50
            elif vol > 4:
                size_mult *= 0.75

            item["max_qty"] = round(max_qty * size_mult, 8)
            item["size_mult"] = round(size_mult, 3)
            item["kelly_pct"] = round(kelly_pct, 2)
            item["sizing_notes"] = []
            if size_mult < 0.5:
                item["sizing_notes"].append(f"Kelly reduced size to {size_mult:.0%}")
            if vol > 4:
                item["sizing_notes"].append(f"Volatility {vol}% cut size")
            sized.append(item)

        report = {
            "sized_opportunities": sized,
            "kelly_fraction_pct": round(kelly_pct, 2),
            "timestamp": time.time(),
        }
        self.memory.write("decisions", "position_sizing", report)
        self.log(f"Sizing: Kelly {kelly_pct:.1f}%, {len(sized)} opportunities sized")
        if kelly_pct < 10:
            self.notifier.on_agent_action("position_sizer", f"Kelly {kelly_pct:.1f}% — aggressive size reduction")
        return report

    @staticmethod
    def _kelly_fraction():
        trades = fetchall("SELECT pnl FROM trades WHERE pnl IS NOT NULL")
        if len(trades) < 5:
            return 25.0
        pnls = [t["pnl"] for t in trades]
        winning = [p for p in pnls if p > 0]
        losing = [abs(p) for p in pnls if p < 0]
        if not winning or not losing:
            return 25.0
        win_rate = len(winning) / len(pnls)
        avg_win = sum(winning) / len(winning)
        avg_loss = sum(losing) / len(losing)
        if avg_loss == 0:
            avg_loss = 1
        avg_win = max(avg_win, 0.01)
        b = avg_win / avg_loss
        q = 1.0 - win_rate
        if b <= 0:
            return 25.0
        kelly = (win_rate * b - q) / b
        kelly = max(0, min(kelly * 100, 25.0))
        return kelly

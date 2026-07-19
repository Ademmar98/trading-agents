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

            # Kelly legitimately computes 0 on a negative-edge book; the old
            # falsy branch (`if kelly_pct else 1.0`) turned that into FULL
            # size. Unconditional now, floored at 0: no edge -> no size.
            size_mult = kelly_pct / 25.0
            size_mult = max(0.0, min(size_mult, 1.0))

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
        # One R-multiple per POSITION, not per trade row:
        #  - scaled exits (partial_tp + runner) write several rows under one
        #    position_id; ungrouped they counted as separate "wins";
        #  - raw-dollar pnl pools strategies with different risk budgets, so
        #    big-notional trades drowned out the rest. Normalizing by the
        #    position's frozen 1R risk (positions.initial_risk x closed qty)
        #    makes Kelly measure edge, not size.
        rows = fetchall("""
            SELECT SUM(t.pnl) AS pnl, SUM(t.qty) AS qty,
                   MAX(p.initial_risk) AS initial_risk
            FROM trades t
            LEFT JOIN positions p ON t.position_id = p.id
            WHERE t.pnl IS NOT NULL
            GROUP BY COALESCE(t.position_id, t.id)
        """)
        r_multiples = []
        for r in rows:
            risk = (r["initial_risk"] or 0) * (r["qty"] or 0)
            if risk <= 0:
                # Legacy/manual rows without a frozen 1R basis have no honest
                # R — skip them rather than distort the sample.
                continue
            r_multiples.append(r["pnl"] / risk)
        # Kelly on a handful of trades is noise; require a real track record.
        if len(r_multiples) < 30:
            return 25.0
        winning = [x for x in r_multiples if x > 0]
        losing = [abs(x) for x in r_multiples if x < 0]
        if not winning or not losing:
            return 25.0
        win_rate = len(winning) / len(r_multiples)
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

import time

from agents.base_agent import BaseAgent

MAX_SPREAD_PCT = 0.35
SL_VOL_MULT = 1.5
TP_VOL_MULT = 3.0


class ExecutionAgent(BaseAgent):
    name = "execution"

    def run(self):
        self.log("Preparing executable orders with spread and slippage checks")
        gate = self.memory.read("decisions", "compliance_gate") or {}
        analysis = self.memory.read("analyses", "market_scan") or {}
        all_analyses = analysis.get("all_analyses", {}) or {}
        executable = []
        rejected = []

        if gate.get("halted"):
            report = {"status": "halted", "orders": [], "rejected": [], "timestamp": time.time()}
            self.memory.write("orders", "execution_plan", report)
            self.log("Execution halted by compliance")
            return report

        for opp in gate.get("approved_opportunities", []) or []:
            symbol = opp.get("symbol")
            data = all_analyses.get(symbol, {}) if isinstance(all_analyses, dict) else {}
            price = opp.get("price") or data.get("price") or data.get("current_price") or 0
            bid = data.get("bid") or price
            ask = data.get("ask") or price
            spread_pct = ((ask - bid) / price * 100) if price and ask and bid and ask >= bid else 0
            if spread_pct > MAX_SPREAD_PCT:
                rejected.append({**opp, "execution_reasons": [f"Spread too wide: {spread_pct:.2f}%"]})
                continue

            qty = round(opp.get("max_qty", 0) * opp.get("confidence", 0.5), 8)
            if qty <= 0:
                rejected.append({**opp, "execution_reasons": ["Computed quantity is zero"]})
                continue

            vol = data.get("volatility") or (opp.get("indicators", {}) or {}).get("volatility") or 1.0
            vol_decimal = max(vol / 100.0, 0.003)
            action = opp.get("action", "BUY")
            if action == "BUY":
                sl_price = round(price * (1 - vol_decimal * SL_VOL_MULT), 5)
                tp_price = round(price * (1 + vol_decimal * TP_VOL_MULT), 5)
            else:
                sl_price = round(price * (1 + vol_decimal * SL_VOL_MULT), 5)
                tp_price = round(price * (1 - vol_decimal * TP_VOL_MULT), 5)

            executable.append({
                **opp,
                "qty": qty,
                "price": price,
                "stop_loss": sl_price,
                "take_profit": tp_price,
                "spread_pct": round(spread_pct, 4),
                "execution_ok": True,
            })

        report = {
            "status": "ready",
            "orders": executable,
            "rejected": rejected,
            "timestamp": time.time(),
        }
        self.memory.write("orders", "execution_plan", report)
        self.log(f"Execution plan: {len(executable)} ready, {len(rejected)} rejected")
        return report

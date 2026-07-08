import time
from datetime import datetime, timezone

from config import SL_VOL_MULT, TP_VOL_MULT, MIN_TP_PCT, RISK_PER_TRADE_PCT
from agents.base_agent import BaseAgent
from core.database import save_plan, update_plan_status
from core.portfolio import load_portfolio
from core.positions import PositionManager

MAX_SPREAD_PCT = 0.35


class ExecutionAgent(BaseAgent):
    name = "execution"

    def __init__(self):
        super().__init__()
        self._pos_mgr = PositionManager()

    def run(self):
        self.log("Preparing executable orders with spread and slippage checks")
        gate = self.memory.read("decisions", "compliance_gate") or {}
        analysis = self.memory.read("analyses", "market_scan") or {}
        all_analyses = analysis.get("all_analyses", {}) or {}
        pricing = self.memory.read("decisions", "pricing") or {}
        pricing_map = pricing.get("pricing_map", {}) or {}
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

            if self._pos_mgr.has_position(symbol):
                rejected.append({**opp, "execution_reasons": ["Position already open for this symbol"]})
                continue

            qty = round(opp.get("max_qty", 0), 8)
            if qty <= 0:
                rejected.append({**opp, "execution_reasons": ["Computed quantity is zero"]})
                continue

            pricing_entry = pricing_map.get(symbol) if isinstance(pricing_map, dict) else None
            if pricing_entry:
                entry_price = pricing_entry.get("entry_price", price)
                sl_price = pricing_entry.get("stop_loss", 0)
                tp_price = pricing_entry.get("take_profit", 0)
                sl_pct = pricing_entry.get("sl_pct", 0)
                tp_pct = pricing_entry.get("tp_pct", 0)
                risk_pct = pricing_entry.get("calculated_risk_pct", RISK_PER_TRADE_PCT)
            else:
                vol = data.get("volatility") or (opp.get("indicators", {}) or {}).get("volatility") or 2.0
                vol_decimal = max(vol / 100.0, 0.005)
                action = opp.get("action", "BUY")
                if action == "BUY":
                    sl_pct = vol_decimal * SL_VOL_MULT * 100
                    tp_pct = vol_decimal * TP_VOL_MULT * 100
                    sl_price = round(price * (1 - vol_decimal * SL_VOL_MULT), 5)
                    tp_price = round(price * (1 + vol_decimal * TP_VOL_MULT), 5)
                else:
                    sl_pct = vol_decimal * SL_VOL_MULT * 100
                    tp_pct = vol_decimal * TP_VOL_MULT * 100
                    sl_price = round(price * (1 + vol_decimal * SL_VOL_MULT), 5)
                    tp_price = round(price * (1 - vol_decimal * TP_VOL_MULT), 5)
                entry_price = price
                risk_pct = RISK_PER_TRADE_PCT

            if tp_pct < MIN_TP_PCT:
                rejected.append({**opp, "execution_reasons": [f"TP too small: {tp_pct:.1f}% < {MIN_TP_PCT}%"]})
                continue

            risk_amount = load_portfolio().equity * (risk_pct / 100)
            if sl_price and entry_price:
                risk_per_unit = abs(entry_price - sl_price)
                if risk_per_unit > 0:
                    risk_capped_qty = risk_amount / risk_per_unit
                    if risk_capped_qty < qty:
                        qty = round(risk_capped_qty, 8)

            action = opp.get("action", "BUY")
            plan_id = f"plan_{symbol}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S%f')}"
            rr = round(tp_pct / sl_pct, 2) if sl_pct > 0 else 0
            plan_entry = {
                "plan_id": plan_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "symbol": symbol,
                "direction": action,
                "entry_price": entry_price,
                "stop_loss": sl_price,
                "take_profit": tp_price,
                "position_size_usd": round(entry_price * qty, 2),
                "position_size_units": qty,
                "confidence": opp.get("confidence", 0),
                "strategy": (opp.get("strategies") or [None])[0] if opp.get("strategies") else "",
                "regime": opp.get("regime", ""),
                "rationale": ", ".join(opp.get("reasons", [])[:3]),
                "risk_reward_ratio": rr,
                "status": "created",
            }
            save_plan(plan_entry)

            executable.append({
                **opp,
                "qty": qty,
                "price": entry_price,
                "entry_price": entry_price,
                "stop_loss": sl_price,
                "take_profit": tp_price,
                "tp_pct": round(tp_pct, 1),
                "sl_pct": round(sl_pct, 1),
                "spread_pct": round(spread_pct, 4),
                "execution_ok": True,
                "plan_id": plan_id,
            })

        report = {
            "status": "ready",
            "orders": executable,
            "rejected": rejected,
            "timestamp": time.time(),
        }
        self.memory.write("orders", "execution_plan", report)
        self.log(f"Execution plan: {len(executable)} ready, {len(rejected)} rejected")
        if executable:
            e = executable[0]
            self.notifier.on_agent_action(
                "execution", f"{len(executable)} orders ready | top: {e['action']} {e['qty']} {e['symbol']} SL={e['sl_pct']:.1f}% TP={e['tp_pct']:.1f}%"
            )
        return report

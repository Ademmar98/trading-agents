import time
from datetime import datetime, timezone

from config import (
    SL_VOL_MULT, TP_VOL_MULT, MIN_TP_PCT, RISK_PER_TRADE_PCT, TRADE_FEE_PCT,
    SCALP_MIN_WIN_PROB, SCALP_ATR_SL_MULT, MAX_SL_PCT, MAX_TP_PCT,
    BROKEN_SL_PCT, SWING_MAX_SL_PCT,
)
from agents.base_agent import BaseAgent
from core.database import save_plan, update_plan_status
from core.portfolio import load_portfolio
from core.positions import PositionManager
from core.risk import session_risk_mult
from core.scalp15 import atr_position_size

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

            # Predictive gate for the 15m scalp stack, right before routing:
            # setups whose estimated win probability misses the bar are
            # aborted outright. The estimate is a smoothed empirical win rate
            # + synergy bonus — an honest heuristic, not a calibrated
            # probability, so the bar is env-tunable (SCALP_MIN_WIN_PROB).
            is_scalp = "scalp_15m" in (opp.get("strategies") or [])
            if is_scalp:
                wp = opp.get("win_prob", 0)
                if wp < SCALP_MIN_WIN_PROB:
                    rejected.append({**opp, "execution_reasons": [
                        f"Scalp win probability {wp:.0%} < {SCALP_MIN_WIN_PROB:.0%} gate"]})
                    continue
                # Size through the position-sizer skill's ATR method:
                # qty = (equity x risk%) / (ATR x multiplier)
                if opp.get("atr"):
                    skill_qty = atr_position_size(
                        load_portfolio().equity, opp["atr"], SCALP_ATR_SL_MULT,
                        opp.get("calculated_risk_pct", RISK_PER_TRADE_PCT))
                    if skill_qty > 0:
                        qty = round(min(qty, skill_qty), 8)

            pricing_entry = pricing_map.get(symbol) if isinstance(pricing_map, dict) else None
            if opp.get("stop_loss") and opp.get("take_profit") and opp.get("entry_price"):
                # The signal carries its own geometry (scalp and swing styles,
                # analyst-priced classics) — another opportunity's pricing_map
                # slot must never override it, or a swing entry would inherit
                # a scalp's 1% stop.
                pricing_entry = opp
            if pricing_entry and pricing_entry.get("action") != opp.get("action", "BUY"):
                # Pricing computed for the opposite direction — its SL/TP would
                # sit on the wrong side of entry; use inline pricing instead.
                pricing_entry = None
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
                sl_pct = min(sl_pct, MAX_SL_PCT)
                tp_pct = min(tp_pct, MAX_TP_PCT)
                if action == "BUY":
                    sl_price = round(price * (1 - sl_pct / 100), 5)
                    tp_price = round(price * (1 + tp_pct / 100), 5)
                else:
                    sl_price = round(price * (1 + sl_pct / 100), 5)
                    tp_price = round(price * (1 - tp_pct / 100), 5)
                entry_price = price
                risk_pct = RISK_PER_TRADE_PCT

            # Broken-geometry guard: a BUY whose TP sits at/below entry or
            # whose SL sits at/above entry is corrupt data, not a trade; a
            # stop farther than BROKEN_SL_PCT from entry means the volatility
            # inputs are garbage. Never route these — reject and alert.
            side = opp.get("action", "BUY")
            is_swing = any(str(s).startswith("swing")
                           for s in (opp.get("strategies") or []))
            sane_sl_bound = SWING_MAX_SL_PCT if is_swing else BROKEN_SL_PCT
            broken = None
            if side == "BUY":
                if tp_price <= entry_price:
                    broken = f"TP ${tp_price:g} at/below entry ${entry_price:g}"
                elif sl_price >= entry_price:
                    broken = f"SL ${sl_price:g} at/above entry ${entry_price:g}"
            else:
                if tp_price >= entry_price:
                    broken = f"TP ${tp_price:g} at/above entry ${entry_price:g} (SELL)"
                elif sl_price <= entry_price:
                    broken = f"SL ${sl_price:g} at/below entry ${entry_price:g} (SELL)"
            sl_dist_pct = (abs(entry_price - sl_price) / entry_price * 100) if entry_price else 0
            if not broken and sl_dist_pct > sane_sl_bound:
                broken = f"SL {sl_dist_pct:.1f}% from entry (> {sane_sl_bound:g}% sanity bound)"
            if broken:
                rejected.append({**opp, "execution_reasons": [f"Broken geometry: {broken}"]})
                self.notifier.on_rejected_signal(symbol, broken)
                continue

            # TP must clear the full cost of the round trip (entry fee + exit
            # fee + spread) with margin, or the trade loses money even when it wins.
            round_trip_cost = 2 * TRADE_FEE_PCT + spread_pct
            min_viable_tp = max(MIN_TP_PCT, round_trip_cost * 1.5)
            if tp_pct < min_viable_tp:
                rejected.append({**opp, "execution_reasons": [
                    f"TP too small: {tp_pct:.2f}% < {min_viable_tp:.2f}% (fees+spread {round_trip_cost:.2f}%)"]})
                continue

            # Session-aware sizing: Asian-session moves are sharper and fills
            # worse — risk half size there (SESSION_RISK_MULTS)
            risk_amount = load_portfolio().equity * (risk_pct / 100) * session_risk_mult()
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
                "execution", f"{len(executable)} orders ready | top: {e['action']} {e['symbol']} x{e['qty']:g} SL={e['sl_pct']:.1f}% TP={e['tp_pct']:.1f}%"
            )
        return report

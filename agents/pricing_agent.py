import time

from agents.base_agent import BaseAgent
from config import SL_VOL_MULT, TP_VOL_MULT, RISK_PER_TRADE_PCT


_REGIME_PRICING = {
    "trending_up":   {"sl_mult": 2.0, "tp_mult": 5.0, "entry_slip": 0.001, "risk_mult": 1.10},
    "trending_down": {"sl_mult": 2.0, "tp_mult": 5.0, "entry_slip": 0.001, "risk_mult": 1.10},
    "trending":      {"sl_mult": 2.0, "tp_mult": 4.5, "entry_slip": 0.002, "risk_mult": 1.00},
    "volatile":      {"sl_mult": 3.0, "tp_mult": 6.0, "entry_slip": 0.003, "risk_mult": 0.85},
    "ranging":       {"sl_mult": 2.5, "tp_mult": 3.5, "entry_slip": 0.002, "risk_mult": 0.90},
}

_DEFAULT_PRICING = {"sl_mult": 2.5, "tp_mult": 4.0, "entry_slip": 0.002, "risk_mult": 0.90}


class PricingAgent(BaseAgent):
    name = "pricing"

    def run(self):
        self.log("Computing dynamic entry, stop-loss, and take-profit prices")
        analysis = self.memory.read("analyses", "market_scan") or {}
        opportunities = analysis.get("opportunities", []) or []
        all_analyses = analysis.get("all_analyses", {}) or {}
        regimes = self.memory.read("analyses", "regime_scan") or {}
        regime_symbols = regimes.get("symbols", {}) or {}

        pricing_map = {}
        for opp in opportunities:
            symbol = opp.get("symbol")
            action = opp.get("action", "BUY")
            price = opp.get("price") or 0
            data = (all_analyses.get(symbol) or {}) if isinstance(all_analyses, dict) else {}
            data_price = data.get("price") or data.get("current_price") or 0
            price = price or data_price
            if price <= 0:
                continue

            vol = data.get("volatility") or (opp.get("indicators", {}) or {}).get("volatility") or 2.0
            vol_dec = max(vol / 100.0, 0.005)

            reg = regime_symbols.get(symbol, {})
            regime = reg.get("regime", "unknown")
            cfg = _REGIME_PRICING.get(regime, _DEFAULT_PRICING)

            sl_mult = cfg["sl_mult"]
            tp_mult = cfg["tp_mult"]
            risk_mult = cfg["risk_mult"]

            bid = data.get("bid") or price
            ask = data.get("ask") or price
            if action == "BUY":
                entry_price = round(bid, 5)
                sl_price = round(price * (1 - vol_dec * sl_mult), 5)
                tp_price = round(price * (1 + vol_dec * tp_mult), 5)
            else:
                entry_price = round(ask, 5)
                sl_price = round(price * (1 + vol_dec * sl_mult), 5)
                tp_price = round(price * (1 - vol_dec * tp_mult), 5)

            sl_pct = vol_dec * sl_mult * 100
            tp_pct = vol_dec * tp_mult * 100
            base_risk = RISK_PER_TRADE_PCT
            risk_pct = round(min(base_risk * risk_mult, base_risk * 1.5), 2)

            pricing_map[symbol] = {
                "symbol": symbol,
                "action": action,
                "regime": regime,
                "entry_price": entry_price,
                "stop_loss": sl_price,
                "take_profit": tp_price,
                "sl_pct": round(sl_pct, 1),
                "tp_pct": round(tp_pct, 1),
                "sl_mult": sl_mult,
                "tp_mult": tp_mult,
                "calculated_risk_pct": risk_pct,
                "volatility_used": round(vol, 2),
                "risk_rationale": (
                    f"{regime}: SL at {sl_mult}x vol ({sl_pct:.1f}%), "
                    f"TP at {tp_mult}x vol ({tp_pct:.1f}%), "
                    f"risk {risk_pct:.2f}%"
                ),
            }

        report = {
            "pricing_map": pricing_map,
            "timestamp": time.time(),
        }
        self.memory.write("decisions", "pricing", report)
        self.log(f"Pricing computed for {len(pricing_map)} symbols")
        return report

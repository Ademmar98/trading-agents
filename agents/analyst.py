import time

from config import WATCHED_SYMBOLS, RISK_PER_TRADE_PCT
from agents.base_agent import BaseAgent
from core.market import MarketData
from core.strategies import scan_symbol
from core.multiframe import analyze_symbol_multiframe
from core.database import get_unprofitable_strategies

_REGIME_PRICING = {
    "trending_up":   {"sl_mult": 2.5, "tp_mult": 4.0, "entry_slip": 0.003, "risk_mult": 1.10},
    "trending_down": {"sl_mult": 2.5, "tp_mult": 4.0, "entry_slip": 0.003, "risk_mult": 1.10},
    "trending":      {"sl_mult": 2.5, "tp_mult": 3.5, "entry_slip": 0.004, "risk_mult": 1.00},
    "volatile":      {"sl_mult": 3.5, "tp_mult": 4.5, "entry_slip": 0.005, "risk_mult": 0.85},
    "ranging":       {"sl_mult": 3.0, "tp_mult": 2.5, "entry_slip": 0.004, "risk_mult": 0.90},
}

_DEFAULT_PRICING = {"sl_mult": 3.0, "tp_mult": 3.5, "entry_slip": 0.003, "risk_mult": 0.90}


def _compute_pricing(symbol, action, price, data, regime, atr_val=0):
    vol = data.get("volatility", 2.0)
    vol_dec = max(vol / 100.0, 0.005)
    atr_pct = (atr_val / price * 100) if atr_val and price > 0 else vol_dec * 100
    atr_dec = max(atr_pct / 100.0, 0.005)

    cfg = _REGIME_PRICING.get(regime, _DEFAULT_PRICING)
    sl_mult = cfg["sl_mult"]
    tp_mult = cfg["tp_mult"]
    risk_mult = cfg["risk_mult"]

    bid = data.get("bid") or price
    ask = data.get("ask") or price

    sl_distance = max(atr_dec * sl_mult, vol_dec * sl_mult * 1.2)
    tp_distance = max(atr_dec * tp_mult, vol_dec * tp_mult * 0.8)
    sma_20 = data.get("sma_20") or 0
    sma_50 = data.get("sma_50") or 0

    if action == "BUY":
        target = bid
        if sma_20 > 0 and target > sma_20:
            entry_price = round(max(sma_20, target * (1 - cfg["entry_slip"])), 5)
        elif sma_50 > 0 and target > sma_50:
            entry_price = round(max(sma_50, target * (1 - cfg["entry_slip"] * 0.7)), 5)
        else:
            entry_price = round(target, 5)
        sl_price = round(entry_price * (1 - sl_distance), 5)
        tp_price = round(entry_price * (1 + tp_distance), 5)
    else:
        target = ask
        if sma_20 > 0 and target < sma_20:
            entry_price = round(min(sma_20, target * (1 + cfg["entry_slip"])), 5)
        elif sma_50 > 0 and target < sma_50:
            entry_price = round(min(sma_50, target * (1 + cfg["entry_slip"] * 0.7)), 5)
        else:
            entry_price = round(target, 5)
        sl_price = round(entry_price * (1 + sl_distance), 5)
        tp_price = round(entry_price * (1 - tp_distance), 5)

    sl_pct = abs(entry_price - sl_price) / entry_price * 100
    tp_pct = abs(tp_price - entry_price) / entry_price * 100
    risk_pct = round(min(RISK_PER_TRADE_PCT * risk_mult, RISK_PER_TRADE_PCT * 1.5), 2)

    return {
        "entry_price": entry_price,
        "stop_loss": sl_price,
        "take_profit": tp_price,
        "sl_pct": round(sl_pct, 1),
        "tp_pct": round(tp_pct, 1),
        "calculated_risk_pct": risk_pct,
        "risk_rationale": f"{regime}: SL at {sl_mult}x vol ({sl_pct:.1f}%), TP at {tp_mult}x vol ({tp_pct:.1f}%), risk {risk_pct:.2f}%",
    }


class ResearchAnalyst(BaseAgent):
    name = "analyst"

    def __init__(self):
        super().__init__()
        self.market = MarketData()

    def run(self):
        self.log("Fetching market data and analyzing symbols")
        prices = self.market.fetch_prices()
        if not prices:
            self.memory.write("analyses", "market_scan", {
                "error": "No market data available",
                "timestamp": time.time(),
            })
            self.log("WARNING: No market data received")
            return

        analyses = {}
        opportunities = []
        regime_scan = self.memory.read("analyses", "regime_scan") or {}
        symbol_regimes = regime_scan.get("symbols", {})
        for symbol, data in prices.items():
            ohlc = self.market.get_ohlc(symbol, days=100)
            hist = self.market.get_historical(symbol)
            indicators = self.market.compute_indicators(hist)

            mtf_signal = analyze_symbol_multiframe(symbol)

            regime = symbol_regimes.get(symbol, {}).get("regime") if symbol_regimes else None
            bad_strats = get_unprofitable_strategies()
            signals = scan_symbol(ohlc, regime=regime, exclude_strategies=bad_strats) if ohlc and len(ohlc) >= 30 else []

            analyses[symbol] = {
                "price": data["price"],
                "change_24h": data["change_24h"],
                "volume_24h": data["volume_24h"],
                "type": data.get("type", "unknown"),
                "bid": data.get("bid"),
                "ask": data.get("ask"),
                **indicators,
                "signals": signals,
                "mtf_signal": mtf_signal,
            }
            seen_actions = set()
            for sig in signals:
                action = sig["action"]
                if action in seen_actions:
                    continue
                seen_actions.add(action)
                confidence = sig["confidence"]
                reasons = list(sig["reasons"])
                if mtf_signal and mtf_signal.get("action") == action:
                    confidence = max(confidence, mtf_signal["confidence"])
                    reasons = mtf_signal["reasons"] + reasons

                opp = {
                    "symbol": symbol,
                    "action": action,
                    "confidence": min(confidence, 0.95),
                    "price": data["price"],
                    "reasons": reasons[:5],
                    "strategies": sig["strategies"],
                    "regime": regime,
                    "multi_timeframe": mtf_signal is not None,
                    "indicators": {
                        "trend": indicators.get("trend", "neutral"),
                        "rsi": indicators.get("rsi_14", 50),
                        "volatility": indicators.get("volatility", 0),
                        "atr": indicators.get("atr", 0),
                    }
                }

                pricing = _compute_pricing(symbol, action, data["price"], {**data, **indicators}, regime, indicators.get("atr", 0))
                opp.update(pricing)
                opportunities.append(opp)

        opportunities.sort(key=lambda o: o["confidence"], reverse=True)
        summary = f"Analyzed {len(analyses)} symbols, found {len(opportunities)} opportunities"

        pricing_map = {o["symbol"]: o for o in opportunities}

        self.memory.write("analyses", "market_scan", {
            "summary": summary,
            "opportunities": opportunities,
            "all_analyses": analyses,
            "timestamp": time.time(),
        })
        self.memory.write("decisions", "pricing", {
            "pricing_map": pricing_map,
            "timestamp": time.time(),
        })
        self.log(summary)
        if opportunities:
            top = opportunities[0]
            self.notifier.on_agent_action(
                "analyst", f"{len(opportunities)} signals | top: {top['action']} {top['symbol']} SL={top['sl_pct']:.1f}% TP={top['tp_pct']:.1f}%"
            )
        return analyses

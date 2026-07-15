import time

from config import (
    WATCHED_SYMBOLS, SMA200_PERIOD, SMA200_DEPLOY_TARGET,
    SMA200_UNKNOWN_TARGET, FIRM_BELLWETHER,
)
from agents.base_agent import BaseAgent
from core.market import MarketData
from core.regime import detect_regime


def firm_deployment(bellwether_closes):
    """The firm's capital-deployment dial — the one rule with evidence behind it.

    Deploy while the bellwether closes above its SMA200; sit in cash below it.
    Validated over 6.6 years including the 2022 bear (analysis/edge_hunt.py):
    max drawdown 76.6% -> 63.9% at a higher Sharpe (0.93 vs 0.85) than holding.
    Insurance, not alpha. Returns (firm_regime, deployment_target).
    """
    closes = [c for c in (bellwether_closes or []) if c]
    if len(closes) < SMA200_PERIOD:
        return "unknown", SMA200_UNKNOWN_TARGET
    sma200 = sum(closes[-SMA200_PERIOD:]) / SMA200_PERIOD
    if closes[-1] > sma200:
        return "risk_on", SMA200_DEPLOY_TARGET
    return "risk_off", 0.0


class RegimeAgent(BaseAgent):
    name = "regime"

    def __init__(self):
        super().__init__()
        self.market = MarketData()

    def run(self):
        self.log("Detecting market regimes")
        regimes = {}
        counts = {}
        for symbol in WATCHED_SYMBOLS:
            ohlc = self.market.get_ohlc(symbol, days=100)
            if not ohlc or len(ohlc) < 60:
                result = {"regime": "unknown", "confidence_multiplier": 0.85, "size_multiplier": 0.75, "notes": ["Insufficient OHLC history"]}
            else:
                detected = detect_regime(ohlc)
                result = detected if isinstance(detected, dict) else {"regime": detected or "unknown"}
                regime = result.get("regime", "unknown")
                if regime == "trending_up":
                    result.update({"favored_action": "BUY", "confidence_multiplier": 1.10, "size_multiplier": 1.00})
                elif regime == "trending_down":
                    result.update({"favored_action": "SELL", "confidence_multiplier": 1.10, "size_multiplier": 0.80})
                elif regime == "volatile":
                    result.update({"confidence_multiplier": 0.85, "size_multiplier": 0.50})
                elif regime == "ranging":
                    result.update({"confidence_multiplier": 0.95, "size_multiplier": 0.70})
                else:
                    result.update({"confidence_multiplier": 0.85, "size_multiplier": 0.60})
            regimes[symbol] = result
            counts[result.get("regime", "unknown")] = counts.get(result.get("regime", "unknown"), 0) + 1

        # Firm-wide dial: the validated SMA200 rule on the bellwether. Needs
        # 200+ daily closes, so it is fetched independently of the per-symbol
        # regime labels above (which remain advisory only).
        bell_closes = []
        try:
            bars = self.market.get_ohlc(FIRM_BELLWETHER, days=SMA200_PERIOD + 60,
                                        interval="1d")
            bell_closes = [b["close"] for b in (bars or [])]
        except Exception as e:
            self.log(f"Bellwether fetch failed ({FIRM_BELLWETHER}): {e}")
        firm_regime, deployment_target = firm_deployment(bell_closes)
        sma200 = (sum(bell_closes[-SMA200_PERIOD:]) / SMA200_PERIOD
                  if len(bell_closes) >= SMA200_PERIOD else None)

        report = {"symbols": regimes, "summary": counts,
                  "firm_regime": firm_regime, "deployment_target": deployment_target,
                  "bellwether": FIRM_BELLWETHER,
                  "bellwether_price": bell_closes[-1] if bell_closes else None,
                  "bellwether_sma200": round(sma200, 2) if sma200 else None,
                  "timestamp": time.time()}
        self.memory.write("analyses", "regime_scan", report)
        detail = (f"{FIRM_BELLWETHER} {bell_closes[-1]:,.0f} vs SMA200 {sma200:,.0f}"
                  if sma200 else f"{FIRM_BELLWETHER} history too short")
        self.log(f"Regime scan: {counts} | firm={firm_regime} "
                 f"deploy<={deployment_target:.0%} ({detail})")
        if deployment_target <= 0:
            self.notifier.on_agent_action(
                "regime", f"CASH — {detail}; no new entries until it reclaims SMA200")
        dominant = max(counts, key=counts.get) if counts else "unknown"
        found_volatile = any(r.get("regime") == "volatile" for r in regimes.values() if isinstance(r, dict))
        if found_volatile:
            self.notifier.on_agent_action("regime", f"VOLATILE detected | dominant: {dominant}")
        return report

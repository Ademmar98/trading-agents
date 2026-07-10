import time
import concurrent.futures

from config import WATCHED_SYMBOLS, TRADING_TIMEFRAME, BACKTEST_BARS, SCALP_15M_ENABLED
from agents.base_agent import BaseAgent
from core.market import MarketData
from core.strategies import scan_symbol
from core.multiframe import analyze_symbol_multiframe
from core.scalping_signals import analyze_symbol_mtf
from core.scalp15 import scalp_15m_signal
from core.database import get_unprofitable_strategies
from core.pricing import compute_pricing


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

        symbols = list(prices.keys())
        regime_scan = self.memory.read("analyses", "regime_scan") or {}
        symbol_regimes = regime_scan.get("symbols", {})
        bad_strats = get_unprofitable_strategies()

        analyses = {}
        opportunities = []

        def analyze_symbol(symbol):
            data = prices[symbol]
            try:
                ohlc = self.market.get_ohlc(symbol, days=BACKTEST_BARS, interval=TRADING_TIMEFRAME)
                hist = self.market.get_historical(symbol)
                indicators = self.market.compute_indicators(hist)
                mtf_signal = analyze_symbol_multiframe(symbol)
                scalping_signal = analyze_symbol_mtf(symbol)
                regime = symbol_regimes.get(symbol, {}).get("regime") if symbol_regimes else None
                scalp15 = None
                if SCALP_15M_ENABLED:
                    # A scalp-stack failure must not sink the symbol's whole analysis
                    try:
                        ohlc15 = self.market.get_ohlc(symbol, days=80, interval="15m")
                        scalp15 = scalp_15m_signal(symbol, regime=regime, ohlc=ohlc15 or None)
                    except Exception:
                        scalp15 = None
                signals = scan_symbol(ohlc, regime=regime, exclude_strategies=bad_strats) if ohlc and len(ohlc) >= 30 else []
                # 30d daily returns so the RiskManager can correlate pairs
                # from shared memory without its own data fetches
                closes = [h.get("close") for h in (hist or []) if h.get("close")]
                returns_30d = [
                    (closes[i] - closes[i - 1]) / closes[i - 1]
                    for i in range(max(1, len(closes) - 30), len(closes)) if closes[i - 1]
                ]
                return symbol, {
                    "price": data["price"],
                    "change_24h": data["change_24h"],
                    "volume_24h": data["volume_24h"],
                    "returns_30d": returns_30d,
                    "type": data.get("type", "unknown"),
                    "bid": data.get("bid"),
                    "ask": data.get("ask"),
                    **indicators,
                    "signals": signals,
                    "mtf_signal": mtf_signal,
                    "scalping_signal": scalping_signal,
                    "scalp15": scalp15,
                }, signals, mtf_signal, scalping_signal, scalp15, regime, indicators
            except Exception as e:
                self.log(f"Error analyzing {symbol}: {e}")
                return symbol, None, [], None, None, None, None, {}

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = {executor.submit(analyze_symbol, sym): sym for sym in symbols}
            for future in concurrent.futures.as_completed(futures):
                result = future.result()
                sym, analysis, signals, mtf_signal, scalping_signal, scalp15, regime, indicators = result
                if analysis is None:
                    continue
                analyses[sym] = analysis
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
                        "symbol": sym, "action": action,
                        "confidence": min(confidence, 0.95),
                        "price": prices[sym]["price"], "reasons": reasons[:5],
                        "strategies": sig["strategies"],
                        "regime": regime, "multi_timeframe": mtf_signal is not None,
                        "indicators": {
                            "trend": indicators.get("trend", "neutral"),
                            "rsi": indicators.get("rsi_14", 50),
                            "volatility": indicators.get("volatility", 0),
                            "atr": indicators.get("atr", 0),
                        }
                    }
                    pricing = compute_pricing(sym, action, prices[sym]["price"],
                                              {**prices[sym], **indicators}, regime, indicators.get("atr", 0))
                    opp.update(pricing)
                    opportunities.append(opp)
                if scalping_signal and scalping_signal["action"] != "HOLD":
                    action = scalping_signal["action"]
                    if action not in seen_actions:
                        seen_actions.add(action)
                        opp = {
                            "symbol": sym, "action": action,
                            "confidence": scalping_signal["confidence"],
                            "price": prices[sym]["price"],
                            "reasons": [f"scalping_mtf:{action}"] + [
                                f"{tf}:{d['action']}({d['confidence']:.2f})"
                                for tf, d in scalping_signal.get("mtf_details", {}).items()
                            ],
                            "strategies": ["scalping_mtf"],
                            "regime": regime, "multi_timeframe": True,
                            "indicators": {
                                "trend": indicators.get("trend", "neutral"),
                                "rsi": indicators.get("rsi_14", 50),
                                "volatility": indicators.get("volatility", 0),
                                "atr": indicators.get("atr", 0),
                            }
                        }
                        pricing = compute_pricing(sym, action, prices[sym]["price"],
                                                  {**prices[sym], **indicators}, regime, indicators.get("atr", 0))
                        opp.update(pricing)
                        opportunities.append(opp)
                # 15m scalp stack: carries its own ATR-derived SL/TP and win
                # probability — no compute_pricing; the execution agent gates
                # it on SCALP_MIN_WIN_PROB before routing.
                if scalp15 and scalp15["action"] not in seen_actions:
                    seen_actions.add(scalp15["action"])
                    opportunities.append({
                        "symbol": sym,
                        "action": scalp15["action"],
                        "confidence": min(scalp15["win_prob"], 0.95),
                        "price": scalp15["price"],
                        "entry_price": scalp15["entry_price"],
                        "stop_loss": scalp15["stop_loss"],
                        "take_profit": scalp15["take_profit"],
                        "sl_pct": scalp15["sl_pct"],
                        "tp_pct": scalp15["tp_pct"],
                        "calculated_risk_pct": scalp15["calculated_risk_pct"],
                        "atr": scalp15["atr"],
                        "win_prob": scalp15["win_prob"],
                        "reasons": scalp15["reasons"][:5],
                        "strategies": ["scalp_15m"],
                        "regime": regime, "multi_timeframe": False,
                        "indicators": {
                            "trend": indicators.get("trend", "neutral"),
                            "rsi": scalp15["rsi"],
                            "volatility": indicators.get("volatility", 0),
                            "atr": scalp15["atr"],
                        },
                    })

        opportunities.sort(key=lambda o: o["confidence"], reverse=True)
        summary = f"Analyzed {len(analyses)} symbols, found {len(opportunities)} opportunities"
        # List is sorted by confidence desc, so setdefault keeps the strongest
        # opportunity per symbol — a symbol can carry both a BUY and a SELL.
        pricing_map = {}
        for o in opportunities:
            pricing_map.setdefault(o["symbol"], o)
        self.memory.write("analyses", "market_scan", {
            "summary": summary, "opportunities": opportunities,
            "all_analyses": analyses, "timestamp": time.time(),
        })
        self.memory.write("decisions", "pricing", {
            "pricing_map": pricing_map, "timestamp": time.time(),
        })
        self.log(summary)
        if opportunities:
            top = opportunities[0]
            self.notifier.on_agent_action(
                "analyst", f"{len(opportunities)} signals | top: {top['action']} {top['symbol']} SL={top['sl_pct']:.1f}% TP={top['tp_pct']:.1f}%"
            )
        return analyses

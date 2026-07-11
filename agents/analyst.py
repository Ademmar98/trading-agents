import time
import concurrent.futures

from config import (
    WATCHED_SYMBOLS, TRADING_TIMEFRAME, BACKTEST_BARS, SCALP_15M_ENABLED,
    SCALP_TIMEFRAMES, BUY_ONLY, MAX_TP_PCT,
)
from agents.base_agent import BaseAgent
from core.market import MarketData
from core.positions import PositionManager
from core.microstructure import vwap as vwap_fn, book_imbalance
from core.strategies import scan_symbol
from core.multiframe import analyze_symbol_multiframe
from core.scalping_signals import analyze_symbol_mtf
from core.scalp15 import scalp_signal
from core.database import get_unprofitable_strategies
from core.indicators import atr as intraday_atr_fn
from core.pricing import compute_pricing, round_sig


class ResearchAnalyst(BaseAgent):
    name = "analyst"

    def __init__(self):
        super().__init__()
        self.market = MarketData()
        self.pos_mgr = PositionManager()

    def _apply_priority_boosts(self, opportunities, analyses):
        """Focus effort where it pays: uptrending and high-liquidity pairs
        get a confidence edge; bid-heavy order books add a little more; news
        sentiment nudges both ways. Boosts are small — evidence, not fiat."""
        vols = sorted((a.get("volume_24h") or 0) for a in analyses.values())
        top_q = vols[int(len(vols) * 0.75)] if vols else 0
        news = self.memory.read("reports", "news_scan") or {}
        news_syms = news.get("symbols", {}) if time.time() - (news.get("timestamp") or 0) < 3600 else {}
        # Order-book depth is a REST call per symbol — spend it only on the
        # strongest candidates
        booked = {}
        for o in sorted(opportunities, key=lambda x: x["confidence"], reverse=True)[:10]:
            sym = o["symbol"]
            if "/" in sym and sym not in booked:
                try:
                    booked[sym] = book_imbalance(sym)
                except Exception:
                    booked[sym] = None
        for o in opportunities:
            a = analyses.get(o["symbol"], {})
            boost = 0.0
            if top_q and (a.get("volume_24h") or 0) >= top_q:
                boost += 0.03
                o.setdefault("reasons", []).append("high liquidity")
            if o.get("regime") in ("trending_up", "trending") and o["action"] == "BUY":
                boost += 0.02
            imb = booked.get(o["symbol"])
            if imb is not None and o["action"] == "BUY":
                if imb > 0.2:
                    boost += 0.03
                    o["reasons"].append(f"bid-heavy book {imb:+.2f}")
                elif imb < -0.4:
                    boost -= 0.03
            n = news_syms.get(o["symbol"]) or {}
            score = n.get("score")
            if score is not None and o["action"] == "BUY":
                boost += max(-0.05, min(0.05, score * 0.05))
                if abs(score) >= 0.5:
                    o["reasons"].append(f"news {'+' if score > 0 else '-'}")
            if boost:
                o["confidence"] = round(min(o["confidence"] + boost, 0.95), 4)

    def _steward_open_trades(self, analyses):
        """Re-analyze open trades each cycle: tighten stops (never widen)
        when the picture turns against a position, and give a working
        winner more target room within the MAX_TP_PCT cap."""
        news = self.memory.read("reports", "news_scan") or {}
        news_syms = news.get("symbols", {}) if time.time() - (news.get("timestamp") or 0) < 3600 else {}
        adjusted = []
        for pos in self.pos_mgr.get_open_positions():
            a = analyses.get(pos["symbol"])
            if not a or pos["side"] != "BUY":
                continue
            price = a.get("price") or pos["current_price"]
            atr_v = a.get("atr") or 0
            if not price or not atr_v:
                continue
            score = (news_syms.get(pos["symbol"]) or {}).get("score", 0)
            bearish_votes = sum([
                a.get("trend") == "bearish",
                (a.get("rsi_14") or 50) > 75,
                score <= -0.5,
            ])
            in_profit = price > pos["entry_price"]
            if bearish_votes >= 2 and in_profit:
                # Evidence turned: protect the gain under the current price
                new_sl = round_sig(price - atr_v)
                if self.pos_mgr.adjust_levels(pos["id"], new_sl=new_sl):
                    adjusted.append(f"{pos['symbol']} SL→{new_sl} (bearish evidence)")
            elif (a.get("trend") == "bullish" and score >= 0 and pos.get("take_profit")
                  and price >= pos["take_profit"] * 0.997):
                # Winner knocking on TP with the wind behind it: extend within cap
                cap = pos["entry_price"] * (1 + MAX_TP_PCT / 100)
                new_tp = round_sig(min(pos["take_profit"] + atr_v, cap))
                if new_tp > pos["take_profit"] and self.pos_mgr.adjust_levels(pos["id"], new_tp=new_tp):
                    adjusted.append(f"{pos['symbol']} TP→{new_tp} (extending winner)")
        if adjusted:
            self.log("Steward: " + "; ".join(adjusted[:4]))
            self.notifier.on_agent_action("analyst", "steward adjusted " + ", ".join(adjusted[:3]))

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
                # SL/TP inputs must come from the TRADING timeframe, not the
                # daily history: compute_indicators' "volatility" is a 14-DAY
                # high-low range (20-30% on stocks), which once priced a META
                # scalp with a 29.5% stop. Overwrite with intraday values.
                try:
                    if ohlc and len(ohlc) >= 20:
                        i_atr = intraday_atr_fn([b["high"] for b in ohlc],
                                                [b["low"] for b in ohlc],
                                                [b["close"] for b in ohlc]) or 0
                        i_closes = [b["close"] for b in ohlc[-15:]]
                        i_hi, i_lo = max(i_closes), min(i_closes)
                        indicators["atr"] = i_atr
                        indicators["volatility"] = round((i_hi - i_lo) / (i_lo or 1) * 100, 3)
                        # Session VWAP: the trader rests BUY limits here when
                        # price is extended above it
                        v = vwap_fn(ohlc[-32:])
                        if v:
                            indicators["vwap"] = v
                except Exception:
                    pass  # daily values stay; the MAX_SL_PCT cap still guards
                mtf_signal = analyze_symbol_multiframe(symbol)
                scalping_signal = analyze_symbol_mtf(symbol)
                regime = symbol_regimes.get(symbol, {}).get("regime") if symbol_regimes else None
                scalp_sigs = []
                if SCALP_15M_ENABLED:
                    # The same stack runs on every configured timeframe; a
                    # failure on one TF must not sink the symbol's analysis
                    for tf in SCALP_TIMEFRAMES:
                        try:
                            tf_ohlc = self.market.get_ohlc(symbol, days=80, interval=tf)
                            sig = scalp_signal(symbol, regime=regime,
                                               ohlc=tf_ohlc or None, timeframe=tf)
                            if sig:
                                scalp_sigs.append(sig)
                        except Exception:
                            continue
                    scalp_sigs.sort(key=lambda s: s["win_prob"], reverse=True)
                signals = scan_symbol(ohlc, regime=regime, exclude_strategies=bad_strats) if ohlc and len(ohlc) >= 30 else []
                if BUY_ONLY:
                    # Firm policy: all analytical effort goes into longs
                    signals = [s for s in signals if s.get("action") == "BUY"]
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
                    "scalp_signals": scalp_sigs,
                }, signals, mtf_signal, scalping_signal, scalp_sigs, regime, indicators
            except Exception as e:
                self.log(f"Error analyzing {symbol}: {e}")
                return symbol, None, [], None, None, [], None, {}

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = {executor.submit(analyze_symbol, sym): sym for sym in symbols}
            for future in concurrent.futures.as_completed(futures):
                result = future.result()
                sym, analysis, signals, mtf_signal, scalping_signal, scalp_sigs, regime, indicators = result
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
                if scalping_signal and scalping_signal["action"] != "HOLD" and not (BUY_ONLY and scalping_signal["action"] == "SELL"):
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
                # Scalp stack across all configured timeframes (sorted by
                # win probability — the strongest TF per action wins). Each
                # carries its own ATR-derived SL/TP; no compute_pricing; the
                # execution agent gates on SCALP_MIN_WIN_PROB before routing.
                for ssig in scalp_sigs:
                    if ssig["action"] in seen_actions:
                        continue
                    seen_actions.add(ssig["action"])
                    opportunities.append({
                        "symbol": sym,
                        "action": ssig["action"],
                        "confidence": min(ssig["win_prob"], 0.95),
                        "price": ssig["price"],
                        "entry_price": ssig["entry_price"],
                        "stop_loss": ssig["stop_loss"],
                        "take_profit": ssig["take_profit"],
                        "sl_pct": ssig["sl_pct"],
                        "tp_pct": ssig["tp_pct"],
                        "calculated_risk_pct": ssig["calculated_risk_pct"],
                        "atr": ssig["atr"],
                        "win_prob": ssig["win_prob"],
                        "reasons": ssig["reasons"][:5],
                        "strategies": [ssig["strategy"]],
                        "regime": regime, "multi_timeframe": False,
                        "timeframe": ssig["timeframe"],
                        "indicators": {
                            "trend": indicators.get("trend", "neutral"),
                            "rsi": ssig["rsi"],
                            "volatility": indicators.get("volatility", 0),
                            "atr": ssig["atr"],
                        },
                    })

        self._apply_priority_boosts(opportunities, analyses)
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
        try:
            self._steward_open_trades(analyses)
        except Exception as e:
            self.log(f"Steward skipped: {e}")
        self.log(summary)
        if opportunities:
            top = opportunities[0]
            self.notifier.on_agent_action(
                "analyst", f"{len(opportunities)} signals | top: {top['action']} {top['symbol']} SL={top['sl_pct']:.1f}% TP={top['tp_pct']:.1f}%"
            )
        return analyses

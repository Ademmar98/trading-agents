from core.strategies import scan_symbol
from core.backtester import fetch_klines
from core.regime import detect_regime

TIMEFRAMES = {
    "15m": {"interval": "15m", "weight": 0.10},
    "1h":  {"interval": "1h",  "weight": 0.15},
    "4h":  {"interval": "4h",  "weight": 0.30},
    "1d":  {"interval": "1d",  "weight": 0.35},
    "1w":  {"interval": "1w",  "weight": 0.10},
}

HIGHER_TFS = ["4h", "1d", "1w"]
LOWER_TFS = ["15m", "1h"]
LOOKBACK = {"15m": 200, "1h": 200, "4h": 150, "1d": 100, "1w": 52}


def analyze_symbol_multiframe(symbol):
    tf_results = {}
    regimes = {}
    for tf_name, tf_info in TIMEFRAMES.items():
        limit = LOOKBACK.get(tf_name, 100)
        ohlc = fetch_klines(symbol, interval=tf_info["interval"], limit=limit)
        if ohlc and len(ohlc) >= 30:
            signals = scan_symbol(ohlc)
            regime = detect_regime(ohlc)
            tf_results[tf_name] = {
                "ohlc_len": len(ohlc),
                "signals": signals,
                "price": ohlc[-1]["close"],
            }
            if isinstance(regime, dict):
                regimes[tf_name] = regime

    return _consolidate_signals(symbol, tf_results, regimes)


def _consolidate_signals(symbol, tf_results, regimes=None):
    if not tf_results:
        return None

    all_buy = []
    all_sell = []
    tf_details = {}
    regime_summary = {}

    for tf_name, result in tf_results.items():
        signals = result.get("signals", [])
        weight = TIMEFRAMES[tf_name]["weight"]
        buy_conf = max([s["confidence"] for s in signals if s["action"] == "BUY"] or [0])
        sell_conf = max([s["confidence"] for s in signals if s["action"] == "SELL"] or [0])
        tf_details[tf_name] = {
            "signals_count": len(signals),
            "buy_confidence": round(buy_conf, 2),
            "sell_confidence": round(sell_conf, 2),
            "price": result["price"],
        }
        if buy_conf > 0:
            all_buy.append(buy_conf * weight)
        if sell_conf > 0:
            all_sell.append(sell_conf * weight)

        if regimes and tf_name in regimes:
            regime_summary[tf_name] = regimes[tf_name].get("regime", "unknown")

    higher_buy = sum(
        tf_details[tf].get("buy_confidence", 0) * TIMEFRAMES[tf]["weight"]
        for tf in HIGHER_TFS if tf in tf_details
    )
    higher_sell = sum(
        tf_details[tf].get("sell_confidence", 0) * TIMEFRAMES[tf]["weight"]
        for tf in HIGHER_TFS if tf in tf_details
    )
    lower_buy = sum(
        tf_details[tf].get("buy_confidence", 0) * TIMEFRAMES[tf]["weight"]
        for tf in LOWER_TFS if tf in tf_details
    )
    lower_sell = sum(
        tf_details[tf].get("sell_confidence", 0) * TIMEFRAMES[tf]["weight"]
        for tf in LOWER_TFS if tf in tf_details
    )

    # Regime-aware bias adjustment
    regime_bias = None
    if regimes:
        htf_regimes = [regime_summary.get(tf) for tf in HIGHER_TFS if tf in regime_summary]
        if htf_regimes:
            bullish_regimes = [r for r in htf_regimes if r in ("trending_up",)]
            bearish_regimes = [r for r in htf_regimes if r in ("trending_down",)]
            volatile_regimes = [r for r in htf_regimes if r in ("volatile",)]
            if len(bullish_regimes) > len(bearish_regimes):
                regime_bias = "BUY"
            elif len(bearish_regimes) > len(bullish_regimes):
                regime_bias = "SELL"
            if volatile_regimes:
                regime_bias = None  # Volatile = no directional bias

    bias = None
    if higher_buy > higher_sell and higher_buy > 0.1:
        bias = "BUY"
    elif higher_sell > higher_buy and higher_sell > 0.1:
        bias = "SELL"

    # If regimes disagree with signal bias, reduce confidence
    if bias and regime_bias and bias != regime_bias:
        confidence_mult = 0.8
    else:
        confidence_mult = 1.0

    entry = None
    if lower_buy > lower_sell and lower_buy > 0.1:
        entry = "BUY"
    elif lower_sell > lower_buy and lower_sell > 0.1:
        entry = "SELL"

    if bias and entry and bias == entry:
        combined = sum(all_buy) if entry == "BUY" else sum(all_sell)
        confidence = min(combined * 1.3 * confidence_mult, 0.95)
        reasons = [f"MTF: {entry} (bias={bias}, entry={entry})"]
        for tf_name, detail in tf_details.items():
            if detail["signals_count"] > 0:
                dir_str = "B" if detail["buy_confidence"] > detail["sell_confidence"] else "S"
                reasons.append(f"{tf_name}:{dir_str}({detail['signals_count']})")
        return {
            "action": entry,
            "confidence": round(confidence, 2),
            "reasons": reasons,
            "mtf_details": tf_details,
            "regime_summary": regime_summary,
            "bias": bias,
        }

    if entry and not bias:
        confidence = (sum(all_buy) if entry == "BUY" else sum(all_sell)) * 0.8 * confidence_mult
        return {
            "action": entry,
            "confidence": round(min(confidence, 0.7), 2),
            "reasons": [f"MTF: {entry} (no HTF bias)"],
            "mtf_details": tf_details,
            "regime_summary": regime_summary,
            "bias": None,
        }

    return None

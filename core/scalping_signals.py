import math
from core.data_provider import fetch_ohlc
from core.indicators import compute_all

TIMEFRAMES = {
    "1m":  {"bars": 200, "weight": 0.20},
    "5m":  {"bars": 200, "weight": 0.35},
    "15m": {"bars": 150, "weight": 0.30},
    "1h":  {"bars": 100, "weight": 0.15},
}

GRADIENT_RANGES = {
    "rsi":       {"max": 35, "mid": 45, "zero": 55},
    "stoch_k":   {"max": 20, "mid": 35, "zero": 50},
    "williams_r":{"max": -80, "mid": -60, "zero": -40},
}


def _wilder(values, period):
    if len(values) < period:
        return sum(values) / max(len(values), 1)
    result = sum(values[:period]) / period
    for v in values[period:]:
        result = (result * (period - 1) + v) / period
    return result


def gaussian_channel(values, period=144, poles=4, mult=1.4):
    if len(values) < 2:
        return {"mid": values[-1] if values else 0, "direction": "flat", "is_green": False}
    sqrt2 = 2 ** 0.5
    p = sqrt2 ** (2 / poles) - 1
    cos_val = math.cos((2 * math.pi) / period)
    beta = (1 - cos_val) / p
    alpha = -beta + math.sqrt(beta * beta + 2 * beta)
    n = len(values)
    filtered = [0.0] * n
    filtered[0] = values[0]
    for i in range(1, n):
        f = values[i]
        for _ in range(poles):
            f = alpha * f + (1 - alpha) * filtered[i - 1]
        filtered[i] = f
    latest, prev = filtered[-1], filtered[-2]
    direction = "up" if latest > prev else "down" if latest < prev else "flat"
    return {"mid": latest, "direction": direction, "is_green": direction == "up"}


def linear_gradient(value, max_val, mid_val, zero_val):
    if max_val < zero_val:
        if value <= max_val:
            return 1.0
        if value >= zero_val:
            return 0.0
        if value <= mid_val:
            return 1.0 - ((value - max_val) / (mid_val - max_val)) * 0.5
        return 0.5 * ((zero_val - value) / (zero_val - mid_val))
    if value >= max_val:
        return 1.0
    if value <= zero_val:
        return 0.0
    if value >= mid_val:
        return 1.0 - ((max_val - value) / (max_val - mid_val)) * 0.5
    return 0.5 * ((value - zero_val) / (mid_val - zero_val))


def macd_crossover_grad(histogram, direction="positive"):
    if len(histogram) < 2:
        return 0.0
    current, previous = histogram[-1], histogram[-2]
    is_fresh = (direction == "positive" and current > 0 and previous <= 0) or \
               (direction == "negative" and current < 0 and previous >= 0)
    is_active = (direction == "positive" and current > 0) or (direction == "negative" and current < 0)
    if is_fresh:
        return 1.0
    if is_active:
        bars = 0
        for v in reversed(histogram[:-1]):
            if (direction == "positive" and v <= 0) or (direction == "negative" and v >= 0):
                break
            bars += 1
        return 1.0 / (1 + bars)
    return 0.0


def compute_regime(highs, lows, closes, period=14):
    n = len(closes)
    if n < period + 1:
        return {"regime": "unknown", "adx": 0, "plus_di": 0, "minus_di": 0, "dmi_dir": 0}
    up_moves = [highs[i] - highs[i-1] for i in range(1, n)]
    down_moves = [lows[i-1] - lows[i] for i in range(1, n)]
    tr = []
    for i in range(1, n):
        tr.append(max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1])))
    plus_dm = [max(u, 0) if u > d and u > 0 else 0 for u, d in zip(up_moves, down_moves)]
    minus_dm = [max(d, 0) if d > u and d > 0 else 0 for u, d in zip(up_moves, down_moves)]
    dx_list = []
    for i in range(period, len(up_moves) + 1):
        wtr = _wilder(tr[i-period:i], period)
        wpdm = _wilder(plus_dm[i-period:i], period)
        wmdm = _wilder(minus_dm[i-period:i], period)
        pdi_w = 100 * wpdm / (wtr or 1e-10)
        mdi_w = 100 * wmdm / (wtr or 1e-10)
        di_sum_w = pdi_w + mdi_w
        dx = 100 * abs(pdi_w - mdi_w) / (di_sum_w or 1e-10)
        dx_list.append(dx)
    atr_val = _wilder(tr, period)
    pdi_val = _wilder(plus_dm, period)
    mdi_val = _wilder(minus_dm, period)
    plus_di = 100 * pdi_val / (atr_val or 1e-10)
    minus_di = 100 * mdi_val / (atr_val or 1e-10)
    dx_window = dx_list[-period:] if len(dx_list) >= period else dx_list
    adx = sum(dx_window) / max(len(dx_window), 1)
    dmi_dir = 1 if plus_di > minus_di else -1 if minus_di > plus_di else 0
    if adx >= 25:
        regime = "trending"
    elif adx <= 20:
        regime = "ranging"
    else:
        regime = "transition"
    return {
        "regime": regime,
        "adx": adx,
        "plus_di": plus_di,
        "minus_di": minus_di,
        "dmi_dir": dmi_dir,
        "atr_pct": (atr_val / closes[-1] * 100) if closes[-1] else 0,
    }


def detect_divergence(price, indicator, lookback=14, sensitivity=0.02):
    if len(price) < lookback or len(indicator) < lookback:
        return "NONE"
    p = price[-lookback:]
    ind = indicator[-lookback:]
    valid = [(a, b) for a, b in zip(p, ind) if a is not None and b is not None]
    if len(valid) < lookback // 2:
        return "NONE"
    p, ind = [v[0] for v in valid], [v[1] for v in valid]
    if len(p) < 4:
        return "NONE"
    mid = len(p) // 2
    p1_low = min(p[:mid])
    p1_low_idx = p.index(p1_low)
    p1_high = max(p[:mid])
    p1_high_idx = p.index(p1_high)
    p2_low = min(p[mid:])
    p2_low_rel = p[mid:].index(p2_low)
    p2_low_idx = mid + p2_low_rel
    p2_high = max(p[mid:])
    p2_high_rel = p[mid:].index(p2_high)
    p2_high_idx = mid + p2_high_rel
    p_low_chg = (p2_low - p1_low) / (abs(p1_low) + 1e-10)
    p_high_chg = (p2_high - p1_high) / (abs(p1_high) + 1e-10)
    ind_low_chg = (ind[p2_low_idx] - ind[p1_low_idx]) / (abs(ind[p1_low_idx]) + 1e-10)
    ind_high_chg = (ind[p2_high_idx] - ind[p1_high_idx]) / (abs(ind[p1_high_idx]) + 1e-10)
    if p_low_chg < -sensitivity and ind_low_chg > sensitivity:
        return "BULLISH_DIV"
    if p_high_chg > sensitivity and ind_high_chg < -sensitivity:
        return "BEARISH_DIV"
    if p_low_chg > sensitivity and ind_low_chg < -sensitivity:
        return "HIDDEN_BULLISH"
    if p_high_chg < -sensitivity and ind_high_chg > sensitivity:
        return "HIDDEN_BEARISH"
    return "NONE"


def ichimoku(highs, lows, closes, tenkan=10, kijun=30, senkou_b=60):
    tenkan = min(tenkan, len(highs) - 1) if highs else 10
    kijun = min(kijun, len(highs) - 1) if highs else 30
    h_tenkan = max(highs[-tenkan:]) if len(highs) >= tenkan else max(highs)
    l_tenkan = min(lows[-tenkan:]) if len(lows) >= tenkan else min(lows)
    h_kijun = max(highs[-kijun:]) if len(highs) >= kijun else max(highs)
    l_kijun = min(lows[-kijun:]) if len(lows) >= kijun else min(lows)
    ts = (h_tenkan + l_tenkan) / 2
    ks = (h_kijun + l_kijun) / 2
    ss_count = min(senkou_b, len(highs))
    h_senkou_b = max(highs[-ss_count:])
    l_senkou_b = min(lows[-ss_count:])
    ssb = (h_senkou_b + l_senkou_b) / 2
    ssa = (ts + ks) / 2
    price = closes[-1]
    cloud_top = max(ssa, ssb)
    cloud_bot = min(ssa, ssb)
    cloud_bullish = ssa > ssb
    if price > cloud_top:
        cloud_signal = 1.0
    elif price < cloud_bot:
        cloud_signal = -1.0
    else:
        cloud_signal = 0.0
    tk_signal = 1.0 if ts > ks else -1.0 if ts < ks else 0.0
    color_signal = 0.25 if cloud_bullish else -0.25
    scores = [s for s in [cloud_signal, tk_signal, color_signal] if s != 0]
    raw = sum(scores) / max(len(scores), 1) if scores else 0.0
    return {
        "tenkan": ts,
        "kijun": ks,
        "span_a": ssa,
        "span_b": ssb,
        "cloud_top": cloud_top,
        "cloud_bot": cloud_bot,
        "cloud_bullish": cloud_bullish,
        "score": max(-1.0, min(1.0, raw)),
    }


def evaluate_tf(ohlc):
    if not ohlc or len(ohlc) < 30:
        return {"action": "HOLD", "confidence": 0, "score": 0, "signal_7tier": "NEUTRAL", "regime": "unknown"}
    
    closes = [c["close"] for c in ohlc]
    last = ohlc[-1]
    indicators = compute_all(ohlc)
    if not indicators:
        return {"action": "HOLD", "confidence": 0, "score": 0, "signal_7tier": "NEUTRAL", "regime": "unknown"}
    
    has_ohl = "high" in ohlc[0] and "low" in ohlc[0]
    highs = [c.get("high", c["close"]) for c in ohlc] if has_ohl else closes
    lows = [c.get("low", c["close"]) for c in ohlc] if has_ohl else closes
    
    # ---- Regime Detection ----
    regime_info = compute_regime(highs, lows, closes)
    regime = regime_info["regime"]
    dmi_dir = regime_info["dmi_dir"]
    adx = regime_info["adx"]
    trend_mult = 1.15 if regime == "trending" else 0.85 if regime == "ranging" else 1.0
    osc_mult = 0.85 if regime == "trending" else 1.15 if regime == "ranging" else 1.0
    
    # ---- Gaussian Channel (trend filter) ----
    gc = gaussian_channel(closes)
    gc_dir = 1 if gc["direction"] == "up" else -1 if gc["direction"] == "down" else 0
    gc_state = gc_dir  # state: ongoing trend direction
    
    # ---- Oscillator Scores (gradient method) ----
    gr = GRADIENT_RANGES
    rsi = indicators.get("rsi_14", 50)
    stoch_k = (indicators.get("stoch_rsi") or {}).get("k", 50) if isinstance(indicators.get("stoch_rsi"), dict) else 50
    williams_r = indicators.get("williams_r", -50)
    bb = indicators.get("bollinger") or {}
    bb_pct = bb.get("bb_pct", 0.5)
    vol_ratio = indicators.get("volume_ratio", 1.0)
    
    rsi_grad = linear_gradient(rsi, gr["rsi"]["max"], gr["rsi"]["mid"], gr["rsi"]["zero"])
    stoch_grad = linear_gradient(stoch_k, gr["stoch_k"]["max"], gr["stoch_k"]["mid"], gr["stoch_k"]["zero"])
    williams_grad = linear_gradient(williams_r, gr["williams_r"]["max"], gr["williams_r"]["mid"], gr["williams_r"]["zero"])
    hist_list = [indicators.get("macd_histogram_prev", 0), indicators.get("macd_histogram", 0)]
    macd_grad = macd_crossover_grad(hist_list, "positive")
    bb_grad = linear_gradient(bb_pct, 0, 0.2, 0.5)
    
    # ---- MACD State/Trigger (from crypto-ta-analyzer) ----
    macd_obj = indicators.get("macd") or {}
    macd_line_val = macd_obj.get("macd", 0) if isinstance(macd_obj, dict) else 0
    macd_sig_val = macd_obj.get("signal", 0) if isinstance(macd_obj, dict) else 0
    macd_hist = macd_obj.get("histogram", 0) if isinstance(macd_obj, dict) else 0
    macd_state = 1 if macd_line_val > macd_sig_val else -1 if macd_line_val < macd_sig_val else 0
    macd_accel = 1 if macd_hist > hist_list[-2] else -1 if hist_list[-1] < hist_list[-2] else 0 if len(hist_list) < 2 else 0
    
    # ---- Buy Score (regime-aware) ----
    base_buy = (
        rsi_grad * 30 * osc_mult +
        stoch_grad * 20 * osc_mult +
        williams_grad * 20 * osc_mult +
        macd_grad * 35 * (trend_mult if regime == "trending" else osc_mult) +
        bb_grad * 25 * osc_mult +
        (vol_ratio >= 1.0) * 20
    )
    
    # GC trend confirmation bonus
    gc_bonus = 20 * trend_mult if gc_state > 0 else 0
    # DMI direction bonus
    dmi_bonus = 15 * trend_mult if dmi_dir > 0 else 0
    # MACD state bonus
    macd_state_bonus = 15 * trend_mult if macd_state > 0 else 0
    
    buy_score = base_buy + gc_bonus + dmi_bonus + macd_state_bonus
    
    # ---- Sell Score ----
    rsi_sell = linear_gradient(rsi, 80, 70, 60)
    bb_sell = linear_gradient(bb_pct, 1.0, 0.85, 0.7)
    macd_sell = macd_crossover_grad(hist_list, "negative")
    base_sell = rsi_sell * 30 + bb_sell * 25 + macd_sell * 35
    gc_sell_bonus = 20 if gc_state < 0 else 0
    dmi_sell_bonus = 15 if dmi_dir < 0 else 0
    sell_score = base_sell + gc_sell_bonus + dmi_sell_bonus
    
    # ---- Quality Gate ----
    rng = last.get("high", 0) - last.get("low", 0)
    ibs = (last["close"] - last.get("low", 0)) / rng if rng > 0 else 0.5
    atr_val = indicators.get("atr", 0)
    atr_pct = (atr_val / last["close"] * 100) if last.get("close", 0) > 0 and atr_val else 0
    
    # Regime-aware quality gate
    max_atr_pct = 4.5 if regime == "volatile" else 3.5
    quality_ok = ibs < 0.3 and atr_pct < max_atr_pct and 0.8 < vol_ratio < 5.0
    
    # ---- Divergence Detection ----
    if has_ohl:
        rsi_s = indicators.get("rsi_14")
        rsi_series = [50] * len(closes)  # We don't have full series, use current
    div_rsi = "NONE"
    div_macd = "NONE"
    
    # ---- Ichimoku ----
    ichi = ichimoku(highs, lows, closes) if has_ohl else None
    ichi_score = ichi["score"] if ichi else 0.0
    
    # ---- Volume Confirmation (OBV + MFI) ----
    mfi_val = indicators.get("mfi", 50) if has_ohl else 50
    obv_val = indicators.get("obv", 0)
    vol_confirm = 0.0
    if has_ohl and mfi_val:
        if gc_state > 0:
            vol_confirm = 1.0 if mfi_val > 50 else 0.5 if mfi_val > 40 else 0.0
        elif gc_state < 0:
            vol_confirm = 1.0 if mfi_val < 50 else 0.5 if mfi_val < 60 else 0.0
        else:
            vol_confirm = 0.5
    
    # ---- Combined Action Logic ----
    trend_ok = gc["direction"] == "up" or gc["is_green"]
    min_buy_threshold = 90 if regime == "ranging" else 100
    
    if buy_score >= min_buy_threshold and buy_score >= sell_score and trend_ok and quality_ok:
        action = "BUY"
        raw_score = buy_score
    elif sell_score >= 100 and sell_score > buy_score:
        action = "SELL"
        raw_score = sell_score
    else:
        action = "HOLD"
        raw_score = max(buy_score, sell_score)
    
    # ---- 7-Tier Signal (from crypto-ta-analyzer) ----
    net = (buy_score - sell_score) / max(buy_score + sell_score, 1)
    confidence = min(abs(net), 0.95)
    
    if action == "BUY":
        if confidence >= 0.7:
            signal_7tier = "STRONG_BUY"
        elif confidence >= 0.5:
            signal_7tier = "BUY"
        else:
            signal_7tier = "WEAK_BUY"
    elif action == "SELL":
        if confidence >= 0.7:
            signal_7tier = "STRONG_SELL"
        elif confidence >= 0.5:
            signal_7tier = "SELL"
        else:
            signal_7tier = "WEAK_SELL"
    else:
        signal_7tier = "NEUTRAL"
    
    return {
        "action": action,
        "signal_7tier": signal_7tier,
        "confidence": min(confidence, 0.95),
        "score": raw_score,
        "regime": regime,
        "adx": adx,
        "dmi_dir": dmi_dir,
        "gc_dir": gc_state,
        "buy_score": buy_score,
        "sell_score": sell_score,
        "quality_ok": quality_ok,
        "ichi_score": ichi_score,
        "vol_confirm": vol_confirm,
        "atr_pct": atr_pct,
    }


def analyze_symbol_mtf(symbol):
    tf_results = []
    for tf_name, tf_info in TIMEFRAMES.items():
        ohlc = fetch_ohlc(symbol, interval=tf_name, limit=tf_info["bars"])
        if ohlc and len(ohlc) >= 30:
            result = evaluate_tf(ohlc)
            result["timeframe"] = tf_name
            result["weight"] = tf_info["weight"]
            result["price"] = ohlc[-1]["close"]
            tf_results.append(result)
    if not tf_results:
        return {"action": "HOLD", "confidence": 0, "signal_7tier": "NEUTRAL", "mtf_details": {}}
    
    weighted_buy = sum(r["weight"] for r in tf_results if r["action"] == "BUY" and r["confidence"] > 0)
    weighted_sell = sum(r["weight"] for r in tf_results if r["action"] == "SELL" and r["confidence"] > 0)
    
    if weighted_buy > weighted_sell:
        action = "BUY"
        avg_conf = sum(r["confidence"] * r["weight"] for r in tf_results if r["action"] == "BUY") / max(weighted_buy, 0.01)
    elif weighted_sell > weighted_buy:
        action = "SELL"
        avg_conf = sum(r["confidence"] * r["weight"] for r in tf_results if r["action"] == "SELL") / max(weighted_sell, 0.01)
    else:
        action = "HOLD"
        avg_conf = 0
    
    mtf_details = {
        r["timeframe"]: {
            "action": r["action"],
            "signal_7tier": r.get("signal_7tier", "NEUTRAL"),
            "confidence": r["confidence"],
            "score": r["score"],
            "regime": r.get("regime", "unknown"),
            "adx": r.get("adx", 0),
            "quality_ok": r.get("quality_ok", False),
            "buy_score": r.get("buy_score", 0),
            "sell_score": r.get("sell_score", 0),
        }
        for r in tf_results
    }
    
    regimes = [r.get("regime", "unknown") for r in tf_results]
    dominant_regime = max(set(regimes), key=regimes.count) if regimes else "unknown"
    
    if action == "BUY":
        signal_7tier = "STRONG_BUY" if avg_conf >= 0.7 else "BUY" if avg_conf >= 0.5 else "WEAK_BUY"
    elif action == "SELL":
        signal_7tier = "STRONG_SELL" if avg_conf >= 0.7 else "SELL" if avg_conf >= 0.5 else "WEAK_SELL"
    else:
        signal_7tier = "NEUTRAL"
    
    return {
        "action": action,
        "signal_7tier": signal_7tier,
        "confidence": min(avg_conf, 0.95),
        "dominant_regime": dominant_regime,
        "mtf_details": mtf_details,
    }

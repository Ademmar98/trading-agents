from math import isnan

MIN_OHLC = 30


def _swing_highs(ohlc, window=5):
    highs = []
    for i in range(window, len(ohlc) - window):
        if all(ohlc[i]["high"] >= ohlc[i - j]["high"] for j in range(1, window + 1)) and \
           all(ohlc[i]["high"] >= ohlc[i + j]["high"] for j in range(1, window + 1)):
            highs.append((i, ohlc[i]["high"], ohlc[i]["ts"]))
    return highs


def _swing_lows(ohlc, window=5):
    lows = []
    for i in range(window, len(ohlc) - window):
        if all(ohlc[i]["low"] <= ohlc[i - j]["low"] for j in range(1, window + 1)) and \
           all(ohlc[i]["low"] <= ohlc[i + j]["low"] for j in range(1, window + 1)):
            lows.append((i, ohlc[i]["low"], ohlc[i]["ts"]))
    return lows


def _ema(data, period):
    if len(data) < period:
        return None
    k = 2 / (period + 1)
    ema = [data[0]]
    for d in data[1:]:
        ema.append(d * k + ema[-1] * (1 - k))
    return ema


def _rsi(closes, period=14):
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def _true_range(c):
    return max(c["high"] - c["low"],
               abs(c["high"] - c["close"]),
               abs(c["low"] - c["close"]))


def _atr(ohlc, period=14):
    if len(ohlc) < period + 1:
        return None
    trs = [_true_range(ohlc[i]) for i in range(1, period + 1)]
    atr = sum(trs) / period
    for i in range(period + 1, len(ohlc)):
        tr = _true_range(ohlc[i])
        atr = (atr * (period - 1) + tr) / period
    return atr


# ---- ICT STRATEGIES ----

def detect_fvg(ohlc):
    if len(ohlc) < 5:
        return None
    i = len(ohlc) - 3
    c1, c2, c3 = ohlc[i], ohlc[i + 1], ohlc[i + 2]
    bullish_fvg = c1["low"] > c3["high"] and c2["close"] < c3["high"]
    bearish_fvg = c1["high"] < c3["low"] and c2["close"] > c3["low"]
    if bullish_fvg:
        gap_top = c1["low"]
        gap_bot = c3["high"]
        if c3["close"] > gap_top:
            return {"action": "BUY", "confidence": 0.65, "reasons": [f"Bullish FVG {gap_bot:.5f}-{gap_top:.5f}"]}
    if bearish_fvg:
        gap_bot = c1["high"]
        gap_top = c3["low"]
        if c3["close"] < gap_bot:
            return {"action": "SELL", "confidence": 0.65, "reasons": [f"Bearish FVG {gap_bot:.5f}-{gap_top:.5f}"]}
    return None


def detect_order_block(ohlc, window=8):
    if len(ohlc) < 20:
        return None
    for i in range(len(ohlc) - window, len(ohlc) - 2):
        candle = ohlc[i]
        forward_move = abs(ohlc[i + 2]["close"] - candle["close"]) / candle["close"]
        if forward_move < 0.002:
            continue
        if candle["close"] > candle["open"]:
            body_top = candle["close"]
            if any(ohlc[i + 1 + j]["low"] <= body_top for j in range(3) if i + 1 + j < len(ohlc)):
                continue
            if ohlc[min(i + 4, len(ohlc) - 1)]["close"] > body_top:
                continue
            current = ohlc[-1]
            if abs(current["close"] - body_top) / body_top < 0.003:
                return {"action": "BUY", "confidence": 0.6, "reasons": ["Bullish OB near retest"]}
        else:
            body_bot = candle["close"]
            if any(ohlc[i + 1 + j]["high"] >= body_bot for j in range(3) if i + 1 + j < len(ohlc)):
                continue
            if ohlc[min(i + 4, len(ohlc) - 1)]["close"] < body_bot:
                continue
            current = ohlc[-1]
            if abs(current["close"] - body_bot) / body_bot < 0.003:
                return {"action": "SELL", "confidence": 0.6, "reasons": ["Bearish OB near retest"]}
    return None


def detect_liquidity_sweep(ohlc, window=10):
    if len(ohlc) < 30:
        return None
    recent = ohlc[-window:]
    prev = ohlc[-window * 2:-window]
    if not prev:
        return None
    swing_high_price = max(c["high"] for c in prev)
    swing_low_price = min(c["low"] for c in prev)
    current = ohlc[-1]
    for c in recent[:-1]:
        if c["high"] > swing_high_price:
            if current["close"] < swing_high_price and current["close"] > ohlc[-1]["open"]:
                return {"action": "SELL", "confidence": 0.7, "reasons": ["Liquidity sweep high, reversal SELL"]}
        if c["low"] < swing_low_price:
            if current["close"] > swing_low_price and current["close"] > ohlc[-1]["open"]:
                return {"action": "BUY", "confidence": 0.7, "reasons": ["Liquidity sweep low, reversal BUY"]}
    return None


def detect_bos_choch(ohlc, window=5):
    if len(ohlc) < 30:
        return None
    highs = _swing_highs(ohlc, window)
    lows = _swing_lows(ohlc, window)
    if len(highs) < 2 or len(lows) < 2:
        return None
    current = ohlc[-1]
    prev_highs = [h for h in highs if h[0] < len(ohlc) - 3]
    prev_lows = [l for l in lows if l[0] < len(ohlc) - 3]
    if len(prev_highs) >= 2:
        h1, h2 = prev_highs[-2], prev_highs[-1]
        if h2[1] < h1[1] and current["close"] > h2[1]:
            return {"action": "BUY", "confidence": 0.65, "reasons": ["BOS: broke downtrend structure"]}
    if len(prev_lows) >= 2:
        l1, l2 = prev_lows[-2], prev_lows[-1]
        if l2[1] > l1[1] and current["close"] < l2[1]:
            return {"action": "SELL", "confidence": 0.65, "reasons": ["BOS: broke uptrend structure"]}
    return None


def detect_ote(ohlc):
    if len(ohlc) < 20:
        return None
    highs = _swing_highs(ohlc, 5)
    lows = _swing_lows(ohlc, 5)
    if len(highs) < 1 or len(lows) < 1:
        return None
    recent_highs = [h for h in highs if h[0] < len(ohlc) - 2]
    recent_lows = [l for l in lows if l[0] < len(ohlc) - 2]
    if not recent_highs or not recent_lows:
        return None
    last_high = max(recent_highs, key=lambda x: x[0])
    last_low = max(recent_lows, key=lambda x: x[0])
    current = ohlc[-1]
    if last_high[0] > last_low[0]:
        move = last_high[1] - last_low[1]
        fib_618 = last_high[1] - move * 0.618
        fib_79 = last_high[1] - move * 0.79
        if fib_618 >= current["close"] >= fib_79:
            return {"action": "BUY", "confidence": 0.7, "reasons": ["OTE buy zone (61.8-79%)"]}
    else:
        move = last_low[1] - last_high[1]
        fib_618 = last_low[1] - move * 0.618
        fib_79 = last_low[1] - move * 0.79
        if fib_618 <= current["close"] <= fib_79:
            return {"action": "SELL", "confidence": 0.7, "reasons": ["OTE sell zone (61.8-79%)"]}
    return None


def detect_market_structure(ohlc):
    if len(ohlc) < 30:
        return None
    highs = _swing_highs(ohlc, 5)
    lows = _swing_lows(ohlc, 5)
    if len(highs) < 2 or len(lows) < 2:
        return None
    current = ohlc[-1]
    if highs[-1][1] > highs[-2][1] and lows[-1][1] > lows[-2][1]:
        return {"action": "BUY", "confidence": 0.5, "reasons": ["Uptrend HH/HL structure"]}
    if highs[-1][1] < highs[-2][1] and lows[-1][1] < lows[-2][1]:
        return {"action": "SELL", "confidence": 0.5, "reasons": ["Downtrend LH/LL structure"]}
    return None


# ---- CLASSIC STRATEGIES ----

def detect_sma_crossover(ohlc):
    if len(ohlc) < 50:
        return None
    closes = [c["close"] for c in ohlc]
    sma_20 = [sum(closes[i - 19:i + 1]) / 20 for i in range(19, len(closes))]
    sma_50 = [sum(closes[i - 49:i + 1]) / 50 for i in range(49, len(closes))]
    if len(sma_20) < 3 or len(sma_50) < 3:
        return None
    if sma_20[-3] <= sma_50[-3] and sma_20[-1] > sma_50[-1]:
        return {"action": "BUY", "confidence": 0.6, "reasons": ["Golden cross SMA 20/50"]}
    if sma_20[-3] >= sma_50[-3] and sma_20[-1] < sma_50[-1]:
        return {"action": "SELL", "confidence": 0.6, "reasons": ["Death cross SMA 20/50"]}
    return None


def detect_rsi_divergence(ohlc):
    if len(ohlc) < 20:
        return None
    closes = [c["close"] for c in ohlc]
    rsi_vals = []
    for i in range(14, len(closes)):
        v = _rsi(closes[:i + 1], 14)
        rsi_vals.append(v if v is not None else 50)
    if len(rsi_vals) < 10:
        return None
    price_lows = [min(closes[i:i + 5]) for i in range(len(closes) - 10, len(closes))]
    rsi_lows = [min(rsi_vals[i:i + 3]) for i in range(len(rsi_vals) - 10, len(rsi_vals))]
    if len(price_lows) >= 2 and len(rsi_lows) >= 2:
        if price_lows[-1] < price_lows[-2] and rsi_lows[-1] > rsi_lows[-2]:
            return {"action": "BUY", "confidence": 0.7, "reasons": ["Bullish RSI divergence"]}
        if price_lows[-1] > price_lows[-2] and rsi_lows[-1] < rsi_lows[-2]:
            return {"action": "SELL", "confidence": 0.7, "reasons": ["Bearish RSI divergence"]}
    return None


def detect_macd(ohlc):
    if len(ohlc) < 35:
        return None
    closes = [c["close"] for c in ohlc]
    ema12 = _ema(closes, 12)
    ema26 = _ema(closes, 26)
    if not ema12 or not ema26:
        return None
    macd_line = [ema12[i] - ema26[i] for i in range(len(ema26))]
    signal_line = _ema(macd_line, 9) if len(macd_line) >= 9 else None
    if not signal_line or len(signal_line) < 3:
        return None
    if macd_line[-3] <= signal_line[-3] and macd_line[-1] > signal_line[-1]:
        return {"action": "BUY", "confidence": 0.55, "reasons": ["MACD bullish cross"]}
    if macd_line[-3] >= signal_line[-3] and macd_line[-1] < signal_line[-1]:
        return {"action": "SELL", "confidence": 0.55, "reasons": ["MACD bearish cross"]}
    return None


def detect_bollinger(ohlc, period=20):
    if len(ohlc) < period + 5:
        return None
    closes = [c["close"] for c in ohlc[-period - 5:]]
    sma = sum(closes[-period:]) / period
    variance = sum((c - sma) ** 2 for c in closes[-period:]) / period
    std_dev = variance ** 0.5
    upper, lower = sma + 2 * std_dev, sma - 2 * std_dev
    current = closes[-1]
    if current < lower:
        return {"action": "BUY", "confidence": 0.55, "reasons": ["Price below lower Bollinger Band"]}
    if current > upper:
        return {"action": "SELL", "confidence": 0.55, "reasons": ["Price above upper Bollinger Band"]}
    return None


def detect_atr_breakout(ohlc):
    if len(ohlc) < 20:
        return None
    atr = _atr(ohlc, 14)
    if atr is None:
        return None
    recent = ohlc[-5:]
    closes = [c["close"] for c in recent]
    sma_close = sum(closes) / len(closes)
    current = ohlc[-1]["close"]
    atr_mult = atr * 1.5
    if abs(current - sma_close) < atr_mult * 0.3:
        return None
    direction = "BUY" if current > sma_close else "SELL"
    if direction == "BUY" and len([c for c in recent if c["close"] > c["open"]]) >= 3:
        return {"action": "BUY", "confidence": 0.55, "reasons": [f"ATR breakout {direction}"]}
    if direction == "SELL" and len([c for c in recent if c["close"] < c["open"]]) >= 3:
        return {"action": "SELL", "confidence": 0.55, "reasons": [f"ATR breakout {direction}"]}
    return None


# ---- PRICE ACTION STRATEGIES ----

def detect_engulfing(ohlc):
    if len(ohlc) < 3:
        return None
    c1, c2 = ohlc[-2], ohlc[-1]
    if c2["close"] > c2["open"] and c1["close"] < c1["open"]:
        if c2["open"] < c1["close"] and c2["close"] > c1["open"]:
            return {"action": "BUY", "confidence": 0.6, "reasons": ["Bullish engulfing"]}
    if c2["close"] < c2["open"] and c1["close"] > c1["open"]:
        if c2["open"] > c1["close"] and c2["close"] < c1["open"]:
            return {"action": "SELL", "confidence": 0.6, "reasons": ["Bearish engulfing"]}
    return None


def detect_pin_bar(ohlc):
    if len(ohlc) < 2:
        return None
    c = ohlc[-1]
    body = abs(c["close"] - c["open"])
    total_range = c["high"] - c["low"]
    if total_range == 0:
        return None
    upper_wick = c["high"] - max(c["open"], c["close"])
    lower_wick = min(c["open"], c["close"]) - c["low"]
    if lower_wick > body * 2 and lower_wick > upper_wick * 2 and lower_wick > total_range * 0.5:
        return {"action": "BUY", "confidence": 0.55, "reasons": ["Hammer / bullish pin bar"]}
    if upper_wick > body * 2 and upper_wick > lower_wick * 2 and upper_wick > total_range * 0.5:
        return {"action": "SELL", "confidence": 0.55, "reasons": ["Shooting star / bearish pin bar"]}
    return None


def detect_inside_bar(ohlc):
    if len(ohlc) < 5:
        return None
    mother = ohlc[-3]
    inside = ohlc[-2]
    current = ohlc[-1]
    if inside["high"] <= mother["high"] and inside["low"] >= mother["low"]:
        brk_high = inside["high"]
        brk_low = inside["low"]
        if current["close"] > brk_high:
            return {"action": "BUY", "confidence": 0.55, "reasons": ["Inside bar breakout up"]}
        if current["close"] < brk_low:
            return {"action": "SELL", "confidence": 0.55, "reasons": ["Inside bar breakout down"]}
    return None


def detect_double_top_bottom(ohlc, window=10):
    if len(ohlc) < 30:
        return None
    recent = ohlc[-window:]
    earlier = ohlc[-window * 2:-window]
    if not earlier:
        return None
    high1 = max(c["high"] for c in earlier)
    high1_idx = max(i for i, c in enumerate(earlier) if c["high"] == high1)
    high2 = max(c["high"] for c in recent)
    low1 = min(c["low"] for c in earlier)
    low1_idx = min(i for i, c in enumerate(earlier) if c["low"] == low1)
    low2 = min(c["low"] for c in recent)
    if abs(high2 - high1) / high1 < 0.005 and high2 >= high1:
        return {"action": "SELL", "confidence": 0.6, "reasons": ["Double top rejection"]}
    if abs(low2 - low1) / low1 < 0.005 and low2 <= low1:
        return {"action": "BUY", "confidence": 0.6, "reasons": ["Double bottom reversal"]}
    return None


# ---- ADDITIONAL STRATEGIES ----

def _vwap(ohlc):
    if len(ohlc) < 20:
        return None
    vol_sum = sum(c["volume"] for c in ohlc[-20:])
    if vol_sum == 0:
        return None
    pv_sum = sum(c["close"] * c["volume"] for c in ohlc[-20:])
    vwap = pv_sum / vol_sum
    current = ohlc[-1]["close"]
    bands = (max(c["high"] for c in ohlc[-20:]) - min(c["low"] for c in ohlc[-20:])) / vwap
    if current < vwap * (1 - bands * 0.5):
        return {"action": "BUY", "confidence": 0.55, "reasons": ["Price below VWAP support"]}
    if current > vwap * (1 + bands * 0.5):
        return {"action": "SELL", "confidence": 0.55, "reasons": ["Price above VWAP resistance"]}
    return None


def detect_ema_cross(ohlc):
    if len(ohlc) < 50:
        return None
    closes = [c["close"] for c in ohlc]
    ema_9 = _ema(closes, 9)
    ema_21 = _ema(closes, 21)
    if not ema_9 or not ema_21 or len(ema_9) < 5 or len(ema_21) < 5:
        return None
    if ema_9[-4] <= ema_21[-4] and ema_9[-1] > ema_21[-1]:
        return {"action": "BUY", "confidence": 0.6, "reasons": ["EMA 9/21 golden cross"]}
    if ema_9[-4] >= ema_21[-4] and ema_9[-1] < ema_21[-1]:
        return {"action": "SELL", "confidence": 0.6, "reasons": ["EMA 9/21 death cross"]}
    return None


def _stochastic(closes, k_period=14, d_period=3):
    if len(closes) < k_period + d_period:
        return None, None
    k_vals = []
    for i in range(k_period - 1, len(closes)):
        high = max(closes[i - k_period + 1:i + 1])
        low = min(closes[i - k_period + 1:i + 1])
        if high == low:
            k_vals.append(50)
        else:
            k_vals.append((closes[i] - low) / (high - low) * 100)
    d_vals = []
    for i in range(d_period - 1, len(k_vals)):
        d_vals.append(sum(k_vals[i - d_period + 1:i + 1]) / d_period)
    return k_vals, d_vals


def detect_stochastic_rsi(ohlc):
    if len(ohlc) < 30:
        return None
    closes = [c["close"] for c in ohlc]
    k, d = _stochastic(closes)
    if not k or not d:
        return None
    if k[-1] < 20 and d[-1] < 20 and k[-1] > d[-1]:
        return {"action": "BUY", "confidence": 0.65, "reasons": ["Stochastic oversold crossover"]}
    if k[-1] > 80 and d[-1] > 80 and k[-1] < d[-1]:
        return {"action": "SELL", "confidence": 0.65, "reasons": ["Stochastic overbought crossover"]}
    return None


def _ichimoku(ohlc):
    if len(ohlc) < 52:
        return None, None, None, None, None
    highs = [c["high"] for c in ohlc]
    lows = [c["low"] for c in ohlc]
    closes = [c["close"] for c in ohlc]
    tenkan = (max(highs[-9:]) + min(lows[-9:])) / 2
    kijun = (max(highs[-26:]) + min(lows[-26:])) / 2
    senkou_a = (tenkan + kijun) / 2
    senkou_b = (max(highs[-52:]) + min(lows[-52:])) / 2
    chikou = closes[-26] if len(closes) > 26 else closes[-1]
    return tenkan, kijun, senkou_a, senkou_b, chikou


def detect_ichimoku(ohlc):
    tenkan, kijun, senkou_a, senkou_b, chikou = _ichimoku(ohlc)
    if tenkan is None:
        return None
    current = ohlc[-1]["close"]
    if tenkan > kijun and current > senkou_a and current > senkou_b:
        return {"action": "BUY", "confidence": 0.6, "reasons": ["Ichimoku bullish (TK cross + cloud above)"]}
    if tenkan < kijun and current < senkou_a and current < senkou_b:
        return {"action": "SELL", "confidence": 0.6, "reasons": ["Ichimoku bearish (TK cross + cloud below)"]}
    return None


def detect_keltner(ohlc, period=20):
    if len(ohlc) < period + 5:
        return None
    closes = [c["close"] for c in ohlc[-period - 5:]]
    highs = [c["high"] for c in ohlc[-period - 5:]]
    lows = [c["low"] for c in ohlc[-period - 5:]]
    ema_val = _ema(closes, period)
    if not ema_val:
        return None
    ema_val = ema_val[-1]
    trs = [max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1])) for i in range(1, len(closes))]
    atr_val = sum(trs[-period:]) / period
    upper = ema_val + 2 * atr_val
    lower = ema_val - 2 * atr_val
    current = closes[-1]
    if current < lower:
        return {"action": "BUY", "confidence": 0.55, "reasons": ["Price below lower Keltner channel"]}
    if current > upper:
        return {"action": "SELL", "confidence": 0.55, "reasons": ["Price above upper Keltner channel"]}
    return None


def detect_volume_breakout(ohlc):
    if len(ohlc) < 30:
        return None
    recent = ohlc[-5:]
    older = ohlc[-30:-5]
    avg_vol_older = sum(c["volume"] for c in older) / len(older) if older else 0
    if avg_vol_older == 0:
        return None
    for c in recent:
        if c["volume"] > avg_vol_older * 2:
            direction = "BUY" if c["close"] > c["open"] else "SELL"
            return {"action": direction, "confidence": 0.6, "reasons": [f"Volume breakout {direction}"]}
    return None


def detect_support_resistance(ohlc):
    if len(ohlc) < 30:
        return None
    closes = [c["close"] for c in ohlc]
    highs = [c["high"] for c in ohlc]
    lows = [c["low"] for c in ohlc[-20:]]
    resistance = sum(highs[-5:]) / 5
    support = min(lows[-5:])
    current = closes[-1]
    if current <= support * 1.005:
        return {"action": "BUY", "confidence": 0.5, "reasons": ["Price near support level"]}
    if current >= resistance * 0.995:
        return {"action": "SELL", "confidence": 0.5, "reasons": ["Price near resistance level"]}
    return None


ALL_STRATEGIES = [
    ("ICT - FVG", detect_fvg),
    ("ICT - Order Block", detect_order_block),
    ("ICT - Liquidity Sweep", detect_liquidity_sweep),
    ("ICT - BOS/CHoCH", detect_bos_choch),
    ("ICT - OTE", detect_ote),
    ("ICT - Market Structure", detect_market_structure),
    ("Classic - SMA Crossover", detect_sma_crossover),
    ("Classic - EMA Cross 9/21", detect_ema_cross),
    ("Classic - RSI Divergence", detect_rsi_divergence),
    ("Classic - MACD", detect_macd),
    ("Classic - Bollinger", detect_bollinger),
    ("Classic - Keltner Channel", detect_keltner),
    ("Classic - ATR Breakout", detect_atr_breakout),
    ("Classic - VWAP", _vwap),
    ("Classic - Ichimoku", detect_ichimoku),
    ("Classic - Stochastic RSI", detect_stochastic_rsi),
    ("PA - Engulfing", detect_engulfing),
    ("PA - Pin Bar", detect_pin_bar),
    ("PA - Inside Bar", detect_inside_bar),
    ("PA - Double Top/Bot", detect_double_top_bottom),
    ("PA - Volume Breakout", detect_volume_breakout),
    ("PA - S/R Levels", detect_support_resistance),
]


def scan_symbol(ohlc):
    signals = []
    for name, fn in ALL_STRATEGIES:
        try:
            sig = fn(ohlc)
            if sig:
                sig["strategy"] = name
                signals.append(sig)
        except Exception:
            pass
    if not signals:
        return []
    combined = {}
    for s in signals:
        action = s["action"]
        combined.setdefault(action, {"action": action, "confidence": 0, "reasons": [], "strategies": []})
        combined[action]["confidence"] = max(combined[action]["confidence"], s["confidence"])
        combined[action]["reasons"].extend(s["reasons"])
        combined[action]["strategies"].append(s["strategy"])
    for a in combined:
        n = len(combined[a]["strategies"])
        combined[a]["confidence"] = min(combined[a]["confidence"] + n * 0.05, 0.95)
    return list(combined.values())

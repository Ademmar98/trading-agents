from math import isnan
import logging

_log = logging.getLogger("strategies")

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
        if candle["close"] == 0:
            continue
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
            if body_top and abs(current["close"] - body_top) / body_top < 0.003:
                return {"action": "BUY", "confidence": 0.6, "reasons": ["Bullish OB near retest"]}
        else:
            body_bot = candle["close"]
            if any(ohlc[i + 1 + j]["high"] >= body_bot for j in range(3) if i + 1 + j < len(ohlc)):
                continue
            if ohlc[min(i + 4, len(ohlc) - 1)]["close"] < body_bot:
                continue
            current = ohlc[-1]
            if body_bot and abs(current["close"] - body_bot) / body_bot < 0.003:
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
    # Non-overlapping pivot windows: compare the prior 5-bar window against
    # the latest 5-bar window. The old sliding windows overlapped by 4 bars,
    # so consecutive "lows" were nearly identical and the divergence check
    # was comparing a window with itself.
    win = 5
    price_low_prior = min(closes[-win * 2:-win])
    price_low_recent = min(closes[-win:])
    rsi_low_prior = min(rsi_vals[-win * 2:-win])
    rsi_low_recent = min(rsi_vals[-win:])
    if price_low_recent < price_low_prior and rsi_low_recent > rsi_low_prior:
        return {"action": "BUY", "confidence": 0.7, "reasons": ["Bullish RSI divergence"]}
    if price_low_recent > price_low_prior and rsi_low_recent < rsi_low_prior:
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
    if high1 and abs(high2 - high1) / high1 < 0.005 and high2 >= high1:
        return {"action": "SELL", "confidence": 0.6, "reasons": ["Double top rejection"]}
    if low1 and abs(low2 - low1) / low1 < 0.005 and low2 <= low1:
        return {"action": "BUY", "confidence": 0.6, "reasons": ["Double bottom reversal"]}
    return None


# ---- ADDITIONAL STRATEGIES ----

def _vwap(ohlc):
    if len(ohlc) < 21:
        return None
    ref = ohlc[-21:-1]
    vol_sum = sum(c["volume"] for c in ref)
    if vol_sum == 0:
        return None
    # VWAP must use the typical price (H+L+C)/3, not the close.
    pv_sum = sum(((c["high"] + c["low"] + c["close"]) / 3) * c["volume"] for c in ref)
    vwap = pv_sum / vol_sum
    current = ohlc[-1]["close"]
    bands = (max(c["high"] for c in ref) - min(c["low"] for c in ref)) / vwap
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
    # Senkou spans are displaced 26 bars FORWARD: the cloud value seen at the
    # latest bar was computed 26 bars ago from data ending then. The old code
    # used current-bar values for the spans (no displacement), so "price vs
    # cloud" compared price against a cloud from the wrong time. Senkou B at
    # the latest bar therefore needs 52 + 26 = 78 bars of history.
    if len(ohlc) < 78:
        return None, None, None, None, None
    highs = [c["high"] for c in ohlc]
    lows = [c["low"] for c in ohlc]
    closes = [c["close"] for c in ohlc]
    tenkan = (max(highs[-9:]) + min(lows[-9:])) / 2
    kijun = (max(highs[-26:]) + min(lows[-26:])) / 2
    tenkan_then = (max(highs[-35:-26]) + min(lows[-35:-26])) / 2
    kijun_then = (max(highs[-52:-26]) + min(lows[-52:-26])) / 2
    senkou_a = (tenkan_then + kijun_then) / 2
    senkou_b = (max(highs[-78:-26]) + min(lows[-78:-26])) / 2
    chikou = closes[-1]  # current close, plotted 26 bars back
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


def detect_donchian(ohlc, period=20):
    if len(ohlc) < period + 3:
        return None
    # Channel from PRIOR bars only: including the current bar puts its own
    # high/low inside the channel, so a breakout close can never fire.
    prior = ohlc[-period - 1:-1]
    upper = max(c["high"] for c in prior)
    lower = min(c["low"] for c in prior)
    current = ohlc[-1]["close"]
    if current > upper and ohlc[-2]["close"] <= upper:
        return {"action": "BUY", "confidence": 0.6, "reasons": ["Donchian breakout upper"]}
    if current < lower and ohlc[-2]["close"] >= lower:
        return {"action": "SELL", "confidence": 0.6, "reasons": ["Donchian breakdown lower"]}
    return None


def _heikin_ashi(ohlc):
    """Proper Heikin-Ashi recursion: ha_open = (prev_ha_open + prev_ha_close)/2.
    (The old code carried only the previous ha_open forward, so the HA open
    stayed frozen near bar 0 and the trend read was meaningless.)"""
    ha = []
    for i in range(len(ohlc)):
        c = ohlc[i]
        ha_close = (c["open"] + c["high"] + c["low"] + c["close"]) / 4
        ha_open = (ha[-1]["open"] + ha[-1]["close"]) / 2 if ha else (c["open"] + c["close"]) / 2
        ha_high = max(c["high"], ha_open, ha_close)
        ha_low = min(c["low"], ha_open, ha_close)
        ha.append({"open": ha_open, "close": ha_close, "high": ha_high, "low": ha_low})
    return ha


def detect_heikin_ashi(ohlc):
    if len(ohlc) < 10:
        return None
    ha = _heikin_ashi(ohlc)
    last5 = ha[-5:]
    green = sum(1 for c in last5 if c["close"] > c["open"])
    bodies = [abs(c["close"] - c["open"]) for c in last5]
    avg_body = sum(bodies) / len(bodies) if bodies else 0
    if green >= 4 and avg_body > 0:
        return {"action": "BUY", "confidence": 0.55, "reasons": ["Heikin-Ashi strong uptrend"]}
    if green <= 1 and avg_body > 0:
        return {"action": "SELL", "confidence": 0.55, "reasons": ["Heikin-Ashi strong downtrend"]}
    return None


def _typical_price(ohlc):
    return [(c["high"] + c["low"] + c["close"]) / 3 for c in ohlc]


def _raw_money_flow(ohlc):
    tp = _typical_price(ohlc)
    return [tp[i] * ohlc[i]["volume"] for i in range(len(ohlc))]


def detect_mfi(ohlc, period=14):
    if len(ohlc) < period * 2:
        return None
    closes = [c["close"] for c in ohlc]
    mf = _raw_money_flow(ohlc)
    mfi_vals = []
    for i in range(period, len(mf)):
        pos = neg = 0
        for j in range(i - period, i):
            if closes[j + 1] > closes[j]:
                pos += mf[j]
            else:
                neg += mf[j]
        mfi_val = 100 - (100 / (1 + pos / neg)) if neg > 0 else 100
        mfi_vals.append(mfi_val)
    if len(mfi_vals) < 3:
        return None
    cur_mfi = mfi_vals[-1]
    prev_price = closes[-3]
    cur_price = closes[-1]
    if cur_mfi < 20 and cur_price > prev_price:
        return {"action": "BUY", "confidence": 0.6, "reasons": ["MFI oversold bullish divergence"]}
    if cur_mfi > 80 and cur_price < prev_price:
        return {"action": "SELL", "confidence": 0.6, "reasons": ["MFI overbought bearish divergence"]}
    return None


def _adx_single(ohlc, period=14):
    """Return ADX value for the full series using Wilder's method."""
    if len(ohlc) < period + 1:
        return None
    trs, plus_dm, minus_dm = [], [], []
    for i in range(1, len(ohlc)):
        tr = _true_range(ohlc[i])
        trs.append(tr)
        up_move = ohlc[i]["high"] - ohlc[i - 1]["high"]
        down_move = ohlc[i - 1]["low"] - ohlc[i]["low"]
        if up_move > down_move and up_move > 0:
            plus_dm.append(up_move)
        else:
            plus_dm.append(0)
        if down_move > up_move and down_move > 0:
            minus_dm.append(down_move)
        else:
            minus_dm.append(0)
    # Wilder smoothing: seed with the SMA of the first `period` values, then
    # smooth across the WHOLE series via prev*(n-1)/n + cur/n. The old code
    # only ever averaged the FIRST `period` values and ignored everything
    # after, so the DX barely moved regardless of the latest price action.
    atr = sum(trs[:period]) / period
    sm_plus = sum(plus_dm[:period]) / period
    sm_minus = sum(minus_dm[:period]) / period
    for i in range(period, len(trs)):
        atr = (atr * (period - 1) + trs[i]) / period
        sm_plus = (sm_plus * (period - 1) + plus_dm[i]) / period
        sm_minus = (sm_minus * (period - 1) + minus_dm[i]) / period
    di_p = sm_plus / atr * 100 if atr > 0 else 0
    di_n = sm_minus / atr * 100 if atr > 0 else 0
    dx = abs(di_p - di_n) / (di_p + di_n) * 100 if (di_p + di_n) > 0 else 0
    return dx


def detect_adx(ohlc, period=14):
    if len(ohlc) < period * 3:
        return None
    dx_vals = []
    for i in range(period * 2, len(ohlc)):
        dx = _adx_single(ohlc[:i + 1], period)
        if dx is not None:
            dx_vals.append(dx)
    if len(dx_vals) < period:
        return None
    adx = sum(dx_vals[-period:]) / period
    closes = [c["close"] for c in ohlc[-period:]]
    trend_up = closes[-1] > closes[0] if len(closes) > 1 else False
    if adx >= 25:
        direction = "BUY" if trend_up else "SELL"
        return {"action": direction, "confidence": 0.55, "reasons": [f"ADX strong trend ({adx:.0f})"]}
    return None


def detect_pivot_reversal(ohlc, window=5):
    if len(ohlc) < window * 4:
        return None
    highs = _swing_highs(ohlc, window)
    lows = _swing_lows(ohlc, window)
    recent_highs = [h for h in highs if h[0] >= len(ohlc) - window * 2]
    recent_lows = [l for l in lows if l[0] >= len(ohlc) - window * 2]
    current = ohlc[-1]["close"]
    if recent_highs:
        pivot_high = max(recent_highs, key=lambda x: x[1])
        if current < pivot_high[1] * 0.995:
            return {"action": "SELL", "confidence": 0.55, "reasons": ["Pivot high rejection"]}
    if recent_lows:
        pivot_low = min(recent_lows, key=lambda x: x[1])
        if current > pivot_low[1] * 1.005:
            return {"action": "BUY", "confidence": 0.55, "reasons": ["Pivot low bounce"]}
    return None


def detect_volume_price_trend(ohlc):
    if len(ohlc) < 30:
        return None
    vpt = [0.0]
    for i in range(1, len(ohlc)):
        change = (ohlc[i]["close"] - ohlc[i - 1]["close"]) / max(ohlc[i - 1]["close"], 1e-10)
        vpt.append(vpt[-1] + change * ohlc[i]["volume"])
    vpt_sma = sum(vpt[-14:]) / 14 if len(vpt) >= 14 else sum(vpt) / len(vpt)
    cur = vpt[-1]
    if cur > vpt_sma * 1.02:
        return {"action": "BUY", "confidence": 0.5, "reasons": ["VPT bullish divergence"]}
    if cur < vpt_sma * 0.98:
        return {"action": "SELL", "confidence": 0.5, "reasons": ["VPT bearish divergence"]}
    return None


# ---------------------------------------------------------------------------
# New strategy families (core/strats/*), merged into ALL_STRATEGIES below.
# Two registry conventions exist in those modules:
#   * (display_name, fn) tuples mirroring ALL_STRATEGIES —
#     trend_momentum.STRATEGIES, mean_reversion.MEAN_REVERSION_STRATEGIES,
#     breakout_volatility.STRATEGIES, volume_orderflow.STRATEGIES,
#     quant_forex_specific.STRATEGIES
#   * (machine_tag, fn) tuples —
#     ict_smc_priceaction.ICT_SMC_PRICEACTION_STRATEGIES; the tag itself is
#     kept as the strategy name so per-tag attribution survives scan_symbol's
#     combining (its own scan_ict_smc_priceaction attributes by the same tag).
# NOTE: core/strats/volume_orderflow.py imports _swing_highs/_swing_lows from
# this module, so these imports must stay BELOW the helper definitions, and
# the merge is retried lazily on first use if the first attempt happened in
# the middle of that circular import.
# ---------------------------------------------------------------------------

_FAMILY_REGISTRY_SPECS = (
    ("core.strats.trend_momentum", "STRATEGIES"),
    ("core.strats.mean_reversion", "MEAN_REVERSION_STRATEGIES"),
    ("core.strats.breakout_volatility", "STRATEGIES"),
    ("core.strats.volume_orderflow", "STRATEGIES"),
    ("core.strats.ict_smc_priceaction", "ICT_SMC_PRICEACTION_STRATEGIES"),
    ("core.strats.quant_forex_specific", "STRATEGIES"),
)


def _family_strategy_entries():
    """Import the six core/strats family registries and normalise both
    registry shapes into (name, fn) entries."""
    import importlib
    entries = []
    for mod_name, reg_name in _FAMILY_REGISTRY_SPECS:
        mod = importlib.import_module(mod_name)
        for name, fn in getattr(mod, reg_name):
            entries.append((name, fn))
    return entries


try:
    _FAMILY_ENTRIES = _family_strategy_entries()
except (ImportError, AttributeError):  # circular import in progress; retry lazily
    _FAMILY_ENTRIES = None


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
    ("Classic - Donchian Channel", detect_donchian),
    ("PA - Heikin-Ashi", detect_heikin_ashi),
    ("Classic - MFI", detect_mfi),
    ("Classic - ADX", detect_adx),
    ("PA - Pivot Reversal", detect_pivot_reversal),
    ("Classic - VPT", detect_volume_price_trend),
] + (_FAMILY_ENTRIES or [])


def _ensure_family_merged():
    """Return ALL_STRATEGIES, retrying the family merge once if the module
    level attempt ran mid circular-import (idempotent)."""
    global _FAMILY_ENTRIES
    if _FAMILY_ENTRIES is None:
        try:
            _FAMILY_ENTRIES = _family_strategy_entries()
        except (ImportError, AttributeError):
            _FAMILY_ENTRIES = []
        ALL_STRATEGIES.extend(_FAMILY_ENTRIES)
    return ALL_STRATEGIES

# Regime map. Legacy lists are preserved exactly; every new family strategy
# is added under sensible regimes (trend families -> trending*, mean
# reversion -> ranging, breakout/volatility -> volatile, volume/orderflow and
# ICT/quant split by setup type, with cross-listed subsets where a strategy
# genuinely fits two regimes). strategies_for_regime FAILS OPEN (with a
# logged note) for any ALL_STRATEGIES name not listed in ANY regime, so
# future additions are never silently dropped by the filter.
# ---------------------------------------------------------------------------

_TRENDING_LEGACY = [
    "ICT - BOS/CHoCH", "ICT - Market Structure", "ICT - Order Block",
    "Classic - SMA Crossover", "Classic - EMA Cross 9/21",
    "Classic - MACD", "Classic - Ichimoku",
    "Classic - Donchian Channel", "PA - Heikin-Ashi",
    "ICT - Liquidity Sweep", "Classic - ADX",
]

_TREND_FAMILY = [n for n, _ in ALL_STRATEGIES if n.startswith("Trend - ")]
_MR_FAMILY = [n for n, _ in ALL_STRATEGIES if n.startswith("MR - ")]
_BREAKOUT_FAMILY = [n for n, _ in ALL_STRATEGIES
                    if n.startswith(("Breakout - ", "Volatility - "))]
_VO_FAMILY = [n for n, _ in ALL_STRATEGIES if n.startswith("VO - ")]
_ICT_PA_FAMILY = [n for n, _ in ALL_STRATEGIES if n.startswith("ict_pa_")]


def _pick(pool, names):
    """Subset of `pool` matching `names`, in pool order."""
    wanted = set(names)
    return [n for n in pool if n in wanted]


# Trend-family strategies whose logic is breakout/continuation — they also
# belong in volatile regimes.
_TREND_BREAKOUT_SUBSET = _pick(_TREND_FAMILY, [
    "Trend - Turtle S1 Donchian 20/10",
    "Trend - Turtle S2 Donchian 55/20",
    "Trend - Ichimoku Kumo Breakout",
    "Trend - Keltner Breakout 20/2ATR",
    "Trend - Dual Thrust Opening Range",
])

# Breakout-family entries that are really trend-continuation setups.
_BREAKOUT_TREND_SUBSET = _pick(_BREAKOUT_FAMILY, [
    "Breakout - Donchian 20 (Turtle S1)",
    "Breakout - Donchian 55 (Turtle S2)",
    "Breakout - N-Day High Trend-Filtered",
    "Breakout - Retest Continuation",
    "Breakout - Volume-Confirmed Donchian",
    "Breakout - VCP",
    "Breakout - Keltner Channel",
    "Volatility - Bollinger Band Walk",
    "Volatility - TTM Squeeze",
    "Volatility - ATR Expansion",
])

# Volume/orderflow split: trend-confirming vs mean-reverting vs breakout.
_VO_TREND_SUBSET = _pick(_VO_FAMILY, [
    "VO - OBV Trend Confirm", "VO - OBV Divergence", "VO - CVD Proxy Trend",
    "VO - VWAP Pullback", "VO - AVWAP Reclaim", "VO - Volume Dry-Up",
    "VO - Force Index Pullback", "VO - Klinger Cross", "VO - EMV Zero Cross",
    "VO - Chaikin ADL Divergence",
])
_VO_RANGE_SUBSET = _pick(_VO_FAMILY, [
    "VO - VWAP Mean Reversion", "VO - MFI Extremes", "VO - CMF Filter",
    "VO - Climactic Reversal", "VO - POC Retest",
])
_VO_BREAKOUT_SUBSET = _pick(_VO_FAMILY, [
    "VO - OBV Breakout Lead", "VO - VWAP Squeeze Break",
    "VO - Value Area Break", "VO - LVN Vacuum", "VO - CVD Proxy Divergence",
    "VO - RVOL Breakout", "VO - Climactic Reversal", "VO - MFI Extremes",
])

# ICT/SMC price-action split (registry names are the machine tags).
_ICT_PA_TREND_SUBSET = _pick(_ICT_PA_FAMILY, [
    "ict_pa_order_block", "ict_pa_breaker", "ict_pa_mitigation",
    "ict_pa_bos_pullback", "ict_pa_choch_reversal", "ict_pa_ote",
    "ict_pa_premium_discount", "ict_pa_unicorn", "ict_pa_power_of_three",
    "ict_pa_silver_bullet", "ict_pa_killzone_orb", "ict_pa_engulfing",
    "ict_pa_morning_star", "ict_pa_three_soldiers", "ict_pa_harami",
    "ict_pa_sr_flip", "ict_pa_supply_demand",
])
_ICT_PA_RANGE_SUBSET = _pick(_ICT_PA_FAMILY, [
    "ict_pa_fvg_retrace", "ict_pa_ifvg", "ict_pa_ote",
    "ict_pa_premium_discount", "ict_pa_pin_bar", "ict_pa_doji_extreme",
    "ict_pa_tweezer", "ict_pa_inside_bar", "ict_pa_sr_flip",
    "ict_pa_supply_demand", "ict_pa_engulfing", "ict_pa_harami",
])
_ICT_PA_VOL_SUBSET = _pick(_ICT_PA_FAMILY, [
    "ict_pa_liquidity_sweep", "ict_pa_turtle_soup", "ict_pa_judas_swing",
    "ict_pa_fvg_retrace", "ict_pa_ifvg", "ict_pa_silver_bullet",
    "ict_pa_killzone_orb", "ict_pa_choch_reversal", "ict_pa_doji_extreme",
    "ict_pa_pin_bar", "ict_pa_engulfing", "ict_pa_morning_star",
    "ict_pa_three_soldiers",
])

# Quant/forex-specific split. The three regime-switchers adapt internally,
# so they are listed under every regime.
_QUANT_TREND = ["Quant - TSMOM Classic", "Quant - TSMOM Ensemble"]
_QUANT_RANGE = [
    "Quant - OU Mean Reversion", "Quant - Range Grid", "Quant - Infinity Grid",
    "Quant - Weekend Drift", "Quant - Turn of Month", "Quant - Time of Day",
    "Quant - NY Close Reversion", "Quant - Post-Event Drift",
]
_QUANT_VOL = ["Quant - London Breakout", "Quant - Overlap ORB"]
_QUANT_REGIME_SWITCHERS = [
    "Quant - ADX Regime Switch", "Quant - Vol Percentile Regime",
    "Quant - Hurst Regime",
]

REGIME_STRATEGIES = {
    "trending_up": (
        _TRENDING_LEGACY + ["Classic - ATR Breakout"]
        + _TREND_FAMILY + _BREAKOUT_TREND_SUBSET + _VO_TREND_SUBSET
        + _ICT_PA_TREND_SUBSET + _QUANT_TREND + _QUANT_REGIME_SWITCHERS
    ),
    "trending_down": (
        _TRENDING_LEGACY + ["Classic - ATR Breakout"]
        + _TREND_FAMILY + _BREAKOUT_TREND_SUBSET + _VO_TREND_SUBSET
        + _ICT_PA_TREND_SUBSET + _QUANT_TREND + _QUANT_REGIME_SWITCHERS
    ),
    "trending": (
        _TRENDING_LEGACY + ["Classic - VPT"]
        + _TREND_FAMILY + _BREAKOUT_TREND_SUBSET + _VO_TREND_SUBSET
        + _ICT_PA_TREND_SUBSET + _QUANT_TREND + _QUANT_REGIME_SWITCHERS
    ),
    "ranging": ([
        "ICT - OTE", "ICT - FVG", "ICT - Order Block",
        "Classic - Bollinger", "Classic - RSI Divergence",
        "Classic - Stochastic RSI", "Classic - Keltner Channel",
        "PA - Inside Bar", "PA - Engulfing", "PA - Pin Bar",
        "PA - Double Top/Bot", "PA - S/R Levels",
        "Classic - VWAP", "Classic - MFI",
        "PA - Pivot Reversal", "Classic - VPT",
    ]
        + _MR_FAMILY + _VO_RANGE_SUBSET + _ICT_PA_RANGE_SUBSET
        + _QUANT_RANGE + _QUANT_REGIME_SWITCHERS
    ),
    "volatile": ([
        "ICT - FVG", "ICT - Liquidity Sweep",
        "Classic - ATR Breakout", "PA - Volume Breakout",
        "Classic - VWAP", "Classic - Bollinger",
        "PA - Double Top/Bot", "PA - Engulfing", "PA - Pin Bar",
        "ICT - Order Block", "PA - Pivot Reversal",
        "Classic - VPT",
    ]
        + _BREAKOUT_FAMILY + _TREND_BREAKOUT_SUBSET + _VO_BREAKOUT_SUBSET
        + _ICT_PA_VOL_SUBSET + _QUANT_VOL + _QUANT_REGIME_SWITCHERS
    ),
}

_REGIME_KNOWN_NAMES = None
_fail_open_noted = set()


def _regime_known_names():
    global _REGIME_KNOWN_NAMES
    if _REGIME_KNOWN_NAMES is None:
        known = set()
        for names in REGIME_STRATEGIES.values():
            known.update(names)
        _REGIME_KNOWN_NAMES = known
    return _REGIME_KNOWN_NAMES


def strategies_for_regime(regime):
    _ensure_family_merged()
    names = REGIME_STRATEGIES.get(regime)
    if not names:
        return ALL_STRATEGIES
    name_set = set(names)
    known = _regime_known_names()
    selected = []
    for n, fn in ALL_STRATEGIES:
        if n in name_set:
            selected.append((n, fn))
        elif n not in known:
            # Fail OPEN: a strategy not listed in ANY regime still runs, so
            # future additions are never silently dropped by the filter.
            if n not in _fail_open_noted:
                _log.warning(
                    "Strategy %r is not listed in REGIME_STRATEGIES; "
                    "failing open (it runs in every regime)", n)
                _fail_open_noted.add(n)
            selected.append((n, fn))
    return selected


def scan_symbol(ohlc, regime=None, exclude_strategies=None):
    strategies = strategies_for_regime(regime) if regime else _ensure_family_merged()
    if exclude_strategies:
        exclude = set(exclude_strategies)
        strategies = [(n, fn) for n, fn in strategies if n not in exclude]
    signals = []
    for name, fn in strategies:
        try:
            sig = fn(ohlc)
            if sig:
                # Per-tag attribution: the registry name stays the canonical
                # "strategy" (regime filtering and the unprofitable-strategy
                # exclusion loop match on it, and signals of different
                # strategy names are never merged into one name); a family
                # module's embedded machine tag is preserved under "tag".
                embedded = sig.get("strategy")
                if embedded and embedded != name:
                    sig.setdefault("tag", embedded)
                sig["strategy"] = name
                signals.append(sig)
        except Exception as e:
            _log.warning("Strategy %s: %s", name, e)
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
        combined[a]["confidence"] = min(combined[a]["confidence"], 0.95)
    return list(combined.values())

import math
from statistics import stdev, mean


def _tr(high, low, prev_close):
    return max(high - low, abs(high - prev_close), abs(low - prev_close))


def sma(values, period):
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


def ema(values, period):
    if len(values) < period:
        return None
    multiplier = 2 / (period + 1)
    result = sum(values[:period]) / period
    for v in values[period:]:
        result = (v - result) * multiplier + result
    return result


def ema_all(values, period):
    if len(values) < period:
        return []
    multiplier = 2 / (period + 1)
    result = [sum(values[:period]) / period]
    for v in values[period:]:
        result.append((v - result[-1]) * multiplier + result[-1])
    return result


def rsi(closes, period=14):
    if len(closes) < period + 1:
        return 50.0
    diffs = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [d if d > 0 else 0 for d in diffs]
    losses = [-d if d < 0 else 0 for d in diffs]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def macd(closes, fast=12, slow=26, signal=9):
    if len(closes) < slow + signal:
        return None
    fast_ema = ema_all(closes, fast)
    slow_ema = ema_all(closes, slow)
    if not fast_ema or not slow_ema:
        return None
    offset = len(fast_ema) - len(slow_ema)
    macd_line = [fast_ema[i + offset] - slow_ema[i] for i in range(len(slow_ema))]
    signal_line = ema_all(macd_line, signal)
    if not signal_line:
        return None
    offset2 = len(macd_line) - len(signal_line)
    histogram = [macd_line[i + offset2] - signal_line[i] for i in range(len(signal_line))]
    return {
        "macd": round(macd_line[-1], 4),
        "signal": round(signal_line[-1], 4),
        "histogram": round(histogram[-1], 4),
        "trend": "bullish" if histogram[-1] > 0 else "bearish",
    }


def bollinger_bands(closes, period=20, std_dev=2.0):
    if len(closes) < period:
        return None
    middle = sma(closes, period)
    if middle is None:
        return None
    latest = closes[-period:]
    sd = stdev(latest) if len(latest) > 1 else 0
    upper = middle + sd * std_dev
    lower = middle - sd * std_dev
    width = ((upper - lower) / middle) * 100 if middle else 0
    current = closes[-1]
    bb_pct = (current - lower) / (upper - lower) if (upper - lower) > 0 else 0.5
    return {
        "upper": round(upper, 4),
        "middle": round(middle, 4),
        "lower": round(lower, 4),
        "width": round(width, 2),
        "bb_pct": round(bb_pct, 3),
        "squeeze": width < 5.0 if width else False,
    }


def atr(highs, lows, closes, period=14):
    if len(highs) < period + 1 or len(lows) < period + 1 or len(closes) < period + 1:
        return None
    tr_values = []
    for i in range(1, len(closes)):
        tr_values.append(_tr(highs[i], lows[i], closes[i - 1]))
    if len(tr_values) < period:
        return None
    atr_val = sum(tr_values[:period]) / period
    for i in range(period, len(tr_values)):
        atr_val = (atr_val * (period - 1) + tr_values[i]) / period
    return round(atr_val, 4)


def stochastic_rsi(closes, period=14, k=3, d=3):
    if len(closes) < period + k + d:
        return None
    rsi_values = []
    for i in range(period, len(closes) + 1):
        rsi_values.append(rsi(closes[:i], period))
    if len(rsi_values) < k:
        return None
    stoch_values = []
    for i in range(k, len(rsi_values) + 1):
        chunk = rsi_values[i - k:i]
        low = min(chunk)
        high = max(chunk)
        if high == low:
            stoch = 50.0
        else:
            stoch = (rsi_values[i - 1] - low) / (high - low) * 100
        stoch_values.append(stoch)
    if len(stoch_values) < d:
        return None
    k_line = stoch_values[-1]
    d_line = sma(stoch_values, d) if len(stoch_values) >= d else stoch_values[-1]
    return {
        "k": round(k_line, 2),
        "d": round(d_line, 2),
        "overbought": k_line > 80 and d_line > 80,
        "oversold": k_line < 20 and d_line < 20,
    }


def mfi(highs, lows, closes, volumes, period=14):
    if len(closes) < period + 1:
        return None
    typical_prices = [(highs[i] + lows[i] + closes[i]) / 3 for i in range(len(closes))]
    money_flows = [typical_prices[i] * volumes[i] for i in range(len(closes))]
    positive_flow = 0
    negative_flow = 0
    for i in range(len(typical_prices) - period, len(typical_prices)):
        if i == 0:
            continue
        if typical_prices[i] > typical_prices[i - 1]:
            positive_flow += money_flows[i]
        else:
            negative_flow += money_flows[i]
    if negative_flow == 0:
        return 100.0
    mfi_val = 100.0 - (100.0 / (1.0 + positive_flow / negative_flow))
    return round(mfi_val, 2)


def obv(closes, volumes):
    if not closes or not volumes or len(closes) != len(volumes):
        return None
    obv_val = 0
    for i in range(1, len(closes)):
        if closes[i] > closes[i - 1]:
            obv_val += volumes[i]
        elif closes[i] < closes[i - 1]:
            obv_val -= volumes[i]
    return int(obv_val)


def compute_all(ohlc):
    if not ohlc:
        return {}
    closes = [c["close"] for c in ohlc]
    has_ohl = "high" in ohlc[0] and "low" in ohlc[0]
    highs = [c.get("high", c["close"]) for c in ohlc] if has_ohl else None
    lows = [c.get("low", c["close"]) for c in ohlc] if has_ohl else None
    volumes = [c.get("volume", 0) for c in ohlc]
    if not closes:
        return {}
    current = closes[-1]
    result = {
        "current_price": current,
        "sma_20": sma(closes, 20),
        "sma_50": sma(closes, 50),
        "sma_200": sma(closes, 200),
        "ema_12": ema(closes, 12),
        "ema_26": ema(closes, 26),
        "rsi_14": rsi(closes, 14),
    }
    macd_val = macd(closes)
    if macd_val:
        result["macd"] = macd_val
    bb = bollinger_bands(closes)
    if bb:
        result["bollinger"] = bb
    if highs and lows:
        atr_val = atr(highs, lows, closes)
        if atr_val:
            result["atr"] = atr_val
        vol_high = max(highs[-14:]) if len(highs) >= 14 else max(highs)
        vol_low = min(lows[-14:]) if len(lows) >= 14 else min(lows)
        result["volatility"] = round((vol_high - vol_low) / (vol_low or 1) * 100, 2)
        mfi_val = mfi(highs, lows, closes, volumes)
        if mfi_val is not None:
            result["mfi"] = mfi_val
    else:
        vol_high = max(closes[-14:]) if len(closes) >= 14 else max(closes)
        vol_low = min(closes[-14:]) if len(closes) >= 14 else min(closes)
        result["volatility"] = round((vol_high - vol_low) / (vol_low or 1) * 100, 2)
    stoch = stochastic_rsi(closes)
    if stoch:
        result["stoch_rsi"] = stoch
    obv_val = obv(closes, volumes)
    if obv_val is not None:
        result["obv"] = obv_val
    if result.get("sma_20") and result.get("sma_50"):
        result["trend"] = "bullish" if result["sma_20"] > result["sma_50"] else "bearish"
    else:
        result["trend"] = "neutral"
    return result

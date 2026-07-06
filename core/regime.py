def detect_regime(ohlc):
    if len(ohlc) < 60:
        return "unknown"
    closes = [c["close"] for c in ohlc]
    highs = [c["high"] for c in ohlc]
    lows = [c["low"] for c in ohlc]

    def _ema(data, period):
        if len(data) < period:
            return None
        k = 2 / (period + 1)
        ema = [data[0]]
        for d in data[1:]:
            ema.append(d * k + ema[-1] * (1 - k))
        return ema[-1] if ema else None

    def _atr(ohlc, period=14):
        if len(ohlc) < period + 1:
            return None
        trs = []
        for i in range(1, period + 1):
            tr = max(ohlc[i]["high"] - ohlc[i]["low"],
                     abs(ohlc[i]["high"] - ohlc[i - 1]["close"]),
                     abs(ohlc[i]["low"] - ohlc[i - 1]["close"]))
            trs.append(tr)
        atr = sum(trs) / period
        for i in range(period + 1, len(ohlc)):
            tr = max(ohlc[i]["high"] - ohlc[i]["low"],
                     abs(ohlc[i]["high"] - ohlc[i - 1]["close"]),
                     abs(ohlc[i]["low"] - ohlc[i - 1]["close"]))
            atr = (atr * (period - 1) + tr) / period
        return atr

    current = closes[-1]
    sma_20 = sum(closes[-20:]) / 20
    sma_50 = sum(closes[-50:]) / 50

    adx = _adx(highs, lows, closes, 14)
    bb_width, bb_avg_width = _bb(closes, 20)
    atr_val = _atr(ohlc, 14)
    atr_pct = (atr_val / current * 100) if current and atr_val else 0

    if adx is not None and adx > 25:
        if current > sma_50 and sma_20 > sma_50:
            regime = "trending_up"
        elif current < sma_50 and sma_20 < sma_50:
            regime = "trending_down"
        else:
            regime = "trending"
    elif bb_width > bb_avg_width * 1.5:
        regime = "volatile"
    elif atr_pct > 4:
        regime = "volatile"
    else:
        regime = "ranging"

    # Additional regime characteristics
    bb_position = (current - sma_20) / (sma_20 * 0.05 + 0.01) if sma_20 else 0
    volume_ratio = _volume_ratio(ohlc[-20:])

    return {
        "regime": regime,
        "adx": round(adx, 1) if adx else 0,
        "atr_pct": round(atr_pct, 2),
        "volatility": round(atr_pct * 2, 2),
        "trend_strength": round(adx, 1) if adx else 0,
        "bb_position": round(bb_position, 2),
        "volume_ratio": round(volume_ratio, 2),
        "sma_20_50_cross": "bullish" if sma_20 > sma_50 else "bearish",
        "price_vs_sma": round((current / sma_20 - 1) * 100, 2) if sma_20 else 0,
    }


def _volume_ratio(ohlc_segment):
    if len(ohlc_segment) < 10:
        return 1.0
    recent = [c["volume"] for c in ohlc_segment[-5:]]
    older = [c["volume"] for c in ohlc_segment[:-5]]
    avg_older = sum(older) / len(older) if older else 1
    return (sum(recent) / len(recent)) / avg_older if avg_older > 0 else 1.0


def _adx(highs, lows, closes, period=14):
    if len(closes) < period * 2:
        return None
    plus_dm, minus_dm, tr = [], [], []
    for i in range(1, len(closes)):
        up_move = highs[i] - highs[i - 1]
        down_move = lows[i - 1] - lows[i]
        plus_dm.append(max(up_move, 0) if up_move > down_move else 0)
        minus_dm.append(max(down_move, 0) if down_move > up_move else 0)
        tr.append(max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1])))
    if not tr:
        return None
    atr = sum(tr[:period]) / period
    avg_plus = sum(plus_dm[:period]) / period
    avg_minus = sum(minus_dm[:period]) / period
    for i in range(period, len(tr)):
        atr = (atr * (period - 1) + tr[i]) / period
        avg_plus = (avg_plus * (period - 1) + plus_dm[i]) / period
        avg_minus = (avg_minus * (period - 1) + minus_dm[i]) / period
    pdi = (avg_plus / atr) * 100 if atr > 0 else 0
    ndi = (avg_minus / atr) * 100 if atr > 0 else 0
    dx = abs(pdi - ndi) / (pdi + ndi) * 100 if (pdi + ndi) > 0 else 0
    return dx


def _bb(closes, period=20):
    if len(closes) < period:
        return 0, 0
    sma = sum(closes[-period:]) / period
    variance = sum((c - sma) ** 2 for c in closes[-period:]) / period
    std = variance ** 0.5
    bb_width = 2 * std / sma * 100 if sma > 0 else 0
    all_bb = []
    for i in range(period, len(closes)):
        s = sum(closes[i - period:i]) / period
        v = sum((c - s) ** 2 for c in closes[i - period:i]) / period
        sd = v ** 0.5
        all_bb.append(2 * sd / s * 100 if s > 0 else 0)
    avg_width = sum(all_bb[-20:]) / min(len(all_bb[-20:]), 1) if all_bb else bb_width
    return bb_width, avg_width

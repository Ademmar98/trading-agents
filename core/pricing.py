from config import RISK_PER_TRADE_PCT


REGIME_PRICING = {
    "trending_up":   {"sl_mult": 2.5, "tp_mult": 4.0, "entry_slip": 0.003, "risk_mult": 1.10},
    "trending_down": {"sl_mult": 2.5, "tp_mult": 4.0, "entry_slip": 0.003, "risk_mult": 1.10},
    "trending":      {"sl_mult": 2.5, "tp_mult": 3.5, "entry_slip": 0.004, "risk_mult": 1.00},
    "volatile":      {"sl_mult": 3.5, "tp_mult": 4.5, "entry_slip": 0.005, "risk_mult": 0.85},
    "ranging":       {"sl_mult": 3.0, "tp_mult": 2.5, "entry_slip": 0.004, "risk_mult": 0.90},
}

DEFAULT_PRICING = {"sl_mult": 3.0, "tp_mult": 3.5, "entry_slip": 0.003, "risk_mult": 0.90}


def compute_pricing(symbol, action, price, data, regime=None, atr_val=0):
    vol = data.get("volatility", 2.0)
    vol_dec = max(vol / 100.0, 0.005)
    atr_pct = (atr_val / price * 100) if atr_val and price > 0 else vol_dec * 100
    atr_dec = max(atr_pct / 100.0, 0.005)

    cfg = REGIME_PRICING.get(regime, DEFAULT_PRICING) if regime else DEFAULT_PRICING
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

    denom = entry_price or 1e-8
    sl_pct = abs(entry_price - sl_price) / denom * 100
    tp_pct = abs(tp_price - entry_price) / denom * 100
    risk_pct = round(min(RISK_PER_TRADE_PCT * risk_mult, RISK_PER_TRADE_PCT * 1.5), 2)

    return {
        "symbol": symbol,
        "action": action,
        "regime": regime or "unknown",
        "entry_price": entry_price,
        "stop_loss": sl_price,
        "take_profit": tp_price,
        "sl_pct": round(sl_pct, 1),
        "tp_pct": round(tp_pct, 1),
        "sl_mult": sl_mult,
        "tp_mult": tp_mult,
        "calculated_risk_pct": risk_pct,
        "volatility_used": round(vol, 2),
        "atr_pct": round(atr_pct, 2),
        "risk_rationale": (
            f"{regime or 'unknown'}: SL at {sl_mult}x vol ({sl_pct:.1f}%), "
            f"TP at {tp_mult}x vol ({tp_pct:.1f}%), "
            f"risk {risk_pct:.2f}%"
        ),
    }

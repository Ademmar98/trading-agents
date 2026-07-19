from math import floor, log10

from config import RISK_PER_TRADE_PCT, MAX_SL_PCT, MAX_TP_PCT


def round_sig(x, sig=6):
    """Round to significant figures, not fixed decimals. round(x, 5) turns a
    $0.00003 micro-cap's price grid into 33% steps — SL/TP geometry becomes
    nonsense and backtests explode (a real +13,716% artifact)."""
    if not x:
        return x
    return round(x, max(0, sig - 1 - floor(log10(abs(x)))))


# risk_mult parked at 1.0 in EVERY regime until regime detection is
# re-validated: the old ADX/regime dial was never validated and sat on an
# arithmetic bug in core/regime.py (see config.py's SMA200 note), so letting
# it scale position risk up or down multiplied noise. Structure kept — only
# the sizing multipliers are neutralized; sl/tp/entry_slip are unchanged.
REGIME_PRICING = {
    "trending_up":   {"sl_mult": 1.5, "tp_mult": 2.5, "entry_slip": 0.001, "risk_mult": 1.0},
    "trending_down": {"sl_mult": 1.5, "tp_mult": 2.5, "entry_slip": 0.001, "risk_mult": 1.0},
    "trending":      {"sl_mult": 1.5, "tp_mult": 2.0, "entry_slip": 0.002, "risk_mult": 1.0},
    "volatile":      {"sl_mult": 2.0, "tp_mult": 2.5, "entry_slip": 0.003, "risk_mult": 1.0},
    "ranging":       {"sl_mult": 1.5, "tp_mult": 1.5, "entry_slip": 0.002, "risk_mult": 1.0},
}

DEFAULT_PRICING = {"sl_mult": 1.5, "tp_mult": 2.0, "entry_slip": 0.002, "risk_mult": 0.90}


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

    # ATR-first placement: the pair's own noise sets the stop; MIN/MAX act
    # only as sanity rails. The old max(atr, range-vol) formula ballooned in
    # volatile sessions until every stop landed at exactly the cap — six
    # correlated alts all stopped at -2% in one 30-minute dip.
    from core.risk import vol_aware_stop_loss
    sl_pct_val = vol_aware_stop_loss(atr_pct, sl_mult)
    if sl_pct_val is None:
        sl_pct_val = max(0.3, min(vol_dec * 100 * sl_mult * 1.2, MAX_SL_PCT))
    sl_distance = sl_pct_val / 100.0
    # TP keeps the regime's R:R ratio relative to the actual stop
    tp_distance = min(sl_distance * (tp_mult / sl_mult), MAX_TP_PCT / 100.0)
    sma_20 = data.get("sma_20") or 0
    sma_50 = data.get("sma_50") or 0

    if action == "BUY":
        target = bid
        if sma_20 > 0 and target > sma_20:
            entry_price = round_sig(max(sma_20, target * (1 - cfg["entry_slip"])))
        elif sma_50 > 0 and target > sma_50:
            entry_price = round_sig(max(sma_50, target * (1 - cfg["entry_slip"] * 0.7)))
        else:
            entry_price = round_sig(target)
        sl_price = round_sig(entry_price * (1 - sl_distance))
        tp_price = round_sig(entry_price * (1 + tp_distance))
    else:
        target = ask
        if sma_20 > 0 and target < sma_20:
            entry_price = round_sig(min(sma_20, target * (1 + cfg["entry_slip"])))
        elif sma_50 > 0 and target < sma_50:
            entry_price = round_sig(min(sma_50, target * (1 + cfg["entry_slip"] * 0.7)))
        else:
            entry_price = round_sig(target)
        sl_price = round_sig(entry_price * (1 + sl_distance))
        tp_price = round_sig(entry_price * (1 - tp_distance))

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

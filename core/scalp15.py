"""15-minute scalping signal stack.

Entry requires all three to agree:
- Trend filter: price above EMA(SCALP_EMA_PERIOD) for longs, below for shorts.
- Momentum: MACD histogram crossing zero in the trend direction on the
  latest bar (fresh crossover, not a stale state).
- RSI guard: longs blocked when overbought, shorts blocked when oversold —
  no buying local tops or selling local bottoms.

Exits are volatility-adaptive: SL sits SCALP_ATR_SL_MULT x ATR(14) from
entry, TP is derived from the win-rate/R:R matrix (higher estimated win
probability -> quicker target). Position size follows the position-sizer
skill's ATR method: qty = (equity x risk%) / (ATR x multiplier).
"""
from config import (
    SCALP_EMA_PERIOD, SCALP_ATR_SL_MULT, SCALP_RSI_OVERBOUGHT,
    SCALP_RSI_OVERSOLD, RISK_PER_TRADE_PCT,
)
from core.data_provider import fetch_ohlc
from core.database import fetchone
from core.indicators import ema_all, rsi, atr

TIMEFRAME = "15m"
MIN_BARS = 60


def _macd_series(closes, fast=12, slow=26, signal=9):
    """MACD histogram as a series — crossover detection needs the previous
    value, which indicators.macd() (last-value-only) doesn't expose."""
    fast_e = ema_all(closes, fast)
    slow_e = ema_all(closes, slow)
    if not fast_e or not slow_e:
        return []
    off = len(fast_e) - len(slow_e)
    macd_line = [fast_e[i + off] - slow_e[i] for i in range(len(slow_e))]
    sig = ema_all(macd_line, signal)
    if not sig:
        return []
    off2 = len(macd_line) - len(sig)
    return [macd_line[i + off2] - sig[i] for i in range(len(sig))]


def estimate_win_probability(regime_aligned):
    """Estimated win probability for the scalp stack.

    Laplace-smoothed empirical win rate of the 'scalp_15m' strategy
    ((wins + 1) / (trades + 2), prior 0.5) plus a synergy bonus when the
    detected regime agrees with the trade direction. This is an honest
    heuristic, NOT a calibrated probability — treat gates on it accordingly.
    """
    row = fetchone(
        "SELECT trades, win_rate FROM strategy_stats WHERE strategy='scalp_15m'"
    )
    n = row["trades"] if row and row["trades"] else 0
    wins = (row["win_rate"] / 100.0) * n if row and row["win_rate"] else 0
    base = (wins + 1.0) / (n + 2.0)
    bonus = 0.10 if regime_aligned else 0.0
    return max(0.05, min(0.95, base + bonus))


def rr_for_win_prob(wp):
    """Win-rate matrix -> R:R target.

    High estimated win probability earns a quick scalping target; a weaker
    estimate must reach further so expectancy (wp*rr - (1-wp)) stays
    positive. Breakeven win rate for R:R r is 1/(1+r).
    """
    if wp >= 0.85:
        return 1.0
    if wp >= 0.70:
        return 1.2
    if wp >= 0.60:
        return 1.5
    return 2.0


def scalp_15m_signal(symbol, regime=None, ohlc=None):
    """Return a scalp setup dict, or None when the stack doesn't align."""
    if ohlc is None:
        ohlc = fetch_ohlc(symbol, interval=TIMEFRAME, limit=130)
    if not ohlc or len(ohlc) < MIN_BARS:
        return None
    closes = [b["close"] for b in ohlc]
    highs = [b["high"] for b in ohlc]
    lows = [b["low"] for b in ohlc]
    price = closes[-1]

    ema_series = ema_all(closes, SCALP_EMA_PERIOD)
    hist = _macd_series(closes)
    if not ema_series or len(hist) < 2 or not price:
        return None
    ema_v = ema_series[-1]
    rsi_v = rsi(closes)
    atr_v = atr(highs, lows, closes)
    if not atr_v or not ema_v:
        return None

    crossed_up = hist[-2] <= 0 < hist[-1]
    crossed_down = hist[-2] >= 0 > hist[-1]

    action = None
    if price > ema_v and crossed_up and rsi_v < SCALP_RSI_OVERBOUGHT:
        action = "BUY"
    elif price < ema_v and crossed_down and rsi_v > SCALP_RSI_OVERSOLD:
        action = "SELL"
    if not action:
        return None

    regime_aligned = (
        (action == "BUY" and regime in ("trending_up", "trending")) or
        (action == "SELL" and regime in ("trending_down",))
    )
    wp = estimate_win_probability(regime_aligned)
    rr = rr_for_win_prob(wp)

    sl_dist = atr_v * SCALP_ATR_SL_MULT
    if action == "BUY":
        stop_loss = price - sl_dist
        take_profit = price + sl_dist * rr
    else:
        stop_loss = price + sl_dist
        take_profit = price - sl_dist * rr

    return {
        "action": action,
        "price": price,
        "entry_price": round(price, 5),
        "stop_loss": round(stop_loss, 5),
        "take_profit": round(take_profit, 5),
        "sl_pct": round(sl_dist / price * 100, 2),
        "tp_pct": round(sl_dist * rr / price * 100, 2),
        "atr": atr_v,
        "ema": round(ema_v, 5),
        "rsi": round(rsi_v, 1),
        "rr": rr,
        "win_prob": round(wp, 3),
        "calculated_risk_pct": RISK_PER_TRADE_PCT,
        "reasons": [
            f"scalp15 EMA{SCALP_EMA_PERIOD} {'up' if action == 'BUY' else 'down'}trend",
            "scalp15 MACD cross",
            f"scalp15 RSI {rsi_v:.0f}",
            f"scalp15 wp {wp:.0%} rr {rr}",
        ],
    }


def atr_position_size(equity, atr_value, atr_multiplier=SCALP_ATR_SL_MULT,
                      risk_pct=RISK_PER_TRADE_PCT):
    """position-sizer skill, 'atr_based' method:
    stop_distance = atr * multiplier; qty = (equity * risk%) / stop_distance.
    (See .agents/skills/position-sizer/scripts/position_sizer.py.)"""
    stop_distance = atr_value * atr_multiplier
    if stop_distance <= 0 or equity <= 0:
        return 0.0
    return (equity * risk_pct / 100.0) / stop_distance

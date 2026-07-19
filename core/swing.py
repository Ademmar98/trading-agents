"""Swing desk: multi-day BUY setups from daily structure with 4h alignment.

Runs beside the scalp stack under its own strategy tags so the learning
loop and the scorecard judge each style separately:

- swing_breakout — today's close clears the highest high of the prior 20
  daily bars (trend continuation).
- swing_pullback — uptrend intact, price back within one ATR of the daily
  EMA21 with RSI reset to 35-55 and a bullish close (buy the dip).
- swing_momentum — fresh daily MACD histogram cross above zero with RSI
  not yet overbought (early trend turn).

Requirements before any setup counts (BUY-only firm — no counter-trend
swings):
- 60+ daily bars and 60+ 4h bars of history;
- daily uptrend: SMA20 > SMA50 and price > SMA50;
- 4h alignment: latest 4h close above its EMA50.

Exits are swing-scale by firm policy: SL = SWING_ATR_SL_MULT x daily ATR,
clamped to [SWING_MIN_SL_PCT, SWING_MAX_SL_PCT] (1-25%); TP = SL x SWING_RR,
clamped to [SWING_MIN_TP_PCT, SWING_MAX_TP_PCT] (3-100%). Position size uses
the position-sizer skill's coupling downstream (risk$ / stop distance), so a
25% stop automatically opens a far smaller position than a 1% stop; the
exit-strategies skill's ATR-multiple stop / R-multiple target method is the
basis for the geometry.
"""
from config import (
    SWING_ATR_SL_MULT, SWING_RR, SWING_MIN_SL_PCT, SWING_MAX_SL_PCT,
    SWING_MIN_TP_PCT, SWING_MAX_TP_PCT, SWING_RISK_PER_TRADE_PCT,
)
from core.indicators import sma, ema, ema_all, rsi, atr
from core.pricing import round_sig

MIN_BARS = 60


def _clamp(v, lo, hi):
    return max(lo, min(v, hi))


def _macd_hist(closes, fast=12, slow=26, signal=9):
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


def swing_signal(symbol, ohlc_1d, ohlc_4h, regime=None):
    """Return one swing BUY setup dict for the symbol, or None.

    The dict carries its own complete geometry (entry/SL/TP + percents) so
    the execution agent never re-prices it with scalp-scale caps.
    """
    if not ohlc_1d or len(ohlc_1d) < MIN_BARS or not ohlc_4h or len(ohlc_4h) < MIN_BARS:
        return None
    closes = [b["close"] for b in ohlc_1d]
    highs = [b["high"] for b in ohlc_1d]
    lows = [b["low"] for b in ohlc_1d]
    price = closes[-1]
    sma20 = sma(closes, 20)
    sma50 = sma(closes, 50)
    if not price or not sma20 or not sma50:
        return None

    # Requirement 1: daily uptrend — longs only, with the trend
    if not (sma20 > sma50 and price > sma50):
        return None

    # Requirement 2: 4h alignment — the entry timeframe must agree
    closes_4h = [b["close"] for b in ohlc_4h]
    ema50_4h = ema(closes_4h, 50)
    if not ema50_4h or closes_4h[-1] <= ema50_4h:
        return None

    atr_d = atr(highs, lows, closes)
    if not atr_d:
        return None
    atr_pct = atr_d / price * 100
    rsi_d = rsi(closes)
    ema21 = ema(closes, 21)

    setup = None
    conf = 0.0
    detail = ""
    prior_high = max(highs[-21:-1])
    if price > prior_high:
        setup, conf = "swing_breakout", 0.70
        detail = f"close above 20d high {round_sig(prior_high)}"
    elif ema21 and abs(price - ema21) <= atr_d and 35 <= rsi_d <= 55 and closes[-1] > closes[-2]:
        setup, conf = "swing_pullback", 0.65
        detail = f"pullback to daily EMA21, RSI {rsi_d:.0f}"
    else:
        hist = _macd_hist(closes)
        if len(hist) >= 2 and hist[-2] <= 0 < hist[-1] and rsi_d < 70:
            setup, conf = "swing_momentum", 0.60
            detail = f"daily MACD turned positive, RSI {rsi_d:.0f}"
    if not setup:
        return None

    sl_pct = _clamp(atr_pct * SWING_ATR_SL_MULT, SWING_MIN_SL_PCT, SWING_MAX_SL_PCT)
    tp_pct = _clamp(sl_pct * SWING_RR, SWING_MIN_TP_PCT, SWING_MAX_TP_PCT)

    return {
        "action": "BUY",
        "style": "swing",
        "strategy": setup,
        "confidence": conf,
        "price": price,
        "entry_price": round_sig(price),
        "stop_loss": round_sig(price * (1 - sl_pct / 100)),
        "take_profit": round_sig(price * (1 + tp_pct / 100)),
        "sl_pct": round(sl_pct, 2),
        "tp_pct": round(tp_pct, 2),
        "atr": atr_d,
        "calculated_risk_pct": SWING_RISK_PER_TRADE_PCT,
        "timeframe": "1d",
        "reasons": [
            f"{setup}: daily uptrend + 4h aligned",
            detail,
            f"SL {sl_pct:.1f}% / TP {tp_pct:.1f}% (daily ATR {atr_pct:.2f}%)",
        ],
    }

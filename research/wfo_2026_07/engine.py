"""Long-only spot backtest engine with regime filtering, ATR risk geometry,
and walk-forward optimization. No shorts, no leverage (halal constraints).

Execution model (anti-look-ahead):
  - indicators/signals computed on bar t close
  - entries/exits at bar t+1 OPEN
  - intrabar SL/TP on high/low touch; SL has priority when both touch
  - costs: FEE_SIDE of notional per side (taker + slippage)
"""
import numpy as np
import pandas as pd

FEE_SIDE = 0.0007          # 0.05% taker + 0.02% slippage
RISK_PCT = 0.5             # % of sleeve equity risked per trade
MAX_POS_PCT = 15.0         # % of sleeve equity max notional (mirror firm cap)
HOURS_YEAR = 24 * 365


# ── indicators ──
def add_indicators(df, donch_n=48):
    o, h, l, c = df["open"].values, df["high"].values, df["low"].values, df["close"].values
    out = df.copy()
    out["ema50"] = df["close"].ewm(span=50, adjust=False).mean()
    out["ema200"] = df["close"].ewm(span=200, adjust=False).mean()

    prev_c = np.roll(c, 1); prev_c[0] = c[0]
    tr = np.maximum(h - l, np.maximum(abs(h - prev_c), abs(l - prev_c)))
    out["atr"] = pd.Series(tr).ewm(alpha=1 / 14, adjust=False).mean().values
    out["atr_pct"] = out["atr"] / out["close"] * 100
    out["atr_rank"] = out["atr_pct"].rolling(24 * 90, min_periods=24 * 10).rank(pct=True)

    # Wilder ADX(14)
    up = h - np.roll(h, 1); dn = np.roll(l, 1) - l
    up[0] = dn[0] = 0
    plus_dm = np.where((up > dn) & (up > 0), up, 0.0)
    minus_dm = np.where((dn > up) & (dn > 0), dn, 0.0)
    atr_w = pd.Series(tr).ewm(alpha=1 / 14, adjust=False).mean()
    plus_di = 100 * pd.Series(plus_dm).ewm(alpha=1 / 14, adjust=False).mean() / atr_w
    minus_di = 100 * pd.Series(minus_dm).ewm(alpha=1 / 14, adjust=False).mean() / atr_w
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    out["adx"] = dx.ewm(alpha=1 / 14, adjust=False).mean().fillna(0).values

    out["donch_hi"] = df["high"].rolling(donch_n).max().shift(1)   # prior N bars
    out["prev_high"] = df["high"].shift(1)
    out["touched_ema50"] = (df["low"] <= out["ema50"]).rolling(3).max().shift(1)
    return out


def regime_of(row, adx_thr):
    if row.close < row.ema200:
        return "bear"
    if row.atr_rank > 0.90:
        return "highvol"
    if row.adx >= adx_thr:
        return "trend"
    return "range"


# ── core backtest over one prepared frame ──
def backtest(df, adx_thr=22, k_sl=2.5, k_tr=3.5, k_tp=8.0,
             entry_mode="breakout", start_equity=100_000.0):
    c = df["close"].values; o = df["open"].values
    h = df["high"].values; l = df["low"].values
    ema50 = df["ema50"].values; ema200 = df["ema200"].values
    atr = df["atr"].values; atr_rank = df["atr_rank"].values
    adx = df["adx"].values; donch = df["donch_hi"].values
    prev_high = df["prev_high"].values; touched = df["touched_ema50"].values

    equity = start_equity
    cash = start_equity
    pos_qty = 0.0; pos_entry = 0.0; sl = tp = 0.0
    peak_close = 0.0; entry_atr = 0.0; entry_regime = ""
    entered_at = 0
    trades = []
    eq_curve = np.empty(len(df)); eq_curve[:] = np.nan
    pending_entry = None       # set on signal bar, executed next open
    pending_exit = False

    for i in range(len(df)):
        # 1. execute pending orders at THIS bar's open
        if pending_exit and pos_qty > 0:
            px = o[i] * (1 - FEE_SIDE)
            cash += pos_qty * px
            trades.append(dict(entry=pos_entry, exit=o[i], qty=pos_qty,
                               pnl=pos_qty * (px - pos_entry),
                               reason="regime_exit", regime=entry_regime,
                               bars=i - entered_at, i_entry=entered_at, i_exit=i))
            pos_qty = 0.0
        pending_exit = False
        if pending_entry is not None and pos_qty == 0:
            risk_cash = equity * RISK_PCT / 100
            sl_dist = pending_entry["k_sl"] * pending_entry["atr"]
            qty = risk_cash / sl_dist if sl_dist > 0 else 0.0
            qty *= pending_entry["mult"]
            cap_qty = equity * MAX_POS_PCT / 100 / o[i]
            qty = min(qty, cap_qty, cash / (o[i] * (1 + FEE_SIDE)))
            if qty > 0:
                px = o[i] * (1 + FEE_SIDE)
                cash -= qty * px
                pos_qty = qty; pos_entry = px
                entry_atr = pending_entry["atr"]
                sl = o[i] - pending_entry["k_sl"] * entry_atr
                tp = o[i] + pending_entry["k_tp"] * entry_atr
                peak_close = o[i]
                entry_regime = pending_entry["regime"]
                entered_at = i
        pending_entry = None

        # 2. intrabar SL/TP on this bar (SL priority — conservative)
        if pos_qty > 0:
            exit_px = None; reason = None
            if l[i] <= sl:
                exit_px, reason = sl, "stop_loss"
            elif h[i] >= tp:
                exit_px, reason = tp, "take_profit"
            if exit_px is not None:
                px = exit_px * (1 - FEE_SIDE)
                cash += pos_qty * px
                trades.append(dict(entry=pos_entry, exit=exit_px, qty=pos_qty,
                                   pnl=pos_qty * (px - pos_entry),
                                   reason=reason, regime=entry_regime,
                                   bars=i - entered_at, i_entry=entered_at, i_exit=i))
                pos_qty = 0.0

        # 3. end-of-bar bookkeeping + signals for next bar
        if pos_qty > 0:
            peak_close = max(peak_close, c[i])
            # trailing activates after +1 ATR of progress
            if peak_close >= pos_entry + entry_atr:
                sl = max(sl, peak_close - k_tr * entry_atr)
            if c[i] < ema200[i]:
                pending_exit = True

        equity = cash + pos_qty * c[i]
        eq_curve[i] = equity

        if pos_qty == 0 and not np.isnan(donch[i]) and i > 24 * 10:
            r = ("bear" if c[i] < ema200[i]
                 else "highvol" if atr_rank[i] > 0.90
                 else "trend" if adx[i] >= adx_thr
                 else "range")
            sig = None; mult = 1.0
            if r == "trend":
                if entry_mode in ("breakout", "both") and c[i] > donch[i]:
                    sig = "breakout"
                if (entry_mode in ("pullback", "both") and sig is None
                        and touched[i] > 0 and c[i] > prev_high[i]):
                    sig, mult = "pullback", 1.25
            if sig:
                pending_entry = dict(atr=atr[i], k_sl=k_sl, k_tp=k_tp,
                                     mult=mult, regime=r)

    return trades, pd.Series(eq_curve, index=df.index)


# ── metrics ──
def metrics(trades, eq, start_equity=100_000.0):
    eq = eq.dropna()
    if len(eq) < 10:
        return dict(sharpe=0, cagr=0, maxdd=0, calmar=0, pf=0, wr=0,
                    trades=0, net=0, exposure=0)
    ret = eq.pct_change().dropna()
    sharpe = (ret.mean() / ret.std() * np.sqrt(HOURS_YEAR)) if ret.std() > 0 else 0.0
    yrs = len(eq) / HOURS_YEAR
    cagr = (eq.iloc[-1] / eq.iloc[0]) ** (1 / yrs) - 1 if yrs > 0 else 0.0
    peak = eq.cummax()
    maxdd = ((eq - peak) / peak).min()
    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    gw = sum(t["pnl"] for t in wins); gl = abs(sum(t["pnl"] for t in losses))
    pf = gw / gl if gl > 0 else (np.inf if gw > 0 else 0.0)
    in_pos_bars = sum(t["bars"] for t in trades)
    return dict(
        sharpe=round(float(sharpe), 2),
        cagr=round(float(cagr) * 100, 2),
        maxdd=round(float(maxdd) * 100, 2),
        calmar=round(float(cagr / abs(maxdd)) if maxdd else 0.0, 2),
        pf=round(float(pf), 2),
        wr=round(len(wins) / len(trades) * 100, 1) if trades else 0,
        trades=len(trades),
        net=round(float(eq.iloc[-1] - eq.iloc[0]), 2),
        exposure=round(in_pos_bars / len(eq) * 100, 1),
    )

"""Limit (maker) vs market (taker) entry economics — the one testable, novel
question in the 'liquidity/limit-zone' brief. OHLCV only; no L2 order-book
data, so NO liquidity walls / fill-reliability claims are fabricated.

For each firm-representative long-entry candidate (1H uptrend bar), compare:
  MARKET  : fill at close[t] (taker 0.05%), always fills.
  LIMIT@o : rest a buy limit o below close[t] (or at a bullish FVG fill level),
            active W bars; fills at the limit (maker 0.02%) if price touches it,
            else MISSED (no trade). Bracket 2xATR SL / 3xATR TP, exit taker.

Reports per method: fill rate, net expectancy PER SIGNAL (missed = 0 — the
honest per-decision metric) and per fill, win rate, and the opportunity cost
of missed winners. Plus a crash stress test (-10%+ within 6h).
"""
import glob
import json
import os

import numpy as np
import pandas as pd

DIR = os.path.dirname(__file__)
TAKER, MAKER = 0.0005, 0.0002
W = 6          # limit active window (hours)
H = 48         # bracket horizon (hours)
STRIDE = 6     # decorrelate candidate entries
K_SL, K_TP = 2.0, 3.0


def atr(df, n=14):
    h, l, c = df["high"].values, df["low"].values, df["close"].values
    pc = np.roll(c, 1); pc[0] = c[0]
    tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
    return pd.Series(tr).ewm(alpha=1 / n, adjust=False).mean().values


def bracket(df, f, entry_level, sl, tp, entry_fee):
    """Exit walk from bar f+1; SL priority; timeout at close. Net return incl.
    entry_fee (side) and taker exit fee."""
    o, h, l, c = df["open"].values, df["high"].values, df["low"].values, df["close"].values
    entry_cost = entry_level * (1 + entry_fee)
    end = min(f + 1 + H, len(df))
    for j in range(f + 1, end):
        if l[j] <= sl:
            return (sl * (1 - TAKER)) / entry_cost - 1, "SL", j - f
        if h[j] >= tp:
            return (tp * (1 - TAKER)) / entry_cost - 1, "TP", j - f
    j = end - 1
    return (c[j] * (1 - TAKER)) / entry_cost - 1, "timeout", j - f


def study(df):
    df = df.reset_index(drop=True)
    c = df["close"].values; h = df["high"].values; l = df["low"].values
    a = atr(df)
    ema200 = pd.Series(c).ewm(span=200, adjust=False).mean().values
    n = len(df)
    # bullish FVG at t: gap where low[t] > high[t-2] (3-candle imbalance);
    # fill level = high[t-2] (below price). Precompute.
    fvg_level = np.full(n, np.nan)
    for t in range(2, n):
        if l[t] > h[t - 2]:
            fvg_level[t] = h[t - 2]

    methods = ["market", "lim_0.1%", "lim_0.25%", "lim_0.5%", "lim_1xATR", "lim_FVG"]
    rec = {m: {"signals": 0, "fills": 0, "rets": [], "wins": 0,
               "missed_would_win": 0, "missed": 0} for m in methods}
    crash = {"market": [], "lim_0.5%": []}

    for t in range(250, n - H - 2):
        if t % STRIDE or c[t] <= ema200[t]:      # firm-representative: longs in uptrend
            continue
        atr_t = a[t]
        if atr_t <= 0:
            continue
        # market baseline (fills at close[t], bracket from t)
        m_ret, _, _ = bracket(df, t, c[t], c[t] - K_SL * atr_t, c[t] + K_TP * atr_t, TAKER)
        rec["market"]["signals"] += 1; rec["market"]["fills"] += 1
        rec["market"]["rets"].append(m_ret); rec["market"]["wins"] += m_ret > 0

        # limit variants
        offs = {"lim_0.1%": c[t] * 0.001, "lim_0.25%": c[t] * 0.0025,
                "lim_0.5%": c[t] * 0.005, "lim_1xATR": atr_t}
        limits = {k: c[t] - v for k, v in offs.items()}
        if not np.isnan(fvg_level[t]) and fvg_level[t] < c[t]:
            limits["lim_FVG"] = fvg_level[t]
        for m, lim in limits.items():
            rec[m]["signals"] += 1
            fill_bar = next((j for j in range(t + 1, min(t + 1 + W, n)) if l[j] <= lim), None)
            if fill_bar is None:
                rec[m]["missed"] += 1
                rec[m]["missed_would_win"] += m_ret > 0    # opportunity cost proxy
                rec[m]["rets"].append(0.0)                 # per-signal: missed earns 0
                continue
            r, _, _ = bracket(df, fill_bar, lim, lim - K_SL * atr_t, lim + K_TP * atr_t, MAKER)
            rec[m]["fills"] += 1; rec[m]["rets"].append(r); rec[m]["wins"] += r > 0

        # crash stress: does the next 6h drop >10%? did the 0.5% limit catch a knife?
        fwd_min = l[t + 1:t + 7].min() if t + 7 <= n else c[t]
        if fwd_min / c[t] - 1 <= -0.10:
            crash["market"].append(m_ret)
            lim05 = c[t] * (1 - 0.005)
            fb = next((j for j in range(t + 1, min(t + 1 + W, n)) if l[j] <= lim05), None)
            if fb is not None:
                r, _, _ = bracket(df, fb, lim05, lim05 - K_SL * atr_t, lim05 + K_TP * atr_t, MAKER)
                crash["lim_0.5%"].append(r)

    out = {}
    for m, d in rec.items():
        rets = np.array(d["rets"])
        filled = [x for x in d["rets"]] if m == "market" else None
        fill_rets = np.array([r for r in d["rets"]]) if m == "market" else \
            np.array([r for r in d["rets"] if r != 0.0])
        out[m] = {
            "signals": d["signals"],
            "fill_rate_pct": round(d["fills"] / d["signals"] * 100, 1) if d["signals"] else 0,
            "exp_per_signal_bps": round(float(rets.mean()) * 1e4, 2) if len(rets) else 0,
            "exp_per_fill_bps": round(float(fill_rets.mean()) * 1e4, 2) if len(fill_rets) else 0,
            "win_rate_pct": round(d["wins"] / max(d["fills"], 1) * 100, 1),
            "missed": d["missed"],
            "missed_that_would_have_won": d["missed_would_win"],
        }
    out["_crash"] = {
        "n_crash_events": len(crash["market"]),
        "market_exp_bps": round(float(np.mean(crash["market"])) * 1e4, 1) if crash["market"] else None,
        "limit0.5_caught": len(crash["lim_0.5%"]),
        "limit0.5_exp_bps": round(float(np.mean(crash["lim_0.5%"])) * 1e4, 1) if crash["lim_0.5%"] else None,
    }
    return out


def main():
    results = {}
    for f in sorted(glob.glob(os.path.join(DIR, "*_1h.parquet"))):
        sym = os.path.basename(f).replace("_1h.parquet", "")
        df = pd.read_parquet(f)
        results[sym] = study(df)
        print(sym, "done", flush=True)
    json.dump(results, open(os.path.join(DIR, "limit_RESULTS.json"), "w"), indent=1, default=str)
    print("saved")


if __name__ == "__main__":
    main()

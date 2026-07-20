"""Control test: naive regime-hold (long while close > EMA200, flat below).
A-priori parameters, zero optimization. Result: REJECTED — the hourly EMA200
flips 500-700 times over the sample; churn plus 50-70% drawdowns kill it.
Kept as the honest control for the WFO study (see README)."""
from pathlib import Path

import numpy as np
import pandas as pd

from engine import add_indicators

OUT = str(Path(__file__).parent / "data")
FEE = 0.0007


def regime_hold(df, adx_gate=0):
    c, o = df["close"].values, df["open"].values
    ema200 = df["ema200"].values
    adx = df["adx"].values
    eq = np.empty(len(df))
    eq[:] = np.nan
    cash, qty, entry = 1.0, 0.0, 0.0
    pending = None
    trades = []
    for i in range(len(df)):
        if pending == "buy" and qty == 0:
            qty = cash / (o[i] * (1 + FEE))
            cash = 0.0
            entry = o[i]
        elif pending == "sell" and qty > 0:
            cash = qty * o[i] * (1 - FEE)
            trades.append(o[i] / entry - 1)
            qty = 0.0
        pending = None
        if i > 24 * 10:
            if qty == 0 and c[i] > ema200[i] and adx[i] >= adx_gate:
                pending = "buy"
            elif qty > 0 and c[i] < ema200[i]:
                pending = "sell"
        eq[i] = cash + qty * c[i]
    return pd.Series(eq, index=df.index).dropna(), trades


if __name__ == "__main__":
    print(f"{'sym':7s} {'seg':4s} {'strat%':>9s} {'B&H%':>9s} {'sharpe':>7s} {'maxdd%':>7s} {'flips':>6s}")
    for s in ["BTCUSD", "ETHUSD", "SOLUSD"]:
        df = pd.read_parquet(f"{OUT}/{s}_1h.parquet").set_index("ts")
        prep = add_indicators(df, donch_n=48)
        n = len(prep)
        split = int(n * 0.70)
        for name, lo, hi in [("IS", 0, split), ("OOS", split, n), ("ALL", 0, n)]:
            seg = prep.iloc[max(0, lo - 2400):hi]
            eq, trades = regime_hold(seg)
            off = lo - max(0, lo - 2400)
            eq = eq.iloc[off:] if off else eq
            ret = eq.pct_change().dropna()
            sharpe = ret.mean() / ret.std() * np.sqrt(24 * 365) if ret.std() > 0 else 0
            total = (eq.iloc[-1] / eq.iloc[0] - 1) * 100
            px = df["close"].iloc[lo:hi]
            bh = (px.iloc[-1] / px.iloc[0] - 1) * 100
            dd = ((eq - eq.cummax()) / eq.cummax()).min() * 100
            print(f"{s:7s} {name:4s} {total:9.1f} {bh:9.1f} {sharpe:7.2f} {dd:7.1f} {len(trades):6d}")

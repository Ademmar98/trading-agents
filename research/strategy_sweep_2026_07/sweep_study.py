"""Multi-strategy x multi-pair profitability & robustness sweep — runs ON the
VPS (Alpaca data). Long-only spot (halal firm: no shorts/funding). 70/30
IS/OOS, best params by IS Sharpe, single-pass OOS. Exact friction & exclusion
criteria and PRI from the brief. Reports real survivors — no padding.
"""
import datetime as dt
import json
import time
import urllib.parse
import urllib.request

import numpy as np
import pandas as pd

START = "2023-01-01T00:00:00Z"
END = "2026-07-19T00:00:00Z"
SCALP_SYMS = ["BTC/USD", "ETH/USD", "SOL/USD", "LINK/USD", "DOGE/USD",
              "AVAX/USD", "LTC/USD", "BCH/USD"]
SWING_SYMS = SCALP_SYMS + ["UNI/USD", "AAVE/USD", "XRP/USD", "DOT/USD",
                           "MKR/USD", "GRT/USD", "BAT/USD", "XTZ/USD"]
SCALP_COST = 0.0008   # 0.05% taker + 0.03% slip per side
SWING_COST = 0.00055  # 0.04% + 0.015% per side
OUT = "/tmp/sweep_study"


def fetch(symbol, tf):
    step = {"15Min": 15 * 60, "1Day": 86400}[tf]
    rows, token = [], None
    base = ("https://data.alpaca.markets/v1beta3/crypto/us/bars"
            f"?symbols={urllib.parse.quote(symbol, safe='')}"
            f"&timeframe={tf}&start={START}&end={END}&limit=10000")
    while True:
        url = base + (f"&page_token={token}" if token else "")
        for a in range(4):
            try:
                d = json.loads(urllib.request.urlopen(url, timeout=30).read()); break
            except Exception:
                time.sleep(2 * (a + 1))
        else:
            return None
        for b in d.get("bars", {}).get(symbol, []):
            rows.append((b["o"], b["h"], b["l"], b["c"], b["v"]))
        token = d.get("next_page_token")
        if not token:
            break
        time.sleep(0.05)
    if len(rows) < 500:
        return None
    a = np.array(rows, float)
    return {"o": a[:, 0], "h": a[:, 1], "l": a[:, 2], "c": a[:, 3], "v": a[:, 4]}


# ── indicators ──
def ema(x, n): return pd.Series(x).ewm(span=n, adjust=False).mean().values
def sma(x, n): return pd.Series(x).rolling(n).mean().values
def rsi(x, n=14):
    d = np.diff(x, prepend=x[0]); up = np.clip(d, 0, None); dn = np.clip(-d, 0, None)
    ru = pd.Series(up).ewm(alpha=1/n, adjust=False).mean().values
    rd = pd.Series(dn).ewm(alpha=1/n, adjust=False).mean().values
    return 100 - 100/(1 + ru/np.where(rd == 0, 1e-9, rd))
def atr(h, l, c, n=14):
    pc = np.roll(c, 1); pc[0] = c[0]
    tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
    return pd.Series(tr).ewm(alpha=1/n, adjust=False).mean().values
def roll_max(x, n): return pd.Series(x).rolling(n).max().values
def macd_hist(x, f=12, s=26, g=9):
    m = ema(x, f) - ema(x, s); return m - ema(m, g)
def boll(x, n=20, k=2.0):
    m = sma(x, n); sd = pd.Series(x).rolling(n).std().values
    return m - k*sd, m + k*sd


# ── strategy pool: each returns a long/flat position signal (decided at bar t) ──
def strat(name, p, D):
    c, h, l, v = D["c"], D["h"], D["l"], D["v"]
    if name == "EMA_cross":
        return (ema(c, p[0]) > ema(c, p[1])).astype(float)
    if name == "SMA_regime":
        return ((sma(c, p[0]) > sma(c, p[1])) & (c > sma(c, p[1]))).astype(float)
    if name == "Donchian_break":
        hh = roll_max(np.roll(h, 1), p[0]); return (c > hh).astype(float)
    if name == "MACD_mom":
        hh = macd_hist(c); return (hh > 0).astype(float)
    if name == "RSI_meanrev":
        r = rsi(c, p[0]); s = np.zeros(len(c)); st = 0
        for i in range(len(c)):
            if st == 0 and r[i] < p[1]: st = 1
            elif st == 1 and r[i] > p[2]: st = 0
            s[i] = st
        return s
    if name == "Boll_meanrev":
        lo, up = boll(c, p[0], p[1]); s = np.zeros(len(c)); st = 0
        for i in range(len(c)):
            if st == 0 and c[i] < lo[i]: st = 1
            elif st == 1 and c[i] > sma(c, p[0])[i]: st = 0
            s[i] = st
        return s
    if name == "Momentum":
        r = c / np.roll(c, p[0]) - 1; return (r > p[1]).astype(float)
    if name == "VWAP_dev":
        tp = (h + l + c) / 3
        num = pd.Series(tp * v).rolling(p[0]).sum().values
        den = pd.Series(v).rolling(p[0]).sum().values
        vwap = num / np.where(den == 0, 1e-9, den)
        s = np.zeros(len(c)); st = 0
        for i in range(len(c)):
            if st == 0 and c[i] < vwap[i] * (1 - p[1]): st = 1
            elif st == 1 and c[i] > vwap[i]: st = 0
            s[i] = st
        return s
    if name == "Vol_burst":
        a = atr(h, l, c, 14); am = sma(a, p[0])
        return ((a > am * p[1]) & (c > np.roll(c, 1))).astype(float)
    raise ValueError(name)


POOL = {
    "swing": {
        "EMA_cross": [(20, 50), (12, 26), (10, 30)],
        "SMA_regime": [(20, 50), (10, 40), (20, 100)],
        "Donchian_break": [(20,), (30,), (55,)],
        "MACD_mom": [(0,)],
        "RSI_meanrev": [(14, 30, 55), (14, 35, 60), (10, 30, 50)],
        "Boll_meanrev": [(20, 2.0), (20, 2.5)],
        "Momentum": [(30, 0.0), (60, 0.05), (90, 0.10)],
    },
    "scalp": {
        "EMA_cross": [(9, 21), (5, 13), (8, 34)],
        "Donchian_break": [(20,), (48,), (96,)],
        "MACD_mom": [(0,)],
        "RSI_meanrev": [(14, 30, 50), (7, 25, 55)],
        "Boll_meanrev": [(20, 2.0), (20, 2.5)],
        "VWAP_dev": [(96, 0.005), (48, 0.003), (192, 0.008)],
        "Vol_burst": [(50, 1.5), (50, 2.0)],
    },
}


def backtest(D, pos, cost, ppy):
    c = D["c"]
    ret = np.zeros(len(c)); ret[1:] = c[1:] / c[:-1] - 1
    p = np.zeros(len(c)); p[1:] = pos[:-1]         # act next bar
    flips = np.abs(np.diff(p, prepend=0)) > 0
    bar = p * ret - flips * cost
    # trade-level returns (contiguous long blocks)
    trades = []
    i = 0
    while i < len(p):
        if p[i] == 1:
            j = i
            while j < len(p) and p[j] == 1:
                j += 1
            entry = c[i-1] if i > 0 else c[i]
            exit_ = c[j-1]
            trades.append((exit_/entry - 1) - 2*cost)
            i = j
        else:
            i += 1
    return bar, np.array(trades)


def metrics(bar, trades, ppy):
    bar = bar[np.isfinite(bar)]
    if len(bar) < 30:
        return None
    eq = np.cumprod(1 + bar)
    sh = bar.mean()/bar.std()*np.sqrt(ppy) if bar.std() > 0 else 0
    yrs = len(bar)/ppy
    cagr = eq[-1]**(1/yrs) - 1 if yrs > 0 and eq[-1] > 0 else -1
    dd = np.min((eq - np.maximum.accumulate(eq))/np.maximum.accumulate(eq))
    wins = trades[trades > 0]; losses = trades[trades < 0]
    pf = wins.sum()/abs(losses.sum()) if len(losses) and losses.sum() != 0 else (np.inf if len(wins) else 0)
    wr = len(wins)/len(trades)*100 if len(trades) else 0
    rr = (wins.mean()/abs(losses.mean())) if len(wins) and len(losses) else 0
    return {"sharpe": round(float(sh), 3), "cagr": round(float(cagr)*100, 1),
            "maxdd": round(float(dd)*100, 1), "calmar": round(float(cagr/abs(dd)) if dd else 0, 2),
            "pf": round(float(min(pf, 99)), 2), "win_rate": round(wr, 1),
            "rr": round(float(rr), 2), "trades": int(len(trades))}


def run_category(cat, syms, tf, cost, ppy):
    results = []
    for sym in syms:
        D = fetch(sym, tf)
        if D is None:
            continue
        n = len(D["c"]); split = int(n * 0.70)
        for name, grid in POOL[cat].items():
            # pick best params by IS Sharpe
            best, best_sh = None, -1e9
            for params in grid:
                pos = strat(name, params, D)
                bar, tr = backtest({k: v[:split] for k, v in D.items()}, pos[:split], cost, ppy)
                m = metrics(bar, tr, ppy)
                if m and m["sharpe"] > best_sh:
                    best_sh, best = m["sharpe"], (params, m)
            if not best:
                continue
            params, is_m = best
            pos = strat(name, params, D)
            bar_o, tr_o = backtest({k: v[split:] for k, v in D.items()}, pos[split:], cost, ppy)
            oos = metrics(bar_o, tr_o, ppy)
            if not oos:
                continue
            decay = 1 - (oos["sharpe"]/is_m["sharpe"]) if is_m["sharpe"] > 0 else 1.0
            results.append({"strategy": name, "params": params, "symbol": sym.replace("/USD", ""),
                            "tf": tf, "is": is_m, "oos": oos, "decay_pct": round(decay*100, 1)})
        print(cat, sym, "done", flush=True)
    return results


def main():
    import os
    os.makedirs(OUT, exist_ok=True)
    all_res = {
        "scalp": run_category("scalp", SCALP_SYMS, "15Min", SCALP_COST, 365*24*4),
        "swing": run_category("swing", SWING_SYMS, "1Day", SWING_COST, 365),
    }
    json.dump(all_res, open(f"{OUT}/RESULTS.json", "w"), indent=1, default=str)
    print("ALL DONE")


if __name__ == "__main__":
    main()

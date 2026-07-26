#!/usr/bin/env python3
"""
EDGE HUNT v4 — the honest rerun of "Deep Hunting v3".

WHY v3 COULD NOT SIMPLY BE RERUN
--------------------------------
v3's three strategies do not read market data for their signals:

  1. Funding Rate Capture (v3's reported "winner", +1.36%) — the funding rate
     is np.random.uniform() drawn from five hand-picked buckets, four of which
     have a POSITIVE mean. The result is positive before any price is read.
     It also requires long spot + short perp, i.e. leverage and shorts.
  2. On-Chain Alpha — the "on-chain flow signal" is
     -(past 72h return) * 3 + np.random.normal(0, 0.2). No chain data exists
     anywhere in the file. It produced 0 trades.
  3. Cross-Exchange Arbitrage — "exchange B" is exchange A multiplied by a
     always-positive synthetic spread, so B is always dearer than A: a riskless
     one-way arb that cannot exist. It produced 0 arb trades.

v3 also falls back to synthetic prices (seed 42) when the API fails.
Rerunning it on any period just redraws the RNG.

WHAT THIS DOES INSTEAD
----------------------
Real Binance spot klines, last 30 days, LONG-ONLY SPOT (no leverage, no
shorts, no funding income — a spot account cannot collect funding).
Four timeframes, several classic price signals, and buy-and-hold as the
benchmark that actually matters.

Per the study-12 lesson, the FIRST number reported is gross expectancy per
trade against friction. If a signal cannot clear the round trip before costs,
nothing downstream can save it.
"""
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import requests

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

SPOT = "https://api.binance.com"
OUT = Path(__file__).parent / "edge_hunt_v4_results.json"
DAYS = 30
TIMEFRAMES = ["5m", "15m", "1h", "4h"]
BARS_PER_DAY = {"5m": 288, "15m": 96, "1h": 24, "4h": 6}
FEES = {"taker_030": 0.0030, "audit_014": 0.0014}   # round trip
STABLES = {"USDC", "USDT", "BUSD", "TUSD", "FDUSD", "DAI", "USDP", "XAUT", "RLUSD"}
LEV = ("UP", "DOWN", "BULL", "BEAR", "3L", "3S", "5L", "5S")


def universe(n=20, min_vol=5_000_000):
    r = requests.get(f"{SPOT}/api/v3/ticker/24hr", timeout=30).json()
    out = []
    for t in r:
        s = t["symbol"]
        if not s.endswith("USDT"):
            continue
        b = s[:-4]
        if b in STABLES or any(x in b for x in LEV):
            continue
        v = float(t.get("quoteVolume", 0))
        if v >= min_vol:
            out.append((s, v))
    out.sort(key=lambda x: -x[1])
    return [s for s, _ in out[:n]]


def klines(symbol, interval, days=DAYS):
    need = BARS_PER_DAY[interval] * days
    rows, end = [], None
    s = requests.Session()
    while len(rows) < need:
        p = {"symbol": symbol, "interval": interval, "limit": 1000}
        if end:
            p["endTime"] = end
        try:
            b = s.get(f"{SPOT}/api/v3/klines", params=p, timeout=20).json()
        except Exception:
            break
        if not isinstance(b, list) or not b:
            break
        rows = b + rows
        end = b[0][0] - 1
        if len(b) < 1000:
            break
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "vol",
                                     "ct", "qv", "n", "tbb", "tbq", "ig"])
    df = df[["ts", "open", "high", "low", "close", "vol"]].astype(float)
    df["ts"] = pd.to_datetime(df["ts"].astype("int64"), unit="ms")
    return df.drop_duplicates("ts").sort_values("ts").reset_index(drop=True).tail(need)


# ------------------------------------------------------------- signals ----
def sig_ma_cross(df, fast=20, slow=50):
    f, s = df.close.rolling(fast).mean(), df.close.rolling(slow).mean()
    return (f > s) & (f.shift() <= s.shift()), (f < s)


def sig_rsi_revert(df, period=14, lo=30, hi=60):
    d = df.close.diff()
    up = d.clip(lower=0).rolling(period).mean()
    dn = (-d.clip(upper=0)).rolling(period).mean()
    rsi = 100 - 100 / (1 + up / dn.replace(0, np.nan))
    return (rsi < lo) & (rsi.shift() >= lo), (rsi > hi)


def sig_donchian(df, n=20):
    hh = df.high.rolling(n).max().shift()
    ll = df.low.rolling(n).min().shift()
    return df.close > hh, df.close < ll


def sig_trend_pullback(df, ma=50, dip=0.02):
    m = df.close.rolling(ma).mean()
    return (df.close > m) & (df.close < df.close.rolling(10).max() * (1 - dip)), (df.close < m)


SIGNALS = {"ma_cross": sig_ma_cross, "rsi_revert": sig_rsi_revert,
           "donchian": sig_donchian, "trend_pullback": sig_trend_pullback}


def run(df, entries, exits, fee_rt):
    """Long-only spot. Enter next bar's open after a signal; no lookahead."""
    o = df.open.to_numpy()
    e_in = entries.to_numpy()
    e_out = exits.to_numpy()
    pos, entry, trades = False, 0.0, []
    for i in range(1, len(df) - 1):
        if not pos and e_in[i]:
            pos, entry = True, o[i + 1]
        elif pos and e_out[i]:
            px = o[i + 1]
            trades.append((px - entry) / entry)
            pos = False
    if pos:
        trades.append((df.close.iloc[-1] - entry) / entry)
    if not trades:
        return None
    g = np.array(trades)
    n = g - fee_rt
    return {"trades": len(g), "gross_per_trade": float(g.mean()),
            "net_per_trade": float(n.mean()), "gross_sum": float(g.sum()),
            "net_sum": float(n.sum()),
            "win_rate": float((n > 0).mean() * 100)}


def main():
    syms = universe()
    print(f"universe: {len(syms)} pairs | {DAYS}d | long-only spot, no leverage")
    print(f"timeframes: {', '.join(TIMEFRAMES)}\n")

    state = {"days": DAYS, "symbols": syms, "results": {}}
    bh = {}

    for tf in TIMEFRAMES:
        data = {}
        for s in syms:
            d = klines(s, tf)
            if len(d) > 100:
                data[s] = d
            time.sleep(0.05)
        if not data:
            continue
        bh[tf] = float(np.mean([(d.close.iloc[-1] - d.open.iloc[0]) / d.open.iloc[0]
                                for d in data.values()]) * 100)

        for name, fn in SIGNALS.items():
            agg = {k: [] for k in FEES}
            gross_pt, tot = [], 0
            for s, d in data.items():
                ent, ex = fn(d)
                for fk, fv in FEES.items():
                    r = run(d, ent.fillna(False), ex.fillna(False), fv)
                    if r:
                        agg[fk].append(r)
                        if fk == "audit_014":
                            gross_pt.append(r["gross_per_trade"])
                            tot += r["trades"]
            if not gross_pt:
                continue
            key = f"{tf}|{name}"
            state["results"][key] = {
                "trades": tot,
                "gross_per_trade_pct": float(np.mean(gross_pt) * 100),
                "pairs": len(gross_pt),
            }
            for fk in FEES:
                if agg[fk]:
                    net = np.array([r["net_sum"] for r in agg[fk]]) * 100
                    state["results"][key][fk] = {
                        "net_sum_pct": float(net.sum()),
                        "mean_pair_pct": float(net.mean()),
                        "pairs_positive": int((net > 0).sum()),
                        "win_rate": float(np.mean([r["win_rate"] for r in agg[fk]])),
                    }
        print(f"  {tf} done  (buy&hold mean {bh[tf]:+.2f}%)")

    state["buy_hold"] = bh
    OUT.write_text(json.dumps(state, indent=2), encoding="utf-8")
    report(state)


def report(st):
    print("\n" + "=" * 92)
    print(f"EDGE HUNT v4 — REAL DATA, {st['days']}d, LONG-ONLY SPOT, {len(st['symbols'])} pairs")
    print("=" * 92)
    print("\nBuy & hold benchmark (mean across pairs):")
    for tf, v in st["buy_hold"].items():
        print(f"   {tf:>4s}  {v:+7.2f}%")

    print(f"\n{'timeframe|signal':<26} {'trades':>7} {'gross/trade':>12} "
          f"{'vs 0.14% fee':>13} {'net@0.14%':>11} {'net@0.30%':>11} {'pairs+':>7}")
    print("-" * 92)
    rows = sorted(st["results"].items(),
                  key=lambda kv: -kv[1].get("audit_014", {}).get("net_sum_pct", -1e9))
    for k, r in rows:
        a = r.get("audit_014", {})
        t = r.get("taker_030", {})
        gpt = r["gross_per_trade_pct"]
        verdict = "CLEARS" if gpt > 0.14 else "below"
        print(f"{k:<26} {r['trades']:>7} {gpt:>11.4f}% {verdict:>13} "
              f"{a.get('net_sum_pct', 0):>10.1f}% {t.get('net_sum_pct', 0):>10.1f}% "
              f"{a.get('pairs_positive', 0):>3}/{r['pairs']:<3}")
    print("=" * 92)
    best = rows[0] if rows else None
    if best:
        k, r = best
        a = r.get("audit_014", {})
        print(f"\nBest by net: {k}  net {a.get('net_sum_pct', 0):+.1f}% across {r['pairs']} pairs")
        tf = k.split("|")[0]
        print(f"Buy & hold over the same window ({tf}): {st['buy_hold'].get(tf, 0):+.2f}% per pair "
              f"(= {st['buy_hold'].get(tf, 0) * r['pairs']:+.1f}% summed)")


if __name__ == "__main__":
    main()

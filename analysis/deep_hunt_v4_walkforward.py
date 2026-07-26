#!/usr/bin/env python3
"""
Deep Hunt v4 — walk-forward across earlier 30-day windows.

The single-window result (+3.26%, 1.19% max DD, 17 trades) was measured in one
mildly-positive month with the config selected in-sample. This reruns THAT SAME
config, unchanged, across consecutive non-overlapping 30-day windows going back
through the available history — including whatever down months exist.

Nothing is re-fitted. The config is frozen at the in-sample winner:
    v4_agg_20_sl10_tp4_trail

For each window it reports the strategy against the only benchmarks that matter
for a prop challenge: the return, the max drawdown against the $300 (6%) wall,
and what an equal-weight buy-and-hold of the same coins would have done.
"""
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from analysis.deep_hunt_v4_propr import (  # noqa: E402
    backtest_propr, SYMBOLS, INITIAL_CAPITAL, MAX_DD_LIMIT,
)

HL = "https://api.hyperliquid.xyz/info"
OUT = Path(__file__).parent / "deep_hunt_v4_walkforward.json"
WINDOW_H = 720          # 30 days of 1h bars
N_WINDOWS = 6
HISTORY_DAYS = 215

BEST = {"name": "v4_agg_20_sl10_tp4_trail", "sl_pct": 0.10, "tp_pct": 0.04,
        "pos_size_pct": 0.20, "max_concurrent": 3,
        "trail_activation": 0.025, "trail_distance": 0.02, "max_hold": 36}


def fetch_history(symbol, days=HISTORY_DAYS):
    now = int(datetime.now(timezone.utc).timestamp() * 1000)
    start = now - days * 24 * 3600 * 1000
    try:
        r = requests.post(HL, json={"type": "candleSnapshot", "req": {
            "coin": symbol, "interval": "1h",
            "startTime": start, "endTime": now}}, timeout=25)
        d = r.json()
    except Exception as e:
        print(f"  {symbol}: {e}")
        return pd.DataFrame()
    if not d:
        return pd.DataFrame()
    df = pd.DataFrame([{
        "open": float(c["o"]), "high": float(c["h"]), "low": float(c["l"]),
        "close": float(c["c"]), "volume": float(c["v"]),
        "timestamp": pd.to_datetime(int(c["t"]), unit="ms", utc=True)} for c in d])
    return df.set_index("timestamp").sort_index()


def buy_hold(window_data):
    """Equal-weight, no rebalancing: return and max drawdown of just holding."""
    curves = []
    for df in window_data.values():
        c = df["close"].to_numpy()
        if len(c) > 100:
            curves.append(c / c[0])
    if not curves:
        return None
    n = min(len(c) for c in curves)
    basket = np.vstack([c[:n] for c in curves]).mean(axis=0)
    eq = INITIAL_CAPITAL * basket
    dd = (np.maximum.accumulate(eq) - eq).max()
    return {"return_pct": float((eq[-1] / INITIAL_CAPITAL - 1) * 100),
            "max_dd_usd": float(dd),
            "max_dd_pct": float(dd / INITIAL_CAPITAL * 100),
            "failed": bool(dd > MAX_DD_LIMIT)}


def main():
    print("=" * 96)
    print("DEEP HUNT v4 — WALK-FORWARD OVER EARLIER 30-DAY WINDOWS")
    print("=" * 96)
    print(f"config FROZEN at the in-sample winner: {BEST['name']}")
    print(f"nothing re-fitted; {N_WINDOWS} non-overlapping windows of {WINDOW_H}h\n")

    hist = {}
    for i, s in enumerate(SYMBOLS, 1):
        df = fetch_history(s)
        if not df.empty:
            hist[s] = df
        print(f"  [{i}/{len(SYMBOLS)}] {s}: {len(df)} bars")
        time.sleep(0.25)

    if not hist:
        print("no data")
        return

    end = min(df.index.max() for df in hist.values())
    rows = []
    for w in range(N_WINDOWS):
        w_end = end - pd.Timedelta(hours=WINDOW_H * w)
        w_start = w_end - pd.Timedelta(hours=WINDOW_H)
        wd = {s: df.loc[(df.index > w_start) & (df.index <= w_end)]
              for s, df in hist.items()}
        wd = {s: d for s, d in wd.items() if len(d) >= 220}
        if len(wd) < 4:
            print(f"  window {w}: only {len(wd)} symbols with data, skipping")
            continue

        bh = buy_hold(wd)
        r = backtest_propr(wd, BEST)
        rows.append({
            "window": w,
            "start": str(w_start.date()), "end": str(w_end.date()),
            "symbols": len(wd),
            "strat": {k: r.get(k) for k in
                      ("trades", "return_pct", "win_rate", "profit_factor",
                       "max_dd_pct", "max_dd_usd", "challenge_failed",
                       "daily_limits_hit")} if r else None,
            "buy_hold": bh,
        })
        print(f"  window {w} ({w_start.date()} -> {w_end.date()}) done")

    state = {"config": BEST, "windows": rows}
    OUT.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")
    report(rows)


def report(rows):
    print("\n" + "=" * 96)
    print(f"{'window':<24} {'B&H ret':>9} {'B&H DD':>8} {'B&H':>7} | "
          f"{'trades':>6} {'strat ret':>10} {'strat DD':>9} {'WR':>6} {'verdict':>9}")
    print("-" * 96)
    for r in sorted(rows, key=lambda x: x["window"]):
        b, s = r["buy_hold"], r["strat"]
        if not b:
            continue
        bhv = "FAIL" if b["failed"] else "ok"
        if s:
            v = "FAILED" if s["challenge_failed"] else "survives"
            print(f"{r['start']}->{r['end']:<12} {b['return_pct']:>8.2f}% "
                  f"{b['max_dd_pct']:>7.2f}% {bhv:>7} | {s['trades']:>6} "
                  f"{s['return_pct']:>9.2f}% {s['max_dd_pct']:>8.2f}% "
                  f"{s['win_rate']:>5.1f}% {v:>9}")
        else:
            print(f"{r['start']}->{r['end']:<12} {b['return_pct']:>8.2f}% "
                  f"{b['max_dd_pct']:>7.2f}% {bhv:>7} |  no trades")
    print("=" * 96)

    ok = [r for r in rows if r["strat"]]
    if not ok:
        return
    rets = [r["strat"]["return_pct"] for r in ok]
    dds = [r["strat"]["max_dd_pct"] for r in ok]
    tr = [r["strat"]["trades"] for r in ok]
    fails = sum(1 for r in ok if r["strat"]["challenge_failed"])
    bh_fails = sum(1 for r in ok if r["buy_hold"]["failed"])
    print(f"\n  windows tested        {len(ok)}")
    print(f"  total trades          {sum(tr)}")
    print(f"  mean return           {np.mean(rets):+.2f}%   "
          f"(median {np.median(rets):+.2f}%)")
    print(f"  positive windows      {sum(1 for x in rets if x > 0)}/{len(rets)}")
    print(f"  worst window          {min(rets):+.2f}%")
    print(f"  max DD: mean {np.mean(dds):.2f}%  worst {max(dds):.2f}%  "
          f"(wall 6.00%)")
    print(f"  CHALLENGE FAILURES    {fails}/{len(ok)}   "
          f"(buy & hold would fail {bh_fails}/{len(ok)})")
    beat = sum(1 for r in ok
               if r["strat"]["return_pct"] > r["buy_hold"]["return_pct"])
    print(f"  beat buy & hold on return: {beat}/{len(ok)}")


if __name__ == "__main__":
    main()

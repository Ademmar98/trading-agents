#!/usr/bin/env python3
"""
Deep Hunt v5 — improvement sweep judged WALK-FORWARD, not in-sample.

The v4 headline (+3.30%/mo) was the best of 6 windows and the window the config
was picked on; out-of-sample it ran at +0.50%/mo. So this sweep does NOT select
on a single window. Every variant is run over all 6 non-overlapping 30-day
windows and ranked on out-of-sample behaviour:

    - OOS mean return (windows 1-5, never used for selection)
    - how many of those windows are positive  (consistency beats peak)
    - challenge failures (a variant that ever breaches 6% is disqualified)

Levers tested, each with a mechanical reason rather than a curve-fit:
  universe : v4 traded 12 coins and took only 84 trades in 6 months. More
             symbols means more qualifying dips -- more opportunity at the SAME
             per-trade edge. This is the one lever that adds sample instead of
             consuming it.
  take_profit: v4 exits at +4% with a 10% stop. In the crash windows the win
             rate was 71-80%, which hints the bounces run further than 4%.
  position : worst OOS drawdown was 4.06% against a 6% wall, so there is
             headroom -- but sizing scales losses too, so it is tested, not
             assumed.
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

from analysis.deep_hunt_v4_propr import backtest_propr, INITIAL_CAPITAL, MAX_DD_LIMIT  # noqa

HL = "https://api.hyperliquid.xyz/info"
OUT = Path(__file__).parent / "deep_hunt_v5_results.json"
CACHE = Path(__file__).parent / "hl_1h_cache"
CACHE.mkdir(exist_ok=True)
WINDOW_H, N_WINDOWS, HISTORY_DAYS = 720, 6, 215

CORE12 = ["BTC", "ETH", "SOL", "DOGE", "XRP", "AVAX", "LINK",
          "SUI", "NEAR", "AAVE", "INJ", "FET"]
WIDE = CORE12 + ["ATOM", "LTC", "ARB", "OP", "CRV", "LDO", "APT", "UNI",
                 "BCH", "COMP", "MKR", "WLD", "SNX", "GMX", "STX", "DYDX",
                 "TRX", "APE"]

VARIANTS = []
for uni_name, uni in (("core12", CORE12), ("wide30", WIDE)):
    for tp in (0.04, 0.06, 0.08):
        for size in (0.20, 0.25):
            VARIANTS.append({
                "name": f"{uni_name}_tp{int(tp*100)}_sz{int(size*100)}",
                "universe": uni_name, "symbols": uni,
                "cfg": {"name": f"{uni_name}_tp{int(tp*100)}_sz{int(size*100)}",
                        "sl_pct": 0.10, "tp_pct": tp, "pos_size_pct": size,
                        "max_concurrent": 3, "trail_activation": 0.025,
                        "trail_distance": 0.02, "max_hold": 36},
            })


def fetch(symbol):
    p = CACHE / f"{symbol}_1h.parquet"
    if p.exists():
        return pd.read_parquet(p)
    now = int(datetime.now(timezone.utc).timestamp() * 1000)
    start = now - HISTORY_DAYS * 24 * 3600 * 1000
    try:
        d = requests.post(HL, json={"type": "candleSnapshot", "req": {
            "coin": symbol, "interval": "1h",
            "startTime": start, "endTime": now}}, timeout=25).json()
    except Exception:
        return pd.DataFrame()
    if not d:
        return pd.DataFrame()
    df = pd.DataFrame([{
        "open": float(c["o"]), "high": float(c["h"]), "low": float(c["l"]),
        "close": float(c["c"]), "volume": float(c["v"]),
        "timestamp": pd.to_datetime(int(c["t"]), unit="ms", utc=True)} for c in d])
    df = df.set_index("timestamp").sort_index()
    df.to_parquet(p)
    return df


def main():
    syms = sorted(set(WIDE))
    print(f"fetching {len(syms)} symbols...")
    hist = {}
    for s in syms:
        df = fetch(s)
        if len(df) > 3000:
            hist[s] = df
        time.sleep(0.12)
    print(f"usable: {len(hist)}\n")

    end = min(df.index.max() for df in hist.values())
    windows = []
    for w in range(N_WINDOWS):
        we = end - pd.Timedelta(hours=WINDOW_H * w)
        ws = we - pd.Timedelta(hours=WINDOW_H)
        windows.append((w, ws, we))

    rows = []
    for v in VARIANTS:
        per_win = []
        for w, ws, we in windows:
            wd = {s: hist[s].loc[(hist[s].index > ws) & (hist[s].index <= we)]
                  for s in v["symbols"] if s in hist}
            wd = {s: d for s, d in wd.items() if len(d) >= 220}
            if len(wd) < 4:
                continue
            r = backtest_propr(wd, v["cfg"])
            if r:
                per_win.append({"w": w, "ret": r["return_pct"],
                                "dd": r["max_dd_pct"], "trades": r["trades"],
                                "wr": r["win_rate"],
                                "failed": r["challenge_failed"]})
        if not per_win:
            continue
        oos = [p for p in per_win if p["w"] != 0]
        rows.append({
            "name": v["name"], "universe": v["universe"],
            "tp": v["cfg"]["tp_pct"], "size": v["cfg"]["pos_size_pct"],
            "all_mean": float(np.mean([p["ret"] for p in per_win])),
            "oos_mean": float(np.mean([p["ret"] for p in oos])) if oos else 0.0,
            "oos_pos": int(sum(1 for p in oos if p["ret"] > 0)),
            "oos_n": len(oos),
            "oos_worst": float(min(p["ret"] for p in oos)) if oos else 0.0,
            "worst_dd": float(max(p["dd"] for p in per_win)),
            "failures": int(sum(1 for p in per_win if p["failed"])),
            "trades": int(sum(p["trades"] for p in per_win)),
            "windows": per_win,
        })
        r0 = rows[-1]
        print(f"  {v['name']:22s} OOS {r0['oos_mean']:+6.2f}%/mo "
              f"pos {r0['oos_pos']}/{r0['oos_n']}  worstDD {r0['worst_dd']:5.2f}%  "
              f"trades {r0['trades']:4d}  fails {r0['failures']}")

    OUT.write_text(json.dumps({"variants": rows}, indent=2), encoding="utf-8")
    report(rows)


def report(rows):
    print("\n" + "=" * 100)
    print("RANKED BY OUT-OF-SAMPLE MEAN (windows 1-5; window 0 excluded — v4 was chosen there)")
    print("=" * 100)
    print(f"{'variant':<24} {'OOS/mo':>8} {'pos':>6} {'worst':>8} {'allmo':>8} "
          f"{'worstDD':>8} {'trades':>7} {'fails':>6}")
    print("-" * 100)
    ok = [r for r in rows if r["failures"] == 0]
    for r in sorted(rows, key=lambda x: -x["oos_mean"]):
        flag = "" if r["failures"] == 0 else "  DISQUALIFIED (breached 6%)"
        print(f"{r['name']:<24} {r['oos_mean']:>7.2f}% {r['oos_pos']:>3}/{r['oos_n']:<2} "
              f"{r['oos_worst']:>7.2f}% {r['all_mean']:>7.2f}% {r['worst_dd']:>7.2f}% "
              f"{r['trades']:>7} {r['failures']:>6}{flag}")
    print("=" * 100)

    base = next((r for r in rows if r["name"] == "core12_tp4_sz20"), None)
    if base:
        print(f"\nv4 baseline (core12_tp4_sz20): OOS {base['oos_mean']:+.2f}%/mo, "
              f"{base['trades']} trades, worst DD {base['worst_dd']:.2f}%")
    if ok:
        best = max(ok, key=lambda x: x["oos_mean"])
        print(f"best qualifying:              {best['name']}  OOS {best['oos_mean']:+.2f}%/mo, "
              f"{best['trades']} trades, worst DD {best['worst_dd']:.2f}%")
        if base and base["oos_mean"]:
            print(f"improvement over v4:          "
                  f"{best['oos_mean'] - base['oos_mean']:+.2f} pts/mo "
                  f"({best['oos_mean']/base['oos_mean']:.1f}x)")
        print(f"\nCAUTION: {len(rows)} variants were compared. Some of the gap to v4 is "
              f"selection.\nJudge on consistency (pos windows, worst window) as much as the mean.")


if __name__ == "__main__":
    main()

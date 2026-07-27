#!/usr/bin/env python3
"""
Deep Hunt v4 — 30-symbol universe over the last 6 months, month by month.

The deployed runner is frozen at 12 majors because widening it previously broke
the challenge. This re-runs that test at 30 symbols with the config UNCHANGED
(v4_agg_20_sl10_tp4_trail) and reports what each month actually produced, plus a
per-symbol x per-month profit matrix and the 12-major baseline beside it.

Nothing is refitted. The only variable is the universe.

HOW THE 30 ARE PICKED (before any return is looked at):
  1. candidate pool = the Hyperliquid perp list in deep_hunt_v4_propr.SYMBOLS
  2. keep only symbols with a FULL 720 bars in EVERY one of the 6 windows, so
     the universe is constant across months and a month is not flattered by a
     coin that listed halfway through it
  3. rank the survivors by dollar volume in the OLDEST window only — a
     point-in-time proxy, so the ranking cannot see the months it is tested on
  4. the 12 majors are pinned in, then the list is filled to 30 by that rank

SURVIVORSHIP CAVEAT, stated once and not worked around: the candidate pool is
today's listing. Coins delisted during the 6 months are absent, so the universe
is biased toward things that survived. That flatters a long-only dip buyer.
"""
import json
import pickle
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from analysis.deep_hunt_v4_propr import (  # noqa: E402
    backtest_propr, INITIAL_CAPITAL, MAX_DD_LIMIT, SYMBOLS as POOL,
)

HL = "https://api.hyperliquid.xyz/info"
CACHE = HERE / "hl_wide_6mo_1h.pkl"
OUT = HERE / "deep_hunt_v4_wide30_6mo_results.json"
WINDOW_H = 720
N_WINDOWS = 6
HISTORY_DAYS = 215
UNIVERSE_SIZE = 30

MAJORS = ["BTC", "ETH", "SOL", "DOGE", "XRP", "AVAX",
          "LINK", "SUI", "NEAR", "AAVE", "INJ", "FET"]

# Frozen at the deployed config. Do not tune anything here.
BEST = {"name": "v4_agg_20_sl10_tp4_trail", "sl_pct": 0.10, "tp_pct": 0.04,
        "pos_size_pct": 0.20, "max_concurrent": 3,
        "trail_activation": 0.025, "trail_distance": 0.02, "max_hold": 36}


def fetch(symbol, days=HISTORY_DAYS):
    now = int(datetime.now(timezone.utc).timestamp() * 1000)
    start = now - days * 24 * 3600 * 1000
    try:
        r = requests.post(HL, json={"type": "candleSnapshot", "req": {
            "coin": symbol, "interval": "1h",
            "startTime": start, "endTime": now}}, timeout=25)
        d = r.json()
    except Exception:
        return pd.DataFrame()
    if not d or not isinstance(d, list):
        return pd.DataFrame()
    df = pd.DataFrame([{
        "open": float(c["o"]), "high": float(c["h"]), "low": float(c["l"]),
        "close": float(c["c"]), "volume": float(c["v"]),
        "timestamp": pd.to_datetime(int(c["t"]), unit="ms", utc=True)} for c in d])
    return df.set_index("timestamp").sort_index()


def load_history(refresh=False):
    if CACHE.exists() and not refresh:
        age_h = (time.time() - CACHE.stat().st_mtime) / 3600
        if age_h < 12:
            print(f"  using cache ({age_h:.1f}h old): {CACHE.name}")
            return pickle.loads(CACHE.read_bytes())
    hist = {}
    for i, s in enumerate(POOL, 1):
        df = fetch(s)
        if len(df) > 100:
            hist[s] = df
        if i % 20 == 0 or i == len(POOL):
            print(f"    [{i}/{len(POOL)}] fetched, {len(hist)} usable")
        time.sleep(0.2)
    CACHE.write_bytes(pickle.dumps(hist))
    return hist


def slice_windows(hist):
    """6 non-overlapping 30-day windows, newest first (window 0 = last month)."""
    end = max(df.index.max() for df in hist.values())
    out = []
    for w in range(N_WINDOWS):
        w_end = end - pd.Timedelta(hours=WINDOW_H * w)
        w_start = w_end - pd.Timedelta(hours=WINDOW_H)
        out.append((w, w_start, w_end,
                    {s: df.loc[(df.index > w_start) & (df.index <= w_end)]
                     for s, df in hist.items()}))
    return out


def align(wd):
    """backtest_propr addresses every symbol with one shared iloc index and does
    not verify they match. Intersecting the timelines makes that assumption true."""
    if not wd:
        return {}
    common = None
    for df in wd.values():
        common = df.index if common is None else common.intersection(df.index)
    common = common.sort_values()
    return {s: df.loc[common] for s, df in wd.items()}


def buy_hold(wd):
    curves = [d["close"].to_numpy() / d["close"].to_numpy()[0]
              for d in wd.values() if len(d) > 100]
    if not curves:
        return None
    n = min(len(c) for c in curves)
    eq = INITIAL_CAPITAL * np.vstack([c[:n] for c in curves]).mean(axis=0)
    dd = (np.maximum.accumulate(eq) - eq).max()
    return {"return_pct": float((eq[-1] / INITIAL_CAPITAL - 1) * 100),
            "max_dd_pct": float(dd / INITIAL_CAPITAL * 100),
            "failed": bool(dd > MAX_DD_LIMIT)}


def pick_universe(windows):
    """Constant universe: full history in every window, ranked by dollar volume
    in the OLDEST window so the ranking never sees the months it is tested on."""
    full = None
    for _, _, _, wd in windows:
        ok = {s for s, d in wd.items() if len(d) >= WINDOW_H - 2}
        full = ok if full is None else (full & ok)
    oldest = windows[-1][3]
    dollar_vol = {s: float((oldest[s]["volume"] * oldest[s]["close"]).mean())
                  for s in full}
    ranked = sorted(full, key=lambda s: dollar_vol[s], reverse=True)
    pinned = [s for s in MAJORS if s in full]
    rest = [s for s in ranked if s not in pinned]
    universe = pinned + rest[:max(0, UNIVERSE_SIZE - len(pinned))]
    # entry order = liquidity rank; backtest_propr fills max_concurrent in dict
    # order, so the most liquid names get first refusal on a contested bar
    universe.sort(key=lambda s: dollar_vol[s], reverse=True)
    missing = [s for s in MAJORS if s not in full]
    return universe, dollar_vol, missing, len(full)


def run(windows, universe, label):
    rows = []
    for w, ws, we, wd in windows:
        sub = align({s: wd[s] for s in universe if s in wd})
        if len(sub) < 4:
            continue
        r = backtest_propr(sub, BEST)
        rows.append({
            "window": w, "month": f"{ws.date()} -> {we.date()}",
            "symbols": len(sub),
            "strat": None if not r else {
                k: r[k] for k in ("trades", "total_pnl", "return_pct", "win_rate",
                                  "profit_factor", "max_dd_pct", "max_dd_usd",
                                  "challenge_failed", "daily_limits_hit")},
            "per_symbol": {} if not r else {s: round(v["pnl"], 2)
                                            for s, v in r["per_symbol"].items()},
            "buy_hold": buy_hold(sub),
        })
        s = rows[-1]["strat"]
        tag = "  CHALLENGE FAILED" if s and s["challenge_failed"] else ""
        print(f"    {label:>8} {ws.date()} -> {we.date()}  "
              f"{('%d trades  %+.2f%%  DD %.2f%%' % (s['trades'], s['return_pct'], s['max_dd_pct'])) if s else 'no trades'}{tag}")
    return rows


def main():
    print("=" * 112)
    print("DEEP HUNT v4 — 30-SYMBOL UNIVERSE, LAST 6 MONTHS, MONTH BY MONTH")
    print(f"run {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}")
    print("=" * 112)
    print(f"config FROZEN: {BEST['name']}  (SL {BEST['sl_pct']:.0%} / TP "
          f"{BEST['tp_pct']:.0%} / size {BEST['pos_size_pct']:.0%} / "
          f"max {BEST['max_concurrent']} concurrent)")
    print(f"only variable: universe 12 -> {UNIVERSE_SIZE}\n")

    print(f"Loading history for {len(POOL)} candidates...")
    hist = load_history()
    if not hist:
        print("no data")
        return
    windows = slice_windows(hist)

    universe, dvol, missing, n_full = pick_universe(windows)
    print(f"\n  {n_full} of {len(POOL)} candidates have full history in all "
          f"{N_WINDOWS} windows")
    if missing:
        print(f"  majors WITHOUT full history (excluded): {', '.join(missing)}")
    print(f"  universe ({len(universe)}), liquidity-ranked:")
    for i in range(0, len(universe), 10):
        print("    " + "  ".join(f"{s:<9}" for s in universe[i:i + 10]))
    added = [s for s in universe if s not in MAJORS]
    print(f"\n  the {len(added)} added beyond the majors: {', '.join(added)}")

    print(f"\n  windows: {windows[-1][1].date()} -> {windows[0][2].date()}\n")
    wide = run(windows, universe, "wide30")
    print()
    base = run(windows, [s for s in MAJORS if s in hist], "majors12")

    # ── monthly table ──
    print("\n" + "=" * 112)
    print("MONTH BY MONTH — 30 symbols vs the 12 majors, same frozen config")
    print("=" * 112)
    print(f"{'month':<26} {'B&H':>8} | {'30 trades':>10} {'30 PnL':>10} "
          f"{'30 ret':>8} {'30 DD':>7} {'30 fail':>8} | {'12 PnL':>10} "
          f"{'12 ret':>8} {'12 DD':>7} {'12 fail':>8}")
    print("-" * 112)
    base_by = {r["window"]: r for r in base}
    for r in sorted(wide, key=lambda x: -x["window"]):
        s, b = r["strat"], base_by.get(r["window"], {}).get("strat")
        bh = r["buy_hold"]
        if not s:
            continue
        print(f"{r['month']:<26} {bh['return_pct']:>7.2f}% | {s['trades']:>10} "
              f"${s['total_pnl']:>+9.2f} {s['return_pct']:>+7.2f}% "
              f"{s['max_dd_pct']:>6.2f}% "
              f"{('FAILED' if s['challenge_failed'] else 'ok'):>8} | "
              f"${b['total_pnl']:>+9.2f} {b['return_pct']:>+7.2f}% "
              f"{b['max_dd_pct']:>6.2f}% "
              f"{('FAILED' if b['challenge_failed'] else 'ok'):>8}")

    def summarise(rows, name):
        ok = [r["strat"] for r in rows if r["strat"]]
        if not ok:
            return None
        rets = [x["return_pct"] for x in ok]
        fails = sum(1 for x in ok if x["challenge_failed"])
        compounded = float(np.prod([1 + x / 100 for x in rets]) - 1) * 100
        out = {"months": len(ok), "trades": sum(x["trades"] for x in ok),
               "mean_return_pct": round(float(np.mean(rets)), 2),
               "median_return_pct": round(float(np.median(rets)), 2),
               "compounded_pct": round(compounded, 2),
               "positive_months": sum(1 for x in rets if x > 0),
               "worst_month_pct": round(min(rets), 2),
               "best_month_pct": round(max(rets), 2),
               "worst_dd_pct": round(max(x["max_dd_pct"] for x in ok), 2),
               "challenge_failures": fails}
        print(f"\n  {name}")
        print(f"    months {out['months']}   trades {out['trades']}   "
              f"mean {out['mean_return_pct']:+.2f}%/mo   "
              f"median {out['median_return_pct']:+.2f}%   "
              f"compounded {out['compounded_pct']:+.2f}%")
        print(f"    positive {out['positive_months']}/{out['months']}   "
              f"worst {out['worst_month_pct']:+.2f}%   "
              f"best {out['best_month_pct']:+.2f}%   "
              f"worst DD {out['worst_dd_pct']:.2f}% (wall 6.00%)   "
              f"CHALLENGE FAILURES {out['challenge_failures']}/{out['months']}")
        return out

    print("\n" + "=" * 112)
    print("SUMMARY")
    print("=" * 112)
    sum_wide = summarise(wide, f"{UNIVERSE_SIZE} symbols")
    sum_base = summarise(base, "12 majors")

    # ── per-symbol x per-month matrix ──
    print("\n" + "=" * 112)
    print(f"PER-SYMBOL PROFIT BY MONTH — {UNIVERSE_SIZE}-symbol run (USD, blank = no trade)")
    print("=" * 112)
    months = [r for r in sorted(wide, key=lambda x: -x["window"])]
    hdr = "".join(f"{m['month'][5:10]:>11}" for m in months)
    print(f"{'symbol':<10}{hdr}{'TOTAL':>12}{'months+':>9}")
    print("-" * 112)
    totals = {}
    for s in universe:
        cells, tot, pos, traded = [], 0.0, 0, 0
        for m in months:
            v = m["per_symbol"].get(s)
            if v is None:
                cells.append(f"{'-':>11}")
            else:
                cells.append(f"{v:>+11.2f}")
                tot += v
                traded += 1
                pos += 1 if v > 0 else 0
        totals[s] = tot
        if traded:
            print(f"{s:<10}{''.join(cells)}{tot:>+12.2f}{f'{pos}/{traded}':>9}")
    dead = [s for s in universe if all(m["per_symbol"].get(s) is None for m in months)]
    print("-" * 112)
    row = "".join(f"{sum(m['per_symbol'].values()):>+11.2f}" for m in months)
    print(f"{'TOTAL':<10}{row}{sum(totals.values()):>+12.2f}")
    if dead:
        print(f"\n  never traded in 6 months ({len(dead)}): {', '.join(dead)}")
    winners = {s: v for s, v in totals.items() if v > 0}
    losers = {s: v for s, v in totals.items() if v < 0}
    print(f"  profitable over the 6 months: {len(winners)}/{len(universe) - len(dead)} "
          f"that traded")
    top = sorted(totals.items(), key=lambda x: -x[1])[:5]
    bot = sorted(totals.items(), key=lambda x: x[1])[:5]
    print(f"  best:  " + ", ".join(f"{s} ${v:+.2f}" for s, v in top))
    print(f"  worst: " + ", ".join(f"{s} ${v:+.2f}" for s, v in bot))
    if winners:
        conc = max(winners.values()) / sum(totals.values()) if sum(totals.values()) > 0 else None
        if conc is not None:
            print(f"  concentration: the single best symbol is {conc:.0%} of total net PnL")

    OUT.write_text(json.dumps({
        "run_utc": datetime.now(timezone.utc).isoformat(),
        "config": BEST, "universe": universe,
        "added_beyond_majors": added,
        "majors_without_full_history": missing,
        "selection": "full history in all 6 windows; ranked by dollar volume in the "
                     "OLDEST window; majors pinned; survivorship caveat applies",
        "wide": wide, "majors12": base,
        "summary_wide": sum_wide, "summary_majors12": sum_base,
        "per_symbol_totals": {s: round(v, 2) for s, v in totals.items()},
    }, indent=2, default=str), encoding="utf-8")
    print(f"\n  saved -> {OUT}")


if __name__ == "__main__":
    main()

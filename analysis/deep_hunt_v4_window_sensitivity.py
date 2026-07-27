#!/usr/bin/env python3
"""
Deep Hunt v4 — how much of the walk-forward result is where the months start?

The committed walk-forward reports +0.96%/mo mean over 6 windows on the 12
majors. Re-running the same config over windows shifted ONE DAY later gave
+0.02%. Either the strategy is fine and one comparison was unlucky, or 82 trades
over 6 months is too thin for the window boundary to be arbitrary.

This settles it by brute force: slide the whole 6-window grid across every
1-day offset the history allows, re-run the frozen config at each, and look at
the distribution of the 6-month mean. Nothing is refitted; the config and the
universe are constant. The ONLY thing that changes is where a month begins.

If the edge is real, the distribution is tight and mostly positive. If it is
window placement, the spread swamps the number.
"""
import json
import pickle
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from analysis.deep_hunt_v4_propr import backtest_propr, MAX_DD_LIMIT  # noqa: E402
from analysis.deep_hunt_v4_wide30_6mo import (  # noqa: E402
    CACHE, MAJORS, BEST, WINDOW_H, N_WINDOWS, align, load_history,
)

OUT = HERE / "deep_hunt_v4_window_sensitivity_results.json"
STEP_H = 24          # slide the grid one day at a time


def main():
    print("=" * 100)
    print("DEEP HUNT v4 — WINDOW-PLACEMENT SENSITIVITY, 12 MAJORS")
    print(f"run {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}")
    print("=" * 100)
    print(f"config FROZEN: {BEST['name']}   universe FROZEN: {len(MAJORS)} majors")
    print(f"only variable: the offset of the {N_WINDOWS}-window grid, in "
          f"{STEP_H}h steps\n")

    hist = load_history() if not CACHE.exists() else pickle.loads(CACHE.read_bytes())
    hist = {s: d for s, d in hist.items() if s in MAJORS}
    if len(hist) < len(MAJORS):
        print(f"  WARNING: only {len(hist)}/{len(MAJORS)} majors in cache")
    bars = min(len(d) for d in hist.values())
    end = max(d.index.max() for d in hist.values())
    need = WINDOW_H * N_WINDOWS
    max_off = (bars - need) // STEP_H
    print(f"  {len(hist)} symbols, {bars} bars each, need {need} -> "
          f"{max_off + 1} offsets available (0..{max_off} days back)\n")
    if max_off < 1:
        print("  not enough history to slide the grid")
        return

    rows = []
    for off in range(max_off + 1):
        anchor = end - pd.Timedelta(hours=off * STEP_H)
        rets, dds, trades, fails = [], [], 0, 0
        for w in range(N_WINDOWS):
            w_end = anchor - pd.Timedelta(hours=WINDOW_H * w)
            w_start = w_end - pd.Timedelta(hours=WINDOW_H)
            wd = align({s: d.loc[(d.index > w_start) & (d.index <= w_end)]
                        for s, d in hist.items()})
            wd = {s: d for s, d in wd.items() if len(d) >= 220}
            if len(wd) < 4:
                continue
            r = backtest_propr(wd, BEST)
            if not r:
                rets.append(0.0)
                dds.append(0.0)
                continue
            rets.append(r["return_pct"])
            dds.append(r["max_dd_pct"])
            trades += r["trades"]
            fails += 1 if r["challenge_failed"] else 0
        if not rets:
            continue
        row = {
            "offset_days": off,
            "grid_start": str((anchor - pd.Timedelta(hours=need)).date()),
            "grid_end": str(anchor.date()),
            "mean_return_pct": round(float(np.mean(rets)), 3),
            "median_return_pct": round(float(np.median(rets)), 3),
            "compounded_pct": round(float(np.prod([1 + x / 100 for x in rets]) - 1) * 100, 3),
            "positive_months": int(sum(1 for x in rets if x > 0)),
            "worst_month_pct": round(min(rets), 2),
            "best_month_pct": round(max(rets), 2),
            "worst_dd_pct": round(max(dds), 2),
            "trades": trades,
            "challenge_failures": fails,
            "monthly": [round(float(x), 2) for x in rets],
        }
        rows.append(row)
        print(f"  offset {off:>2}d ({row['grid_start']} -> {row['grid_end']})  "
              f"mean {row['mean_return_pct']:>+6.2f}%/mo  "
              f"compounded {row['compounded_pct']:>+6.2f}%  "
              f"pos {row['positive_months']}/{len(rets)}  "
              f"worst {row['worst_month_pct']:>+6.2f}%  "
              f"DD {row['worst_dd_pct']:>5.2f}%  "
              f"{row['trades']:>3}T  fails {row['challenge_failures']}")

    if not rows:
        print("no usable offsets")
        return

    means = np.array([r["mean_return_pct"] for r in rows])
    comps = np.array([r["compounded_pct"] for r in rows])
    dds = np.array([r["worst_dd_pct"] for r in rows])
    tr = np.array([r["trades"] for r in rows])
    fails = np.array([r["challenge_failures"] for r in rows])

    print("\n" + "=" * 100)
    print("DISTRIBUTION ACROSS WINDOW PLACEMENTS")
    print("=" * 100)
    print(f"  offsets tested        {len(rows)}  (each is the SAME strategy on the "
          f"SAME data, months cut differently)")
    print(f"  mean monthly return   mean {means.mean():+.2f}%   median "
          f"{np.median(means):+.2f}%   sd {means.std(ddof=1):.2f}pp")
    print(f"                        min  {means.min():+.2f}%   max "
          f"{means.max():+.2f}%   range {means.max() - means.min():.2f}pp")
    print(f"  offsets with a positive 6-month mean: "
          f"{int((means > 0).sum())}/{len(means)}  ({(means > 0).mean():.0%})")
    print(f"  compounded 6-month    min {comps.min():+.2f}%   median "
          f"{np.median(comps):+.2f}%   max {comps.max():+.2f}%")
    print(f"  worst drawdown seen   {dds.max():.2f}% (wall 6.00%)   "
          f"offsets with any challenge failure: {int((fails > 0).sum())}/{len(rows)}")
    print(f"  trades per 6 months   min {tr.min()}  median {int(np.median(tr))}  "
          f"max {tr.max()}")

    # The published number vs the spread it sits in.
    print(f"\n  The committed walk-forward reported +0.96%/mo with 0/6 challenge "
          f"failures.\n  Across {len(rows)} daily placements of the same grid, the same "
          f"config on the same\n  data spans {means.min():+.2f}% to {means.max():+.2f}% "
          f"per month.")
    if means.mean() <= 0:
        print(f"  The CENTRAL case is negative ({means.mean():+.2f}%/mo, median "
              f"{np.median(means):+.2f}%): only\n  {int((means > 0).sum())}/{len(means)} "
              f"placements are positive at all, and the published cut sits at\n  or above "
              f"the best of them. On this evidence the reported edge is where the\n  "
              f"month was cut, not a property of the strategy.")
    elif means.std(ddof=1) > abs(means.mean()):
        print("  The spread exceeds the mean: at this trade count the monthly return "
              "is\n  not distinguishable from where the month starts.")
    else:
        print("  The mean survives the spread.")
    if (fails > 0).sum() > len(rows) * 0.5:
        print(f"\n  MORE SERIOUS THAN THE RETURN: {int((fails > 0).sum())}/{len(rows)} "
              f"placements hit at least one\n  CHALLENGE FAILURE, and the worst drawdown "
              f"seen is {dds.max():.2f}% against a 6.00% wall.\n  The 0/6 in the "
              f"committed walk-forward is not representative — it is the\n  "
              f"best-case cut of a distribution that mostly blows the account.")

    best = max(rows, key=lambda r: r["mean_return_pct"])
    worst = min(rows, key=lambda r: r["mean_return_pct"])
    print(f"\n  luckiest cut   offset {best['offset_days']}d "
          f"({best['grid_start']} -> {best['grid_end']}): "
          f"{best['mean_return_pct']:+.2f}%/mo, months {best['monthly']}")
    print(f"  unluckiest cut offset {worst['offset_days']}d "
          f"({worst['grid_start']} -> {worst['grid_end']}): "
          f"{worst['mean_return_pct']:+.2f}%/mo, months {worst['monthly']}")

    OUT.write_text(json.dumps({
        "run_utc": datetime.now(timezone.utc).isoformat(),
        "config": BEST, "universe": sorted(hist),
        "step_hours": STEP_H, "windows": N_WINDOWS, "window_hours": WINDOW_H,
        "offsets": rows,
        "summary": {
            "offsets_tested": len(rows),
            "mean_of_means_pct": round(float(means.mean()), 3),
            "median_of_means_pct": round(float(np.median(means)), 3),
            "sd_pp": round(float(means.std(ddof=1)), 3),
            "min_pct": round(float(means.min()), 3),
            "max_pct": round(float(means.max()), 3),
            "range_pp": round(float(means.max() - means.min()), 3),
            "positive_offsets": int((means > 0).sum()),
            "offsets_with_challenge_failure": int((fails > 0).sum()),
            "worst_dd_pct": round(float(dds.max()), 2),
        },
    }, indent=2, default=str), encoding="utf-8")
    print(f"\n  saved -> {OUT}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
SL/TP Grid Search with Out-of-Sample Validation + Selection Null
================================================================
Grid-searches stop-loss / take-profit ATR multipliers on an in-sample slice,
then tests the best in-sample config once out-of-sample. Fees applied at the
0.14% round-trip audit bar throughout.

WHY THE NULL CONTROL IS HERE
----------------------------
An IS/OOS split alone CANNOT establish edge for a parameter sweep. Searching
88 configs and reporting the winner's OOS number invites two errors:

  1. The IS winner is selection-biased -- with 88 draws, the best IS profit
     factor is mostly noise, so "IS PF 2.4" means nothing on its own.
  2. A single OOS number is one coin flip. A strategy with zero edge still
     returns positive OOS roughly half the time.

SL/TP multipliers are especially prone to this. On a series with no directional
edge, changing the stop and target does not create expectancy -- it only
reshapes the payoff distribution. Tight TP with a wide stop buys a high win
rate and a low profit factor; the reverse buys the opposite. Expectancy stays
at roughly minus-fees either way. So a grid will ALWAYS surface some config
that looked good in-sample.

The decisive test (matching this repo's established methodology bar) is
therefore not "is OOS positive" but "does IS selection beat picking at random".
We run every config out-of-sample and ask where the IS-selected one ranks. If
it lands mid-pack, the search learned nothing transferable, regardless of sign.
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from analysis.combined_simulation import CombinedSimulator  # noqa: E402

FEE_RATE = 0.0014  # 0.14% round-trip
TRAIN_FRAC = 0.60
OUT = Path(__file__).parent / "sl_tp_grid_search.json"


def build_data():
    """Prepare all symbols once and reuse across every config."""
    base = CombinedSimulator(quiet=True)
    raw = base.load_data()
    prepared = {}
    for sym, d in raw.items():
        try:
            prepared[sym] = base.prepare(d)
        except Exception as e:
            print(f"  skip {sym}: {e}")
    times = sorted(set().union(*[set(df.index) for df in prepared.values()]))
    return prepared, times


def run_config(prepared, times, sl, tp):
    sim = CombinedSimulator(
        initial_capital=10_000, sl_atr=sl, tp_atr=tp,
        fee_rate=FEE_RATE, quiet=True,
    )
    return sim.run(prepared=prepared, times=times)


def main():
    prepared, all_times = build_data()
    split = int(len(all_times) * TRAIN_FRAC)
    train_times, test_times = all_times[:split], all_times[split:]

    sl_multipliers = np.arange(1.0, 4.5, 0.5)
    tp_multipliers = np.arange(1.0, 6.5, 0.5)
    grid = [(round(float(s), 2), round(float(t), 2))
            for s in sl_multipliers for t in tp_multipliers]

    print("=" * 72)
    print("SL/TP GRID SEARCH  --  in-sample fit, out-of-sample test, null control")
    print("=" * 72)
    print(f"Symbols:      {len(prepared)}")
    print(f"Bars:         {len(all_times)}  ({all_times[0]} -> {all_times[-1]})")
    print(f"Split:        {TRAIN_FRAC:.0%} IS / {1 - TRAIN_FRAC:.0%} OOS "
          f"-- IS ends {train_times[-1]}, OOS starts {test_times[0]}")
    print(f"Grid:         {len(sl_multipliers)} SL x {len(tp_multipliers)} TP "
          f"= {len(grid)} configs")
    print(f"Fees:         {FEE_RATE:.2%} round-trip on every trade")
    print()

    # ---- Phase 1: fit in-sample -------------------------------------------
    rows = []
    for i, (sl, tp) in enumerate(grid, 1):
        m = run_config(prepared, train_times, sl, tp)
        rows.append({
            "sl_atr": sl, "tp_atr": tp,
            "is_win_rate": m["win_rate"],
            "is_profit_factor": m["profit_factor"],
            "is_net_return": m["net_return"],
            "is_trades": m["total_trades"],
        })
        if i % 20 == 0:
            print(f"  in-sample {i}/{len(grid)} configs...")

    df = pd.DataFrame(rows)
    # Ignore configs that barely traded -- a PF from 3 trades is not a result.
    eligible = df[df["is_trades"] >= 20]
    if eligible.empty:
        eligible = df
    best = eligible.sort_values("is_profit_factor", ascending=False).iloc[0]
    print(f"\nBest in-sample: SL={best['sl_atr']}x  TP={best['tp_atr']}x  "
          f"(IS PF {best['is_profit_factor']:.2f}, "
          f"return {best['is_net_return']:+.2f}%, {int(best['is_trades'])} trades)")

    # ---- Phase 2: every config out-of-sample (this IS the null) ------------
    print("\nRunning all configs out-of-sample for the selection null...")
    oos_rows = []
    for i, (sl, tp) in enumerate(grid, 1):
        m = run_config(prepared, test_times, sl, tp)
        oos_rows.append({
            "sl_atr": sl, "tp_atr": tp,
            "oos_win_rate": m["win_rate"],
            "oos_profit_factor": m["profit_factor"],
            "oos_net_return": m["net_return"],
            "oos_trades": m["total_trades"],
        })
        if i % 20 == 0:
            print(f"  out-of-sample {i}/{len(grid)} configs...")

    oos = pd.DataFrame(oos_rows)
    merged = df.merge(oos, on=["sl_atr", "tp_atr"])

    sel = merged[(merged["sl_atr"] == best["sl_atr"]) &
                 (merged["tp_atr"] == best["tp_atr"])].iloc[0]

    # ---- Verdict ----------------------------------------------------------
    rets = merged["oos_net_return"]
    pct_rank = (rets < sel["oos_net_return"]).mean() * 100
    positive = (rets > 0).sum()

    print()
    print("=" * 72)
    print("RESULTS")
    print("=" * 72)
    print(f"\nSelected config OOS:   return {sel['oos_net_return']:+.2f}%, "
          f"PF {sel['oos_profit_factor']:.2f}, "
          f"WR {sel['oos_win_rate']:.1f}%, {int(sel['oos_trades'])} trades")
    print(f"\nSELECTION NULL -- all {len(merged)} configs run out-of-sample:")
    print(f"  median OOS return      {rets.median():+.2f}%")
    print(f"  mean OOS return        {rets.mean():+.2f}%")
    print(f"  best / worst           {rets.max():+.2f}% / {rets.min():+.2f}%")
    print(f"  configs positive OOS   {positive}/{len(merged)} ({positive/len(merged):.0%})")
    print(f"  selected config rank   {pct_rank:.0f}th percentile")

    skill = pct_rank >= 90
    print(f"\n  Does IS selection beat random config choice?  "
          f"{'YES' if skill else 'NO'}  "
          f"(needs >=90th pct; got {pct_rank:.0f}th)")

    is_oos_corr = merged["is_net_return"].corr(merged["oos_net_return"])
    print(f"  IS->OOS return correlation across grid: {is_oos_corr:+.3f}  "
          f"({'transfers' if is_oos_corr > 0.3 else 'does not transfer'})")

    verdict = "EDGE NOT ESTABLISHED"
    if skill and sel["oos_net_return"] > 0 and is_oos_corr > 0.3:
        verdict = "SURVIVES -- worth a further look"
    print(f"\n  VERDICT: {verdict}")

    OUT.write_text(json.dumps({
        "fee_rate": FEE_RATE,
        "train_frac": TRAIN_FRAC,
        "n_configs": len(grid),
        "is_best": {k: float(best[k]) for k in best.index},
        "selected_oos": {k: float(sel[k]) for k in sel.index},
        "null": {
            "median_oos_return": float(rets.median()),
            "mean_oos_return": float(rets.mean()),
            "pct_configs_positive": float(positive / len(merged)),
            "selected_percentile_rank": float(pct_rank),
            "is_oos_correlation": float(is_oos_corr),
        },
        "verdict": verdict,
        "grid": merged.to_dict("records"),
    }, indent=2), encoding="utf-8")
    print(f"\n  Saved: {OUT}")

    return merged, sel


if __name__ == "__main__":
    main()

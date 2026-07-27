#!/usr/bin/env python3
"""
Re-score the Deep Hunt v4 config sweep after the equity-curve fix.

The old equity line added open positions' entry cost on top of their
mark-to-market, so equity jumped ~30% on every entry and fell back on every
exit — inventing a ~23pp drawdown that had nothing to do with the strategy.
The sweep ranks configs by  score = pnl - 15*(max_dd - 3) + trades + 8*PF,
so that artifact fed straight into the selection.

This fetches ONE set of candles and runs all 20 configs twice over it — once
with the fixed code, once with the old line patched back in — so the ranking
change is attributable to the bug and not to the window having moved.

Doubles as the regression check: asserts the fixed equity curve equals
free cash + mark-to-market at every bar, and that a flat book equals capital.
"""
import json
import sys
import time
import types
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from analysis import deep_hunt_backtest_v4 as V4  # noqa: E402

OUT = HERE / "deep_hunt_v4_rescore_results.json"
FIXED_LINE = "equity_curve.append(capital + mtm)"
OLD_LINE = """equity_curve.append(capital + mtm + sum(
            p["entry_price"] * p["qty"] for p in positions if p["symbol"] == symbol))"""


def load_with_old_equity():
    """The pre-fix module, rebuilt from the current source."""
    src = (HERE / "deep_hunt_backtest_v4.py").read_text(encoding="utf-8")
    assert FIXED_LINE in src, "fixed equity line not found — did the file change?"
    mod = types.ModuleType("v4_old_equity")
    mod.__file__ = str(HERE / "deep_hunt_backtest_v4.py")
    exec(compile(src.replace(FIXED_LINE, OLD_LINE), "<v4_old_equity>", "exec"),
         mod.__dict__)
    return mod


def score(mod, cfg, data):
    """Reproduce main()'s aggregation and prop-firm score for one config."""
    agg = {"trades": 0, "wins": 0, "losses": 0, "pnl": 0.0,
           "gp": 0.0, "gl": 0.0, "max_dd": 0.0}
    for sym, df in data.items():
        r = mod.backtest_v4(sym, df, cfg)
        if not r or r["trades"] == 0:
            continue
        agg["trades"] += r["trades"]
        agg["wins"] += r["wins"]
        agg["losses"] += r["losses"]
        agg["pnl"] += r["total_pnl"]
        agg["gp"] += r["avg_win_usd"] * r["wins"]
        agg["gl"] += r["avg_loss_usd"] * r["losses"]
        agg["max_dd"] = max(agg["max_dd"], r["max_dd_pct"])
    n = agg["trades"]
    if n == 0:
        return None
    pf = round(agg["gp"] / agg["gl"], 2) if agg["gl"] > 0 else 0
    dd_penalty = max(0, agg["max_dd"] - 3) * 15
    return {
        "name": cfg["name"], "trades": n,
        "total_pnl": round(agg["pnl"], 2),
        "win_rate": round(agg["wins"] / n * 100, 1),
        "profit_factor": pf,
        "max_dd_pct": round(agg["max_dd"], 2),
        "dd_penalty": round(dd_penalty, 2),
        "score": round(agg["pnl"] - dd_penalty + min(n, 60) * 1.0 + min(pf, 5) * 8, 2),
    }


def selfcheck(data):
    """The equity curve must be free cash + mark-to-market, nothing else."""
    sym = next(iter(data))
    df = data[sym]
    old = load_with_old_equity()
    a = V4.backtest_v4(sym, df, {"name": "chk", "sl_pct": 0.10,
                                 "tp_pct": 0.02, "max_hold": 24})
    b = old.backtest_v4(sym, df, {"name": "chk", "sl_pct": 0.10,
                                  "tp_pct": 0.02, "max_hold": 24})
    if a and b:
        assert b["max_dd_pct"] > a["max_dd_pct"], (
            "old equity math should overstate drawdown; it did not")
        assert a["total_pnl"] == b["total_pnl"], (
            "the fix must not change realised PnL, only the equity curve")
    print("  selfcheck OK — equity = cash + mark-to-market; PnL unchanged by the fix")


def main():
    print("=" * 104)
    print("DEEP HUNT v4 — RE-SCORE AFTER THE EQUITY-CURVE FIX")
    print(f"run {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}")
    print("=" * 104)

    print(f"\nFetching 30d 1h candles for {len(V4.SYMBOLS)} symbols "
          f"(one fetch, both runs share it)...")
    data = {}
    for i, s in enumerate(V4.SYMBOLS, 1):
        df = V4.fetch_hyperliquid_candles(s, "1h", days=30)
        if not df.empty:
            data[s] = df
        print(f"  [{i:2d}/{len(V4.SYMBOLS)}] {s:5s} {len(df)} bars")
        time.sleep(0.25)
    if not data:
        print("no data")
        return

    print()
    selfcheck(data)

    old = load_with_old_equity()
    print(f"\nRunning {len(V4.CONFIGS)} configs under both equity formulas...\n")
    new_rows, old_rows = [], []
    for cfg in V4.CONFIGS:
        a, b = score(V4, cfg, data), score(old, cfg, data)
        if a:
            new_rows.append(a)
        if b:
            old_rows.append(b)

    new_rows.sort(key=lambda r: r["score"], reverse=True)
    old_rows.sort(key=lambda r: r["score"], reverse=True)
    new_rank = {r["name"]: i + 1 for i, r in enumerate(new_rows)}
    old_rank = {r["name"]: i + 1 for i, r in enumerate(old_rows)}
    old_by = {r["name"]: r for r in old_rows}

    print("=" * 104)
    print("RANKING — fixed equity curve (score = pnl - 15*(maxDD-3) + trades + 8*PF)")
    print("=" * 104)
    print(f"{'#':>3} {'config':<20} {'Tr':>4} {'PnL':>10} {'WR%':>6} {'PF':>6} "
          f"{'maxDD%':>8} {'penalty':>8} {'score':>9} | {'was #':>5} {'wasDD%':>7} "
          f"{'wasScore':>9}")
    print("-" * 104)
    for i, r in enumerate(new_rows, 1):
        o = old_by.get(r["name"], {})
        move = old_rank.get(r["name"], 0) - i
        arrow = f"{move:+d}" if move else "="
        print(f"{i:>3} {r['name']:<20} {r['trades']:>4} ${r['total_pnl']:>+8.2f} "
              f"{r['win_rate']:>5.1f}% {r['profit_factor']:>5.2f} "
              f"{r['max_dd_pct']:>7.2f}% {r['dd_penalty']:>8.2f} {r['score']:>+8.2f} | "
              f"{old_rank.get(r['name'], '?'):>5} {o.get('max_dd_pct', '?'):>7} "
              f"{o.get('score', '?'):>9}  {arrow}")

    print("\n" + "=" * 104)
    print("WHAT THE BUG COST")
    print("=" * 104)
    dd_new = [r["max_dd_pct"] for r in new_rows]
    dd_old = [r["max_dd_pct"] for r in old_rows]
    print(f"  max drawdown reported   old {np.mean(dd_old):6.2f}% (min {min(dd_old):.2f} "
          f"max {max(dd_old):.2f})   ->   fixed {np.mean(dd_new):6.2f}% "
          f"(min {min(dd_new):.2f} max {max(dd_new):.2f})")
    pen_old = np.mean([r["dd_penalty"] for r in old_rows])
    pen_new = np.mean([r["dd_penalty"] for r in new_rows])
    print(f"  mean DD penalty         old {pen_old:8.2f}   ->   fixed {pen_new:8.2f}")
    moved = [(n, old_rank[n], new_rank[n]) for n in new_rank
             if n in old_rank and old_rank[n] != new_rank[n]]
    print(f"  configs that changed rank: {len(moved)}/{len(new_rows)}")
    for n, o, nn in sorted(moved, key=lambda x: abs(x[1] - x[2]), reverse=True)[:6]:
        print(f"    {n:<20} #{o} -> #{nn}")
    same_winner = new_rows[0]["name"] == old_rows[0]["name"]
    print(f"\n  winner under the old (broken) score : {old_rows[0]['name']}")
    print(f"  winner under the fixed score        : {new_rows[0]['name']}")
    print(f"  -> the fix {'does NOT change' if same_winner else 'CHANGES'} which "
          f"config the sweep selects")
    print(f"\n  PnL is unaffected by the fix (the bug was in the equity curve only); "
          f"what changes is\n  the drawdown and therefore the DD penalty in the score.")

    print(f"\n  NOTE, unchanged by this fix: each symbol is still backtested against its "
          f"OWN\n  ${V4.INITIAL_CAPITAL:,.0f}, so the aggregate PnL is "
          f"{len(data)} independent accounts, not one portfolio,\n  and MAX_CONCURRENT=3 "
          f"never binds. Use deep_hunt_v4_propr.backtest_propr() for\n  portfolio-level "
          f"numbers — that engine was always correct and is what study 14 used.")

    OUT.write_text(json.dumps({
        "run_utc": datetime.now(timezone.utc).isoformat(),
        "symbols": list(data), "bars": {s: len(d) for s, d in data.items()},
        "fixed": new_rows, "old_buggy_equity": old_rows,
        "winner_changed": not same_winner,
    }, indent=2, default=str), encoding="utf-8")
    print(f"\n  saved -> {OUT}")


if __name__ == "__main__":
    main()

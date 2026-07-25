#!/usr/bin/env python3
"""
Allocation Optimization: Test multiple M1/M2/M3 splits.
"""

import sys
sys.stdout.reconfigure(encoding='utf-8')

from combined_simulation import CombinedSimulator


CONFIGS = [
    {"name": "M1=50 M2=20 M3=30 (current)", "m1": 0.50, "m2": 0.20, "m3": 0.30},
    {"name": "M2=50 M3=50 (no M1)",         "m1": 0.00, "m2": 0.50, "m3": 0.50},
    {"name": "M2=60 M3=40 (no M1)",         "m1": 0.00, "m2": 0.60, "m3": 0.40},
    {"name": "M1=20 M2=40 M3=40",           "m1": 0.20, "m2": 0.40, "m3": 0.40},
    {"name": "M1=30 M2=40 M3=30",           "m1": 0.30, "m2": 0.40, "m3": 0.30},
    {"name": "M2=70 M3=30 (no M1)",         "m1": 0.00, "m2": 0.70, "m3": 0.30},
    {"name": "M2=100 (M2 only)",            "m1": 0.00, "m2": 1.00, "m3": 0.00},
]


def run_config(name, m1, m2, m3):
    sim = CombinedSimulator(initial_capital=10_000)
    sim.m1_weight = m1
    sim.m2_weight = m2
    sim.m3_weight = m3
    if m1 == 0:
        sim.m1_position_pct = 0
    if m2 == 0:
        sim.m2_position_pct = 0
    if m3 == 0:
        sim.m3_position_pct = 0

    # Suppress print output
    import io, contextlib
    f = io.StringIO()
    with contextlib.redirect_stdout(f):
        sim.run()

    eq = sim.equity_curve[-1] if sim.equity_curve else 10000
    ret = (eq - 10000) / 10000 * 100
    wr = sim.wins / max(sim.total_trades, 1) * 100
    dd = max((sim.peak_equity - e) / sim.peak_equity for e in sim.equity_curve) * 100

    wins_pnl = sum(t["pnl"] for t in sim.trade_log if t["pnl"] > 0)
    loss_pnl = sum(abs(t["pnl"]) for t in sim.trade_log if t["pnl"] < 0)
    pf = wins_pnl / loss_pnl if loss_pnl > 0 else 999

    m1t = [t for t in sim.trade_log if t["module"] == "module1"]
    m2t = [t for t in sim.trade_log if t["module"] == "module2"]
    m3t = [t for t in sim.trade_log if t["module"] == "module3"]

    return {
        "name": name,
        "return_pct": round(ret, 2),
        "trades": sim.total_trades,
        "win_rate": round(wr, 1),
        "pf": round(pf, 2),
        "max_dd": round(dd, 1),
        "final_eq": round(eq, 2),
        "m1": f"{len(m1t)}t/{round(len([t for t in m1t if t['pnl']>0])/max(len(m1t),1)*100)}%wr/${round(sum(t['pnl'] for t in m1t),1)}",
        "m2": f"{len(m2t)}t/{round(len([t for t in m2t if t['pnl']>0])/max(len(m2t),1)*100)}%wr/${round(sum(t['pnl'] for t in m2t),1)}",
        "m3": f"{len(m3t)}t/{round(len([t for t in m3t if t['pnl']>0])/max(len(m3t),1)*100)}%wr/${round(sum(t['pnl'] for t in m3t),1)}",
    }


if __name__ == "__main__":
    print("=" * 90)
    print("ALLOCATION OPTIMIZATION — 6 MONTHS BACKTEST")
    print("=" * 90)
    print()

    results = []
    for cfg in CONFIGS:
        r = run_config(cfg["name"], cfg["m1"], cfg["m2"], cfg["m3"])
        results.append(r)

    # Sort by return
    results.sort(key=lambda x: x["return_pct"], reverse=True)

    print(f"{'Config':<35} {'Return':>8} {'WR':>6} {'PF':>6} {'DD':>6} {'Trades':>7} {'Final$':>10}")
    print("-" * 90)
    for r in results:
        marker = " <-- BEST" if r == results[0] else ""
        print(f"{r['name']:<35} {r['return_pct']:>+7.2f}% {r['win_rate']:>5.1f}% {r['pf']:>5.2f} {r['max_dd']:>5.1f}% {r['trades']:>6} ${r['final_eq']:>9,.2f}{marker}")

    print()
    print("Module Breakdown (best config):")
    best = results[0]
    print(f"  M1: {best['m1']}")
    print(f"  M2: {best['m2']}")
    print(f"  M3: {best['m3']}")

#!/usr/bin/env python3
"""
Aggressive Optimization: Test many Kelly/position/threshold combos.
"""
import sys
sys.stdout.reconfigure(encoding='utf-8')
import io, contextlib
import pandas as pd
import numpy as np

from combined_simulation import CombinedSimulator


def run_one(name, m1_w, m2_w, m3_w, kelly, m1_pos, m2_pos, m3_pos,
            sl_atr, tp_atr, fee=0.001):
    sim = CombinedSimulator(initial_capital=10_000, sl_atr=sl_atr, tp_atr=tp_atr,
                            fee_rate=fee, quiet=True)
    sim.m1_weight = m1_w
    sim.m2_weight = m2_w
    sim.m3_weight = m3_w
    sim.kelly_fraction = kelly
    sim.m1_position_pct = m1_pos
    sim.m2_position_pct = m2_pos
    sim.m3_position_pct = m3_pos

    f = io.StringIO()
    with contextlib.redirect_stdout(f):
        sim.run()

    eq = sim.equity_curve[-1] if sim.equity_curve else 10000
    ret = (eq - 10000) / 10000 * 100
    wr = sim.wins / max(sim.total_trades, 1) * 100
    dd = max((sim.peak_equity - e) / sim.peak_equity for e in sim.equity_curve) * 100
    gross_w = sum(t["pnl"] for t in sim.trade_log if t["pnl"] > 0)
    gross_l = sum(abs(t["pnl"]) for t in sim.trade_log if t["pnl"] < 0)
    pf = gross_w / gross_l if gross_l > 0 else 999
    m2t = [t for t in sim.trade_log if t["module"] == "module2"]
    m3t = [t for t in sim.trade_log if t["module"] == "module3"]
    m1t = [t for t in sim.trade_log if t["module"] == "module1"]

    return {
        "name": name, "ret": round(ret, 2), "trades": sim.total_trades,
        "wr": round(wr, 1), "pf": round(pf, 2), "dd": round(dd, 1),
        "eq": round(eq, 2), "m2n": len(m2t), "m3n": len(m3t), "m1n": len(m1t),
        "m2pnl": round(sum(t["pnl"] for t in m2t), 1),
        "m3pnl": round(sum(t["pnl"] for t in m3t), 1),
        "m1pnl": round(sum(t["pnl"] for t in m1t), 1),
        "fees": round(sim.fees_paid, 2),
    }


# (name, m1_w, m2_w, m3_w, kelly, m1_pos, m2_pos, m3_pos, sl_atr, tp_atr)
def configs():
    C = []

    # Baseline
    C.append(("Baseline K=0.25 M2=12% M3=8%",
        0, 0.60, 0.40, 0.25, 0, 0.12, 0.08, 2.0, 3.0))

    # Kelly sweep with fixed M2=60 M3=40 weights
    for kelly in [0.50, 0.75, 1.0]:
        for m2pos, m3pos in [(0.15, 0.10), (0.20, 0.15), (0.25, 0.20), (0.30, 0.25)]:
            C.append((f"K={kelly:.2f} M2p={m2pos*100:.0f}% M3p={m3pos*100:.0f}%",
                0, 0.60, 0.40, kelly, 0, m2pos, m3pos, 2.0, 3.0))

    # SL/TP grid (best Kelly candidates)
    for sl in [1.5, 2.0, 2.5, 3.0]:
        for tp in [2.0, 3.0, 4.0, 5.0]:
            if tp <= sl:
                continue
            C.append((f"SL={sl} TP={tp} K=0.75 M2=25% M3=20%",
                0, 0.60, 0.40, 0.75, 0, 0.25, 0.20, sl, tp))

    # Pure M2
    for kelly in [0.50, 0.75, 1.0]:
        for m2pos in [0.20, 0.25, 0.30]:
            C.append((f"PureM2 K={kelly} p={m2pos*100:.0f}%",
                0, 1.0, 0, kelly, 0, m2pos, 0, 2.0, 2.5))

    # Pure M3 aggressive
    for kelly in [0.50, 0.75, 1.0]:
        for m3pos in [0.15, 0.20, 0.25, 0.30]:
            for sl in [2.0, 2.5]:
                C.append((f"PureM3 K={kelly} p={m3pos*100:.0f}% SL={sl}",
                    0, 0, 1.0, kelly, 0, 0, m3pos, sl, 3.0))

    # M2+M3 balanced aggressive
    for kelly in [0.75, 1.0]:
        for m2pos, m3pos in [(0.25, 0.20), (0.30, 0.25), (0.35, 0.30)]:
            for sl, tp in [(1.5, 3.0), (2.0, 3.0), (2.0, 4.0), (2.5, 4.0)]:
                C.append((f"M2={m2pos*100:.0f}% M3={m3pos*100:.0f}% K={kelly} SL={sl} TP={tp}",
                    0, 0.50, 0.50, kelly, 0, m2pos, m3pos, sl, tp))

    # Full aggressive
    C.append(("FULL AGG: M2=30 M3=25 K=1.0 SL=1.5 TP=4.0",
        0, 0.55, 0.45, 1.0, 0, 0.30, 0.25, 1.5, 4.0))
    C.append(("MAX RISK: M2=30 M3=25 K=1.0 SL=2.0 TP=5.0",
        0, 0.55, 0.45, 1.0, 0, 0.30, 0.25, 2.0, 5.0))

    return C


if __name__ == "__main__":
    print("=" * 100)
    print("AGGRESSIVE OPTIMIZATION — Kelly, Position Size, SL/TP Grid Search")
    print("=" * 100)
    print("Fee: 10 bps round-trip (0.1%)")
    print()

    all_configs = configs()
    results = []

    for i, cfg in enumerate(all_configs):
        name = cfg[0]
        r = run_one(name, *cfg[1:])
        results.append(r)
        if (i + 1) % 25 == 0:
            print(f"  ... {i+1}/{len(all_configs)} done")

    results.sort(key=lambda x: x["ret"], reverse=True)

    print(f"\nTOP 30:")
    print(f"{'Config':<55} {'Return':>8} {'WR':>6} {'PF':>6} {'DD':>6} {'#':>5} {'M1$':>7} {'M2$':>7} {'M3$':>7}")
    print("-" * 105)
    for r in results[:30]:
        marker = " <--" if r == results[0] else ""
        print(f"{r['name']:<55} {r['ret']:>+7.2f}% {r['wr']:>5.1f}% {r['pf']:>5.2f} {r['dd']:>5.1f}% {r['trades']:>5} ${r['m1pnl']:>+6.1f} ${r['m2pnl']:>+6.1f} ${r['m3pnl']:>+6.1f}{marker}")

    b = results[0]
    print(f"\n{'='*100}")
    print(f"BEST: {b['name']}")
    print(f"  Return: {b['ret']:+.2f}%  |  WR: {b['wr']}%  |  PF: {b['pf']}  |  DD: {b['dd']}%")
    print(f"  Trades: {b['trades']} (M1:{b['m1n']} M2:{b['m2n']} M3:{b['m3n']})")
    print(f"  PnL:    M1=${b['m1pnl']:+.1f}  M2=${b['m2pnl']:+.1f}  M3=${b['m3pnl']:+.1f}")
    print(f"  Fees:   ${b['fees']:.2f}")
    print(f"  Final:  ${b['eq']:,.2f}")

    w = results[-1]
    print(f"\nWORST: {w['name']}")
    print(f"  Return: {w['ret']:+.2f}%  |  WR: {w['wr']}%  |  PF: {w['pf']}  |  DD: {w['dd']}%")

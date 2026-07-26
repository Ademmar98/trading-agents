#!/usr/bin/env python3
"""
MAX AGGRESSIVE: Push spot trading to absolute limits.
Tests: max position sizes, tightest stops, most relaxed thresholds.
"""
import sys
sys.stdout.reconfigure(encoding='utf-8')
import io, contextlib
import pandas as pd
import numpy as np

from combined_simulation import CombinedSimulator


def run_one(name, m2_w, m3_w, kelly, m2_pos, m3_pos, sl, tp, fee, max_pos, per_mod, cvd, rv, dip):
    sim = CombinedSimulator(initial_capital=10_000, sl_atr=sl, tp_atr=tp,
                            fee_rate=fee, quiet=True)
    sim.m1_weight = 0
    sim.m2_weight = m2_w
    sim.m3_weight = m3_w
    sim.kelly_fraction = kelly
    sim.m1_position_pct = 0
    sim.m2_position_pct = m2_pos
    sim.m3_position_pct = m3_pos
    sim.max_positions = max_pos
    sim.per_module_max = per_mod
    sim.max_exposure = 0.95  # allow 95% exposure
    sim.m3_cvd_thresh = cvd
    sim.m3_rv_thresh = rv
    sim.m3_dip_thresh = dip

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

    # Monthly returns
    monthly = {}
    for i, ts in enumerate(sim.timestamps):
        key = ts.strftime("%Y-%m")
        monthly[key] = sim.equity_curve[i]
    m_rets = []
    prev = 10000
    for k, e in sorted(monthly.items()):
        m_rets.append((k, (e - prev) / prev * 100))
        prev = e

    return {
        "name": name, "ret": round(ret, 2), "trades": sim.total_trades,
        "wr": round(wr, 1), "pf": round(pf, 2), "dd": round(dd, 1),
        "eq": round(eq, 2), "m2n": len(m2t), "m3n": len(m3t),
        "m2pnl": round(sum(t["pnl"] for t in m2t), 1),
        "m3pnl": round(sum(t["pnl"] for t in m3t), 1),
        "fees": round(sim.fees_paid, 2),
        "monthly": m_rets,
    }


if __name__ == "__main__":
    print("=" * 100)
    print("MAX AGGRESSIVE — Pushing spot trading to absolute limits")
    print("=" * 100)

    configs = []

    # --- Tier 1: Max position sizes (no risk management) ---
    for kelly in [1.0, 1.5, 2.0]:
        for pos in [0.40, 0.50, 0.60, 0.80, 1.0]:
            for sl, tp in [(1.0, 4.0), (1.0, 5.0), (1.5, 4.0), (1.5, 5.0)]:
                configs.append((f"K={kelly} p={pos*100:.0f}% SL={sl} TP={tp}",
                    0, 1.0, kelly, 0, pos, sl, tp, 0.001, 6, 6, -0.3, 1.2, -0.005))

    # --- Tier 2: Relaxed M3 thresholds (more signals) ---
    for cvd, rv, dip in [(-0.2, 1.0, -0.003), (-0.15, 0.9, -0.003),
                          (-0.1, 0.8, -0.002), (-0.1, 0.7, -0.001)]:
        for kelly in [1.0, 1.5]:
            for pos in [0.40, 0.60]:
                configs.append((f"relaxed cvd={cvd} rv={rv} K={kelly} p={pos*100:.0f}%",
                    0, 1.0, kelly, 0, pos, 1.5, 4.0, 0.001, 6, 6, cvd, rv, dip))

    # --- Tier 3: Ultra-tight stops (more trades, faster compounding) ---
    for kelly in [1.0, 1.5, 2.0]:
        for sl in [0.75, 1.0]:
            for tp in [3.0, 4.0, 5.0]:
                configs.append((f"ULTRA TIGHT SL={sl} TP={tp} K={kelly}",
                    0, 1.0, kelly, 0, 0.60, sl, tp, 0.001, 8, 6, -0.2, 1.0, -0.003))

    # --- Tier 4: All-in configs (80-100% position) ---
    for kelly in [1.0, 1.5, 2.0]:
        for pos in [0.80, 1.0]:
            configs.append((f"ALL-IN p={pos*100:.0f}% K={kelly} SL=1.5 TP=5.0",
                0, 1.0, kelly, 0, pos, 1.5, 5.0, 0.001, 8, 6, -0.15, 0.9, -0.003))

    results = []
    for i, cfg in enumerate(configs):
        r = run_one(*cfg)
        results.append(r)
        if (i + 1) % 30 == 0:
            print(f"  ... {i+1}/{len(configs)} done")

    # Sort by return
    results.sort(key=lambda x: x["ret"], reverse=True)

    print(f"\nTOP 20:")
    print(f"{'Config':<50} {'Ret':>8} {'WR':>6} {'PF':>6} {'DD':>6} {'#':>5}")
    print("-" * 85)
    for r in results[:20]:
        marker = " <--" if r == results[0] else ""
        print(f"{r['name']:<50} {r['ret']:>+7.2f}% {r['wr']:>5.1f}% {r['pf']:>5.2f} {r['dd']:>5.1f}% {r['trades']:>5}{marker}")

    b = results[0]
    print(f"\n{'='*100}")
    print(f"BEST: {b['name']}")
    print(f"  Return:  {b['ret']:+.2f}% total  |  Monthly avg: {b['ret']/6:+.2f}%")
    print(f"  Win Rate: {b['wr']}%  |  PF: {b['pf']}  |  DD: {b['dd']}%")
    print(f"  Trades:  {b['trades']}  |  M2: {b['m2n']}  |  M3: {b['m3n']}")
    print(f"  PnL:     M2=${b['m2pnl']:+.1f}  M3=${b['m3pnl']:+.1f}  Fees=${b['fees']:.0f}")
    print(f"  Final:   ${b['eq']:,.2f}")
    print(f"\n  Monthly breakdown:")
    for m, r in b["monthly"]:
        print(f"    {m}: {r:+.2f}%")

    # Check which configs hit the target
    print(f"\n{'='*100}")
    print("CONFIGS HITTING 10%+ MONTHLY TARGET:")
    for r in results:
        monthly_avg = r["ret"] / 6
        if monthly_avg >= 10:
            print(f"  {r['name']}: {r['ret']:+.2f}% total ({monthly_avg:+.1f}%/mo) DD={r['dd']}%")

    monthly_avg_best = b["ret"] / 6
    if monthly_avg_best < 10:
        print(f"  NONE — best monthly avg is {monthly_avg_best:+.1f}%")
        print(f"\n  NOTE: 10-30% monthly requires LEVERAGE or gambling-level risk.")
        print(f"  Current max achievable with spot: {monthly_avg_best:+.1f}%/month")
        print(f"  Annualized: {(1 + b['ret']/100)**12 - 1:+.0f}%")

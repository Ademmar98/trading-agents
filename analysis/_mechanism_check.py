# -*- coding: utf-8 -*-
"""Re-simulate the LIVE mechanism (CFG_DEADLINE: mom lb96 k3/long, 1% risk,
2ATR stop, 2R TP, 2 slots, hold 72, cooldown 4, halts 2%/4.5%) on the FULL
dataset Feb 2 -> Sep 6 2026 -- the first run to include the ARB windfall.

Answers: is the mechanism that produced +$523 on ARB profitable by itself?
"""
import sys, io, pickle
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
import json
import numpy as np
import sim

DEADLINE = sim.Cfg(strat="mom", lb=96, k=3, allow_short=False,
                   risk_pct=0.01, sl_atr=2.0, tp_r=2.0,
                   max_concurrent=2, max_hold=72, cooldown=4,
                   daily_halt=0.02, dd_halt=0.045)

raw = pickle.loads(open("/opt/propr-bot/candles_1y.pkl", "rb").read())
D = sim.prep(raw)
n_cols = len(D["cols"])
print(f"data: {len(D['index'])/24:.0f} days "
      f"({D['index'][0]:%Y-%m-%d} -> {D['index'][-1]:%Y-%m-%d}), {n_cols} assets",
      flush=True)

def run(Dx, cfg, rules, lo=0, hi=None):
    sig = sim.SIGNALS[cfg.strat](Dx, cfg)
    return sim.simulate(Dx, sig, cfg, rules, lo, hi)

def show(tag, r):
    print(f"  {tag:<24} {r['outcome']:<12} ret {r['return_pct']:+7.2f}%  "
          f"mdD {r['max_dd_pct']:5.2f}%  tr {r['trades']:>4}  "
          f"WR {r['win_rate']:5.1f}%  PF {r['profit_factor']:5.2f}  "
          f"trd/d {r['trades_per_day']:5.2f}")

def subset_keep(keep):
    out = dict(D)
    for f in ("open", "high", "low", "close", "atr", "valid"):
        out[f] = D[f][:, keep]
    out["lev"] = D["lev"][keep]
    out["cols"] = [D["cols"][i] for i in keep]
    return out

# free rules: no walls, no halts -> pure mechanism expectancy
free = sim.Rules(target_pct=99, daily_loss_pct=99, max_dd_pct=99)
loose = sim.Cfg(**{**DEADLINE.__dict__, "daily_halt": 9, "dd_halt": 9})

print("\n[A] PURE MECHANISM (no walls, no halts) -- full span:")
r_full = run(D, loose, free)
show("full universe", r_full)

# leave-one-out, contribution = P&L delta when asset removed
print("\n[B] LEAVE-ONE-OUT (drop each asset, rerun mechanism), biggest movers:")
loo = []
for i, name in enumerate(D["cols"]):
    keep = [j for j in range(n_cols) if j != i]
    r = run(subset_keep(keep), loose, free)
    loo.append((name, r_full["return_pct"] - r["return_pct"], r["return_pct"]))
loo.sort(key=lambda x: -abs(x[1]))
print("  (positive delta = that asset ADDED returns; negative = it cost)")
for name, delta, r_ret in loo:
    flag = " <-- ARB" if name == "ARB" else (" <-- TOP" if loo.index((name, delta, r_ret)) < 3 and delta > 0 else "")
    print(f"    {name:<8} contribution {delta:+7.2f}%   without it ret {r_ret:+7.2f}%{flag}")

print("\n  without ARB:", end=" ")
show("", run(subset_keep([j for j in range(n_cols) if D["cols"][j] != "ARB"]), loose, free))
short = [i for i in range(n_cols) if D["cols"][i] not in ("ARB")]
# drop the 3 biggest winners
wins = sorted(loo, key=lambda x: -x[1])[:3]
print("  drop top-3 winners (%s):" % ",".join(w[0] for w in wins), end=" ")
keep = [j for j in range(n_cols) if D["cols"][j] not in {w[0] for w in wins}]
show("", run(subset_keep(keep), loose, free))

# cut data to Aug 30 (the pre-windfall span) and compare on the identical cfg
n_aug30 = 0
for t, d in enumerate(D["index"]):
    if d <= __import__("datetime").datetime(2026, 8, 30, 23, tzinfo=__import__("datetime").timezone.utc):
        n_aug30 = t
print(f"\n[C] SPAN CUT: pre-windfall end {D['index'][n_aug30]:%Y-%m-%d} "
      f"vs full end {D['index'][-1]:%Y-%m-%d} (ARB run included):")
show("to Aug 30", run(D, loose, free, hi=n_aug30 + 1))
show("to Sep 06", r_full)

# [D] real challenge: 30-day sliding windows with true rules/walls
print("\n[D] REAL WALLS -- sliding 30d windows (Rules: +10% / -3% daily / -$300dd):")
wins_ = sim.windows(D, days=30, step=5)
rules = sim.Rules()
sig = sim.SIGNALS[DEADLINE.strat](D, DEADLINE)
rs = [sim.simulate(D, sig, DEADLINE, rules, lo, hi) for lo, hi in wins_]
n_w = len(rs)
passes = sum(r["hit_target"] for r in rs)
breaches = sum(r["breached"] for r in rs)
print(f"  windows={n_w}  pass {passes}/{n_w} ({passes/n_w:.0%})  "
      f"breach {breaches}/{n_w} ({breaches/n_w:.0%})  "
      f"median ret {np.median([r['return_pct'] for r in rs]):+.2f}%")
print("\n  the newest 6 windows (most recent = today's situation):")
for (lo, hi), r in list(zip(wins_, rs))[:6]:
    print(f"    {D['index'][lo]:%m-%d} -> {D['index'][hi-1]:%m-%d}  "
          f"{r['outcome']:<11} ret {r['return_pct']:+7.2f}%  PF {r['profit_factor']:5.2f}")

# [E] what does ARB alone do to each window's PnL (delta when ARB removed)?
print("\n[E] ARB WINDOW CONTRIBUTION (excluding ARB per window):")
sig_no = sim.SIGNALS[loose.strat](subset_keep([j for j in range(n_cols) if D["cols"][j] != "ARB"]), loose)
rs_no = [sim.simulate(subset_keep([j for j in range(n_cols) if D["cols"][j] != "ARB"]), sig_no, loose, free, lo, hi)
         for lo, hi in wins_]
deltas = [a["return_pct"] - b["return_pct"] for a, b in zip(rs, rs_no)]
print(f"  per-window ARB delta: max {max(deltas):+.2f}%  "
      f"median {np.median(deltas):+.2f}%  last-window {deltas[0]:+.2f}%")

out = {"span_days": len(D["index"]) / 24,
       "full_regime": r_full,
       "without_arb_unrestricted": run(subset_keep(
           [j for j in range(n_cols) if D["cols"][j] != "ARB"]), loose, free),
       "leave_one_out": loo,
       "windows_pass": passes, "windows_total": n_w, "windows_breach": breaches}
json.dump(out, open("/opt/propr-bot/mechanism_check_results.json", "w"), indent=1)
print("\n-> /opt/propr-bot/mechanism_check_results.json")
# -*- coding: utf-8 -*-
"""Sleeve sensitivity + regime threshold sensitivity on full window."""
import io, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, r"C:\Users\DELL\OneDrive\1m\propr-bot")
import numpy as np
import sim
from sim import simulate, Rules, load, prep, Cfg

CFG = Cfg(strat="mom", lb=96, k=3, allow_short=False,
          risk_pct=0.005, sl_atr=2.0, tp_r=2.0,
          max_concurrent=1, max_hold=72, cooldown=4,
          daily_halt=0.02, dd_halt=0.045)
R = Rules(start_equity=1000.0, target_pct=0.10, daily_loss_pct=0.05,
          max_dd_pct=0.10, fee=0.00075, fee_maker=0.00075, funding_8h=0.0001)
D = prep(load())
sig = sim.SIGNALS["mom"](D, CFG)

def sleeve_sig(D, cfg, floor):
    O, C, ATR, V = D["open"], D["close"], D["atr"], D["valid"]
    mom = sim.SIGNALS["mom"](D, cfg)
    s = np.zeros(C.shape, np.int8)
    for t in range(1, C.shape[0]):
        prev = mom[t - 1]
        for a in range(C.shape[1]):
            if prev[a] > 0 and V[t, a] and V[t - 1, a] and ATR[t - 1, a] > 0:
                gap = abs(O[t, a] / C[t - 1, a] - 1)
                cap = floor * ATR[t - 1, a] / C[t - 1, a]
                if gap <= cap + 1e-12:
                    s[t, a] = 1
    return s

print("== sleeve floor sensitivity ==")
for fl in (0.25, 0.5, 0.75, 1.0, 2.0, 999.0):
    s = sleeve_sig(D, CFG, fl)
    r = simulate(D, s, CFG, R)
    r1 = simulate(D, s, CFG, R, lo=0, hi=len(D["close"]) // 2)
    r2 = simulate(D, s, CFG, R, lo=len(D["close"]) // 2, hi=len(D["close"]))
    print(f"  floor={fl:6.2f}  ret {r['return_pct']:+6.2f}% PF {r['profit_factor']:.2f} "
          f"maxDD {r['max_dd_pct']:.1f}% win {r['win_rate']:.0f}% tr {r['trades']} "
          f"first {r1['return_pct']:+.1f}% second {r2['return_pct']:+.1f}%")
print("DONE")
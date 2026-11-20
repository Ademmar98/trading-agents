# -*- coding: utf-8 -*-
"""Fee sensitivity on the best signal (dip_07_40): is the negative expectancy
purely the taker fee, or is there no gross edge at all?"""
import sys, io, pickle
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
import numpy as np
import pandas as pd
import sim

D = sim.prep(pickle.loads(open("/opt/propr-bot/candles_1y.pkl", "rb").read()))
C, VALID = D["close"], D["valid"]

cfg = sim.Cfg(strat="dip", lb=96, dip=0.07, rsi=40, allow_short=False,
              risk_pct=0.01, sl_atr=2.0, tp_r=2.0, max_hold=72,
              max_concurrent=2, cooldown=4, daily_halt=0.02, dd_halt=0.045)
sig = sim.SIGNALS["dip"](D, cfg)

# also mom for comparison
mcfg = sim.Cfg(strat="mom", lb=96, k=3, allow_short=False, risk_pct=0.01,
               sl_atr=2.0, tp_r=2.0, max_concurrent=2, max_hold=72, cooldown=4,
               daily_halt=0.02, dd_halt=0.045)
msig = sim.SIGNALS["mom"](D, mcfg)

print("Loose walls (full-span, no early stop): ret/PF vs taker fee\n")
for name, sg, cf in (("dip_07_40", sig, cfg), ("mom_96k3", msig, mcfg)):
    print(f"  {name}:")
    for fee in (0.0, 0.0002, 0.0005, 0.00075, 0.0010):
        r = sim.simulate(D, sg, cf, sim.Rules(target_pct=99, daily_loss_pct=0.99,
                                              max_dd_pct=0.99, fee=fee))
        print(f"    fee {fee:.4%}: ret {r['return_pct']:+7.2f}%  dd {r['max_dd_pct']:5.2f}%  "
              f"trd {r['trades']:>3}  wr {r['win_rate']:5.1f}%  pf {r['profit_factor']:5.2f}")
    print()

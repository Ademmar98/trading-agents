# -*- coding: utf-8 -*-
"""Last-30-days A/B: pre-update baseline vs updated bot (regime gate +
entry sleeve), same exact methodology as _backtest_filters.py."""
import io, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, r"C:\Users\DELL\OneDrive\1m\propr-bot")
import numpy as np
import sim
from sim import simulate, Rules, load, prep, Cfg, windows

CFG = Cfg(strat="mom", lb=96, k=3, allow_short=False,
          risk_pct=0.005, sl_atr=2.0, tp_r=2.0,
          max_concurrent=1, max_hold=72, cooldown=4,
          daily_halt=0.02, dd_halt=0.045)
R = Rules(start_equity=1000.0, target_pct=0.10, daily_loss_pct=0.05,
          max_dd_pct=0.10, fee=0.00075, fee_maker=0.00075, funding_8h=0.0001)

D = prep(load())
wins = windows(D, days=30)          # newest first
lo, hi = wins[0]
print(f"data {D['index'][0].date()} -> {D['index'][-1].date()}  assets {len(D['cols'])}")
print(f"LAST MONTH window: {D['index'][lo].date()} -> {D['index'][hi - 1].date()}  "
      f"({hi - lo} bars)\n")

sig = sim.SIGNALS["mom"](D, CFG)

def regime_mask(D):
    C, V, H, L = D["close"], D["valid"], D["high"], D["low"]
    mask = np.ones(C.shape[0], bool)
    btc = D["cols"].index("BTC")
    for t in range(96, C.shape[0]):
        if not (V[t, btc] and V[t - 96, btc]):
            continue
        mid96 = (H[t - 95:t + 1, btc].max() + L[t - 95:t + 1, btc].min()) / 2
        if C[t, btc] < mid96:
            mask[t] = False; continue
        if V[t, btc] and V[t - 24, btc]:
            r24 = C[t, btc] / C[t - 24, btc] - 1
            if r24 <= -0.02:
                mask[t] = False; continue
        base, now = C[t - 24, :], C[t, :]
        ok = (base > 0) & V[t - 24, :] & V[t, :]
        frac = (ok & (now > base)).sum() / max(1, ok.sum())
        if frac < 0.30:
            mask[t] = False
    return mask

def sleeve_sig(D, cfg, floor=0.5):
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

def show(name, s):
    r = simulate(D, s, CFG, R, lo, hi)
    print(f"  {name:20s} ret {r['return_pct']:+6.2f}%  PF {r['profit_factor']:.2f}  "
          f"maxDD {r['max_dd_pct']:.1f}%  win {r['win_rate']:.0f}%  tr {r['trades']}  "
          f"outcome {r['outcome']}")

mk = regime_mask(D)
s_slv = sleeve_sig(D, CFG)

print("== LAST 30 DAYS A/B ==")
show("pre-update", sig)
show("+ regime gate", sig * mk[:, None])
show("+ entry sleeve", s_slv)
show("updated-both", s_slv * mk[:, None])
print("DONE")
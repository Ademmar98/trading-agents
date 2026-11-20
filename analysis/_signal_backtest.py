# -*- coding: utf-8 -*-
"""Backtest candidate DIRECTIONAL signals on ~1yr HL data, reusing sim engine.
Walls are loosened so the full year runs and total expectancy (PF, win rate,
return) is measured cleanly. Custom signal functions only."""

import sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
import numpy as np
import pandas as pd
import sim

CACHE_1Y = "/opt/propr-bot/candles_1y.pkl"
OUT = "/opt/propr-bot/signal_bt_results.json"

import pickle
D = sim.prep(pickle.loads(open(CACHE_1Y, "rb").read()))
n, m = len(D["index"]), len(D["cols"])
print(f"prep: {n} bars = {n/24:.0f} days x {m} assets", flush=True)
C, H, L = D["close"], D["high"], D["low"]
VALID = D["valid"]

# loose walls so the whole year runs and PF/return are clean
RULES = sim.Rules(target_pct=99.0, daily_loss_pct=0.99, max_dd_pct=0.99)

# ---- signal builders: (bars x assets) int8 of {0,+1}, long only ----------
def sig_dip(D, cfg):
    """Buy a dip off the rolling lb high that meets RSI oversold AND stays in
    an uptrend (close > rolling sma). Contrarian-long mean reversion."""
    C = D["close"]
    hh = pd.DataFrame(C).rolling(cfg.lb).max().to_numpy()
    drop = C / hh - 1
    rsi = _rsi(C)
    sma = pd.DataFrame(C).rolling(24).mean().to_numpy()
    sig = np.zeros(C.shape, np.int8)
    sig[(drop <= -cfg.dip) & (rsi < cfg.rsi) & (C > sma)] = 1
    sig[~VALID] = 0
    return sig


def sig_momf(D, cfg):
    """Cross-sectional momentum long the top-k, but only names whose realized
    vol (std of hourly returns) is below the cross-section PERCENTILE — keeps
    only calmer names so it must differ from plain mom."""
    C = D["close"]; lb, k = cfg.lb, cfg.k
    ret = np.full_like(C, np.nan)
    ret[lb:] = C[lb:] / C[:-lb] - 1
    ret[~VALID] = np.nan
    r = np.log(C / np.maximum(C, 1e-12))
    vol = pd.DataFrame(r).rolling(48).std().to_numpy(copy=True)
    vol[~VALID] = np.nan
    lo = np.nanpercentile(np.where(VALID, vol, np.nan), 40, axis=1, keepdims=True)
    hi = np.nanpercentile(np.where(VALID, vol, np.nan), 70, axis=1, keepdims=True)
    okvol = (vol >= lo) & (vol <= hi)   # calm-to-mid vol band
    sig = np.zeros(C.shape, np.int8)
    rows = np.arange(C.shape[0])[:, None]
    hi = np.argsort(np.nan_to_num(ret, nan=-np.inf), axis=1)[:, -k:]
    m = np.zeros(C.shape, bool); m[rows, hi] = True
    sig[m & (ret > 0) & okvol] = 1
    sig[~VALID] = 0
    return sig


def sig_ema(D, cfg):
    """Long when fast EMA > slow EMA and close above slow EMA (trend regime)."""
    C = D["close"]; f, s = cfg.lb, cfg.n
    ef = pd.DataFrame(C).ewm(span=f, adjust=False).mean().to_numpy()
    es = pd.DataFrame(C).ewm(span=s, adjust=False).mean().to_numpy()
    sig = np.zeros(C.shape, np.int8)
    sig[(ef > es) & (C > es)] = 1
    sig[~VALID] = 0
    return sig


def sig_bb(D, cfg):
    """Long on close above the upper Bollinger band (vol expansion breakout)."""
    C = D["close"]; lb, nstd = cfg.lb, cfg.rsi / 10.0
    m = pd.DataFrame(C).rolling(lb).mean().to_numpy()
    sd = pd.DataFrame(C).rolling(lb).std().to_numpy()
    up = m + nstd * sd
    sig = np.zeros(C.shape, np.int8)
    sig[C > up] = 1
    sig[~VALID] = 0
    return sig


def _rsi(C, n=14):
    d = pd.DataFrame(C).diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return (100 - 100 / (1 + up / dn.replace(0, np.nan))).to_numpy()


SIGS = {"dip": lambda D, cfg: sim.SIGNALS["dip"](D, cfg),
        "donch": lambda D, cfg: sim.SIGNALS["donch"](D, cfg),
        "momf": sig_momf, "ema": sig_ema, "bb": sig_bb}


def run(cfg, sigfun):
    sig = sigfun(D, cfg)
    r = sim.simulate(D, sig, cfg, RULES)
    return r


def line(name, r):
    print(f"{name:<16} ret={r['return_pct']:+8.2f}%  dd={r['max_dd_pct']:6.2f}%  "
          f"trd={r['trades']:>4}  wr={r['win_rate']:5.1f}%  "
          f"pf={r['profit_factor']:6.2f}", flush=True)


def base(**kw):
    d = dict(strat="X", lb=96, k=3, n=192, dip=0.07, rsi=25.0,
             allow_short=False, risk_pct=0.01, sl_atr=2.0, tp_r=2.0,
             max_hold=72, max_concurrent=2, cooldown=4, trail_r=0.0,
             daily_halt=0.02, dd_halt=0.045)
    d.update(kw)
    return sim.Cfg(**d)


cands = [
    # dip-buy (mean reversion, builtin) - buy oversold dip below local high
    ("dip_05_30",  base(dip=0.05, rsi=30), "dip"),
    ("dip_07_40",  base(dip=0.07, rsi=40), "dip"),
    ("dip_10_30",  base(dip=0.10, rsi=30), "dip"),
    ("dip_04_35",  base(dip=0.04, lb=48, rsi=35), "dip"),
    # Donchian breakout, long only
    ("donch_24",   base(n=24, lb=24, dip=0.05, rsi=30, allow_short=False), "donch"),
    ("donch_48",   base(n=48, lb=48, dip=0.05, rsi=30, allow_short=False), "donch"),
    ("donch_96",   base(n=96, lb=96, dip=0.05, rsi=30, allow_short=False), "donch"),
    # momentum + vol filter (now genuinely different from plain mom)
    ("momf_96k3",  base(lb=96, k=3), "momf"),
    ("momf_48k5",  base(lb=48, k=5, n=96), "momf"),
    # EMA trend following
    ("ema_24_96",  base(lb=24, n=96), "ema"),
    ("ema_48_192", base(lb=48, n=192), "ema"),
    # Bollinger breakout
    ("bb_24_2",    base(lb=24, rsi=20), "bb"),
    ("bb_48_25",   base(lb=48, rsi=25), "bb"),
]

results = []
print("Signal candidates (loose walls, full-year expectancy):")
print("-" * 88)
for name, cfg, s in cands:
    r = run(cfg, SIGS[s])
    results.append({"signal": name, "cfg": cfg.__dict__, "result": r})
    line(name, r)

# baseline: the current live strategy on the same year, same loose walls
print("-" * 88)
base_cfg = sim.Cfg(strat="mom", lb=96, k=3, allow_short=False, risk_pct=0.01,
                   sl_atr=2.0, tp_r=2.0, max_concurrent=2, max_hold=72,
                   cooldown=4, daily_halt=0.02, dd_halt=0.045)
base_sig = sim.SIGNALS["mom"](D, base_cfg)
base_r = sim.simulate(D, base_sig, base_cfg, RULES)
line("current_mom", base_r)
results.append({"signal": "current_mom", "cfg": base_cfg.__dict__, "result": base_r})

json.dump(results, open(OUT, "w"), indent=1, default=str)
print(f"\n-> {OUT}")

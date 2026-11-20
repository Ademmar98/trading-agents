#!/usr/bin/env python3
"""Step 2: Run entry optimization on cached data"""
import sys, time, json, pickle
import numpy as np
import pandas as pd
from pathlib import Path

sys.stdout.reconfigure(line_buffering=True)

INITIAL_CAPITAL = 5000.0
DAILY_LOSS_LIMIT = 150.0
MAX_DD_LIMIT = 300.0
FEE = 0.0008
SLIP = 0.0005
COST = FEE + SLIP
DATA = Path(__file__).parent / "hl_177_30d_1h.pkl"


def rsi(c, p=14):
    d = c.diff()
    g = d.where(d > 0, 0.0).ewm(alpha=1/p, min_periods=p).mean()
    l = (-d.where(d < 0, 0.0)).ewm(alpha=1/p, min_periods=p).mean()
    return 100 - 100 / (1 + g / (l + 1e-10))


def cvd_z(v, c, lb=20):
    d = np.sign(c.diff())
    cvd = (v * d).cumsum()
    m = cvd.rolling(lb).mean()
    s = cvd.rolling(lb).std()
    return (cvd - m) / (s + 1e-10)


def preproc(all_data, cfg):
    dp = cfg.get("dip", 0.05)
    rt = cfg.get("rsi", 30)
    vm = cfg.get("vol", 1.8)
    ct = cfg.get("cvd", -0.8)
    lb = cfg.get("lb", 50)
    proc = {}
    for sym, df in all_data.items():
        d = df.copy()
        d["rsi"] = rsi(d["close"])
        d["cvd_z"] = cvd_z(d["volume"], d["close"])
        d["vol_spike"] = d["volume"] / (d["volume"].rolling(20).mean() + 1e-10)
        d["rh"] = d["high"].rolling(lb).max()
        d["dip"] = (d["rh"] - d["close"]) / d["rh"]
        d["ema50"] = d["close"].ewm(span=50).mean()
        d["ema200"] = d["close"].ewm(span=200).mean()
        e12 = d["close"].ewm(span=12).mean()
        e26 = d["close"].ewm(span=26).mean()
        d["macd_h"] = (e12 - e26) - (e12 - e26).ewm(span=9).mean()
        d["macd_hp"] = d["macd_h"].shift(1)
        bb_m = d["close"].rolling(20).mean()
        bb_s = d["close"].rolling(20).std()
        d["bb_pct"] = (d["close"] - (bb_m - 2*bb_s)) / (4*bb_s + 1e-10)
        d["vol_rp"] = d["volume"] / (d["volume"].rolling(10).max() + 1e-10)
        sig = ((d["dip"] >= dp) & (d["rsi"] < rt) &
               (d["vol_spike"] > vm) & (d["cvd_z"] < ct))
        if cfg.get("green"): sig &= (d["close"] > d["open"])
        if cfg.get("macd"): sig &= (d["macd_h"] > d["macd_hp"])
        if cfg.get("bb"): sig &= (d["bb_pct"] < 0.1)
        if cfg.get("vdec"): sig &= (d["vol_rp"] < 0.7)
        d["signal"] = sig
        proc[sym] = d
    return proc


def bt(proc, cfg):
    sl_p, tp_p = cfg["sl"], cfg["tp"]
    ps = cfg.get("ps", 0.10)
    mc = cfg.get("mc", 1)
    ta = cfg.get("ta", 0.0)
    td = cfg.get("td", 0.02)
    cd = cfg.get("cd", 3)
    mh = cfg.get("mh", 36)

    cap = INITIAL_CAPITAL
    peak = INITIAL_CAPITAL
    positions = []
    trades = []
    d_pnl = 0.0
    cur_day = None
    d_halt = False
    failed = False
    leb = {s: -cd - 1 for s in proc}

    ref = list(proc.keys())[0]
    ts_arr = proc[ref].index

    for bi in range(220, len(ts_arr)):
        ts = ts_arr[bi]
        px = {}
        for s in proc:
            px[s] = proc[s].iloc[bi]["close"] if bi < len(proc[s]) else proc[s].iloc[-1]["close"]

        day = ts.date()
        if cur_day is None or day != cur_day:
            if cur_day is not None: d_pnl = 0.0
            cur_day = day
            d_halt = False

        if failed: break

        # EXIT
        closed = set()
        for p in positions:
            s = p[0]
            if s not in proc or bi >= len(proc[s]): continue
            r = proc[s].iloc[bi]
            bh = bi - p[5]
            # trail update
            if p[6]:  # trailing active
                nt = r["high"] * (1 - td)
                if nt > p[3]: p[3] = nt
            elif ta > 0 and r["high"] > p[1] * (1 + ta):
                p[6] = True
                p[3] = p[1] * 1.001

            ep = None
            if r["low"] <= p[3]: ep = p[3] * (1 - SLIP)
            elif r["high"] >= p[4]: ep = p[4] * (1 - SLIP)
            elif bh >= mh: ep = r["close"] * (1 - SLIP)
            elif p[6] and r["low"] <= p[3]: ep = p[3] * (1 - SLIP)

            if ep is not None:
                pnl = (ep - p[1]) * p[2] - ep * p[2] * COST
                cap += p[1] * p[2] + pnl
                d_pnl += pnl
                trades.append(pnl)
                closed.add(s)

        positions = [p for p in positions if p[0] not in closed]

        eq = cap + sum(p[1] * p[2] + (px[p[0]] - p[1]) * p[2] for p in positions)
        if eq > peak: peak = eq
        if (peak - eq) > MAX_DD_LIMIT:
            failed = True
            for p in positions:
                ep = px[p[0]] * (1 - SLIP)
                pnl = (ep - p[1]) * p[2] - ep * p[2] * COST
                trades.append(pnl)
            positions.clear()
            break

        if d_pnl < -DAILY_LOSS_LIMIT:
            d_halt = True
            for p in positions:
                ep = px[p[0]] * (1 - SLIP)
                pnl = (ep - p[1]) * p[2] - ep * p[2] * COST
                trades.append(pnl)
            positions.clear()
        if d_halt: continue

        # ENTRY
        held = {p[0] for p in positions}
        for s in proc:
            if s in held or len(positions) >= mc: continue
            if (bi - leb[s]) < cd or bi >= len(proc[s]): continue
            r = proc[s].iloc[bi]
            if not r["signal"]: continue
            ep = r["close"] * (1 + SLIP)
            notional = cap * ps
            fee = notional * COST
            if notional + fee > cap: continue
            qty = notional / ep
            positions.append([s, ep, qty, ep*(1-sl_p), ep*(1+tp_p), bi, False])
            cap -= (ep * qty + fee)
            leb[s] = bi

    for p in positions:
        if p[0] in px:
            ep = px[p[0]] * (1 - SLIP)
            pnl = (ep - p[1]) * p[2] - ep * p[2] * COST
            cap += p[1] * p[2] + pnl
            trades.append(pnl)

    pnl = cap - INITIAL_CAPITAL
    w = sum(1 for t in trades if t > 0)
    gp = sum(t for t in trades if t > 0)
    gl = abs(sum(t for t in trades if t <= 0))
    return pnl, len(trades), round(w/max(len(trades),1)*100,1), round(gp/max(gl,.01),2), failed


def main():
    with open(DATA, "rb") as f:
        all_data = pickle.load(f)
    print(f"{len(all_data)} symbols loaded\n")

    # Focused grid: ~35 configs
    grid = []
    # 1. Threshold sweep (27 combos)
    for dip in [0.05, 0.07, 0.10]:
        for rsi_v in [30, 25, 20]:
            for cvd_v in [-0.8, -1.0, -1.2]:
                grid.append({"name": f"d{int(dip*100)}r{rsi_v}c{abs(int(cvd_v*10))}",
                    "dip": dip, "rsi": rsi_v, "vol": 2.0, "cvd": cvd_v,
                    "sl": 0.10, "tp": 0.04, "ps": 0.10, "mc": 1})

    # 2. Confirmation filters on best base (d7r25c10)
    base = {"dip": 0.07, "rsi": 25, "vol": 2.0, "cvd": -1.0, "sl": 0.10, "tp": 0.04, "ps": 0.10, "mc": 1}
    for fn, flt in [("green", {"green": True}), ("macd", {"macd": True}),
                     ("bb", {"bb": True}), ("vdec", {"vdec": True}),
                     ("g+m", {"green": True, "macd": True}),
                     ("g+bb", {"green": True, "bb": True}),
                     ("g+m+v", {"green": True, "macd": True, "vdec": True})]:
        grid.append({"name": f"d7r25+{fn}", **base, **flt})

    # 3. TP/SL combos on best
    for sl in [0.08, 0.10, 0.12]:
        for tp in [0.03, 0.04, 0.05, 0.06]:
            grid.append({"name": f"sl{int(sl*100)}tp{int(tp*100)}",
                "dip": 0.07, "rsi": 25, "vol": 2.0, "cvd": -1.0,
                "green": True, "macd": True,
                "sl": sl, "tp": tp, "ps": 0.10, "mc": 1})

    print(f"Running {len(grid)} configs...\n")
    results = []
    for i, cfg in enumerate(grid):
        if i < 32: continue  # skip already-run configs
        t0 = time.time()
        proc = preproc(all_data, cfg)
        pnl, ntr, wr, pf, failed = bt(proc, cfg)
        el = time.time() - t0
        results.append({"name": cfg["name"], "pnl": round(pnl, 2), "n": ntr,
                         "wr": wr, "pf": pf, "failed": failed, "cfg": cfg})
        flag = " FAIL" if failed else ""
        print(f"[{i+1:2d}/{len(grid)}] {cfg['name']:20s} {ntr:2d}T PnL=$ {pnl:7.2f} WR={wr:5.1f}% PF={pf:5.2f} ({el:.1f}s){flag}")

    # Add known results from previous runs
    known = [
        {"name": "d5r30c8", "pnl": 33.70, "n": 15, "wr": 46.7, "pf": 1.74, "cfg": {"dip": 0.05, "rsi": 30, "vol": 2.0, "cvd": -0.8, "sl": 0.10, "tp": 0.04, "ps": 0.10, "mc": 1}},
        {"name": "d5r25c8", "pnl": 73.06, "n": 16, "wr": 62.5, "pf": 2.07, "cfg": {"dip": 0.05, "rsi": 25, "vol": 2.0, "cvd": -0.8, "sl": 0.10, "tp": 0.04, "ps": 0.10, "mc": 1}},
        {"name": "d5r25c10", "pnl": 36.35, "n": 15, "wr": 60.0, "pf": 1.50, "cfg": {"dip": 0.05, "rsi": 25, "vol": 2.0, "cvd": -1.0, "sl": 0.10, "tp": 0.04, "ps": 0.10, "mc": 1}},
        {"name": "d7r30c8", "pnl": 54.41, "n": 19, "wr": 52.6, "pf": 1.61, "cfg": {"dip": 0.07, "rsi": 30, "vol": 2.0, "cvd": -0.8, "sl": 0.10, "tp": 0.04, "ps": 0.10, "mc": 1}},
        {"name": "d7r25c8", "pnl": 29.60, "n": 17, "wr": 58.8, "pf": 1.30, "cfg": {"dip": 0.07, "rsi": 25, "vol": 2.0, "cvd": -0.8, "sl": 0.10, "tp": 0.04, "ps": 0.10, "mc": 1}},
        {"name": "d7r20c8", "pnl": 16.29, "n": 14, "wr": 64.3, "pf": 1.21, "cfg": {"dip": 0.07, "rsi": 20, "vol": 2.0, "cvd": -0.8, "sl": 0.10, "tp": 0.04, "ps": 0.10, "mc": 1}},
        {"name": "d7r25+g+m", "pnl": 28.54, "n": 4, "wr": 75.0, "pf": 3.55, "cfg": {"dip": 0.07, "rsi": 25, "vol": 2.0, "cvd": -1.0, "green": True, "macd": True, "sl": 0.10, "tp": 0.04, "ps": 0.10, "mc": 1}},
    ]
    results = known + results

    ok = sorted([r for r in results if not r["failed"]], key=lambda x: x["pnl"], reverse=True)
    fl = [r for r in results if r["failed"]]

    print(f"\n{'='*90}")
    print(f"TOP 15 PROFITABLE ({len(ok)} passed, {len(fl)} failed):")
    print(f"{'='*90}")
    for i, r in enumerate(ok[:15]):
        print(f"  {i+1:2d} {r['name']:20s} {r['n']:2d}T  PnL=$ {r['pnl']:7.2f}  WR={r['wr']:5.1f}%  PF={r['pf']:5.2f}")

    out = [{"name": r["name"], "pnl": r["pnl"], "n": r["n"], "wr": r["wr"],
             "pf": r["pf"], "cfg": r["cfg"]} for r in ok[:30]]
    with open(Path(__file__).parent / "deep_hunt_v5_entries.json", "w") as f:
        json.dump(out, f, indent=2)
    print(f"\nSaved to deep_hunt_v5_entries.json")


if __name__ == "__main__":
    main()

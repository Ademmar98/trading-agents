#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Fast Mass Backtest: Top 50 pairs x core strategies (optimized for speed)
"""
import sys
import os
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, stdev
import requests

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.data_provider import fetch_binance_klines
from core.strategies import ALL_STRATEGIES, _ensure_family_merged
from core.pricing import round_sig

BACKTEST_BARS = 365
FEE_RATIO = 0.001  # 0.05% per side = 0.1% round trip
MAX_POSITIONS = 3
RESULTS_DIR = Path(__file__).parent / "mass_backtest_results"
RESULTS_DIR.mkdir(exist_ok=True)


def fetch_top_50():
    print("Fetching top 50 USDT pairs by 24h volume...")
    r = requests.get("https://api.binance.com/api/v3/ticker/24hr", timeout=15)
    data = r.json()
    usdt = [t for t in data if t["symbol"].endswith("USDT") and float(t.get("quoteVolume", 0)) > 0]
    usdt.sort(key=lambda t: float(t["quoteVolume"]), reverse=True)
    top50 = []
    for t in usdt[:50]:
        sym = t["symbol"].replace("USDT", "/USD")
        vol = float(t["quoteVolume"])
        top50.append({"symbol": sym, "volume_24h": vol})
        print(f"  {sym:12s} vol=${vol/1e6:,.0f}M")
    return top50


def calc_sl_tp(entry, side, vol_pct):
    vol = max(vol_pct, 1.0) / 100
    sl_dist = vol * 1.5
    tp_dist = vol * 2.0
    if side == "BUY":
        sl = round_sig(entry * (1 - sl_dist))
        tp = round_sig(entry * (1 + tp_dist))
    else:
        sl = round_sig(entry * (1 + sl_dist))
        tp = round_sig(entry * (1 - tp_dist))
    return sl, tp


def backtest_strategy(symbol, name, fn, ohlc):
    if len(ohlc) < 60:
        return None
    
    cash = 10000.0
    positions = []
    trades = []
    equity = []
    
    for i in range(50, len(ohlc)):
        cur = ohlc[i]
        h, l, c = cur["high"], cur["low"], cur["close"]
        
        new_pos = []
        for p in positions:
            s, e, q, sl, tp = p["side"], p["entry"], p["qty"], p["sl"], p["tp"]
            exit_px = None
            
            if (s == "BUY" and l <= sl) or (s == "SELL" and h >= sl):
                exit_px, reason = sl, "SL"
            elif (s == "BUY" and h >= tp) or (s == "SELL" and l <= tp):
                exit_px, reason = tp, "TP"
            
            if exit_px:
                pnl = (exit_px - e) * q if s == "BUY" else (e - exit_px) * q
                fee = q * exit_px * FEE_RATIO
                cash += q * exit_px - fee
                trades.append(pnl - fee - q * e * FEE_RATIO)
            else:
                new_pos.append(p)
        positions = new_pos
        
        if len(positions) < MAX_POSITIONS:
            try:
                sig = fn(ohlc[:i+1])
                if sig and sig.get("action") in ("BUY", "SELL"):
                    side = sig["action"]
                    qty = (cash * 0.15) / c
                    if qty > 0.001:
                        # Vol from recent closes
                        rec = [x["close"] for x in ohlc[max(0,i-20):i+1]]
                        rets = [(rec[j]-rec[j-1])/rec[j-1] for j in range(1, len(rec))]
                        vol = stdev(rets)*100 if len(rets)>1 else 2.0
                        sl, tp = calc_sl_tp(c, side, vol)
                        cost = qty * c * (1 + FEE_RATIO)
                        if cost <= cash:
                            cash -= cost
                            positions.append({"side": side, "entry": c, "qty": qty, "sl": sl, "tp": tp})
            except:
                pass
        
        equity.append(cash + sum((p["qty"]*c if p["side"]=="BUY" else p["qty"]*(2*p["entry"]-c)) for p in positions))
    
    for p in positions:
        pnl = (ohlc[-1]["close"]-p["entry"])*p["qty"] if p["side"]=="BUY" else (p["entry"]-ohlc[-1]["close"])*p["qty"]
        trades.append(pnl)
    
    if not equity or len(trades) < 3:
        return None
    
    final = equity[-1]
    ret = ((final - 10000) / 10000) * 100
    wins = [t for t in trades if t > 0]
    losses = [t for t in trades if t < 0]
    wr = len(wins)/len(trades)*100 if trades else 0
    
    peak = equity[0]
    max_dd = 0
    for v in equity:
        if v > peak: peak = v
        dd = (peak-v)/peak*100 if peak > 0 else 0
        if dd > max_dd: max_dd = dd
    
    rets = [equity[i]-equity[i-1] for i in range(1, len(equity))]
    sharpe = (mean(rets)/stdev(rets))*365**0.5 if len(rets)>1 and stdev(rets)>0 else 0
    
    bh = ((ohlc[-1]["close"]-ohlc[50]["close"])/ohlc[50]["close"])*100
    
    return {
        "strategy": name, "symbol": symbol,
        "return": round(ret, 2), "trades": len(trades),
        "win_rate": round(wr, 1), "max_dd": round(max_dd, 2),
        "sharpe": round(sharpe, 2), "benchmark": round(bh, 2),
        "beats_bh": ret >= bh,
    }


def run():
    _ensure_family_merged()
    strats = [(n, f) for n, f in ALL_STRATEGIES]
    
    print(f"\n{'='*70}")
    print(f"FAST MASS BACKTEST: {len(strats)} strategies x top 50 pairs")
    print(f"{'='*70}\n")
    
    pairs = fetch_top_50()
    if not pairs:
        print("Failed to fetch pairs")
        return
    
    # Cache OHLC data
    ohlc_cache = {}
    all_results = []
    strat_stats = {}
    
    for pair in pairs:
        sym = pair["symbol"]
        print(f"\n--- {sym} (${pair['volume_24h']/1e6:,.0f}M) ---")
        
        ohlc = fetch_binance_klines(sym, "1d", BACKTEST_BARS + 100)
        if not ohlc or len(ohlc) < 60:
            print(f"  Skipped ({len(ohlc) if ohlc else 0} bars)")
            continue
        
        print(f"  {len(ohlc)} bars loaded")
        
        for name, fn in strats:
            result = backtest_strategy(sym, name, fn, ohlc)
            if result and result["trades"] >= 3:
                all_results.append(result)
                
                if name not in strat_stats:
                    strat_stats[name] = {"rets": [], "wrs": [], "dds": [], "shs": [], "bh": 0, "n": 0}
                ss = strat_stats[name]
                ss["rets"].append(result["return"])
                ss["wrs"].append(result["win_rate"])
                ss["dds"].append(result["max_dd"])
                ss["shs"].append(result["sharpe"])
                ss["bh"] += 1 if result["beats_bh"] else 0
                ss["n"] += 1
                
                tag = "+" if result["return"] > 0 else "-"
                print(f"  {name:40s} {tag}{result['return']:+6.1f}% WR:{result['win_rate']:4.1f}% N:{result['trades']:3d}")
        
        time.sleep(0.05)
    
    # Report
    print(f"\n\n{'='*70}")
    print("STRATEGY RANKING (by avg return)")
    print(f"{'='*70}")
    print(f"{'Strategy':42s} {'AvgRet%':>7s} {'AvgWR%':>6s} {'AvgDD%':>6s} {'AvgSh':>6s} {'Pairs':>5s} {'BeatsBH':>7s}")
    print("-" * 80)
    
    ranked = sorted(strat_stats.items(), key=lambda x: mean(x[1]["rets"]), reverse=True)
    for name, ss in ranked:
        if ss["n"] == 0: continue
        print(f"  {name:40s} {mean(ss['rets']):+6.1f}% {mean(ss['wrs']):5.1f}% {mean(ss['dds']):5.1f}% {mean(ss['shs']):6.2f} {ss['n']:5d} {ss['bh']}/{ss['n']}")
    
    positive = [(n, s) for n, s in ranked if s["n"] > 0 and mean(s["rets"]) > 0]
    print(f"\n{'='*70}")
    print(f"POSITIVE EXPECTANCY: {len(positive)}/{len(strat_stats)} strategies")
    print(f"{'='*70}")
    for n, s in positive:
        print(f"  + {n:40s} {mean(s['rets']):+.1f}%")
    
    # Save
    report = {
        "time": datetime.now(timezone.utc).isoformat(),
        "pairs": len(pairs),
        "strategies": len(strats),
        "ranking": [{"strategy": n, "avg_return": round(mean(s["rets"]),2), "n": s["n"]} for n, s in ranked if s["n"]>0],
        "results": all_results,
    }
    out = RESULTS_DIR / f"fast_backtest_{datetime.now():%Y%m%d_%H%M%S}.json"
    with open(out, "w") as f:
        json.dump(report, f, indent=2)
    print(f"\nSaved to {out}")


if __name__ == "__main__":
    run()

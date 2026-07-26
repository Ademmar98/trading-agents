#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Minimal backtest: Test 10 core strategies on top 10 pairs (quick validation)
"""
import sys
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
from core.pricing import round_sig

# Import just the core strategies we know work
from core.strategies import (
    detect_sma_crossover, detect_ema_cross, detect_macd,
    detect_bollinger, detect_rsi_divergence, detect_atr_breakout,
    detect_stochastic_rsi, detect_engulfing, detect_pin_bar,
    detect_donchian
)

STRATEGIES = [
    ("SMA Crossover", detect_sma_crossover),
    ("EMA Cross 9/21", detect_ema_cross),
    ("MACD", detect_macd),
    ("Bollinger", detect_bollinger),
    ("RSI Divergence", detect_rsi_divergence),
    ("ATR Breakout", detect_atr_breakout),
    ("Stochastic RSI", detect_stochastic_rsi),
    ("Engulfing", detect_engulfing),
    ("Pin Bar", detect_pin_bar),
    ("Donchian", detect_donchian),
]

FEE_RATIO = 0.001
RESULTS_DIR = Path(__file__).parent / "mass_backtest_results"
RESULTS_DIR.mkdir(exist_ok=True)


def fetch_top_10():
    print("Fetching top 10 USDT pairs...")
    r = requests.get("https://api.binance.com/api/v3/ticker/24hr", timeout=15)
    data = r.json()
    usdt = [t for t in data if t["symbol"].endswith("USDT") and float(t.get("quoteVolume", 0)) > 0]
    usdt.sort(key=lambda t: float(t["quoteVolume"]), reverse=True)
    # Skip stablecoins
    skip = {"USDC", "USD1", "RLUSD", "FDUSD", "EUR", "XAUT", "PAXG", "SNDKB", "SPCXB"}
    top10 = []
    for t in usdt:
        sym = t["symbol"].replace("USDT", "/USD")
        base = sym.split("/")[0]
        if base in skip:
            continue
        vol = float(t["quoteVolume"])
        top10.append({"symbol": sym, "volume_24h": vol})
        print(f"  {sym:12s} vol=${vol/1e6:,.0f}M")
        if len(top10) >= 10:
            break
    return top10


def backtest(name, fn, ohlc):
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
            hit_sl = (s == "BUY" and l <= sl) or (s == "SELL" and h >= sl)
            hit_tp = (s == "BUY" and h >= tp) or (s == "SELL" and l <= tp)
            
            if hit_sl:
                pnl = (sl - e) * q if s == "BUY" else (e - sl) * q
                fee = q * sl * FEE_RATIO
                cash += q * sl - fee
                trades.append(pnl - fee - q * e * FEE_RATIO)
            elif hit_tp:
                pnl = (tp - e) * q if s == "BUY" else (e - tp) * q
                fee = q * tp * FEE_RATIO
                cash += q * tp - fee
                trades.append(pnl - fee - q * e * FEE_RATIO)
            else:
                new_pos.append(p)
        positions = new_pos
        
        if len(positions) < 3:
            try:
                sig = fn(ohlc[:i+1])
                if sig and sig.get("action") in ("BUY", "SELL"):
                    side = sig["action"]
                    qty = (cash * 0.15) / c
                    if qty > 0.001:
                        rec = [x["close"] for x in ohlc[max(0,i-20):i+1]]
                        rets = [(rec[j]-rec[j-1])/rec[j-1] for j in range(1, len(rec))]
                        vol = stdev(rets)*100 if len(rets)>1 else 2.0
                        vol = max(vol, 1.0)
                        sl_dist = vol * 1.5 / 100
                        tp_dist = vol * 2.0 / 100
                        sl = round_sig(c * (1 - sl_dist)) if side == "BUY" else round_sig(c * (1 + sl_dist))
                        tp = round_sig(c * (1 + tp_dist)) if side == "BUY" else round_sig(c * (1 - tp_dist))
                        cost = qty * c * (1 + FEE_RATIO)
                        if cost <= cash:
                            cash -= cost
                            positions.append({"side": side, "entry": c, "qty": qty, "sl": sl, "tp": tp})
            except:
                pass
        
        pos_val = sum((p["qty"]*c if p["side"]=="BUY" else p["qty"]*(2*p["entry"]-c)) for p in positions)
        equity.append(cash + pos_val)
    
    # Close remaining
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
        "strategy": name,
        "return": round(ret, 2),
        "trades": len(trades),
        "win_rate": round(wr, 1),
        "max_dd": round(max_dd, 2),
        "sharpe": round(sharpe, 2),
        "benchmark": round(bh, 2),
        "beats_bh": ret >= bh,
    }


def run():
    print(f"\n{'='*70}")
    print(f"QUICK BACKTEST: {len(STRATEGIES)} strategies x top 10 pairs")
    print(f"{'='*70}\n")
    
    pairs = fetch_top_10()
    if not pairs:
        print("Failed")
        return
    
    all_results = []
    strat_stats = {}
    
    for pair in pairs:
        sym = pair["symbol"]
        print(f"\n--- {sym} ---")
        
        ohlc = fetch_binance_klines(sym, "1d", 465)
        if not ohlc or len(ohlc) < 60:
            print(f"  Skipped")
            continue
        
        print(f"  {len(ohlc)} bars")
        
        for name, fn in STRATEGIES:
            result = backtest(name, fn, ohlc)
            if result and result["trades"] >= 3:
                all_results.append(result)
                
                if name not in strat_stats:
                    strat_stats[name] = {"rets": [], "wrs": [], "n": 0, "bh": 0}
                ss = strat_stats[name]
                ss["rets"].append(result["return"])
                ss["wrs"].append(result["win_rate"])
                ss["n"] += 1
                ss["bh"] += 1 if result["beats_bh"] else 0
                
                tag = "+" if result["return"] > 0 else "-"
                print(f"  {name:20s} {tag}{result['return']:+6.1f}% WR:{result['win_rate']:4.1f}%")
        
        time.sleep(0.05)
    
    # Report
    print(f"\n\n{'='*70}")
    print("RESULTS")
    print(f"{'='*70}")
    print(f"{'Strategy':22s} {'AvgRet%':>7s} {'AvgWR%':>6s} {'Pairs':>5s} {'BeatsBH':>7s}")
    print("-" * 55)
    
    ranked = sorted(strat_stats.items(), key=lambda x: mean(x[1]["rets"]), reverse=True)
    for name, ss in ranked:
        if ss["n"] == 0: continue
        print(f"  {name:20s} {mean(ss['rets']):+6.1f}% {mean(ss['wrs']):5.1f}% {ss['n']:5d} {ss['bh']}/{ss['n']}")
    
    positive = [(n, s) for n, s in ranked if s["n"] > 0 and mean(s["rets"]) > 0]
    print(f"\nPositive expectancy: {len(positive)}/{len(strat_stats)}")
    for n, s in positive:
        print(f"  + {n:20s} {mean(s['rets']):+.1f}%")
    
    out = RESULTS_DIR / f"quick_{datetime.now():%Y%m%d_%H%M%S}.json"
    with open(out, "w") as f:
        json.dump({"time": datetime.now(timezone.utc).isoformat(), "results": all_results}, f, indent=2)
    print(f"\nSaved to {out}")


if __name__ == "__main__":
    run()

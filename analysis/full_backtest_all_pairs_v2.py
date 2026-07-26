"""
FULL BACKTEST v2: Optimized for speed
"""
import sys, os, json, time, numpy as np
from datetime import datetime, timezone
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

BINANCE = "https://api.binance.com"
BINANCE_F = "https://fapi.binance.com"
CAPITAL = 10000
FEE = 0.15  # combined fee+spread per side
PERIODS = {"1W": 7, "1M": 30, "3M": 90, "6M": 180, "1Y": 365}


def get_pairs():
    r = requests.get(f"{BINANCE}/api/v3/ticker/24hr", timeout=30)
    tickers = r.json()
    skip_stable = {"USDC","USDT","BUSD","TUSD","FDUSD","DAI","USDP","USDN","EUR","GBP","BIDR","AUD","BRL","TRY","AEUR","UST"}
    pairs = []
    for t in tickers:
        s = t["symbol"]
        if not s.endswith("USDT"):
            continue
        base = s[:-4]
        if base in skip_stable or any(x in base for x in ["UP","DOWN","BULL","BEAR","3L","3S","5L","5S"]):
            continue
        vol = float(t.get("quoteVolume", 0))
        if vol < 100000:
            continue
        pairs.append({"symbol": base, "volume": vol})
    pairs.sort(key=lambda x: x["volume"], reverse=True)
    return pairs[:100]


def fetch_klines(sym, days):
    try:
        limit = min(days * 24, 1000)
        r = requests.get(f"{BINANCE}/api/v3/klines", params={"symbol": f"{sym}USDT", "interval": "1h", "limit": limit}, timeout=20)
        data = r.json()
        if not isinstance(data, list) or not data:
            return [], []
        closes = [float(k[4]) for k in data]
        timestamps = [int(k[0]) // 1000 for k in data]  # unix seconds
        return closes, timestamps
    except:
        return [], []


def fetch_funding(sym, days):
    try:
        end = int(time.time() * 1000)
        start = end - (days * 86400000)
        r = requests.get(f"{BINANCE_F}/fapi/v1/fundingRate", params={"symbol": f"{sym}USDT", "startTime": start, "endTime": end, "limit": 1000}, timeout=20)
        data = r.json()
        if not isinstance(data, list):
            return {}
        lookup = {}
        for fd in data:
            h = fd["fundingTime"] // 3600000
            lookup[h] = float(fd.get("fundingRate", 0))
        return lookup
    except:
        return {}


def backtest(closes, timestamps, funding):
    if len(closes) < 24:
        return None
    
    bal = CAPITAL
    pos = 0
    entry = 0
    trades = []
    
    for i in range(1, len(closes)):
        p = closes[i]
        ts_hour = timestamps[i] // 3600
        fr = funding.get(ts_hour, 0)
        
        if pos == 0 and fr < -0.0001:
            amt = bal * 0.95
            fee = amt * FEE / 100
            pos = (amt - fee) / p
            entry = p
            bal -= amt
        elif pos > 0:
            pnl_pct = (p - entry) / entry * 100
            if pnl_pct >= 10 or pnl_pct <= -5 or fr > 0.001:
                val = pos * p
                fee = val * FEE / 100
                pnl = (p - entry) * pos - fee
                bal += val - fee
                trades.append(pnl)
                pos = 0
    
    if pos > 0:
        bal += pos * closes[-1]
    
    net = bal - CAPITAL
    wr = 0
    if trades:
        wins = len([t for t in trades if t > 0])
        wr = wins / len(trades) * 100
    
    return {
        "ret": round((net / CAPITAL) * 100, 2),
        "n": len(trades),
        "wr": round(wr, 1)
    }


def test_symbol(sym):
    result = {"s": sym}
    
    closes_all, ts_all = fetch_klines(sym, 365)
    funding_all = fetch_funding(sym, 365)
    
    if not closes_all:
        for p in PERIODS:
            result[p] = {"ret": 0, "n": 0, "wr": 0}
        return result
    
    total_bars = len(closes_all)
    
    for pname, days in PERIODS.items():
        bars_needed = days * 24
        sl = min(bars_needed, total_bars)
        c = closes_all[-sl:]
        t = ts_all[-sl:]
        
        bt = backtest(c, t, funding_all)
        if bt:
            result[pname] = bt
        else:
            result[pname] = {"ret": 0, "n": 0, "wr": 0}
    
    return result


def main():
    print("="*95)
    print("FULL BACKTEST: All Binance Pairs x All Timeframes")
    print("="*95)
    print(f"Strategy: Halal spot-only | Capital: ${CAPITAL:,} | Fee: {FEE}%/side")
    print(f"Periods: 1W, 1M, 3M, 6M, 1Y\n")
    
    pairs = get_pairs()
    print(f"Testing {len(pairs)} pairs...\n")
    
    results = []
    with ThreadPoolExecutor(max_workers=5) as ex:
        futs = {ex.submit(test_symbol, p["symbol"]): p["symbol"] for p in pairs}
        done = 0
        for f in as_completed(futs):
            done += 1
            r = f.result()
            results.append(r)
            s = r["s"]
            y = r.get("1Y", {}).get("ret", 0)
            m = r.get("1M", {}).get("ret", 0)
            print(f"  [{done:3d}/{len(pairs)}] {s:<10} 1Y:{y:>7.2f}%  1M:{m:>7.2f}%")
    
    # Sort by 1Y
    results.sort(key=lambda x: x.get("1Y", {}).get("ret", -999), reverse=True)
    
    # ── Full Table ──
    print("\n" + "="*95)
    print(f"{'Symbol':<10} {'1W%':>8} {'Trades':>6} {'1M%':>8} {'Trades':>6} {'3M%':>8} {'Trades':>6} {'6M%':>8} {'Trades':>6} {'1Y%':>8} {'Trades':>6}")
    print("-"*95)
    
    for r in results:
        s = r["s"]
        cols = []
        for p in ["1W", "1M", "3M", "6M", "1Y"]:
            d = r.get(p, {})
            ret = d.get("ret", 0)
            n = d.get("n", 0)
            cols.append(f"{ret:>7.2f}% {n:>5}")
        print(f"{s:<10} {cols[0]:>14} {cols[1]:>14} {cols[2]:>14} {cols[3]:>14} {cols[4]:>14}")
    
    # ── Top 20 per period ──
    for p in ["1W", "1M", "3M", "6M", "1Y"]:
        print(f"\n{'='*95}")
        print(f"TOP 20 GAINERS - {p}")
        print(f"{'='*95}")
        print(f"{'#':<4} {'Symbol':<10} {'Return%':>10} {'Trades':>8} {'WinRate%':>10}")
        print("-"*45)
        
        pr = sorted([r for r in results if r.get(p, {}).get("n", 0) > 0], key=lambda x: x[p]["ret"], reverse=True)
        for i, r in enumerate(pr[:20], 1):
            d = r[p]
            print(f"{i:<4} {r['s']:<10} {d['ret']:>8.2f}%  {d['n']:>6}  {d['wr']:>8.1f}%")
    
    # ── Portfolio stats ──
    print(f"\n{'='*95}")
    print("PORTFOLIO STATS")
    print(f"{'='*95}")
    for p in PERIODS:
        data = [r[p] for r in results if r.get(p, {}).get("n", 0) > 0]
        if data:
            rets = [d["ret"] for d in data]
            pos = len([r for r in rets if r > 0])
            print(f"{p:>4}: {len(data):>3} traded | Avg: {np.mean(rets):>7.2f}% | Med: {np.median(rets):>7.2f}% | Positive: {pos}/{len(data)} ({pos/len(data)*100:.0f}%)")
    
    # ── Save ──
    out = Path(__file__).parent / "full_backtest_all_pairs.json"
    with open(out, "w") as f:
        json.dump({"ts": datetime.now(timezone.utc).isoformat(), "n": len(results), "results": results}, f, indent=2)
    print(f"\nSaved to {out}")


if __name__ == "__main__":
    main()

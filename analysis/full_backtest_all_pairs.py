"""
COMPREHENSIVE BACKTEST: All Pairs, All Timeframes
=================================================
Tests halal spot-only strategy across ALL Binance USDT pairs
for 5 time periods: 1 week, 1 month, 3 months, 6 months, 1 year.
"""

import sys
import os
import json
import time
import numpy as np
import pandas as pd
from datetime import datetime, timedelta, timezone
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests

# Fix Windows encoding
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

sys.path.insert(0, str(Path(__file__).parent.parent))

BINANCE_SPOT_BASE = "https://api.binance.com"
BINANCE_FUTURES_BASE = "https://fapi.binance.com"

INITIAL_CAPITAL = 10000
FEE_PCT = 0.1
SPREAD_PCT = 0.05

# Time periods
PERIODS = {
    "1W": 7,
    "1M": 30,
    "3M": 90,
    "6M": 180,
    "1Y": 365
}


def get_all_binance_usdt_pairs():
    """Get ALL USDT pairs from Binance"""
    try:
        r = requests.get(f"{BINANCE_SPOT_BASE}/api/v3/ticker/24hr", timeout=30)
        tickers = r.json()
        
        usdt_pairs = [t for t in tickers if t["symbol"].endswith("USDT")]
        usdt_pairs.sort(key=lambda x: float(x.get("quoteVolume", 0)), reverse=True)
        
        # Get top 150 by volume (skip stablecoins and leveraged tokens)
        skip = ["USDC", "USDT", "BUSD", "TUSD", "FDUSD", "DAI", "USDP", "USDN"]
        coins = []
        for t in usdt_pairs:
            sym = t["symbol"].replace("USDT", "")
            if sym in skip:
                continue
            if "UP" in sym or "DOWN" in sym or "BULL" in sym or "BEAR" in sym:
                continue
            if "3L" in sym or "3S" in sym or "5L" in sym or "5S" in sym:
                continue
            coins.append({
                "symbol": sym,
                "volume_24h": float(t.get("quoteVolume", 0)),
                "price": float(t.get("lastPrice", 0)),
                "change_24h": float(t.get("priceChangePercent", 0))
            })
            
        return coins[:150]
    except Exception as e:
        print(f"Error: {e}")
        return []


def fetch_binance_klines(symbol, interval, days):
    """Fetch klines with retry"""
    bsym = f"{symbol}USDT"
    for attempt in range(2):
        try:
            limit = min(days * 24, 1000)
            r = requests.get(
                f"{BINANCE_SPOT_BASE}/api/v3/klines",
                params={"symbol": bsym, "interval": interval, "limit": limit},
                timeout=30
            )
            data = r.json()
            if not data or not isinstance(data, list):
                return []
            bars = []
            for k in data:
                bars.append({
                    "ts": k[0] // 1000,
                    "open": float(k[1]),
                    "high": float(k[2]),
                    "low": float(k[3]),
                    "close": float(k[4]),
                    "volume": float(k[5])
                })
            return bars
        except:
            if attempt < 1:
                time.sleep(1)
    return []


def fetch_funding_rates(symbol, days):
    """Fetch funding rates"""
    bsym = f"{symbol}USDT"
    try:
        end_time = int(time.time() * 1000)
        start_time = end_time - (days * 24 * 60 * 60 * 1000)
        all_rates = []
        current_start = start_time
        while current_start < end_time:
            r = requests.get(
                f"{BINANCE_FUTURES_BASE}/fapi/v1/fundingRate",
                params={"symbol": bsym, "startTime": current_start, "endTime": end_time, "limit": 1000},
                timeout=30
            )
            data = r.json()
            if not data or isinstance(data, dict):
                break
            all_rates.extend(data)
            if len(data) < 1000:
                break
            current_start = data[-1]["fundingTime"] + 1
            time.sleep(0.05)
        return all_rates
    except:
        return []


def halal_backtest(prices, funding_data, capital):
    """Halal spot-only backtest"""
    if not prices or len(prices) < 10:
        return None
    
    balance = capital
    position_qty = 0
    entry_price = 0
    total_fees = 0
    trades = []
    
    funding_by_hour = {}
    for fd in funding_data:
        hour = fd.get("fundingTime", 0) // (3600 * 1000)
        rate = float(fd.get("fundingRate", 0))
        funding_by_hour[hour] = rate
    
    for i in range(1, len(prices)):
        price = prices[i]["close"]
        ts_hour = prices[i]["ts"] // 3600
        funding_rate = funding_by_hour.get(ts_hour, 0)
        
        if position_qty == 0 and funding_rate < -0.0001:
            buy_amount = balance * 0.95
            fee = buy_amount * (FEE_PCT + SPREAD_PCT) / 100
            total_fees += fee
            position_qty = (buy_amount - fee) / price
            entry_price = price
            balance -= buy_amount
        elif position_qty > 0:
            pnl_pct = (price - entry_price) / entry_price * 100
            if pnl_pct >= 10:
                sell_value = position_qty * price
                fee = sell_value * (FEE_PCT + SPREAD_PCT) / 100
                total_fees += fee
                pnl = (price - entry_price) * position_qty - fee
                balance += sell_value - fee
                trades.append({"pnl": pnl, "pnl_pct": pnl_pct, "type": "tp"})
                position_qty = 0
            elif pnl_pct <= -5:
                sell_value = position_qty * price
                fee = sell_value * (FEE_PCT + SPREAD_PCT) / 100
                total_fees += fee
                pnl = (price - entry_price) * position_qty - fee
                balance += sell_value - fee
                trades.append({"pnl": pnl, "pnl_pct": pnl_pct, "type": "sl"})
                position_qty = 0
            elif funding_rate > 0.001:
                sell_value = position_qty * price
                fee = sell_value * (FEE_PCT + SPREAD_PCT) / 100
                total_fees += fee
                pnl = (price - entry_price) * position_qty - fee
                balance += sell_value - fee
                trades.append({"pnl": pnl, "pnl_pct": pnl_pct, "type": "funding"})
                position_qty = 0
    
    if position_qty > 0:
        balance += position_qty * prices[-1]["close"]
    
    net = balance - capital
    if trades:
        pnls = [t["pnl"] for t in trades]
        wins = len([p for p in pnls if p > 0])
        win_rate = wins / len(pnls) * 100
    else:
        win_rate = 0
    
    return {
        "return_pct": round((net / capital) * 100, 2),
        "trades": len(trades),
        "win_rate": round(win_rate, 1),
        "net_pnl": round(net, 2)
    }


def process_symbol_all_periods(args):
    """Worker: backtest one symbol across all periods"""
    symbol = args
    
    results = {"symbol": symbol}
    
    for period_name, days in PERIODS.items():
        try:
            prices = fetch_binance_klines(symbol, "1h", days)
            funding = fetch_funding_rates(symbol, days)
            bt = halal_backtest(prices, funding, INITIAL_CAPITAL)
            
            if bt:
                results[period_name] = bt
            else:
                results[period_name] = {"error": "no_data"}
        except:
            results[period_name] = {"error": "exception"}
    
    return results


def main():
    print("\n" + "="*90)
    print("COMPREHENSIVE BACKTEST: All Binance Pairs, All Timeframes")
    print("="*90)
    print(f"Strategy: Halal spot-only (buy when funding negative)")
    print(f"Capital: ${INITIAL_CAPITAL:,.2f}")
    print(f"Periods: 1W, 1M, 3M, 6M, 1Y")
    print("="*90 + "\n")
    
    # Get all pairs
    pairs = get_all_binance_usdt_pairs()
    print(f"Found {len(pairs)} pairs to test\n")
    
    # Run backtests
    all_results = []
    
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {executor.submit(process_symbol_all_periods, p["symbol"]): p["symbol"] for p in pairs}
        
        completed = 0
        for future in as_completed(futures):
            completed += 1
            try:
                result = future.result()
                all_results.append(result)
                
                sym = result["symbol"]
                ret_1y = result.get("1Y", {}).get("return_pct", "N/A")
                ret_1m = result.get("1M", {}).get("return_pct", "N/A")
                
                ret_str = f"1Y:{ret_1y:>7}%  1M:{ret_1m:>7}%"
                print(f"  [{completed}/{len(pairs)}] {sym:<10} {ret_str}")
            except Exception as e:
                print(f"  [{completed}/{len(pairs)}] {futures[future]}: ERROR")
    
    # ── Build Summary Table ──
    print("\n" + "="*90)
    print("FULL RESULTS TABLE (sorted by 1Y return)")
    print("="*90)
    
    # Filter successful
    successful = []
    for r in all_results:
        if any("return_pct" in r.get(p, {}) for p in PERIODS):
            successful.append(r)
    
    # Sort by 1Y return
    successful.sort(key=lambda x: x.get("1Y", {}).get("return_pct", -999), reverse=True)
    
    # Header
    print(f"\n{'Symbol':<10} {'1W%':>8} {'1M%':>8} {'3M%':>8} {'6M%':>8} {'1Y%':>8} {'1Y Trades':>10} {'1Y WR%':>8}")
    print("-"*90)
    
    for r in successful:
        sym = r["symbol"]
        vals = []
        for period in ["1W", "1M", "3M", "6M", "1Y"]:
            data = r.get(period, {})
            if "return_pct" in data:
                vals.append(f"{data['return_pct']:>7.2f}%")
            else:
                vals.append("    N/A")
        
        y1 = r.get("1Y", {})
        trades = y1.get("trades", 0)
        wr = y1.get("win_rate", 0)
        
        print(f"{sym:<10} {vals[0]:>8} {vals[1]:>8} {vals[2]:>8} {vals[3]:>8} {vals[4]:>8} {trades:>10} {wr:>7.1f}%")
    
    # ── Top Performers by Period ──
    for period in PERIODS:
        print(f"\n{'='*90}")
        print(f"TOP 15 GAINERS - {period}")
        print(f"{'='*90}")
        
        period_sorted = [r for r in successful if "return_pct" in r.get(period, {})]
        period_sorted.sort(key=lambda x: x[period]["return_pct"], reverse=True)
        
        print(f"{'Rank':<6} {'Symbol':<10} {'Return%':>10} {'Trades':>10} {'WinRate%':>10}")
        print("-"*50)
        
        for i, r in enumerate(period_sorted[:15], 1):
            d = r[period]
            print(f"{i:<6} {r['symbol']:<10} {d['return_pct']:>8.2f}%  {d['trades']:>8}  {d['win_rate']:>8.1f}%")
    
    # ── Portfolio Stats ──
    print(f"\n{'='*90}")
    print("PORTFOLIO STATS BY PERIOD")
    print(f"{'='*90}")
    
    for period in PERIODS:
        period_data = [r[period] for r in successful if "return_pct" in r.get(period, {})]
        if period_data:
            returns = [d["return_pct"] for d in period_data]
            positive = len([r for r in returns if r > 0])
            print(f"\n{period}: {len(period_data)} pairs | Avg: {np.mean(returns):.2f}% | Median: {np.median(returns):.2f}% | Positive: {positive} ({positive/len(period_data)*100:.0f}%)")
    
    # ── Save Full Results ──
    output_path = Path(__file__).parent / "full_backtest_all_pairs.json"
    with open(output_path, "w") as f:
        json.dump({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "config": {
                "strategy": "halal_spot_only",
                "capital": INITIAL_CAPITAL,
                "periods": PERIODS
            },
            "total_pairs": len(successful),
            "results": successful
        }, f, indent=2)
    
    print(f"\nFull results saved to: {output_path}")


if __name__ == "__main__":
    main()

"""
HALAL SPOT-ONLY BACKTEST: Top 20 Pairs, 1 Year + Extended Pool
==============================================================
Tests the winning strategy on:
1. Top 20 pairs for 365 days
2. Extended pool: adds gainers, losers, new listings, volatility plays
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
BACKTEST_DAYS = 365

# Top 20 from previous backtests
CORE_20 = [
    "SYN", "XLM", "VANRY", "ONDO", "ZAMA",
    "BANK", "KAITO", "POL", "DASH", "ASTER",
    "PUMP", "INJ", "ENA", "OP", "XRP",
    "ADA", "SKL", "BCH", "VIRTUAL", "PENGU"
]

# Extended: gainers, high-vol, new listings, momentum
EXTENDED_ADDITIONS = [
    # High gainers (from 30-day)
    "MUB", "LA", "WLFI", "TRX", "AVAX",
    # High volatility / momentum
    "DOGE", "SHIB", "PEPE", "FLOKI", "WIF",
    "BONK", "TURBO", "MOG", "MYRO", "BRETT",
    # Newer listings (high upside potential)
    "TSLAB", "CRCLB", "GOOGLB", "INTCB", "SOXLB",
    "SNDKB", "EUR", "XAUT", "PAXG", "USDC",
    # DeFi / Layer 1
    "AAVE", "UNI", "LINK", "LDO", "RPL",
    "SOL", "AVAX", "DOT", "MATIC", "ATOM",
    "NEAR", "FIL", "ICP", "APT", "SUI",
    # AI / Narrative
    "FET", "RENDER", "WLD", "TAO", "ARKM",
    # Meme / Culture
    "TRUMP", "BONK", "WIF", "FLOKI", "TURBO",
]

# Remove duplicates and core from extended
EXTENDED = list(dict.fromkeys([p for p in EXTENDED_ADDITIONS if p not in CORE_20]))


def get_all_binance_usdt_pairs():
    """Get ALL USDT pairs from Binance"""
    try:
        r = requests.get(f"{BINANCE_SPOT_BASE}/api/v3/ticker/24hr", timeout=30)
        tickers = r.json()
        
        usdt_pairs = [t for t in tickers if t["symbol"].endswith("USDT")]
        usdt_pairs.sort(key=lambda x: float(x.get("quoteVolume", 0)), reverse=True)
        
        # Get top 200 by volume
        coins = []
        for t in usdt_pairs[:200]:
            sym = t["symbol"].replace("USDT", "")
            coins.append({
                "symbol": sym,
                "volume_24h": float(t.get("quoteVolume", 0)),
                "price": float(t.get("lastPrice", 0)),
                "change_24h": float(t.get("priceChangePercent", 0))
            })
        return coins
    except Exception as e:
        print(f"Error: {e}")
        return []


def fetch_binance_klines(symbol: str, interval: str, days: int):
    """Fetch klines from Binance with retry"""
    bsym = f"{symbol}USDT"
    for attempt in range(3):
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
        except Exception as e:
            if attempt < 2:
                time.sleep(2)
            continue
    return []


def fetch_funding_rates(symbol: str, days: int):
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
                params={
                    "symbol": bsym,
                    "startTime": current_start,
                    "endTime": end_time,
                    "limit": 1000
                },
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
    except Exception:
        return []


def halal_spot_backtest(symbol: str, prices: list, funding_data: list, capital: float):
    """Halal-compliant spot-only backtest"""
    
    if not prices or len(prices) < 24:
        return {"symbol": symbol, "error": "insufficient_data"}
    
    balance = capital
    position_qty = 0
    entry_price = 0
    total_fees = 0
    trades = []
    
    # Funding lookup
    funding_by_hour = {}
    for fd in funding_data:
        hour = fd.get("fundingTime", 0) // (3600 * 1000)
        rate = float(fd.get("fundingRate", 0))
        funding_by_hour[hour] = rate
    
    # Simulate
    for i in range(1, len(prices)):
        price = prices[i]["close"]
        ts_hour = prices[i]["ts"] // 3600
        funding_rate = funding_by_hour.get(ts_hour, 0)
        
        # Buy: funding negative
        if position_qty == 0 and funding_rate < -0.0001:
            buy_amount = balance * 0.95
            fee = buy_amount * (FEE_PCT + SPREAD_PCT) / 100
            total_fees += fee
            position_qty = (buy_amount - fee) / price
            entry_price = price
            balance -= buy_amount
            
        elif position_qty > 0:
            pnl_pct = (price - entry_price) / entry_price * 100
            
            # TP +10%
            if pnl_pct >= 10:
                sell_value = position_qty * price
                fee = sell_value * (FEE_PCT + SPREAD_PCT) / 100
                total_fees += fee
                pnl = (price - entry_price) * position_qty - fee
                balance += sell_value - fee
                trades.append({"entry": entry_price, "exit": price, "pnl": pnl, "pnl_pct": pnl_pct, "type": "tp"})
                position_qty = 0
                
            # SL -5%
            elif pnl_pct <= -5:
                sell_value = position_qty * price
                fee = sell_value * (FEE_PCT + SPREAD_PCT) / 100
                total_fees += fee
                pnl = (price - entry_price) * position_qty - fee
                balance += sell_value - fee
                trades.append({"entry": entry_price, "exit": price, "pnl": pnl, "pnl_pct": pnl_pct, "type": "sl"})
                position_qty = 0
                
            # Funding exit
            elif funding_rate > 0.001:
                sell_value = position_qty * price
                fee = sell_value * (FEE_PCT + SPREAD_PCT) / 100
                total_fees += fee
                pnl = (price - entry_price) * position_qty - fee
                balance += sell_value - fee
                trades.append({"entry": entry_price, "exit": price, "pnl": pnl, "pnl_pct": pnl_pct, "type": "funding_exit"})
                position_qty = 0
    
    # Mark to market
    if position_qty > 0:
        balance += position_qty * prices[-1]["close"]
        
    final = balance
    net = final - capital
    
    # Metrics
    if trades:
        pnls = [t["pnl"] for t in trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        win_rate = len(wins) / len(pnls) * 100
        avg_win = np.mean(wins) if wins else 0
        avg_loss = np.mean(losses) if losses else 0
        pf = abs(sum(wins) / sum(losses)) if losses else float('inf')
    else:
        win_rate = avg_win = avg_loss = pf = 0
        
    return {
        "symbol": symbol,
        "final_balance": round(final, 2),
        "total_return_pct": round((net / capital) * 100, 2),
        "total_trades": len(trades),
        "win_rate": round(win_rate, 1),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "profit_factor": round(pf, 2),
        "total_fees": round(total_fees, 2),
        "net_pnl": round(net, 2)
    }


def process_symbol(args):
    """Worker"""
    symbol, days, capital = args
    prices = fetch_binance_klines(symbol, "1h", days)
    funding = fetch_funding_rates(symbol, days)
    return halal_spot_backtest(symbol, prices, funding, capital)


def run_backtest(name, symbols, days, capital):
    """Run a full backtest suite"""
    print(f"\n{'='*70}")
    print(f"BACKTEST: {name}")
    print(f"{'='*70}")
    print(f"Period: {days} days | Capital per pair: ${capital:,.2f} | Pairs: {len(symbols)}")
    print("="*70 + "\n")
    
    results = []
    
    with ThreadPoolExecutor(max_workers=5) as executor:
        args_list = [(s, days, capital) for s in symbols]
        futures = {executor.submit(process_symbol, args): args[0] for args in args_list}
        
        completed = 0
        for future in as_completed(futures):
            completed += 1
            try:
                result = future.result()
                results.append(result)
                
                sym = futures[future]
                if "error" not in result:
                    status = "+" if result["total_return_pct"] > 0 else ""
                    print(f"  [{completed}/{len(symbols)}] {sym}: {status}{result['total_return_pct']:.2f}% | {result['total_trades']} trades | WR: {result['win_rate']:.0f}%")
                else:
                    print(f"  [{completed}/{len(symbols)}] {sym}: {result['error']}")
                    
            except Exception as e:
                print(f"  [{completed}/{len(symbols)}] {futures[future]}: ERROR")
    
    return results


def analyze_results(results, name):
    """Analyze and print results"""
    successful = [r for r in results if "error" not in r]
    
    if not successful:
        print(f"\nNo successful backtests for {name}")
        return {}
        
    # Sort
    successful.sort(key=lambda x: x["total_return_pct"], reverse=True)
    
    # Stats
    returns = [r["total_return_pct"] for r in successful]
    positive = [r for r in successful if r["total_return_pct"] > 0]
    negative = [r for r in successful if r["total_return_pct"] < 0]
    
    print(f"\n{name} RESULTS")
    print("="*70)
    
    # Top 15
    print("\nTOP 15 PERFORMERS")
    print("-"*70)
    print(f"{'Symbol':<10} {'Return%':<10} {'Trades':<8} {'WinRate':<10} {'PF':<8}")
    print("-"*70)
    for r in successful[:15]:
        pf_str = f"{r['profit_factor']:.2f}" if r['profit_factor'] < 100 else "inf"
        print(f"{r['symbol']:<10} {r['total_return_pct']:>8.2f}%  {r['total_trades']:>6}  {r['win_rate']:>8.1f}%  {pf_str:>8}")
        
    # Bottom 5
    print("\nBOTTOM 5")
    print("-"*70)
    for r in successful[-5:]:
        print(f"{r['symbol']:<10} {r['total_return_pct']:>8.2f}%  {r['total_trades']:>6}  {r['win_rate']:>8.1f}%")
        
    # Stats
    print("\nSTATISTICS")
    print("-"*70)
    print(f"Pairs Traded: {len(successful)}")
    print(f"Average Return: {np.mean(returns):.2f}%")
    print(f"Median Return: {np.median(returns):.2f}%")
    print(f"Std Dev: {np.std(returns):.2f}%")
    print(f"Min: {min(returns):.2f}%")
    print(f"Max: {max(returns):.2f}%")
    print(f"Positive: {len(positive)} ({len(positive)/len(successful)*100:.0f}%)")
    print(f"Negative: {len(negative)} ({len(negative)/len(successful)*100:.0f}%)")
    
    # Portfolios
    total_pnl = sum(r["net_pnl"] for r in successful)
    total_deployed = INITIAL_CAPITAL * len(successful)
    
    print("\nEQUAL-WEIGHT PORTFOLIO")
    print("-"*70)
    print(f"Deployed: ${total_deployed:,.2f}")
    print(f"PnL: ${total_pnl:,.2f}")
    print(f"Return: {(total_pnl / total_deployed) * 100:.2f}%")
    
    # Top 10
    top10 = successful[:10]
    top10_pnl = sum(r["net_pnl"] for r in top10)
    print(f"\nTOP 10 PORTFOLIO")
    print("-"*70)
    print(f"Deployed: ${INITIAL_CAPITAL * 10:,.2f}")
    print(f"PnL: ${top10_pnl:,.2f}")
    print(f"Return: {(top10_pnl / (INITIAL_CAPITAL * 10)) * 100:.2f}%")
    
    # Top 5
    top5 = successful[:5]
    top5_pnl = sum(r["net_pnl"] for r in top5)
    print(f"\nTOP 5 PORTFOLIO")
    print("-"*70)
    print(f"Deployed: ${INITIAL_CAPITAL * 5:,.2f}")
    print(f"PnL: ${top5_pnl:,.2f}")
    print(f"Return: {(top5_pnl / (INITIAL_CAPITAL * 5)) * 100:.2f}%")
    
    return {
        "name": name,
        "pairs_tested": len(successful),
        "avg_return": np.mean(returns),
        "median_return": np.median(returns),
        "positive_pairs": len(positive),
        "negative_pairs": len(negative),
        "total_pnl": total_pnl,
        "portfolio_return": (total_pnl / total_deployed) * 100,
        "top10_return": (top10_pnl / (INITIAL_CAPITAL * 10)) * 100,
        "top5_return": (top5_pnl / (INITIAL_CAPITAL * 5)) * 100,
        "results": successful
    }


def main():
    print("\n" + "="*70)
    print("HALAL SPOT-ONLY BACKTEST: 1-Year + Extended Pool")
    print("="*70)
    print(f"Strategy: Buy-only, no leverage, no margin")
    print(f"Capital per pair: ${INITIAL_CAPITAL:,.2f}")
    print(f"Period: {BACKTEST_DAYS} days")
    print("="*70)
    
    # Fetch all Binance pairs for extended test
    print("\nFetching all Binance USDT pairs...")
    all_pairs = get_all_binance_usdt_pairs()
    all_symbols = [p["symbol"] for p in all_pairs[:150]]
    print(f"Found {len(all_symbols)} pairs")
    
    # ── TEST 1: Core 20, 1 Year ──
    results_core = run_backtest("Core 20 - 1 Year", CORE_20, BACKTEST_DAYS, INITIAL_CAPITAL)
    core_analysis = analyze_results(results_core, "Core 20 - 1 Year")
    
    # ── TEST 2: Extended (40 pairs), 1 Year ──
    extended_symbols = CORE_20 + EXTENDED
    results_extended = run_backtest("Extended 40 - 1 Year", extended_symbols, BACKTEST_DAYS, INITIAL_CAPITAL)
    ext_analysis = analyze_results(results_extended, "Extended 40 - 1 Year")
    
    # ── TEST 3: All Binance Pairs (Top 100), 1 Year ──
    # Use the pairs that exist in Binance
    all_test = list(dict.fromkeys(all_symbols))[:100]
    results_all = run_backtest("All Binance Top 100 - 1 Year", all_test, BACKTEST_DAYS, INITIAL_CAPITAL)
    all_analysis = analyze_results(results_all, "All Binance Top 100 - 1 Year")
    
    # ── COMPARISON ──
    print("\n" + "="*70)
    print("COMPARISON: ALL TESTS")
    print("="*70)
    
    for analysis in [core_analysis, ext_analysis, all_analysis]:
        if analysis:
            print(f"\n{analysis['name']}:")
            print(f"  Pairs: {analysis['pairs_tested']} | Positive: {analysis['positive_pairs']} | Return: {analysis['portfolio_return']:.2f}%")
            print(f"  Top 10: {analysis['top10_return']:.2f}% | Top 5: {analysis['top5_return']:.2f}%")
    
    # ── Save All Results ──
    output = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "tests": []
    }
    
    for analysis in [core_analysis, ext_analysis, all_analysis]:
        if analysis:
            output["tests"].append({
                "name": analysis["name"],
                "pairs_tested": analysis["pairs_tested"],
                "avg_return": analysis["avg_return"],
                "portfolio_return": analysis["portfolio_return"],
                "top10_return": analysis["top10_return"],
                "top5_return": analysis["top5_return"],
                "results": analysis["results"]
            })
    
    output_path = Path(__file__).parent / "halal_1year_extended.json"
    with open(output_path, "w") as f:
        json.dump(output, f, indent=2)
        
    print(f"\nResults saved to: {output_path}")


if __name__ == "__main__":
    main()

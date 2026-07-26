"""
FUNDING RATE BACKTEST: Top 50 Crypto Pairs
==========================================
Backtests delta-neutral funding rate capture across top volume pairs.
Uses real Binance funding rate history where available.
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

BINANCE_FUTURES_BASE = "https://fapi.binance.com"
BINANCE_SPOT_BASE = "https://api.binance.com"
INITIAL_CAPITAL = 10000
FEE_PCT = 0.1
SPREAD_PCT = 0.05


def get_top_50_by_volume():
    """Get top 50 crypto pairs by 24h volume"""
    print("Fetching top 50 pairs by volume...")
    try:
        r = requests.get(f"{BINANCE_SPOT_BASE}/api/v3/ticker/24hr", timeout=15)
        tickers = r.json()
        
        # Filter USDT pairs and sort by quote volume
        usdt_pairs = [t for t in tickers if t["symbol"].endswith("USDT")]
        usdt_pairs.sort(key=lambda x: float(x.get("quoteVolume", 0)), reverse=True)
        
        top_50 = []
        for t in usdt_pairs[:50]:
            top_50.append({
                "symbol": t["symbol"],
                "name": t["symbol"].replace("USDT", ""),
                "volume_24h": float(t.get("quoteVolume", 0)),
                "price": float(t.get("lastPrice", 0)),
                "change_24h": float(t.get("priceChangePercent", 0))
            })
            
        return top_50
    except Exception as e:
        print(f"Error fetching volume data: {e}")
        return []


def fetch_funding_history(symbol: str, days: int = 365) -> list:
    """Fetch historical funding rates from Binance Futures"""
    try:
        end_time = int(time.time() * 1000)
        start_time = end_time - (days * 24 * 60 * 60 * 1000)
        
        all_rates = []
        current_start = start_time
        
        while current_start < end_time:
            r = requests.get(
                f"{BINANCE_FUTURES_BASE}/fapi/v1/fundingRate",
                params={
                    "symbol": symbol,
                    "startTime": current_start,
                    "endTime": end_time,
                    "limit": 1000
                },
                timeout=15
            )
            
            data = r.json()
            if not data or isinstance(data, dict):
                break
                
            all_rates.extend(data)
            
            if len(data) < 1000:
                break
                
            current_start = data[-1]["fundingTime"] + 1
            time.sleep(0.1)  # Rate limit
            
        return all_rates
    except Exception as e:
        print(f"  Error fetching {symbol}: {e}")
        return []


def backtest_funding_rate(symbol: str, funding_data: list, capital: float = INITIAL_CAPITAL):
    """Backtest funding rate capture for a single symbol"""
    if not funding_data or len(funding_data) < 10:
        return {
            "symbol": symbol,
            "error": "insufficient_data",
            "data_points": len(funding_data)
        }
    
    balance = capital
    deployed = 0
    total_funding = 0
    total_costs = 0
    funding_events = []
    
    # Simulate delta-neutral position
    for i, entry in enumerate(funding_data):
        rate = float(entry.get("fundingRate", 0))
        
        # Deploy capital on first period
        if deployed == 0 and balance > 1000:
            deployed = balance * 0.90
            cost = deployed * (FEE_PCT + SPREAD_PCT) / 100
            total_costs += cost
            balance -= deployed
            
        # Collect funding (3x daily = every 8 hours)
        if deployed > 0:
            income = deployed * rate
            total_funding += income
            balance += income
            
            funding_events.append({
                "time": entry.get("fundingTime", 0),
                "rate": rate,
                "income": income,
                "cumulative": total_funding
            })
            
            # Monthly rebalance
            if i > 0 and i % 90 == 0:  # ~30 days
                # Take profit and re-enter
                exit_cost = deployed * (FEE_PCT + SPREAD_PCT) / 100
                total_costs += exit_cost
                balance += deployed
                deployed = 0
                
    # Final unwind
    if deployed > 0:
        exit_cost = deployed * (FEE_PCT + SPREAD_PCT) / 100
        total_costs += exit_cost
        balance += deployed
        
    final = balance
    net = final - capital
    
    # Calculate metrics
    rates = [e["rate"] for e in funding_events]
    
    result = {
        "symbol": symbol,
        "data_points": len(funding_data),
        "final_balance": round(final, 2),
        "total_return_pct": round((net / capital) * 100, 2),
        "total_funding": round(total_funding, 2),
        "total_costs": round(total_costs, 2),
        "net_profit": round(net, 2),
        "avg_funding_rate": round(np.mean(rates) * 100, 4) if rates else 0,
        "median_funding_rate": round(np.median(rates) * 100, 4) if rates else 0,
        "positive_rate_pct": round(sum(1 for r in rates if r > 0) / len(rates) * 100, 1) if rates else 0,
        "max_funding_rate": round(max(rates) * 100, 4) if rates else 0,
        "min_funding_rate": round(min(rates) * 100, 4) if rates else 0,
        "annualized_yield_pct": round((net / capital) * (365 * 3 / max(1, len(funding_data) / 3)) * 100, 2),
        "num_funding_periods": len(funding_events)
    }
    
    return result


def process_symbol(args):
    """Worker function for parallel processing"""
    symbol, days = args
    
    funding_data = fetch_funding_history(symbol, days)
    
    if not funding_data:
        return {
            "symbol": symbol,
            "error": "no_data",
            "data_points": 0
        }
        
    return backtest_funding_rate(symbol, funding_data)


def main():
    print("\n" + "="*70)
    print("FUNDING RATE CAPTURE BACKTEST: Top 50 Crypto Pairs")
    print("="*70)
    print(f"Capital per pair: ${INITIAL_CAPITAL:,.2f}")
    print(f"Fees: {FEE_PCT}% + {SPREAD_PCT}% spread")
    print(f"Backtest period: 365 days")
    print("="*70 + "\n")
    
    # Get top 50 pairs
    pairs = get_top_50_by_volume()
    
    if not pairs:
        print("Failed to fetch pairs. Using default list.")
        # Fallback list
        pairs = [
            {"symbol": "BTCUSDT", "name": "BTC"},
            {"symbol": "ETHUSDT", "name": "ETH"},
            {"symbol": "BNBUSDT", "name": "BNB"},
            {"symbol": "SOLUSDT", "name": "SOL"},
            {"symbol": "XRPUSDT", "name": "XRP"},
            {"symbol": "DOGEUSDT", "name": "DOGE"},
            {"symbol": "ADAUSDT", "name": "ADA"},
            {"symbol": "AVAXUSDT", "name": "AVAX"},
            {"symbol": "LINKUSDT", "name": "LINK"},
            {"symbol": "DOTUSDT", "name": "DOT"},
        ]
        
    print(f"Found {len(pairs)} pairs to test\n")
    
    # Process all symbols
    results = []
    
    # Use ThreadPoolExecutor for parallel fetching
    with ThreadPoolExecutor(max_workers=5) as executor:
        args_list = [(p["symbol"], 365) for p in pairs]
        futures = {executor.submit(process_symbol, args): args[0] for args in args_list}
        
        for future in as_completed(futures):
            try:
                result = future.result()
                results.append(result)
                
                # Print progress
                if "error" not in result:
                    print(f"  OK {result['symbol']}: {result['total_return_pct']:.2f}% return")
                else:
                    print(f"  -- {result['symbol']}: {result['error']}")
                    
            except Exception as e:
                print(f"  ERR {futures[future]}: {e}")
                
    # Sort by return
    results.sort(key=lambda x: x.get("total_return_pct", 0), reverse=True)
    
    # ── Summary ──
    print("\n" + "="*70)
    print("RESULTS: Top 50 Funding Rate Backtest")
    print("="*70)
    
    # Filter successful results
    successful = [r for r in results if "error" not in r]
    failed = [r for r in results if "error" in r]
    
    print(f"\nSuccessful backtests: {len(successful)}")
    print(f"Failed/No data: {len(failed)}")
    
    if successful:
        # Top performers
        print("\n" + "-"*70)
        print("TOP 10 PERFORMERS")
        print("-"*70)
        print(f"{'Symbol':<12} {'Return%':<10} {'Funding$':<12} {'AvgRate%':<10} {'PosRate%':<10}")
        print("-"*70)
        
        for r in successful[:10]:
            print(f"{r['symbol']:<12} {r['total_return_pct']:>8.2f}  ${r['total_funding']:>10.2f}  {r['avg_funding_rate']:>8.4f}  {r['positive_rate_pct']:>8.1f}")
            
        # Bottom performers
        print("\n" + "-"*70)
        print("BOTTOM 10 PERFORMERS")
        print("-"*70)
        print(f"{'Symbol':<12} {'Return%':<10} {'Funding$':<12} {'AvgRate%':<10}")
        print("-"*70)
        
        for r in successful[-10:]:
            print(f"{r['symbol']:<12} {r['total_return_pct']:>8.2f}  ${r['total_funding']:>10.2f}  {r['avg_funding_rate']:>8.4f}")
            
        # Statistics
        returns = [r["total_return_pct"] for r in successful]
        
        print("\n" + "-"*70)
        print("PORTFOLIO STATISTICS")
        print("-"*70)
        print(f"Average Return: {np.mean(returns):.2f}%")
        print(f"Median Return: {np.median(returns):.2f}%")
        print(f"Std Dev: {np.std(returns):.2f}%")
        print(f"Min Return: {min(returns):.2f}%")
        print(f"Max Return: {max(returns):.2f}%")
        
        # Positive vs negative
        positive = [r for r in successful if r["total_return_pct"] > 0]
        negative = [r for r in successful if r["total_return_pct"] < 0]
        print(f"\nPositive: {len(positive)} pairs ({len(positive)/len(successful)*100:.0f}%)")
        print(f"Negative: {len(negative)} pairs ({len(negative)/len(successful)*100:.0f}%)")
        
        # Portfolio simulation
        print("\n" + "-"*70)
        print("PORTFOLIO SIMULATION")
        print("-"*70)
        print(f"Capital per pair: ${INITIAL_CAPITAL:,.2f}")
        print(f"Total pairs tested: {len(successful)}")
        
        # Equal-weight portfolio
        total_deployed = INITIAL_CAPITAL * len(successful)
        avg_return = np.mean(returns) / 100
        portfolio_pnl = sum(r["net_profit"] for r in successful)
        
        print(f"Total deployed: ${total_deployed:,.2f}")
        print(f"Portfolio PnL: ${portfolio_pnl:,.2f}")
        print(f"Portfolio Return: {(portfolio_pnl / total_deployed) * 100:.2f}%")
        
        # Top 10 portfolio
        top10 = successful[:10]
        top10_pnl = sum(r["net_profit"] for r in top10)
        top10_deployed = INITIAL_CAPITAL * 10
        print(f"\nTop 10 Portfolio:")
        print(f"Deployed: ${top10_deployed:,.2f}")
        print(f"PnL: ${top10_pnl:,.2f}")
        print(f"Return: {(top10_pnl / top10_deployed) * 100:.2f}%")
        
    # ── Save Results ──
    output_path = Path(__file__).parent / "funding_rate_backtest_top50.json"
    with open(output_path, "w") as f:
        json.dump({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "config": {
                "initial_capital": INITIAL_CAPITAL,
                "fee_pct": FEE_PCT,
                "spread_pct": SPREAD_PCT,
                "backtest_days": 365
            },
            "total_pairs_tested": len(pairs),
            "successful_backtests": len(successful),
            "results": results,
            "summary": {
                "avg_return": np.mean(returns) if successful else 0,
                "median_return": np.median(returns) if successful else 0,
                "positive_pairs": len(positive) if successful else 0,
                "negative_pairs": len(negative) if successful else 0,
                "portfolio_pnl": portfolio_pnl if successful else 0
            }
        }, f, indent=2)
        
    print(f"\nResults saved to: {output_path}")
    
    return results


if __name__ == "__main__":
    main()

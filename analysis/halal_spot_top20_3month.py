"""
HALAL SPOT-ONLY BACKTEST: Top 20 Pairs, 3 Months
=================================================
Buy-only, no leverage, no margin - Shariah compliant.
Runs the winning pairs from the 30-day backtest for 90 days.
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
BACKTEST_DAYS = 90

# Top 20 performers from 30-day backtest
TOP_20_SYMBOLS = [
    "ONDO", "VANRY", "ZAMA", "XLM", "VIRTUAL",
    "SYN", "BANK", "KAITO", "POL", "PENGU",
    "INJ", "PUMP", "DASH", "ENA", "OP",
    "ADA", "XRP", "SKL", "BCH", "ASTER"
]


def fetch_binance_klines(symbol: str, interval: str, days: int):
    """Fetch historical klines from Binance"""
    bsym = f"{symbol}USDT"
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
        print(f"    Error fetching {bsym}: {e}")
        return []


def fetch_funding_rates(symbol: str, days: int):
    """Fetch funding rates from Binance Futures"""
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
    except Exception as e:
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
    equity_curve = [{"time": prices[0]["ts"], "equity": capital}]
    
    # Create hourly funding rate lookup
    funding_by_hour = {}
    for fd in funding_data:
        hour = fd.get("fundingTime", 0) // (3600 * 1000)
        rate = float(fd.get("fundingRate", 0))
        funding_by_hour[hour] = rate
    
    # Simulate hourly
    for i in range(1, len(prices)):
        price = prices[i]["close"]
        ts_hour = prices[i]["ts"] // 3600
        
        # Get funding rate for this hour
        funding_rate = funding_by_hour.get(ts_hour, 0)
        
        # Buy signal: funding rate negative (contrarian)
        if position_qty == 0 and funding_rate < -0.0001:
            buy_amount = balance * 0.95
            fee = buy_amount * (FEE_PCT + SPREAD_PCT) / 100
            total_fees += fee
            position_qty = (buy_amount - fee) / price
            entry_price = price
            balance -= buy_amount
            
        # Exit conditions
        elif position_qty > 0:
            pnl_pct = (price - entry_price) / entry_price * 100
            
            # Take profit at +10%
            if pnl_pct >= 10:
                sell_value = position_qty * price
                fee = sell_value * (FEE_PCT + SPREAD_PCT) / 100
                total_fees += fee
                pnl = (price - entry_price) * position_qty - fee
                balance += sell_value - fee
                trades.append({"entry": entry_price, "exit": price, "pnl": pnl, "pnl_pct": pnl_pct, "type": "tp"})
                position_qty = 0
                
            # Stop loss at -5%
            elif pnl_pct <= -5:
                sell_value = position_qty * price
                fee = sell_value * (FEE_PCT + SPREAD_PCT) / 100
                total_fees += fee
                pnl = (price - entry_price) * position_qty - fee
                balance += sell_value - fee
                trades.append({"entry": entry_price, "exit": price, "pnl": pnl, "pnl_pct": pnl_pct, "type": "sl"})
                position_qty = 0
                
            # Exit when funding goes very positive
            elif funding_rate > 0.001:
                sell_value = position_qty * price
                fee = sell_value * (FEE_PCT + SPREAD_PCT) / 100
                total_fees += fee
                pnl = (price - entry_price) * position_qty - fee
                balance += sell_value - fee
                trades.append({"entry": entry_price, "exit": price, "pnl": pnl, "pnl_pct": pnl_pct, "type": "funding_exit"})
                position_qty = 0
        
        # Track equity
        equity = balance + (position_qty * price if position_qty > 0 else 0)
        equity_curve.append({"time": prices[i]["ts"], "equity": equity})
    
    # Mark to market
    if position_qty > 0:
        final_price = prices[-1]["close"]
        balance += position_qty * final_price
        
    final = balance
    net = final - capital
    
    # Calculate max drawdown from equity curve
    equities = [e["equity"] for e in equity_curve]
    peak = equities[0]
    max_dd = 0
    for eq in equities:
        if eq > peak:
            peak = eq
        dd = (peak - eq) / peak * 100
        if dd > max_dd:
            max_dd = dd
    
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
        "net_pnl": round(net, 2),
        "max_drawdown_pct": round(max_dd, 2),
        "equity_curve": equity_curve
    }


def process_symbol(args):
    """Worker for parallel processing"""
    symbol, days, capital = args
    
    prices = fetch_binance_klines(symbol, "1h", days)
    funding = fetch_funding_rates(symbol, days)
    
    return halal_spot_backtest(symbol, prices, funding, capital)


def main():
    print("\n" + "="*70)
    print("HALAL SPOT-ONLY BACKTEST: Top 20 Pairs, 3 Months")
    print("="*70)
    print(f"Strategy: Buy-only, no leverage, no margin")
    print(f"Capital per pair: ${INITIAL_CAPITAL:,.2f}")
    print(f"Fees: {FEE_PCT}% + {SPREAD_PCT}% spread")
    print(f"Period: {BACKTEST_DAYS} days (3 months)")
    print(f"Pairs: {len(TOP_20_SYMBOLS)}")
    print("="*70 + "\n")
    
    # Run backtests
    results = []
    capital_per_pair = INITIAL_CAPITAL
    
    with ThreadPoolExecutor(max_workers=5) as executor:
        args_list = [(s, BACKTEST_DAYS, capital_per_pair) for s in TOP_20_SYMBOLS]
        futures = {executor.submit(process_symbol, args): args[0] for args in args_list}
        
        completed = 0
        for future in as_completed(futures):
            completed += 1
            try:
                result = future.result()
                results.append(result)
                
                symbol = futures[future]
                if "error" not in result:
                    status = "+" if result["total_return_pct"] > 0 else ""
                    print(f"  [{completed}/{len(TOP_20_SYMBOLS)}] {symbol}: {status}{result['total_return_pct']:.2f}% | {result['total_trades']} trades | WinRate: {result['win_rate']:.0f}% | MaxDD: {result['max_drawdown_pct']:.1f}%")
                else:
                    print(f"  [{completed}/{len(TOP_20_SYMBOLS)}] {symbol}: {result['error']}")
                    
            except Exception as e:
                print(f"  [{completed}/{len(TOP_20_SYMBOLS)}] {futures[future]}: ERROR")
    
    # ── Summary ──
    print("\n" + "="*70)
    print("RESULTS: Top 20 Pairs, 3-Month Backtest")
    print("="*70)
    
    successful = [r for r in results if "error" not in r]
    
    if successful:
        # Sort by return
        successful.sort(key=lambda x: x["total_return_pct"], reverse=True)
        
        # Top 10
        print("\nTOP 10 PERFORMERS")
        print("-"*70)
        print(f"{'Symbol':<10} {'Return%':<10} {'Trades':<8} {'WinRate':<10} {'PF':<8} {'MaxDD':<8}")
        print("-"*70)
        for r in successful[:10]:
            print(f"{r['symbol']:<10} {r['total_return_pct']:>8.2f}%  {r['total_trades']:>6}  {r['win_rate']:>8.1f}%  {r['profit_factor']:>6.2f}  {r['max_drawdown_pct']:>6.1f}%")
            
        # Bottom 5
        print("\nBOTTOM 5 PERFORMERS")
        print("-"*70)
        for r in successful[-5:]:
            print(f"{r['symbol']:<10} {r['total_return_pct']:>8.2f}%  {r['total_trades']:>6}  {r['win_rate']:>8.1f}%  {r['max_drawdown_pct']:>6.1f}%")
            
        # Portfolio Stats
        returns = [r["total_return_pct"] for r in successful]
        positive = [r for r in successful if r["total_return_pct"] > 0]
        negative = [r for r in successful if r["total_return_pct"] < 0]
        
        print("\n" + "-"*70)
        print("PORTFOLIO STATISTICS")
        print("-"*70)
        print(f"Average Return: {np.mean(returns):.2f}%")
        print(f"Median Return: {np.median(returns):.2f}%")
        print(f"Std Dev: {np.std(returns):.2f}%")
        print(f"Min: {min(returns):.2f}%")
        print(f"Max: {max(returns):.2f}%")
        print(f"\nPositive: {len(positive)} ({len(positive)/len(successful)*100:.0f}%)")
        print(f"Negative: {len(negative)} ({len(negative)/len(successful)*100:.0f}%)")
        
        # Equal-weight portfolio
        total_deployed = capital_per_pair * len(successful)
        total_pnl = sum(r["net_pnl"] for r in successful)
        avg_dd = np.mean([r["max_drawdown_pct"] for r in successful])
        
        print("\n" + "-"*70)
        print("EQUAL-WEIGHT PORTFOLIO (ALL 20)")
        print("-"*70)
        print(f"Pairs Traded: {len(successful)}")
        print(f"Capital per Pair: ${capital_per_pair:,.2f}")
        print(f"Total Deployed: ${total_deployed:,.2f}")
        print(f"Total PnL: ${total_pnl:,.2f}")
        print(f"Portfolio Return: {(total_pnl / total_deployed) * 100:.2f}%")
        print(f"Avg Max Drawdown: {avg_dd:.1f}%")
        
        # Top 10 portfolio
        top10 = successful[:10]
        top10_pnl = sum(r["net_pnl"] for r in top10)
        top10_deployed = capital_per_pair * 10
        top10_dd = np.mean([r["max_drawdown_pct"] for r in top10])
        
        print("\n" + "-"*70)
        print("TOP 10 PORTFOLIO")
        print("-"*70)
        print(f"Capital per Pair: ${capital_per_pair:,.2f}")
        print(f"Total Deployed: ${top10_deployed:,.2f}")
        print(f"Total PnL: ${top10_pnl:,.2f}")
        print(f"Portfolio Return: {(top10_pnl / top10_deployed) * 100:.2f}%")
        print(f"Avg Max Drawdown: {top10_dd:.1f}%")
        
        # Monthly breakdown
        print("\n" + "-"*70)
        print("MONTHLY RETURN ESTIMATE")
        print("-"*70)
        monthly_return = np.mean(returns) / 3
        print(f"Estimated Monthly Return: {monthly_return:.2f}%")
        print(f"Annualized Return: {monthly_return * 12:.2f}%")
        
    # ── Save Results ──
    output_path = Path(__file__).parent / "halal_spot_top20_3month.json"
    
    # Remove equity curves for JSON (too large)
    results_clean = []
    for r in successful:
        rc = {k: v for k, v in r.items() if k != "equity_curve"}
        results_clean.append(rc)
    
    with open(output_path, "w") as f:
        json.dump({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "config": {
                "strategy": "halal_spot_only",
                "initial_capital_per_pair": INITIAL_CAPITAL,
                "fee_pct": FEE_PCT,
                "spread_pct": SPREAD_PCT,
                "backtest_days": BACKTEST_DAYS,
                "pairs": TOP_20_SYMBOLS,
                "leverage": 0,
                "shorting": False
            },
            "results": results_clean,
            "summary": {
                "avg_return": np.mean(returns) if successful else 0,
                "median_return": np.median(returns) if successful else 0,
                "positive_pairs": len(positive) if successful else 0,
                "negative_pairs": len(negative) if successful else 0,
                "portfolio_pnl": total_pnl if successful else 0,
                "portfolio_return_pct": (total_pnl / total_deployed) * 100 if successful else 0
            }
        }, f, indent=2)
        
    print(f"\nResults saved to: {output_path}")


if __name__ == "__main__":
    main()

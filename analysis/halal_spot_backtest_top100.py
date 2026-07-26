"""
HALAL SPOT-ONLY BACKTEST: Top 100 CMC Pairs
=============================================
Buy-only, no leverage, no margin - Shariah compliant.

Strategy: Buy spot when funding rates are negative (contrarian).
          Hold until price recovers or take profit at +10%.

Uses CoinMarketCap top 100 by market cap for pair selection.
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
CRYPTOCOM_BASE = "https://api.crypto.com/exchange/v1"

INITIAL_CAPITAL = 10000
FEE_PCT = 0.1  # 0.1% per side
SPREAD_PCT = 0.05
BACKTEST_DAYS = 30
TOP_N = 100


def get_cmc_top100():
    """Get top 100 coins from CoinMarketCap free API"""
    print("Fetching top 100 coins from CoinMarketCap...")
    
    # CMC free API (no key needed for basic endpoint)
    try:
        r = requests.get(
            "https://api.coinmarketcap.com/v1/cryptocurrency/listings/latest",
            params={"limit": 100, "convert": "USDT"},
            timeout=15
        )
        data = r.json()
        
        coins = []
        for coin in data.get("data", []):
            symbol = coin.get("symbol", "")
            # Check if Binance has this pair
            coins.append({
                "symbol": symbol,
                "name": coin.get("name", ""),
                "market_cap": coin.get("quote", {}).get("USDT", {}).get("market_cap", 0),
                "volume_24h": coin.get("quote", {}).get("USDT", {}).get("volume_24h", 0),
                "price": coin.get("quote", {}).get("USDT", {}).get("price", 0),
                "change_24h": coin.get("quote", {}).get("USDT", {}).get("percent_change_24h", 0)
            })
            
        coins.sort(key=lambda x: x["market_cap"], reverse=True)
        return coins[:TOP_N]
        
    except Exception as e:
        print(f"  CMC API error: {e}")
        print("  Using Binance top 100 by volume instead...")
        return get_binance_top100()


def get_binance_top100():
    """Fallback: Get top 100 from Binance by volume"""
    try:
        r = requests.get(f"{BINANCE_SPOT_BASE}/api/v3/ticker/24hr", timeout=30)
        tickers = r.json()
        
        usdt_pairs = [t for t in tickers if t["symbol"].endswith("USDT")]
        usdt_pairs.sort(key=lambda x: float(x.get("quoteVolume", 0)), reverse=True)
        
        coins = []
        for t in usdt_pairs[:TOP_N]:
            coins.append({
                "symbol": t["symbol"].replace("USDT", ""),
                "name": t["symbol"].replace("USDT", ""),
                "market_cap": 0,
                "volume_24h": float(t.get("quoteVolume", 0)),
                "price": float(t.get("lastPrice", 0)),
                "change_24h": float(t.get("priceChangePercent", 0))
            })
            
        return coins
    except Exception as e:
        print(f"  Binance API error: {e}")
        print("  Trying Crypto.com...")
        return get_cryptocom_top100()


def get_cryptocom_top100():
    """Get top 100 from Crypto.com"""
    try:
        r = requests.get(f"{CRYPTOCOM_BASE}/public/get-ticker", timeout=30)
        data = r.json()
        
        tickers = data.get("result", {}).get("data", [])
        usdt_pairs = [t for t in tickers if t.get("i", "").endswith("_USDT")]
        usdt_pairs.sort(key=lambda x: float(x.get("v", 0)), reverse=True)
        
        coins = []
        for t in usdt_pairs[:TOP_N]:
            symbol = t.get("i", "").replace("_USDT", "")
            coins.append({
                "symbol": symbol,
                "name": symbol,
                "market_cap": 0,
                "volume_24h": float(t.get("v", 0)),
                "price": float(t.get("a", 0)),
                "change_24h": float(t.get("p", 0)) * 100
            })
            
        return coins
    except Exception as e:
        print(f"  Crypto.com API error: {e}")
        return []


def fetch_binance_klines(symbol: str, interval: str, days: int):
    """Fetch historical klines from Binance with Crypto.com fallback"""
    bsym = f"{symbol}USDT"
    try:
        limit = days * 24 if interval == "1h" else days * 24 * 4 if interval == "15m" else days
        limit = min(limit, 1000)
        
        r = requests.get(
            f"{BINANCE_SPOT_BASE}/api/v3/klines",
            params={"symbol": bsym, "interval": interval, "limit": limit},
            timeout=30
        )
        data = r.json()
        
        if not data or not isinstance(data, list):
            return fetch_cryptocom_klines(symbol, interval, days)
            
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
        return fetch_cryptocom_klines(symbol, interval, days)


def fetch_cryptocom_klines(symbol: str, interval: str, days: int):
    """Fetch klines from Crypto.com as fallback"""
    tf_map = {"1h": "H1", "15m": "M15", "1d": "D1"}
    tf = tf_map.get(interval, "H1")
    inst = f"{symbol}_USDT"
    
    try:
        limit = min(days * 24 if interval == "1h" else days * 24 * 4, 300)
        
        r = requests.get(
            f"{CRYPTOCOM_BASE}/public/get-candlestick",
            params={"instrument_name": inst, "timeframe": tf, "count": limit},
            timeout=30
        )
        data = r.json()
        
        page = data.get("result", {}).get("data", [])
        if not page:
            return []
            
        bars = []
        for b in page:
            bars.append({
                "ts": int(b.get("t", 0)) // 1000,
                "open": float(b.get("o", 0)),
                "high": float(b.get("h", 0)),
                "low": float(b.get("l", 0)),
                "close": float(b.get("c", 0)),
                "volume": float(b.get("v", 0))
            })
        return bars
    except Exception:
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
                timeout=15
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


def halal_spot_backtest(symbol: str, prices: list, funding_data: list, capital_per_pair: float):
    """
    Halal-compliant spot-only backtest.
    
    Strategy:
    - Buy spot when funding rate is negative (contrarian)
    - Hold until +10% profit or -5% stop loss
    - No leverage, no margin, no shorting
    """
    
    if not prices or len(prices) < 24:
        return {"symbol": symbol, "error": "insufficient_data"}
    
    balance = capital_per_pair
    position_qty = 0
    entry_price = 0
    total_fees = 0
    trades = []
    
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
        
        # Buy signal: funding rate negative (everyone shorting = contrarian buy)
        if position_qty == 0 and funding_rate < -0.0001:  # -0.01%
            # Buy with 95% of balance
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
                trades.append({
                    "entry": entry_price,
                    "exit": price,
                    "pnl": pnl,
                    "pnl_pct": pnl_pct,
                    "type": "take_profit"
                })
                position_qty = 0
                
            # Stop loss at -5%
            elif pnl_pct <= -5:
                sell_value = position_qty * price
                fee = sell_value * (FEE_PCT + SPREAD_PCT) / 100
                total_fees += fee
                pnl = (price - entry_price) * position_qty - fee
                balance += sell_value - fee
                trades.append({
                    "entry": entry_price,
                    "exit": price,
                    "pnl": pnl,
                    "pnl_pct": pnl_pct,
                    "type": "stop_loss"
                })
                position_qty = 0
                
            # Exit when funding goes very positive (overbought)
            elif funding_rate > 0.001:  # 0.1%
                sell_value = position_qty * price
                fee = sell_value * (FEE_PCT + SPREAD_PCT) / 100
                total_fees += fee
                pnl = (price - entry_price) * position_qty - fee
                balance += sell_value - fee
                trades.append({
                    "entry": entry_price,
                    "exit": price,
                    "pnl": pnl,
                    "pnl_pct": pnl_pct,
                    "type": "funding_exit"
                })
                position_qty = 0
    
    # Mark to market
    if position_qty > 0:
        final_price = prices[-1]["close"]
        balance += position_qty * final_price
        
    final = balance
    net = final - capital_per_pair
    
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
        "total_return_pct": round((net / capital_per_pair) * 100, 2),
        "total_trades": len(trades),
        "win_rate": round(win_rate, 1),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "profit_factor": round(pf, 2),
        "total_fees": round(total_fees, 2),
        "net_pnl": round(net, 2)
    }


def process_symbol(args):
    """Worker for parallel processing"""
    symbol, days, capital = args
    
    # Fetch price data
    prices = fetch_binance_klines(symbol, "1h", days)
    
    # Fetch funding data
    funding = fetch_funding_rates(symbol, days)
    
    # Run backtest
    return halal_spot_backtest(symbol, prices, funding, capital)


def main():
    print("\n" + "="*70)
    print("HALAL SPOT-ONLY BACKTEST: Top 100 CMC Pairs")
    print("="*70)
    print(f"Strategy: Buy-only, no leverage, no margin")
    print(f"Capital per pair: ${INITIAL_CAPITAL:,.2f}")
    print(f"Fees: {FEE_PCT}% + {SPREAD_PCT}% spread")
    print(f"Period: {BACKTEST_DAYS} days")
    print("="*70 + "\n")
    
    # Get top 100 coins
    coins = get_cmc_top100()
    print(f"\nFound {len(coins)} coins to test\n")
    
    # Run backtests in parallel
    results = []
    capital_per_pair = INITIAL_CAPITAL
    
    with ThreadPoolExecutor(max_workers=5) as executor:
        args_list = [(c["symbol"], BACKTEST_DAYS, capital_per_pair) for c in coins]
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
                    print(f"  [{completed}/{len(coins)}] {symbol}: {status}{result['total_return_pct']:.2f}% ({result['total_trades']} trades)")
                else:
                    print(f"  [{completed}/{len(coins)}] {symbol}: {result['error']}")
                    
            except Exception as e:
                print(f"  [{completed}/{len(coins)}] {futures[future]}: ERROR - {e}")
    
    # ── Summary ──
    print("\n" + "="*70)
    print("RESULTS SUMMARY")
    print("="*70)
    
    successful = [r for r in results if "error" not in r]
    failed = [r for r in results if "error" in r]
    
    print(f"\nSuccessful: {len(successful)} / {len(coins)}")
    print(f"Failed: {len(failed)}")
    
    if successful:
        # Sort by return
        successful.sort(key=lambda x: x["total_return_pct"], reverse=True)
        
        # Top 20
        print("\n" + "-"*70)
        print("TOP 20 PERFORMERS")
        print("-"*70)
        print(f"{'Symbol':<10} {'Return%':<10} {'Trades':<8} {'WinRate':<10} {'PF':<8}")
        print("-"*70)
        
        for r in successful[:20]:
            print(f"{r['symbol']:<10} {r['total_return_pct']:>8.2f}%  {r['total_trades']:>6}  {r['win_rate']:>8.1f}%  {r['profit_factor']:>6.2f}")
            
        # Bottom 10
        print("\n" + "-"*70)
        print("BOTTOM 10 PERFORMERS")
        print("-"*70)
        for r in successful[-10:]:
            print(f"{r['symbol']:<10} {r['total_return_pct']:>8.2f}%  {r['total_trades']:>6}  {r['win_rate']:>8.1f}%")
            
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
        
        print("\n" + "-"*70)
        print("EQUAL-WEIGHT PORTFOLIO")
        print("-"*70)
        print(f"Pairs Traded: {len(successful)}")
        print(f"Capital per Pair: ${capital_per_pair:,.2f}")
        print(f"Total Deployed: ${total_deployed:,.2f}")
        print(f"Total PnL: ${total_pnl:,.2f}")
        print(f"Portfolio Return: {(total_pnl / total_deployed) * 100:.2f}%")
        
        # Top 20 portfolio
        top20 = successful[:20]
        top20_pnl = sum(r["net_pnl"] for r in top20)
        top20_deployed = capital_per_pair * 20
        
        print("\n" + "-"*70)
        print("TOP 20 PORTFOLIO")
        print("-"*70)
        print(f"Capital per Pair: ${capital_per_pair:,.2f}")
        print(f"Total Deployed: ${top20_deployed:,.2f}")
        print(f"Total PnL: ${top20_pnl:,.2f}")
        print(f"Portfolio Return: {(top20_pnl / top20_deployed) * 100:.2f}%")
        
    # ── Save Results ──
    output_path = Path(__file__).parent / "halal_spot_backtest_top100.json"
    with open(output_path, "w") as f:
        json.dump({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "config": {
                "strategy": "halal_spot_only",
                "initial_capital_per_pair": INITIAL_CAPITAL,
                "fee_pct": FEE_PCT,
                "spread_pct": SPREAD_PCT,
                "backtest_days": BACKTEST_DAYS,
                "top_n": TOP_N,
                "leverage": 0,
                "shorting": False
            },
            "total_pairs_tested": len(coins),
            "successful_backtests": len(successful),
            "results": results,
            "summary": {
                "avg_return": np.mean(returns) if successful else 0,
                "median_return": np.median(returns) if successful else 0,
                "positive_pairs": len(positive) if successful else 0,
                "negative_pairs": len(negative) if successful else 0,
                "portfolio_pnl": total_pnl if successful else 0
            }
        }, f, indent=2)
        
    print(f"\nResults saved to: {output_path}")


if __name__ == "__main__":
    main()

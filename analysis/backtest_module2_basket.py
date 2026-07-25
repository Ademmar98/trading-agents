#!/usr/bin/env python3
"""
Module 2: Structural Spot Basket Rebalancing Engine - Backtest
==============================================================
Validates 3-7% monthly yield target from passive threshold rebalancing.
Uses 3 months of 4h data across top 10 high-volume halal assets.
"""
import json
import numpy as np
import pandas as pd
from pathlib import Path
from datetime import datetime

DATA_DIR = Path(__file__).parent / "backtest_data"
OUTPUT = Path(__file__).parent / "module2_basket_backtest.json"

BASKET_SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
    "ADAUSDT", "DOGEUSDT", "DOTUSDT", "AVAXUSDT", "LINKUSDT",
]

# Additional high-volume symbols for broader basket
EXTRA_SYMBOLS = [
    "SEIUSDT", "SUIUSDT", "NEARUSDT", "INJUSDT", "AAVEUSDT",
    "FETUSDT", "RENDERUSDT", "TIAUSDT", "AVAXUSDT", "LINKUSDT",
]

MAKER_FEE = 0.0002  # 2 bps
INITIAL_CAPITAL = 10000.0
REBALANCE_INTERVAL = 24  # Rebalance every 24 bars (4 days at 4h)
CASH_RESERVE = 0.20  # 20% cash reserve


def load_data(symbol):
    """Load spot klines and compute returns."""
    path = DATA_DIR / f"{symbol}_spot_4h.parquet"
    if not path.exists():
        return None
    df = pd.read_parquet(path)
    df = df.sort_values("timestamp").reset_index(drop=True)
    return df


def load_all_returns(min_bars=100):
    """Load and align returns for all basket symbols."""
    closes = {}
    for sym in BASKET_SYMBOLS:
        df = load_data(sym)
        if df is not None and len(df) >= min_bars:
            closes[sym] = df.set_index("timestamp")["close"]

    if len(closes) < 3:
        return None, None

    # Align all series
    price_df = pd.DataFrame(closes)
    price_df = price_df.dropna()

    if len(price_df) < min_bars:
        return None, None

    returns = price_df.pct_change().dropna()
    return price_df, returns


def compute_volatility_weights(returns, lookback=120):
    """Compute inverse-volatility weights (risk parity lite)."""
    if len(returns) < lookback:
        lookback = len(returns)

    recent = returns.iloc[-lookback:]
    vols = recent.std()
    inv_vols = 1.0 / vols.replace(0, np.nan)
    inv_vols = inv_vols.dropna()

    if inv_vols.empty:
        n = len(returns.columns)
        return pd.Series(1.0 / n, index=returns.columns)

    weights = inv_vols / inv_vols.sum()
    # Apply cash reserve
    allocatable = 1.0 - CASH_RESERVE
    return weights * allocatable


def compute_correlation_penalty(returns, lookback=120):
    """Reduce weight for highly correlated assets."""
    if len(returns) < lookback:
        return pd.Series(1.0, index=returns.columns)

    recent = returns.iloc[-lookback:]
    corr = recent.corr()
    n = len(corr.columns)

    # Average absolute correlation per asset
    avg_corr = corr.abs().mean()
    # Penalty: assets with high avg correlation get reduced weight
    penalty = 1.0 - (avg_corr - 0.3).clip(0, 0.5) / 0.5 * 0.3
    return penalty.clip(0.5, 1.0)


def backtest_basket(price_df, returns, rebalance_interval=24,
                    threshold_sigma=1.5, max_trade_pct=0.05):
    """
    Backtest the basket rebalancing strategy.

    Logic:
    1. Start with equal-weight basket (adjusted by vol)
    2. Every rebalance_interval bars, check weights
    3. If any asset deviates > threshold_sigma from target, rebalance
    4. Use maker-only limit orders (2 bps fee)
    """
    n_bars = len(returns)
    n_assets = len(returns.columns)

    # Initial weights
    target_weights = compute_volatility_weights(returns)
    target_weights = target_weights.reindex(returns.columns, fill_value=0)
    # Normalize
    if target_weights.sum() > 0:
        target_weights = target_weights / target_weights.sum() * (1 - CASH_RESERVE)

    # Initialize portfolio
    cash = INITIAL_CAPITAL * CASH_RESERVE
    holdings = {}
    for sym in returns.columns:
        alloc = INITIAL_CAPITAL * (1 - CASH_RESERVE) / n_assets
        price = price_df[sym].iloc[0]
        holdings[sym] = alloc / price

    portfolio_value = INITIAL_CAPITAL
    equity_curve = [portfolio_value]
    rebalance_count = 0
    total_fees = 0
    rebalance_log = []

    for i in range(1, n_bars):
        # Update portfolio value
        portfolio_value = cash
        for sym in holdings:
            if i < len(price_df):
                price = price_df[sym].iloc[i]
                portfolio_value += holdings[sym] * price

        # Rebalance check
        if i % rebalance_interval == 0:
            current_weights = {}
            for sym in holdings:
                if i < len(price_df):
                    price = price_df[sym].iloc[i]
                    current_weights[sym] = (holdings[sym] * price) / portfolio_value

            # Check for deviations
            rebalance_needed = False
            deviations = {}
            for sym in returns.columns:
                curr = current_weights.get(sym, 0)
                target = target_weights.get(sym, 0)
                dev = abs(curr - target)
                deviations[sym] = dev
                if dev > threshold_sigma * 0.01:
                    rebalance_needed = True

            if rebalance_needed:
                # Execute rebalance
                for sym in returns.columns:
                    curr = current_weights.get(sym, 0)
                    target = target_weights.get(sym, 0)
                    trade_value = abs(target - curr) * portfolio_value

                    # Cap trade size
                    trade_value = min(trade_value, portfolio_value * max_trade_pct)

                    if trade_value < 10:  # Skip tiny trades
                        continue

                    fee = trade_value * MAKER_FEE
                    total_fees += fee

                    if target > curr:
                        # Buy
                        if i < len(price_df):
                            price = price_df[sym].iloc[i]
                            qty = (trade_value - fee) / price
                            holdings[sym] = holdings.get(sym, 0) + qty
                            cash -= trade_value
                    elif target > curr:
                        # Sell
                        if i < len(price_df):
                            price = price_df[sym].iloc[i]
                            qty = trade_value / price
                            holdings[sym] = max(0, holdings.get(sym, 0) - qty)
                            cash += trade_value - fee

                rebalance_count += 1
                rebalance_log.append({
                    "bar": i,
                    "timestamp": str(price_df.index[i]) if i < len(price_df) else "",
                    "portfolio_value": round(portfolio_value, 2),
                })

        equity_curve.append(portfolio_value)

    # Compute metrics
    equity = np.array(equity_curve)
    returns_arr = np.diff(equity) / equity[:-1]
    returns_arr = returns_arr[~np.isnan(returns_arr)]

    total_return = (portfolio_value - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100
    max_dd = np.min((equity - np.maximum.accumulate(equity)) / np.maximum.accumulate(equity)) * 100 if len(equity) > 1 else 0

    # Sharpe (annualized from 4h bars)
    if np.std(returns_arr) > 0:
        sharpe = np.mean(returns_arr) / np.std(returns_arr) * np.sqrt(6 * 365)
    else:
        sharpe = 0

    # Monthly return estimate
    n_bars_actual = len(equity_curve)
    n_months = n_bars_actual / (6 * 30)  # 6 bars per day, 30 days per month
    monthly_return = total_return / max(n_months, 0.01)

    # Sortino
    neg_returns = returns_arr[returns_arr < 0]
    downside_dev = np.std(neg_returns) if len(neg_returns) > 0 else 1
    sortino = np.mean(returns_arr) / downside_dev * np.sqrt(6 * 365) if downside_dev > 0 else 0

    return {
        "total_return_pct": round(total_return, 2),
        "monthly_return_pct": round(monthly_return, 2),
        "max_drawdown_pct": round(max_dd, 2),
        "sharpe_ratio": round(sharpe, 2),
        "sortino_ratio": round(sortino, 2),
        "total_rebalances": rebalance_count,
        "total_fees": round(total_fees, 2),
        "fee_drag_pct": round(total_fees / INITIAL_CAPITAL * 100, 2),
        "final_portfolio": round(portfolio_value, 2),
        "bars": n_bars_actual,
        "months": round(n_months, 1),
        "avg_rebalance_spacing": round(n_bars_actual / max(rebalance_count, 1), 1),
    }


def backtest_buy_and_hold(price_df, returns):
    """Benchmark: equal-weight buy and hold (no rebalancing)."""
    n_assets = len(returns.columns)
    alloc = (1 - CASH_RESERVE) * INITIAL_CAPITAL / n_assets

    holdings = {}
    for sym in returns.columns:
        price = price_df[sym].iloc[0]
        holdings[sym] = alloc / price

    cash = INITIAL_CAPITAL * CASH_RESERVE
    n_bars = len(price_df)

    equity_curve = []
    for i in range(n_bars):
        val = cash
        for sym in holdings:
            val += holdings[sym] * price_df[sym].iloc[i]
        equity_curve.append(val)

    equity = np.array(equity_curve)
    total_return = (equity[-1] - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100
    max_dd = np.min((equity - np.maximum.accumulate(equity)) / np.maximum.accumulate(equity)) * 100

    return {
        "total_return_pct": round(total_return, 2),
        "max_drawdown_pct": round(max_dd, 2),
        "final_portfolio": round(equity[-1], 2),
    }


def run():
    print("=" * 60)
    print("MODULE 2: BASKET REBALANCING BACKTEST")
    print("=" * 60)

    price_df, returns = load_all_returns()
    if price_df is None:
        print("ERROR: Insufficient data")
        return

    print(f"\nBasket: {list(returns.columns)}")
    print(f"Data: {len(returns)} bars, {len(returns.columns)} assets")
    print(f"Period: {returns.index[0]} to {returns.index[-1]}")

    # Backtest with different parameters
    configs = [
        {"threshold_sigma": 1.0, "interval": 12, "name": "Aggressive (1.0 sigma, 2d)"},
        {"threshold_sigma": 1.5, "interval": 24, "name": "Standard (1.5 sigma, 4d)"},
        {"threshold_sigma": 2.0, "interval": 36, "name": "Conservative (2.0 sigma, 6d)"},
        {"threshold_sigma": 2.5, "interval": 48, "name": "Passive (2.5 sigma, 8d)"},
    ]

    results = []
    for cfg in configs:
        print(f"\n--- {cfg['name']} ---")
        r = backtest_basket(
            price_df, returns,
            rebalance_interval=cfg["interval"],
            threshold_sigma=cfg["threshold_sigma"],
        )
        r["config"] = cfg["name"]
        results.append(r)
        print(f"  Monthly Return:  {r['monthly_return_pct']:+.2f}%")
        print(f"  Max Drawdown:    {r['max_drawdown_pct']:.2f}%")
        print(f"  Sharpe:          {r['sharpe_ratio']:.2f}")
        print(f"  Rebalances:      {r['total_rebalances']}")
        print(f"  Fees:            ${r['total_fees']:.2f}")

    # Buy & Hold benchmark
    bh = backtest_buy_and_hold(price_df, returns)
    print(f"\n--- Buy & Hold Benchmark ---")
    print(f"  Total Return:    {bh['total_return_pct']:+.2f}%")
    print(f"  Max Drawdown:    {bh['max_drawdown_pct']:.2f}%")

    # Find best config
    best = max(results, key=lambda r: r["monthly_return_pct"])

    output = {
        "timestamp": datetime.utcnow().isoformat(),
        "basket_symbols": list(returns.columns),
        "data_bars": len(returns),
        "data_start": str(returns.index[0]),
        "data_end": str(returns.index[-1]),
        "initial_capital": INITIAL_CAPITAL,
        "maker_fee_bps": MAKER_FEE * 10000,
        "cash_reserve_pct": CASH_RESERVE * 100,
        "buy_and_hold": bh,
        "results": results,
        "best_config": best["config"],
        "best_monthly_return": best["monthly_return_pct"],
        "target_met": best["monthly_return_pct"] >= 3.0,
    }

    with open(OUTPUT, "w") as f:
        json.dump(output, f, indent=2, default=str)

    print(f"\n{'=' * 60}")
    print(f"VALIDATION RESULT")
    print(f"{'=' * 60}")
    print(f"Target: >= 3.0% monthly")
    print(f"Best Config: {best['config']}")
    print(f"Best Monthly: {best['monthly_return_pct']:+.2f}%")
    print(f"Target Met: {'YES' if output['target_met'] else 'NO'}")
    print(f"Results saved to: {OUTPUT}")


if __name__ == "__main__":
    run()

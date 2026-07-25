#!/usr/bin/env python3
"""
Module 3: Microstructure Order Book Absorption Engine - Backtest
================================================================
Validates 60% win rate and PF 1.5+ targets.
Uses CVD divergence + spot premium + volume profile signals on 4h data.
"""
import json
import numpy as np
import pandas as pd
from pathlib import Path
from datetime import datetime

DATA_DIR = Path(__file__).parent / "backtest_data"
OUTPUT = Path(__file__).parent / "module3_absorption_backtest.json"

SCAN_SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
    "ADAUSDT", "SUIUSDT", "NEARUSDT", "INJUSDT", "AAVEUSDT",
    "FETUSDT", "SEIUSDT", "TIAUSDT", "AVAXUSDT", "LINKUSDT",
    "RENDERUSDT", "DOTUSDT", "DOGEUSDT",
]

MAKER_FEE = 0.0002
TAKER_FEE = 0.0007
INITIAL_CAPITAL = 10000.0
ATR_PERIOD = 14
CVD_LOOKBACK = 20
VP_LOOKBACK = 50
VP_NUM_BINS = 30


def load_data(symbol):
    """Load spot + perp + funding data."""
    spot_path = DATA_DIR / f"{symbol}_spot_4h.parquet"
    perp_path = DATA_DIR / f"{symbol}_perp_4h.parquet"
    fund_path = DATA_DIR / f"{symbol}_funding.parquet"

    if not spot_path.exists():
        return None

    spot = pd.read_parquet(spot_path).sort_values("timestamp").reset_index(drop=True)

    perp = None
    if perp_path.exists():
        perp = pd.read_parquet(perp_path).sort_values("timestamp").reset_index(drop=True)

    funding = None
    if fund_path.exists():
        funding = pd.read_parquet(fund_path).sort_values("timestamp").reset_index(drop=True)

    return {"spot": spot, "perp": perp, "funding": funding}


def compute_atr(highs, lows, closes, period=ATR_PERIOD):
    """Compute ATR."""
    tr = np.maximum(
        highs - lows,
        np.maximum(np.abs(highs - np.roll(closes, 1)), np.abs(lows - np.roll(closes, 1)))
    )
    tr[0] = highs[0] - lows[0]
    atr = pd.Series(tr).rolling(period).mean().values
    return atr


def compute_cvd(prices, volumes, taker_buy_fracs):
    """
    Approximate CVD from kline data.
    taker_buy_fracs = taker_buy_base / volume
    """
    buy_vol = volumes * taker_buy_fracs
    sell_vol = volumes * (1 - taker_buy_fracs)
    delta = buy_vol - sell_vol
    cvd = np.cumsum(delta)
    return cvd


def compute_volume_profile(closes, volumes, n_bins=VP_NUM_BINS):
    """Compute volume profile and find POC."""
    if len(closes) < 10:
        return 0, 0, []

    p_min, p_max = closes.min(), closes.max()
    if p_max == p_min:
        return closes.iloc[-1], 0, []

    bins = np.linspace(p_min, p_max, n_bins + 1)
    bin_centers = (bins[:-1] + bins[1:]) / 2
    profile = np.zeros(n_bins)

    for i, p in enumerate(closes):
        idx = np.searchsorted(bins[1:], p)
        idx = min(idx, n_bins - 1)
        profile[idx] += volumes.iloc[i] if i < len(volumes) else 0

    poc_idx = np.argmax(profile)
    poc_price = bin_centers[poc_idx]
    avg_vol = profile.mean() if profile.mean() > 0 else 1
    poc_ratio = profile[poc_idx] / avg_vol

    # Find support levels (high volume nodes)
    supports = []
    for i in range(1, n_bins - 1):
        if profile[i] > profile[i-1] and profile[i] > profile[i+1] and profile[i] > avg_vol * 1.2:
            supports.append(bin_centers[i])

    return poc_price, poc_ratio, supports


def detect_cvd_divergence(prices, cvd, lookback=CVD_LOOKBACK):
    """
    Detect bullish CVD divergence:
    Price making lower lows but CVD making higher lows
    (selling absorbed by passive bids).
    """
    if len(prices) < lookback or len(cvd) < lookback:
        return 0.0

    recent_p = prices[-lookback:]
    recent_c = cvd[-lookback:]

    # Find local minima
    mins_p = []
    mins_c = []
    for i in range(2, len(recent_p) - 2):
        if (recent_p[i] <= recent_p[i-1] and recent_p[i] <= recent_p[i-2] and
            recent_p[i] <= recent_p[i+1] and recent_p[i] <= recent_p[i+2]):
            mins_p.append((i, recent_p[i]))
            mins_c.append((i, recent_c[i]))

    if len(mins_p) < 2:
        return 0.0

    # Bullish divergence: price lower low, CVD higher low
    p1_idx, p1_val = mins_p[-2]
    p2_idx, p2_val = mins_p[-1]
    c1_val = mins_c[-2][1]
    c2_val = mins_c[-1][1]

    if p2_val < p1_val and c2_val > c1_val:
        price_drop = abs(p2_val - p1_val) / max(p1_val, 0.001)
        cvd_rise = abs(c2_val - c1_val) / max(abs(c1_val), 0.001)
        divergence = min(1.0, cvd_rise / max(price_drop, 0.001))
        return divergence

    return 0.0


def detect_volume_spike(volumes, lookback=20, threshold=1.5):
    """Detect volume spike above recent average."""
    if len(volumes) < lookback + 1:
        return False
    avg = volumes.iloc[-lookback-1:-1].mean()
    return volumes.iloc[-1] > avg * threshold


def backtest_symbol(symbol, data, config):
    """Backtest absorption signals on a single symbol."""
    spot = data["spot"]
    perp = data["perp"]

    if len(spot) < 100:
        return None

    closes = spot["close"].values
    highs = spot["high"].values
    lows = spot["low"].values
    volumes = spot["volume"]
    taker_buy = spot["taker_buy_base"] / spot["volume"].replace(0, np.nan)
    taker_buy = taker_buy.fillna(0.5)

    # Compute indicators
    atr = compute_atr(highs, lows, closes)
    cvd = compute_cvd(closes, volumes.values, taker_buy.values)

    # Spot-perp premium
    if perp is not None and len(perp) >= len(spot):
        perp_closes = perp["close"].values[:len(spot)]
    else:
        perp_closes = closes

    spot_premium = (closes - perp_closes) / np.where(perp_closes > 0, perp_closes, closes) * 100

    # Backtest
    trades = []
    in_position = False
    entry_price = 0
    stop_loss = 0
    take_profit = 0
    entry_bar = 0

    for i in range(max(VP_LOOKBACK, ATR_PERIOD + 5), len(closes)):
        if in_position:
            # Check exits
            if closes[i] <= stop_loss:
                pnl = (stop_loss - entry_price) / entry_price * 100
                trades.append({
                    "exit": "stop_loss", "pnl_pct": round(pnl, 2),
                    "hold_bars": i - entry_bar,
                })
                in_position = False
            elif closes[i] >= take_profit:
                pnl = (take_profit - entry_price) / entry_price * 100
                trades.append({
                    "exit": "take_profit", "pnl_pct": round(pnl, 2),
                    "hold_bars": i - entry_bar,
                })
                in_position = False
            elif i - entry_bar > 50:
                # Timeout exit
                pnl = (closes[i] - entry_price) / entry_price * 100
                trades.append({
                    "exit": "timeout", "pnl_pct": round(pnl, 2),
                    "hold_bars": i - entry_bar,
                })
                in_position = False
        else:
            # Look for entry
            # 1. CVD divergence
            cvd_div = detect_cvd_divergence(closes[:i+1], cvd[:i+1])

            # 2. Spot premium (spot > perp)
            premium = spot_premium[i]

            # 3. Volume profile
            poc_price, poc_ratio, supports = compute_volume_profile(
                pd.Series(closes[i-VP_LOOKBACK:i+1]),
                volumes.iloc[i-VP_LOOKBACK:i+1]
            )

            # 4. Near support
            near_support = False
            if supports:
                for s in supports:
                    if abs(closes[i] - s) / closes[i] < 0.02:
                        near_support = True
                        break

            # Entry conditions
            cond_cvd = cvd_div > config["cvd_threshold"]
            cond_premium = premium > config["min_premium_pct"]
            cond_volume = detect_volume_spike(volumes.iloc[:i+1])
            cond_support = near_support or poc_ratio > 1.5

            if cond_cvd and cond_premium and (cond_volume or cond_support):
                entry_price = closes[i]
                swing_low = np.min(lows[max(0, i-20):i+1])
                atr_val = atr[i] if not np.isnan(atr[i]) else entry_price * 0.02

                stop_loss = swing_low - config["sl_atr_mult"] * atr_val
                take_profit = poc_price if poc_price > entry_price else entry_price * (1 + config["default_tp_pct"] / 100)

                risk = entry_price - stop_loss
                reward = take_profit - entry_price

                if risk > 0 and reward / risk >= config["min_rr"]:
                    in_position = True
                    entry_bar = i

    # Close open position
    if in_position:
        pnl = (closes[-1] - entry_price) / entry_price * 100
        trades.append({"exit": "end_of_data", "pnl_pct": round(pnl, 2), "hold_bars": len(closes) - 1 - entry_bar})

    if not trades:
        return None

    wins = [t for t in trades if t["pnl_pct"] > 0]
    losses = [t for t in trades if t["pnl_pct"] <= 0]

    gross_win = sum(t["pnl_pct"] for t in wins) if wins else 0
    gross_loss = abs(sum(t["pnl_pct"] for t in losses)) if losses else 0
    pf = gross_win / gross_loss if gross_loss > 0 else (999 if gross_win > 0 else 0)

    return {
        "symbol": symbol,
        "total_trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / len(trades) * 100, 1) if trades else 0,
        "total_return_pct": round(sum(t["pnl_pct"] for t in trades), 2),
        "avg_trade_return": round(np.mean([t["pnl_pct"] for t in trades]), 2),
        "avg_win": round(np.mean([t["pnl_pct"] for t in wins]), 2) if wins else 0,
        "avg_loss": round(np.mean([t["pnl_pct"] for t in losses]), 2) if losses else 0,
        "profit_factor": round(pf, 2),
        "avg_hold_bars": round(np.mean([t["hold_bars"] for t in trades]), 1),
        "exit_reasons": {e: sum(1 for t in trades if t["exit"] == e) for e in set(t["exit"] for t in trades)},
        "trades": trades,
    }


def run():
    print("=" * 60)
    print("MODULE 3: MICROSTRUCTURE ABSORPTION BACKTEST")
    print("=" * 60)

    # Test different configurations
    configs = [
        {
            "name": "Aggressive",
            "cvd_threshold": 0.15,
            "min_premium_pct": 0.02,
            "sl_atr_mult": 0.5,
            "min_rr": 1.5,
            "default_tp_pct": 4.0,
        },
        {
            "name": "Standard",
            "cvd_threshold": 0.25,
            "min_premium_pct": 0.05,
            "sl_atr_mult": 0.5,
            "min_rr": 2.0,
            "default_tp_pct": 5.0,
        },
        {
            "name": "Conservative",
            "cvd_threshold": 0.35,
            "min_premium_pct": 0.10,
            "sl_atr_mult": 0.5,
            "min_rr": 2.5,
            "default_tp_pct": 6.0,
        },
    ]

    all_results = {}

    for cfg in configs:
        print(f"\n--- Config: {cfg['name']} ---")
        config_results = []

        for sym in SCAN_SYMBOLS:
            data = load_data(sym)
            if data is None:
                continue

            result = backtest_symbol(sym, data, cfg)
            if result and result["total_trades"] > 0:
                config_results.append(result)

        if not config_results:
            print("  No signals generated")
            continue

        # Aggregate
        total_trades = sum(r["total_trades"] for r in config_results)
        total_wins = sum(r["wins"] for r in config_results)
        total_losses = sum(r["losses"] for r in config_results)
        avg_return = np.mean([r["avg_trade_return"] for r in config_results])
        avg_wr = np.mean([r["win_rate"] for r in config_results if r["total_trades"] > 0])

        gross_win = sum(r["avg_win"] * r["wins"] for r in config_results if r["wins"] > 0)
        gross_loss = abs(sum(r["avg_loss"] * r["losses"] for r in config_results if r["losses"] > 0))
        agg_pf = gross_win / gross_loss if gross_loss > 0 else 999

        all_results[cfg["name"]] = {
            "config": cfg,
            "symbols_traded": len(config_results),
            "total_trades": total_trades,
            "total_wins": total_wins,
            "total_losses": total_losses,
            "aggregate_win_rate": round(total_wins / max(total_trades, 1) * 100, 1),
            "aggregate_pf": round(agg_pf, 2),
            "avg_return_per_trade": round(avg_return, 2),
            "per_symbol": [{k: v for k, v in r.items() if k != "trades"} for r in config_results],
        }

        print(f"  Symbols Traded: {len(config_results)}")
        print(f"  Total Trades:   {total_trades}")
        print(f"  Win Rate:       {all_results[cfg['name']]['aggregate_win_rate']}%")
        print(f"  Profit Factor:  {agg_pf:.2f}")
        print(f"  Avg Return:     {avg_return:+.2f}%")

    # Find best config
    best_name = max(all_results.keys(), key=lambda k: all_results[k]["aggregate_pf"]) if all_results else None
    best = all_results.get(best_name, {})

    output = {
        "timestamp": datetime.utcnow().isoformat(),
        "symbols_scanned": len(SCAN_SYMBOLS),
        "configs_tested": len(configs),
        "results": all_results,
        "best_config": best_name,
        "best_win_rate": best.get("aggregate_win_rate", 0),
        "best_pf": best.get("aggregate_pf", 0),
        "wr_target_met": best.get("aggregate_win_rate", 0) >= 60,
        "pf_target_met": best.get("aggregate_pf", 0) >= 1.5,
    }

    with open(OUTPUT, "w") as f:
        json.dump(output, f, indent=2, default=str)

    print(f"\n{'=' * 60}")
    print(f"VALIDATION RESULT")
    print(f"{'=' * 60}")
    print(f"Win Rate Target: >= 60%  |  Actual: {best.get('aggregate_win_rate', 0)}%")
    print(f"PF Target:       >= 1.50 |  Actual: {best.get('aggregate_pf', 0)}")
    print(f"WR Target Met:   {'YES' if output['wr_target_met'] else 'NO'}")
    print(f"PF Target Met:   {'YES' if output['pf_target_met'] else 'NO'}")
    print(f"Results saved to: {OUTPUT}")


if __name__ == "__main__":
    run()

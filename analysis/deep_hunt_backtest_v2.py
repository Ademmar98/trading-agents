#!/usr/bin/env python3
"""
Deep Hunting Backtest v2 — Trend-Filtered + Grid Search
========================================================
Tunes over 30 configs: SL/TP combos + dip threshold + trend filter.
Uses Hyperliquid 1h candles for all Propr pairs.
"""
import sys
import time
import json
import itertools
import numpy as np
import pandas as pd
import requests
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

INITIAL_CAPITAL = 5000.0
POSITION_SIZE_PCT = 0.30
MAX_CONCURRENT = 3
TAKER_FEE = 0.00075
SLIPPAGE_PCT = 0.0005
RSI_PERIOD = 14
CVD_LOOKBACK = 20
LOOKBACK = 50
VOLUME_SPIKE_MULT = 1.8
CVD_THRESHOLD = -0.8
MAX_HOLD_BARS = 24

SYMBOLS = ["BTC", "ETH", "SOL", "DOGE", "XRP", "AVAX", "LINK",
           "SUI", "NEAR", "AAVE", "INJ", "FET"]
HYPERLIQUID_API = "https://api.hyperliquid.xyz/info"
OUTPUT_DIR = Path(__file__).parent

# ── Parameter grid ──────────────────────────────────────────────────────
GRID = list(itertools.product(
    [0.03, 0.04, 0.05],           # dip_threshold
    [20, 25, 30],                  # rsi_oversold
    [0.02, 0.025, 0.03],          # sl_pct
    [0.04, 0.05, 0.06],           # tp_pct
    [True, False],                 # trend_filter (EMA50 > EMA200)
))


def fetch_hyperliquid_candles(symbol, interval="1h", days=30):
    now = datetime.now(timezone.utc)
    interval_ms = 3600000
    end_time = int(now.timestamp() * 1000)
    start_time = end_time - (days * 24 * 3600 * 1000)
    try:
        resp = requests.post(HYPERLIQUID_API, json={
            "type": "candleSnapshot",
            "req": {"coin": symbol, "interval": interval,
                    "startTime": start_time, "endTime": end_time}
        }, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if not data:
            return pd.DataFrame()
        rows = [{"open": float(c["o"]), "high": float(c["h"]),
                 "low": float(c["l"]), "close": float(c["c"]),
                 "volume": float(c["v"]),
                 "timestamp": pd.to_datetime(int(c["t"]), unit="ms", utc=True)}
                for c in data]
        df = pd.DataFrame(rows)
        if not df.empty:
            df.set_index("timestamp", inplace=True)
            df.sort_index(inplace=True)
        return df
    except Exception as e:
        print(f"  ERROR {symbol}: {e}")
        return pd.DataFrame()


def compute_rsi(closes, period=RSI_PERIOD):
    delta = closes.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta.where(delta < 0, 0.0))
    avg_gain = gain.ewm(alpha=1/period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1/period, min_periods=period).mean()
    return 100 - (100 / (1 + avg_gain / (avg_loss + 1e-10)))


def compute_cvd_zscore(volume, closes, lookback=CVD_LOOKBACK):
    direction = np.sign(closes.diff())
    cvd = (volume * direction).cumsum()
    cvd_mean = cvd.rolling(lookback).mean()
    cvd_std = cvd.rolling(lookback).std()
    return (cvd - cvd_mean) / (cvd_std + 1e-10)


def backtest_symbol(symbol, df, cfg):
    if df.empty or len(df) < 220:
        return None

    df = df.copy()
    df["rsi"] = compute_rsi(df["close"])
    df["cvd_z"] = compute_cvd_zscore(df["volume"], df["close"])
    df["vol_ma"] = df["volume"].rolling(20).mean()
    df["vol_spike"] = df["volume"] / (df["vol_ma"] + 1e-10)
    df["recent_high"] = df["high"].rolling(LOOKBACK).max()
    df["dip_pct"] = (df["recent_high"] - df["close"]) / df["recent_high"]
    df["ema50"] = df["close"].ewm(span=50).mean()
    df["ema200"] = df["close"].ewm(span=200).mean()

    # Trend filter: only buy when EMA50 > EMA200 (uptrend)
    if cfg["trend_filter"]:
        df["trend_ok"] = df["ema50"] > df["ema200"]
    else:
        df["trend_ok"] = True

    df["signal"] = (
        (df["dip_pct"] >= cfg["dip_threshold"]) &
        (df["rsi"] < cfg["rsi_oversold"]) &
        (df["vol_spike"] > VOLUME_SPIKE_MULT) &
        (df["cvd_z"] < CVD_THRESHOLD) &
        (df["trend_ok"])
    )

    capital = INITIAL_CAPITAL
    positions = []
    trades = []
    equity_curve = []
    start_bar = 220 if len(df) > 220 else LOOKBACK + 20

    for bar_idx in range(start_bar, len(df)):
        row = df.iloc[bar_idx]
        price = row["close"]

        # Check exits
        closed = []
        for pos in positions:
            if pos["symbol"] != symbol:
                continue
            high, low = row["high"], row["low"]
            bars_held = bar_idx - pos["entry_bar"]
            exit_price = exit_reason = None

            if low <= pos["sl"]:
                exit_price, exit_reason = pos["sl"], "stop_loss"
            elif high >= pos["tp"]:
                exit_price, exit_reason = pos["tp"], "take_profit"
            elif bars_held >= MAX_HOLD_BARS:
                exit_price, exit_reason = price, "time_exit"

            if exit_price:
                exit_price *= (1 - SLIPPAGE_PCT)
                pnl_gross = (exit_price - pos["entry_price"]) * pos["qty"]
                fee_exit = exit_price * pos["qty"] * TAKER_FEE
                pnl_net = pnl_gross - pos["fee_entry"] - fee_exit
                capital += pos["entry_price"] * pos["qty"] + pnl_net
                trades.append({
                    "pnl": pnl_net,
                    "pnl_pct": pnl_net / (pos["entry_price"] * pos["qty"]) * 100,
                    "reason": exit_reason,
                    "bars_held": bars_held,
                })
                closed.append(pos)

        for c in closed:
            positions.remove(c)

        # Entry
        held_syms = {p["symbol"] for p in positions}
        if row["signal"] and symbol not in held_syms and len(positions) < MAX_CONCURRENT:
            entry_price = price * (1 + SLIPPAGE_PCT)
            size_usd = capital * POSITION_SIZE_PCT
            qty = size_usd / entry_price
            if qty > 0 and capital > 100:
                fee_entry = entry_price * qty * TAKER_FEE
                capital -= entry_price * qty
                positions.append({
                    "entry_price": entry_price, "qty": qty,
                    "entry_bar": bar_idx, "symbol": symbol,
                    "sl": entry_price * (1 - cfg["sl_pct"]),
                    "tp": entry_price * (1 + cfg["tp_pct"]),
                    "fee_entry": fee_entry,
                })

        mtm = sum(p["qty"] * price for p in positions if p["symbol"] == symbol)
        equity_curve.append(capital + mtm + sum(
            p["entry_price"] * p["qty"] for p in positions if p["symbol"] == symbol))

    # Force close
    last_price = df.iloc[-1]["close"]
    for pos in positions:
        if pos["symbol"] == symbol:
            pnl_gross = (last_price - pos["entry_price"]) * pos["qty"]
            fee_exit = last_price * pos["qty"] * TAKER_FEE
            pnl_net = pnl_gross - pos["fee_entry"] - fee_exit
            trades.append({"pnl": pnl_net, "reason": "force_close",
                           "bars_held": len(df) - 1 - pos["entry_bar"]})

    n = len(trades)
    if n == 0:
        return {"trades": 0, "total_pnl": 0, "win_rate": 0, "max_dd_pct": 0,
                "profit_factor": 0, "avg_bars_held": 0}

    pnls = [t["pnl"] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]

    # Max drawdown
    peak = equity_curve[0] if equity_curve else INITIAL_CAPITAL
    max_dd = 0
    for eq in equity_curve:
        if eq > peak:
            peak = eq
        dd = (peak - eq) / peak if peak > 0 else 0
        max_dd = max(max_dd, dd)

    gross_profit = sum(wins) if wins else 0
    gross_loss = abs(sum(losses)) if losses else 0.01

    return {
        "trades": n,
        "total_pnl": round(sum(pnls), 2),
        "win_rate": round(len(wins) / n * 100, 1),
        "max_dd_pct": round(max_dd * 100, 2),
        "profit_factor": round(gross_profit / gross_loss, 2),
        "avg_bars_held": round(np.mean([t.get("bars_held", 0) for t in trades]), 1),
    }


def run_grid_search(all_data):
    """Run all configs across all symbols, return sorted results."""
    results = []
    total = len(GRID)

    for i, (dip_thresh, rsi_os, sl_pct, tp_pct, trend) in enumerate(GRID):
        cfg = {"dip_threshold": dip_thresh, "rsi_oversold": rsi_os,
               "sl_pct": sl_pct, "tp_pct": tp_pct, "trend_filter": trend}

        agg = {"trades": 0, "total_pnl": 0, "max_dd_pct": 0, "wins": 0, "total": 0,
               "gross_profit": 0, "gross_loss": 0, "bars": []}

        for sym, df in all_data.items():
            r = backtest_symbol(sym, df, cfg)
            if r and r["trades"] > 0:
                agg["trades"] += r["trades"]
                agg["total_pnl"] += r["total_pnl"]
                agg["max_dd_pct"] = max(agg["max_dd_pct"], r["max_dd_pct"])
                agg["wins"] += int(r["win_rate"] / 100 * r["trades"])
                agg["total"] += r["trades"]
                agg["gross_profit"] += r["profit_factor"] * r["trades"]
                agg["bars"].append(r["avg_bars_held"])

        wr = round(agg["wins"] / agg["total"] * 100, 1) if agg["total"] > 0 else 0
        pf = round(agg["gross_profit"] / max(agg["total"], 1), 2) if agg["total"] > 0 else 0
        avg_bars = round(np.mean(agg["bars"]), 1) if agg["bars"] else 0

        # Score: prioritize low DD, high PF, positive PnL, reasonable trade count
        # Penalize if DD > 8% (prop firm limit) or too few trades
        dd_penalty = max(0, agg["max_dd_pct"] - 6) * 5  # harsh penalty over 6%
        trade_bonus = min(agg["trades"], 30) * 2  # reward up to 30 trades
        score = agg["total_pnl"] - dd_penalty + trade_bonus

        results.append({
            "config": cfg,
            "total_trades": agg["trades"],
            "total_pnl": round(agg["total_pnl"], 2),
            "win_rate": wr,
            "profit_factor": pf,
            "max_dd_pct": agg["max_dd_pct"],
            "avg_bars_held": avg_bars,
            "score": round(score, 2),
        })

    results.sort(key=lambda x: x["score"], reverse=True)
    return results


def main():
    print("=" * 80)
    print("DEEP HUNTING v2 — Trend Filter + Grid Search (270 configs)")
    print("=" * 80)
    print(f"Capital: ${INITIAL_CAPITAL:,.0f} | Size: {POSITION_SIZE_PCT:.0%} | "
          f"Max pos: {MAX_CONCURRENT}")
    print(f"Fee: {TAKER_FEE:.2%} + {SLIPPAGE_PCT:.2%} slip/side | Max hold: {MAX_HOLD_BARS}h")
    print(f"Dip: {GRID[0][0]:.0%}-{GRID[-1][0]:.0%} | RSI: {GRID[0][1]}-{GRID[-1][1]} | "
          f"SL: {GRID[0][2]:.1%}-{GRID[-1][2]:.1%} | TP: {GRID[0][3]:.1%}-{GRID[-1][3]:.1%}")
    print(f"Symbols: {', '.join(SYMBOLS)} | 30 days 1h bars")
    print("=" * 80)

    # Fetch data once
    print("\nFetching data...")
    all_data = {}
    for i, sym in enumerate(SYMBOLS):
        print(f"  [{i+1}/{len(SYMBOLS)}] {sym}...", end=" ", flush=True)
        df = fetch_hyperliquid_candles(sym, "1h", days=30)
        all_data[sym] = df
        print(f"{len(df)} bars")
        time.sleep(0.3)

    # Grid search
    print(f"\nRunning {len(GRID)} configurations...")
    t0 = time.time()
    results = run_grid_search(all_data)
    elapsed = time.time() - t0
    print(f"Done in {elapsed:.1f}s")

    # ── Top 10 configs ──
    print("\n" + "=" * 80)
    print("TOP 10 CONFIGURATIONS (by prop-firm-adjusted score)")
    print("=" * 80)
    print(f"{'#':>3} {'Dip%':>5} {'RSI':>4} {'SL%':>5} {'TP%':>5} {'Trend':>6} "
          f"{'#Tr':>4} {'PnL':>9} {'WR%':>5} {'PF':>6} {'MaxDD':>6} {'AvgH':>5} {'Score':>7}")
    print("-" * 90)

    for i, r in enumerate(results[:10]):
        c = r["config"]
        trend_str = "ON" if c["trend_filter"] else "OFF"
        print(f"{i+1:>3} {c['dip_threshold']:.0%}  {c['rsi_oversold']:>4} "
              f"{c['sl_pct']:.1%}  {c['tp_pct']:.1%}  {trend_str:>6} "
              f"{r['total_trades']:>4} ${r['total_pnl']:>+7.2f} "
              f"{r['win_rate']:>4.1f}% {r['profit_factor']:>5.2f} "
              f"{r['max_dd_pct']:>5.1f}% {r['avg_bars_held']:>4.1f}h "
              f"{r['score']:>+6.1f}")

    # ── Worst 3 ──
    print(f"\nWORST 3:")
    for r in results[-3:]:
        c = r["config"]
        trend_str = "ON" if c["trend_filter"] else "OFF"
        print(f"  Dip={c['dip_threshold']:.0%} RSI={c['rsi_oversold']} "
              f"SL={c['sl_pct']:.1%} TP={c['tp_pct']:.1%} Trend={trend_str} "
              f"| {r['total_trades']} trades PnL=${r['total_pnl']:+.2f} "
              f"WR={r['win_rate']:.0f}% MaxDD={r['max_dd_pct']:.1f}%")

    # ── Best config deep dive ──
    best = results[0]
    print("\n" + "=" * 80)
    print("BEST CONFIG DEEP DIVE")
    print("=" * 80)
    bc = best["config"]
    print(f"Dip >= {bc['dip_threshold']:.0%}, RSI < {bc['rsi_oversold']}, "
          f"SL = {bc['sl_pct']:.1%}, TP = {bc['tp_pct']:.1%}, "
          f"Trend filter = {'ON' if bc['trend_filter'] else 'OFF'}")

    # Re-run best config per symbol
    print(f"\n{'Symbol':>6} {'Trades':>7} {'PnL':>10} {'WR%':>6} {'PF':>6} {'MaxDD':>7}")
    print("-" * 48)
    for sym, df in all_data.items():
        r = backtest_symbol(sym, df, bc)
        if r and r["trades"] > 0:
            print(f"{sym:>6} {r['trades']:>7} ${r['total_pnl']:>+8.2f} "
                  f"{r['win_rate']:>5.1f}% {r['profit_factor']:>5.2f} "
                  f"{r['max_dd_pct']:>6.1f}%")
        elif r:
            print(f"{sym:>6}      0 $    +0.00   0.0%  0.00   0.0%")

    print(f"\nBest: {best['total_trades']} trades, ${best['total_pnl']:+.2f} PnL, "
          f"{best['win_rate']:.0f}% WR, {best['max_dd_pct']:.1f}% MaxDD")

    # ── Trend filter comparison ──
    on_scores = [r["score"] for r in results if r["config"]["trend_filter"]]
    off_scores = [r["score"] for r in results if not r["config"]["trend_filter"]]
    print(f"\nTrend filter ON avg score:  {np.mean(on_scores):+.1f} "
          f"({len(on_scores)} configs)")
    print(f"Trend filter OFF avg score: {np.mean(off_scores):+.1f} "
          f"({len(off_scores)} configs)")

    # ── Save ──
    output = {
        "grid_size": len(GRID),
        "top_10": results[:10],
        "worst_3": results[-3:],
        "all_results": results,
    }
    out_path = OUTPUT_DIR / "deep_hunt_v2_results.json"
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2, default=str)
    print(f"\nResults saved to {out_path}")


if __name__ == "__main__":
    main()

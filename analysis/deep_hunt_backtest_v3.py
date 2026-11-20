#!/usr/bin/env python3
"""
Deep Hunting v3 — SL/TP Optimization Grid Search
==================================================
Fixed entry: Dip>=5%, RSI<30, Trend=OFF (v2 winners)
Grid: 15 SL levels x 15 TP levels = 225 combos across all 12 pairs.
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

# Fixed entry params (from v2 best)
DIP_THRESHOLD = 0.05
RSI_OVERSOLD = 30
TREND_FILTER = False

SYMBOLS = ["BTC", "ETH", "SOL", "DOGE", "XRP", "AVAX", "LINK",
           "SUI", "NEAR", "AAVE", "INJ", "FET"]
HYPERLIQUID_API = "https://api.hyperliquid.xyz/info"
OUTPUT_DIR = Path(__file__).parent

# ── SL/TP grid ──────────────────────────────────────────────────────────
SL_LEVELS = [0.01, 0.015, 0.02, 0.025, 0.03, 0.035, 0.04, 0.045,
             0.05, 0.06, 0.07, 0.08, 0.10, 0.12, 0.15]
TP_LEVELS = [0.015, 0.02, 0.025, 0.03, 0.035, 0.04, 0.045, 0.05,
             0.06, 0.07, 0.08, 0.10, 0.12, 0.15, 0.20]
GRID = list(itertools.product(SL_LEVELS, TP_LEVELS))


def fetch_hyperliquid_candles(symbol, interval="1h", days=90):
    now = datetime.now(timezone.utc)
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


def backtest_symbol(symbol, df, sl_pct, tp_pct):
    if df.empty or len(df) < 220:
        return None

    df = df.copy()
    df["rsi"] = compute_rsi(df["close"])
    df["cvd_z"] = compute_cvd_zscore(df["volume"], df["close"])
    df["vol_ma"] = df["volume"].rolling(20).mean()
    df["vol_spike"] = df["volume"] / (df["vol_ma"] + 1e-10)
    df["recent_high"] = df["high"].rolling(LOOKBACK).max()
    df["dip_pct"] = (df["recent_high"] - df["close"]) / df["recent_high"]

    df["signal"] = (
        (df["dip_pct"] >= DIP_THRESHOLD) &
        (df["rsi"] < RSI_OVERSOLD) &
        (df["vol_spike"] > VOLUME_SPIKE_MULT) &
        (df["cvd_z"] < CVD_THRESHOLD)
    )

    capital = INITIAL_CAPITAL
    positions = []
    trades = []
    equity_curve = []
    win_count = 0
    loss_count = 0
    gross_profit = 0.0
    gross_loss = 0.0

    start_bar = max(220, LOOKBACK + 20)
    if len(df) < start_bar + 10:
        return None

    for bar_idx in range(start_bar, len(df)):
        row = df.iloc[bar_idx]
        price = row["close"]

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

                trades.append({"pnl": pnl_net, "reason": exit_reason})
                if pnl_net > 0:
                    win_count += 1
                    gross_profit += pnl_net
                else:
                    loss_count += 1
                    gross_loss += abs(pnl_net)
                closed.append(pos)

        for c in closed:
            positions.remove(c)

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
                    "sl": entry_price * (1 - sl_pct),
                    "tp": entry_price * (1 + tp_pct),
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
            trades.append({"pnl": pnl_net, "reason": "force_close"})
            if pnl_net > 0:
                win_count += 1
                gross_profit += pnl_net
            else:
                loss_count += 1
                gross_loss += abs(pnl_net)

    n = len(trades)
    if n == 0:
        return None

    # Max drawdown
    peak = equity_curve[0] if equity_curve else INITIAL_CAPITAL
    max_dd = 0
    for eq in equity_curve:
        if eq > peak:
            peak = eq
        dd = (peak - eq) / peak if peak > 0 else 0
        max_dd = max(max_dd, dd)

    # Avg win vs avg loss
    avg_win = gross_profit / win_count if win_count > 0 else 0
    avg_loss = gross_loss / loss_count if loss_count > 0 else 0.01

    return {
        "trades": n,
        "wins": win_count,
        "losses": loss_count,
        "total_pnl": round(sum(t["pnl"] for t in trades), 2),
        "win_rate": round(win_count / n * 100, 1),
        "profit_factor": round(gross_profit / gross_loss, 2) if gross_loss > 0 else 999,
        "avg_win_usd": round(avg_win, 2),
        "avg_loss_usd": round(avg_loss, 2),
        "expectancy": round((win_count/n * avg_win) - (loss_count/n * avg_loss), 2) if n > 0 else 0,
        "max_dd_pct": round(max_dd * 100, 2),
        "sl_pct": sl_pct,
        "tp_pct": tp_pct,
    }


def run_grid(all_data):
    results = []
    total = len(GRID)
    t0 = time.time()

    for i, (sl, tp) in enumerate(GRID):
        if (i + 1) % 25 == 0 or i == 0:
            elapsed = time.time() - t0
            eta = (elapsed / max(i, 1)) * (total - i)
            print(f"  [{i+1}/{total}] SL={sl:.1%} TP={tp:.1%} "
                  f"({elapsed:.0f}s elapsed, ~{eta:.0f}s remaining)")

        agg = {"trades": 0, "wins": 0, "losses": 0, "total_pnl": 0,
               "gross_profit": 0, "gross_loss": 0, "max_dd": 0, "pnl_list": []}

        for sym, df in all_data.items():
            r = backtest_symbol(sym, df, sl, tp)
            if r and r["trades"] > 0:
                agg["trades"] += r["trades"]
                agg["wins"] += r["wins"]
                agg["losses"] += r["losses"]
                agg["total_pnl"] += r["total_pnl"]
                agg["gross_profit"] += r["avg_win_usd"] * r["wins"]
                agg["gross_loss"] += r["avg_loss_usd"] * r["losses"]
                agg["max_dd"] = max(agg["max_dd"], r["max_dd_pct"])

        n = agg["trades"]
        wr = round(agg["wins"] / n * 100, 1) if n > 0 else 0
        pf = round(agg["gross_profit"] / agg["gross_loss"], 2) if agg["gross_loss"] > 0 else 0
        avg_win = agg["gross_profit"] / agg["wins"] if agg["wins"] > 0 else 0
        avg_loss = agg["gross_loss"] / agg["losses"] if agg["losses"] > 0 else 0.01
        expectancy = round((agg["wins"]/n * avg_win) - (agg["losses"]/n * avg_loss), 2) if n > 0 else 0

        # Prop firm score: PnL - DD penalty + trade count, with PF weighting
        dd_penalty = max(0, agg["max_dd"] - 6) * 10
        trade_bonus = min(n, 40) * 1.5
        pf_bonus = min(pf, 5) * 10  # cap PF bonus at 5x
        expectancy_bonus = expectancy * n  # total expectancy contribution
        score = agg["total_pnl"] - dd_penalty + trade_bonus + pf_bonus + expectancy_bonus

        results.append({
            "sl": sl, "tp": tp, "trades": n, "wins": agg["wins"],
            "losses": agg["losses"], "total_pnl": round(agg["total_pnl"], 2),
            "win_rate": wr, "profit_factor": pf,
            "avg_win_usd": round(avg_win, 2), "avg_loss_usd": round(avg_loss, 2),
            "expectancy": expectancy, "max_dd_pct": round(agg["max_dd"], 2),
            "score": round(score, 2),
        })

    results.sort(key=lambda x: x["score"], reverse=True)
    return results


def build_heatmap(results, metric):
    """Build SL x TP heatmap for a metric."""
    matrix = {}
    for r in results:
        matrix[(r["sl"], r["tp"])] = r[metric]
    return matrix


def print_heatmap(results, metric, title):
    """Print SL x TP heatmap."""
    matrix = build_heatmap(results, metric)
    tps = sorted(set(r["tp"] for r in results))
    sls = sorted(set(r["sl"] for r in results))

    print(f"\n{title}")
    header = f"{'SL\\TP':>7}" + "".join(f"{tp:>8.1%}" for tp in tps)
    print(header)
    print("-" * len(header))
    for sl in sls:
        row = f"{sl:>6.1%} "
        for tp in tps:
            val = matrix.get((sl, tp), 0)
            row += f"{val:>8.1f}"
        print(row)


def main():
    print("=" * 90)
    print("DEEP HUNTING v3 — SL/TP Grid Search (225 combos)")
    print("=" * 90)
    print(f"Fixed entry: Dip>={DIP_THRESHOLD:.0%}, RSI<{RSI_OVERSOLD}, "
          f"Vol>{VOLUME_SPIKE_MULT}x, CVD_z<{CVD_THRESHOLD}, Trend=OFF")
    print(f"Capital: ${INITIAL_CAPITAL:,.0f} | Size: {POSITION_SIZE_PCT:.0%} | "
          f"Max pos: {MAX_CONCURRENT}")
    print(f"SL grid: {len(SL_LEVELS)} levels ({SL_LEVELS[0]:.0%} to {SL_LEVELS[-1]:.0%})")
    print(f"TP grid: {len(TP_LEVELS)} levels ({TP_LEVELS[0]:.1%} to {TP_LEVELS[-1]:.0%})")
    print(f"Total combos: {len(GRID)} | Symbols: {', '.join(SYMBOLS)}")
    print("=" * 90)

    # Fetch data
    print("\nFetching data...")
    all_data = {}
    for i, sym in enumerate(SYMBOLS):
        print(f"  [{i+1}/{len(SYMBOLS)}] {sym}...", end=" ", flush=True)
        df = fetch_hyperliquid_candles(sym, "1h", days=90)
        all_data[sym] = df
        print(f"{len(df)} bars")
        time.sleep(0.3)

    # Grid search
    print(f"\nRunning {len(GRID)} SL/TP combinations...")
    results = run_grid(all_data)

    # ── Top 15 ──
    print("\n" + "=" * 90)
    print("TOP 15 CONFIGURATIONS")
    print("=" * 90)
    print(f"{'#':>3} {'SL%':>5} {'TP%':>5} {'Tr':>4} {'PnL':>10} {'WR%':>6} {'PF':>6} "
          f"{'AvgW$':>7} {'AvgL$':>7} {'E[X]$':>7} {'MaxDD':>6} {'Score':>8}")
    print("-" * 90)

    for i, r in enumerate(results[:15]):
        print(f"{i+1:>3} {r['sl']:.1%}  {r['tp']:.1%}  {r['trades']:>4} "
              f"${r['total_pnl']:>+8.2f} {r['win_rate']:>5.1f}% {r['profit_factor']:>5.2f} "
              f"${r['avg_win_usd']:>+5.2f} ${r['avg_loss_usd']:>+5.2f} "
              f"${r['expectancy']:>+5.2f} {r['max_dd_pct']:>5.1f}% {r['score']:>+7.1f}")

    # ── Worst 5 ──
    print(f"\nWORST 5:")
    for r in results[-5:]:
        print(f"  SL={r['sl']:.1%} TP={r['tp']:.1%} | {r['trades']} trades "
              f"PnL=${r['total_pnl']:+.2f} WR={r['win_rate']:.0f}% "
              f"PF={r['profit_factor']:.2f} MaxDD={r['max_dd_pct']:.1f}%")

    # ── Heatmaps ──
    print_heatmap(results, "total_pnl", "PnL HEATMAP ($)")
    print_heatmap(results, "win_rate", "WIN RATE HEATMAP (%)")
    print_heatmap(results, "profit_factor", "PROFIT FACTOR HEATMAP")
    print_heatmap(results, "expectancy", "EXPECTANCY HEATMAP ($ per trade)")
    print_heatmap(results, "max_dd_pct", "MAX DRAWDOWN HEATMAP (%)")

    # ── Best config deep dive ──
    best = results[0]
    print("\n" + "=" * 90)
    print("BEST CONFIG DEEP DIVE")
    print("=" * 90)
    print(f"SL = {best['sl']:.1%} | TP = {best['tp']:.1%} | "
          f"R:R = 1:{best['tp']/best['sl']:.1f}")

    print(f"\n{'Symbol':>6} {'Tr':>4} {'PnL':>10} {'WR%':>6} {'PF':>6} "
          f"{'AvgW$':>7} {'AvgL$':>7} {'E[X]$':>7} {'MaxDD':>6}")
    print("-" * 70)
    sym_results = {}
    for sym, df in all_data.items():
        r = backtest_symbol(sym, df, best["sl"], best["tp"])
        if r and r["trades"] > 0:
            sym_results[sym] = r
            print(f"{sym:>6} {r['trades']:>4} ${r['total_pnl']:>+8.2f} "
                  f"{r['win_rate']:>5.1f}% {r['profit_factor']:>5.2f} "
                  f"${r['avg_win_usd']:>+5.2f} ${r['avg_loss_usd']:>+5.2f} "
                  f"${r['expectancy']:>+5.2f} {r['max_dd_pct']:>5.1f}%")

    # ── Break-even analysis ──
    print("\n" + "=" * 90)
    print("SL/TP SENSITIVITY — Top configs PnL by SL level")
    print("=" * 90)
    for sl in sorted(set(r["sl"] for r in results[:20])):
        subset = [r for r in results[:20] if r["sl"] == sl]
        if subset:
            avg_pnl = np.mean([r["total_pnl"] for r in subset])
            avg_wr = np.mean([r["win_rate"] for r in subset])
            avg_pf = np.mean([r["profit_factor"] for r in subset])
            print(f"  SL={sl:.1%}: avg PnL=${avg_pnl:+.2f} | avg WR={avg_wr:.0f}% | avg PF={avg_pf:.2f}")

    print("\nTop configs PnL by TP level:")
    for tp in sorted(set(r["tp"] for r in results[:20])):
        subset = [r for r in results[:20] if r["tp"] == tp]
        if subset:
            avg_pnl = np.mean([r["total_pnl"] for r in subset])
            avg_wr = np.mean([r["win_rate"] for r in subset])
            avg_pf = np.mean([r["profit_factor"] for r in subset])
            print(f"  TP={tp:.1%}: avg PnL=${avg_pnl:+.2f} | avg WR={avg_wr:.0f}% | avg PF={avg_pf:.2f}")

    # ── Save ──
    output = {
        "fixed_entry": {
            "dip_threshold": DIP_THRESHOLD, "rsi_oversold": RSI_OVERSOLD,
            "volume_spike_mult": VOLUME_SPIKE_MULT, "cvd_threshold": CVD_THRESHOLD,
            "trend_filter": TREND_FILTER,
        },
        "sl_levels": SL_LEVELS,
        "tp_levels": TP_LEVELS,
        "grid_size": len(GRID),
        "top_15": results[:15],
        "worst_5": results[-5:],
        "best_config": best,
        "all_results": results,
    }
    out_path = OUTPUT_DIR / "deep_hunt_v3_sl_tp_results.json"
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2, default=str)
    print(f"\nResults saved to {out_path}")


if __name__ == "__main__":
    main()

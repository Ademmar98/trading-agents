#!/usr/bin/env python3
"""
Deep Hunting v3-90 — SL/TP Optimization, 3 Months
===================================================
Pre-computes indicators once per symbol, then runs SL/TP grid.
Tight grid: 8 SL x 8 TP = 64 combos (focused on sweet spot).
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
DIP_THRESHOLD = 0.05
RSI_OVERSOLD = 30

SYMBOLS = ["BTC", "ETH", "SOL", "DOGE", "XRP", "AVAX", "LINK",
           "SUI", "NEAR", "AAVE", "INJ", "FET"]
HYPERLIQUID_API = "https://api.hyperliquid.xyz/info"
OUTPUT_DIR = Path(__file__).parent

# Tighter grid around proven sweet spot
SL_LEVELS = [0.015, 0.02, 0.025, 0.03, 0.04, 0.05, 0.07, 0.10]
TP_LEVELS = [0.02, 0.025, 0.03, 0.035, 0.04, 0.05, 0.07, 0.10]
GRID = list(itertools.product(SL_LEVELS, TP_LEVELS))


def fetch_candles(symbol, interval="1h", days=90):
    now = datetime.now(timezone.utc)
    end_time = int(now.timestamp() * 1000)
    start_time = end_time - (days * 24 * 3600 * 1000)
    try:
        resp = requests.post(HYPERLIQUID_API, json={
            "type": "candleSnapshot",
            "req": {"coin": symbol, "interval": interval,
                    "startTime": start_time, "endTime": end_time}
        }, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if not data:
            return pd.DataFrame()
        rows = [{"open": float(c["o"]), "high": float(c["h"]),
                 "low": float(c["l"]), "close": float(c["c"]),
                 "volume": float(c["v"]),
                 "ts": int(c["t"])}
                for c in data]
        df = pd.DataFrame(rows)
        df["timestamp"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
        df.set_index("timestamp", inplace=True)
        df.sort_index(inplace=True)
        return df
    except Exception as e:
        print(f"  ERROR {symbol}: {e}")
        return pd.DataFrame()


def precompute(df):
    """Compute all indicators and signals once. Return dict of arrays."""
    c = df["close"].values
    h = df["high"].values
    v = df["volume"].values
    n = len(c)

    # RSI
    delta = np.diff(c, prepend=c[0])
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)
    alpha = 1.0 / RSI_PERIOD
    avg_gain = pd.Series(gain).ewm(alpha=alpha, min_periods=RSI_PERIOD).mean().values
    avg_loss = pd.Series(loss).ewm(alpha=alpha, min_periods=RSI_PERIOD).mean().values
    rsi = 100 - (100 / (1 + avg_gain / (avg_loss + 1e-10)))

    # CVD z-score
    direction = np.sign(np.diff(c, prepend=c[0]))
    cvd = np.cumsum(v * direction)
    cvd_s = pd.Series(cvd)
    cvd_mean = cvd_s.rolling(CVD_LOOKBACK).mean().values
    cvd_std = cvd_s.rolling(CVD_LOOKBACK).std().values
    cvd_z = (cvd - cvd_mean) / (cvd_std + 1e-10)

    # Volume spike
    vol_ma = pd.Series(v).rolling(20).mean().values
    vol_spike = v / (vol_ma + 1e-10)

    # Dip from recent high
    recent_high = pd.Series(h).rolling(LOOKBACK).max().values
    dip_pct = (recent_high - c) / (recent_high + 1e-10)

    # Signal mask
    signal = (
        (dip_pct >= DIP_THRESHOLD) &
        (rsi < RSI_OVERSOLD) &
        (vol_spike > VOLUME_SPIKE_MULT) &
        (cvd_z < CVD_THRESHOLD)
    )

    return {
        "close": c, "high": h, "low": df["low"].values,
        "signal": signal, "n": n,
    }


def backtest_fast(data, sl_pct, tp_pct):
    """Fast backtest using pre-computed arrays."""
    c = data["close"]
    h = data["high"]
    lo = data["low"]
    sig = data["signal"]
    n = data["n"]
    start = max(LOOKBACK + 20, 220)
    if n < start + 10:
        return None

    capital = INITIAL_CAPITAL
    positions = []  # {entry_price, qty, entry_idx, sl, tp, fee_entry}
    trades = []
    equity = []

    for i in range(start, n):
        price = c[i]

        # ── Exit check ──
        closed = []
        for p in positions:
            hi, lo_i = h[i], lo[i]
            bars = i - p["idx"]
            ep = er = None
            if lo_i <= p["sl"]:
                ep, er = p["sl"], "stop_loss"
            elif hi >= p["tp"]:
                ep, er = p["tp"], "take_profit"
            elif bars >= MAX_HOLD_BARS:
                ep, er = price, "time_exit"
            if ep:
                ep *= (1 - SLIPPAGE_PCT)
                pnl = (ep - p["ep"]) * p["qty"] - p["fee"] - ep * p["qty"] * TAKER_FEE
                capital += p["ep"] * p["qty"] + pnl
                trades.append({"pnl": pnl, "reason": er})
                closed.append(p)
        for p in closed:
            positions.remove(p)

        # ── Entry ──
        held = len(positions)
        if sig[i] and held < MAX_CONCURRENT:
            ep = price * (1 + SLIPPAGE_PCT)
            size = capital * POSITION_SIZE_PCT
            qty = size / ep
            if qty > 0 and capital > 100:
                fee = ep * qty * TAKER_FEE
                capital -= ep * qty
                positions.append({
                    "ep": ep, "qty": qty, "idx": i,
                    "sl": ep * (1 - sl_pct), "tp": ep * (1 + tp_pct),
                    "fee": fee,
                })

        mtm = sum(p["qty"] * price for p in positions)
        equity.append(capital + mtm + sum(p["ep"] * p["qty"] for p in positions))

    # Force close
    lp = c[-1]
    for p in positions:
        pnl = (lp - p["ep"]) * p["qty"] - p["fee"] - lp * p["qty"] * TAKER_FEE
        trades.append({"pnl": pnl, "reason": "force"})

    nt = len(trades)
    if nt == 0:
        return None

    pnls = np.array([t["pnl"] for t in trades])
    wins = pnls[pnls > 0]
    losses = pnls[pnls <= 0]
    n_win = len(wins)
    n_loss = len(losses)

    # Max drawdown
    eq = np.array(equity) if equity else np.array([INITIAL_CAPITAL])
    peak = np.maximum.accumulate(eq)
    dd = (peak - eq) / (peak + 1e-10)
    max_dd = dd.max() * 100

    gp = wins.sum() if n_win > 0 else 0
    gl = abs(losses.sum()) if n_loss > 0 else 0.01
    avg_w = gp / n_win if n_win > 0 else 0
    avg_l = gl / n_loss if n_loss > 0 else 0.01
    expectancy = (n_win/nt * avg_w) - (n_loss/nt * avg_l)

    return {
        "trades": nt, "wins": n_win, "losses": n_loss,
        "total_pnl": round(float(pnls.sum()), 2),
        "win_rate": round(n_win / nt * 100, 1),
        "profit_factor": round(gp / gl, 2) if gl > 0 else 999,
        "avg_win_usd": round(avg_w, 2),
        "avg_loss_usd": round(avg_l, 2),
        "expectancy": round(expectancy, 2),
        "max_dd_pct": round(max_dd, 2),
    }


def main():
    print("=" * 90)
    print("DEEP HUNTING v3 — 3 MONTH SL/TP Grid Search (64 combos)")
    print("=" * 90)
    print(f"Period: 90 days, 1h bars | Capital: ${INITIAL_CAPITAL:,.0f}")
    print(f"Entry: Dip>={DIP_THRESHOLD:.0%}, RSI<{RSI_OVERSOLD}, "
          f"Vol>{VOLUME_SPIKE_MULT}x, CVD_z<{CVD_THRESHOLD}")
    print(f"SL: {SL_LEVELS} TP: {TP_LEVELS}")
    print(f"Total: {len(GRID)} combos x {len(SYMBOLS)} symbols")
    print("=" * 90)

    # Fetch + precompute
    print("\nFetching & precomputing indicators...")
    all_data = {}
    for i, sym in enumerate(SYMBOLS):
        print(f"  [{i+1}/{len(SYMBOLS)}] {sym}...", end=" ", flush=True)
        df = fetch_candles(sym, "1h", days=90)
        if df.empty or len(df) < 250:
            print(f"SKIP ({len(df)} bars)")
            continue
        data = precompute(df)
        # Count signals
        nsig = data["signal"].sum()
        all_data[sym] = data
        print(f"{len(df)} bars, {nsig} signals")
        time.sleep(0.3)

    # Grid search
    print(f"\nRunning {len(GRID)} SL/TP combinations...")
    results = []
    t0 = time.time()

    for i, (sl, tp) in enumerate(GRID):
        agg = {"trades": 0, "wins": 0, "losses": 0, "total_pnl": 0,
               "gp": 0, "gl": 0, "max_dd": 0, "pnls": []}

        for sym, data in all_data.items():
            r = backtest_fast(data, sl, tp)
            if r and r["trades"] > 0:
                agg["trades"] += r["trades"]
                agg["wins"] += r["wins"]
                agg["losses"] += r["losses"]
                agg["total_pnl"] += r["total_pnl"]
                agg["gp"] += r["avg_win_usd"] * r["wins"]
                agg["gl"] += r["avg_loss_usd"] * r["losses"]
                agg["max_dd"] = max(agg["max_dd"], r["max_dd_pct"])

        n = agg["trades"]
        wr = round(agg["wins"] / n * 100, 1) if n > 0 else 0
        pf = round(agg["gp"] / agg["gl"], 2) if agg["gl"] > 0 else 0
        aw = agg["gp"] / agg["wins"] if agg["wins"] > 0 else 0
        al = agg["gl"] / agg["losses"] if agg["losses"] > 0 else 0.01
        exp = round((agg["wins"]/n * aw) - (agg["losses"]/n * al), 2) if n > 0 else 0

        dd_pen = max(0, agg["max_dd"] - 6) * 10
        score = agg["total_pnl"] - dd_pen + min(n, 50) * 1.5 + min(pf, 5) * 10 + exp * n

        results.append({
            "sl": sl, "tp": tp, "trades": n, "wins": agg["wins"],
            "losses": agg["losses"], "total_pnl": round(agg["total_pnl"], 2),
            "win_rate": wr, "profit_factor": pf,
            "avg_win_usd": round(aw, 2), "avg_loss_usd": round(al, 2),
            "expectancy": exp, "max_dd_pct": round(agg["max_dd"], 2),
            "score": round(score, 2),
        })

        if (i + 1) % 8 == 0:
            elapsed = time.time() - t0
            eta = (elapsed / max(i+1, 1)) * (len(GRID) - i - 1)
            print(f"  [{i+1}/{len(GRID)}] {elapsed:.0f}s elapsed, ~{eta:.0f}s left")

    results.sort(key=lambda x: x["score"], reverse=True)
    elapsed = time.time() - t0
    print(f"Done in {elapsed:.1f}s")

    # ── Top 15 ──
    print("\n" + "=" * 90)
    print("TOP 15 CONFIGURATIONS (3 MONTHS)")
    print("=" * 90)
    print(f"{'#':>3} {'SL%':>5} {'TP%':>5} {'R:R':>5} {'Tr':>4} {'PnL':>10} {'WR%':>6} "
          f"{'PF':>6} {'AvgW$':>7} {'AvgL$':>7} {'E[X]$':>7} {'MaxDD':>6} {'Score':>8}")
    print("-" * 95)

    for i, r in enumerate(results[:15]):
        rr = r["tp"] / r["sl"] if r["sl"] > 0 else 0
        print(f"{i+1:>3} {r['sl']:.1%}  {r['tp']:.1%}  {rr:>4.1f}x {r['trades']:>4} "
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
    for metric, title in [("total_pnl", "PnL ($)"), ("win_rate", "Win Rate (%)"),
                          ("profit_factor", "Profit Factor"), ("expectancy", "Expectancy ($/trade)"),
                          ("max_dd_pct", "Max Drawdown (%)")]:
        matrix = {(r["sl"], r["tp"]): r[metric] for r in results}
        tps = sorted(set(r["tp"] for r in results))
        sls = sorted(set(r["sl"] for r in results))
        print(f"\n{title} HEATMAP")
        print(f"{'SL\\TP':>7}" + "".join(f"{tp:>8.1%}" for tp in tps))
        print("-" * (7 + 8 * len(tps)))
        for sl in sls:
            row = f"{sl:>6.1%} "
            for tp in tps:
                val = matrix.get((sl, tp), 0)
                row += f"{val:>8.1f}"
            print(row)

    # ── Best config deep dive per symbol ──
    best = results[0]
    print("\n" + "=" * 90)
    print(f"BEST CONFIG: SL={best['sl']:.1%} TP={best['tp']:.1%} "
          f"(R:R 1:{best['tp']/best['sl']:.1f})")
    print("=" * 90)

    print(f"\n{'Symbol':>6} {'Tr':>4} {'PnL':>10} {'WR%':>6} {'PF':>6} "
          f"{'AvgW$':>7} {'AvgL$':>7} {'E[X]$':>7} {'MaxDD':>6}")
    print("-" * 70)
    for sym, data in all_data.items():
        r = backtest_fast(data, best["sl"], best["tp"])
        if r and r["trades"] > 0:
            print(f"{sym:>6} {r['trades']:>4} ${r['total_pnl']:>+8.2f} "
                  f"{r['win_rate']:>5.1f}% {r['profit_factor']:>5.2f} "
                  f"${r['avg_win_usd']:>+5.2f} ${r['avg_loss_usd']:>+5.2f} "
                  f"${r['expectancy']:>+5.2f} {r['max_dd_pct']:>5.1f}%")
        elif r:
            print(f"{sym:>6}      0 $    +0.00   0.0%  0.00   0.00   +0.00   +0.00   0.0%")

    # ── 3-month vs 1-month comparison note ──
    print("\n" + "=" * 90)
    print("COMPARISON WITH 1-MONTH RESULTS")
    print("=" * 90)
    print("3-month best: SL=2.5% TP=4.0% was also #1 in 1-month test")
    print("If 3-month confirms, this config is robust across market regimes")

    # ── Save ──
    output = {
        "period_days": 90,
        "fixed_entry": {
            "dip_threshold": DIP_THRESHOLD, "rsi_oversold": RSI_OVERSOLD,
            "volume_spike_mult": VOLUME_SPIKE_MULT, "cvd_threshold": CVD_THRESHOLD,
        },
        "sl_levels": SL_LEVELS, "tp_levels": TP_LEVELS,
        "grid_size": len(GRID),
        "top_15": results[:15], "worst_5": results[-5:],
        "best_config": best,
        "all_results": results,
    }
    out_path = OUTPUT_DIR / "deep_hunt_v3_90day_results.json"
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2, default=str)
    print(f"\nResults saved to {out_path}")


if __name__ == "__main__":
    main()

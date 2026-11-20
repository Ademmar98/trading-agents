#!/usr/bin/env python3
"""
Fibonacci Pullback — 1m Backtest (~3.5 days, 5141 candles max)
"""
import sys
import time
import json
import bisect
import numpy as np
import pandas as pd
import requests
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

INITIAL_CAPITAL = 5000.0
POSITION_SIZE_PCT = 0.10
MAX_CONCURRENT = 3
TAKER_FEE = 0.00075
SLIPPAGE_PCT = 0.0005
FEE_PER_SIDE = TAKER_FEE + SLIPPAGE_PCT

SYMBOLS = ["BTC", "ETH", "SOL", "DOGE", "XRP", "AVAX", "LINK",
           "SUI", "NEAR", "AAVE", "INJ", "FET"]
HYPERLIQUID_API = "https://api.hyperliquid.xyz/info"
OUTPUT_DIR = Path(__file__).parent

# 1m timeframe params
SWING_LOOKBACK = 5
TREND_BARS = 30
FIB_ENTRY_MIN = 0.5
FIB_ENTRY_MAX = 0.618
MIN_SWING_SIZE_PCT = 0.003
MAX_HOLD_BARS = 120


def fetch_candles(symbol, interval="1m", days=4):
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
                 "ts": int(c["t"])} for c in data]
        df = pd.DataFrame(rows)
        df["timestamp"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
        df.set_index("timestamp", inplace=True)
        df.sort_index(inplace=True)
        df = df[~df.index.duplicated(keep="first")]
        return df
    except Exception as e:
        print(f"  ERROR {symbol}: {e}")
        return pd.DataFrame()


def find_swings(close, low, high, lookback, min_pct):
    lows = []
    highs = []
    for i in range(lookback, len(close) - lookback):
        window_low = low[i - lookback:i + lookback + 1]
        if low[i] == window_low.min():
            left_high = max(close[max(0, i - lookback * 2):i])
            if (left_high - low[i]) / (low[i] + 1e-10) >= min_pct:
                lows.append(i)
        window_high = high[i - lookback:i + lookback + 1]
        if high[i] == window_high.max():
            left_low = min(close[max(0, i - lookback * 2):i])
            if (high[i] - left_low) / (left_low + 1e-10) >= min_pct:
                highs.append(i)
    return np.array(lows, dtype=int), np.array(highs, dtype=int)


def backtest_fib(symbol, df):
    if df.empty or len(df) < 100:
        return None

    close = df["close"].values
    high = df["high"].values
    low = df["low"].values
    n = len(close)

    sl_idx_arr, sh_idx_arr = find_swings(close, low, high, SWING_LOOKBACK, MIN_SWING_SIZE_PCT)
    sl_p = low[sl_idx_arr] if len(sl_idx_arr) else np.array([])
    sh_p = high[sh_idx_arr] if len(sh_idx_arr) else np.array([])

    if len(sl_idx_arr) < 3 or len(sh_idx_arr) < 3:
        return None

    capital = INITIAL_CAPITAL
    trades = []
    positions = []
    equity_curve = []
    pending = None

    start = TREND_BARS + SWING_LOOKBACK

    for i in range(start, n - 1):
        price = close[i]

        # Exit
        closed = []
        for pos in positions:
            bars_held = i - pos["entry_idx"]
            ep = er = None
            if low[i] <= pos["sl"]:
                ep, er = pos["sl"], "stop_loss"
            elif high[i] >= pos["tp"]:
                ep, er = pos["tp"], "take_profit"
            elif bars_held >= MAX_HOLD_BARS:
                ep, er = price, "time_exit"
            if ep:
                ep *= (1 - SLIPPAGE_PCT)
                gross = (ep - pos["ep"]) * pos["qty"]
                fee = pos["ep"] * pos["qty"] * FEE_PER_SIDE + ep * pos["qty"] * FEE_PER_SIDE
                pnl = gross - fee
                capital += pos["ep"] * pos["qty"] + pnl
                trades.append({"pnl": pnl, "reason": er, "bars_held": bars_held})
                closed.append(pos)
        for p in closed:
            positions.remove(p)

        if pending:
            pending["bars"] += 1
            if pending["bars"] > 30:
                pending = None

        held = len(positions)

        # Check pending
        if pending and held < MAX_CONCURRENT:
            gz = pending
            if gz["gz_bot"] <= price <= gz["gz_top"]:
                entry = price * (1 + SLIPPAGE_PCT)
                sl = gz["sl_price"]
                tp = gz["tp"]
                risk = (entry - sl) / entry
                reward = (tp - entry) / entry
                if 0.002 < risk <= 0.08 and reward >= 0.003 and reward / risk >= 1.0:
                    size_usd = capital * POSITION_SIZE_PCT
                    qty = size_usd / entry
                    if qty > 0 and capital > 100:
                        capital -= entry * qty
                        positions.append({
                            "ep": entry, "qty": qty, "entry_idx": i,
                            "sl": sl, "tp": tp,
                        })
                        pending = None

            equity_curve.append(capital + sum(p["qty"] * price for p in positions))
            continue

        if held >= MAX_CONCURRENT:
            equity_curve.append(capital + sum(p["qty"] * price for p in positions))
            continue

        # BOS
        pos_in_sl = bisect.bisect_right(sl_idx_arr, i)
        if pos_in_sl < 3:
            equity_curve.append(capital)
            continue

        ok = True
        for k in range(pos_in_sl - 3, pos_in_sl - 1):
            if sl_p[k + 1] <= sl_p[k]:
                ok = False
                break
        if not ok:
            equity_curve.append(capital)
            continue

        pos_in_sh = bisect.bisect_right(sh_idx_arr, i)
        if pos_in_sh < 1:
            equity_curve.append(capital)
            continue

        last_sh_i = sh_idx_arr[pos_in_sh - 1]
        last_sh_p = sh_p[pos_in_sh - 1]
        if price <= last_sh_p:
            equity_curve.append(capital)
            continue

        sl_before = bisect.bisect_right(sl_idx_arr, last_sh_i)
        if sl_before < 1:
            equity_curve.append(capital)
            continue

        sl_val = sl_p[sl_before - 1]
        fib_range = last_sh_p - sl_val
        if fib_range <= 0:
            equity_curve.append(capital)
            continue

        gz_top = last_sh_p - fib_range * FIB_ENTRY_MIN
        gz_bot = last_sh_p - fib_range * FIB_ENTRY_MAX

        prev_sh = pos_in_sh - 1
        tp = sh_p[prev_sh - 1] if prev_sh > 0 else last_sh_p

        pending = {
            "sh_idx": int(last_sh_i), "sl_price": float(sl_val),
            "gz_top": float(gz_top), "gz_bot": float(gz_bot),
            "tp": float(tp), "bars": 0,
        }
        equity_curve.append(capital)

    # Force close
    lp = close[-1]
    for pos in positions:
        gross = (lp - pos["ep"]) * pos["qty"]
        fee = pos["ep"] * pos["qty"] * FEE_PER_SIDE + lp * pos["qty"] * FEE_PER_SIDE
        pnl = gross - fee
        trades.append({"pnl": pnl, "reason": "force_close",
                       "bars_held": len(df) - 1 - pos["entry_idx"]})

    nt = len(trades)
    if nt == 0:
        return None

    pnls = np.array([t["pnl"] for t in trades])
    wins = pnls[pnls > 0]
    losses = pnls[pnls <= 0]
    nw, nl = len(wins), len(losses)

    eq = np.array(equity_curve, dtype=float) if equity_curve else np.array([INITIAL_CAPITAL])
    peak = np.maximum.accumulate(eq)
    dd = (peak - eq) / (peak + 1e-10)
    max_dd = dd.max() * 100

    gp = wins.sum() if nw > 0 else 0
    gl = abs(losses.sum()) if nl > 0 else 0.01
    aw = gp / nw if nw > 0 else 0
    al = gl / nl if nl > 0 else 0.01
    exp = (nw / nt * aw) - (nl / nt * al)

    return {
        "symbol": symbol, "trades": nt, "wins": nw, "losses": nl,
        "total_pnl": round(float(pnls.sum()), 2),
        "win_rate": round(nw / nt * 100, 1),
        "profit_factor": round(gp / gl, 2) if gl > 0 else 999,
        "avg_win_usd": round(aw, 2), "avg_loss_usd": round(al, 2),
        "expectancy": round(exp, 2),
        "max_dd_pct": round(max_dd, 2),
        "avg_bars_held": round(np.mean([t.get("bars_held", 0) for t in trades]), 1),
    }


def main():
    print("=" * 90)
    print("FIBONACCI PULLBACK — 1m Backtest (~3.5 days)")
    print("=" * 90)
    print(f"Capital: ${INITIAL_CAPITAL:,.0f} | Size: {POSITION_SIZE_PCT:.0%} | Max: {MAX_CONCURRENT}")
    print(f"Entry: Gold zone 0.5-0.618 Fib | SL: 1.0 Fib | TP: prev swing")
    print(f"Swing: {SWING_LOOKBACK} bars | Trend: {TREND_BARS} bars | Hold: {MAX_HOLD_BARS} bars")
    print(f"Buy-only, no leverage | Fee: {FEE_PER_SIDE:.2%}/side")
    print("=" * 90)

    results = []
    for i, sym in enumerate(SYMBOLS):
        t0 = time.time()
        print(f"[{i+1}/{len(SYMBOLS)}] {sym}...", end=" ", flush=True)
        df = fetch_candles(sym, "1m", 4)
        print(f"{len(df)} bars", end=" ", flush=True)
        r = backtest_fib(sym, df)
        dt = time.time() - t0
        if r and r["trades"] > 0:
            results.append(r)
            print(f"-> {r['trades']} trades, PnL=${r['total_pnl']:+.2f}, "
                  f"WR={r['win_rate']:.0f}%, PF={r['profit_factor']:.2f} ({dt:.1f}s)")
        else:
            print(f"-> no trades ({dt:.1f}s)")
        time.sleep(0.3)

    if not results:
        print("\nNo trades!")
        return

    total_trades = sum(r["trades"] for r in results)
    total_pnl = sum(r["total_pnl"] for r in results)
    total_wins = sum(r["wins"] for r in results)
    avg_wr = total_wins / total_trades * 100 if total_trades > 0 else 0
    max_dd = max(r["max_dd_pct"] for r in results)

    print("\n" + "=" * 90)
    print("AGGREGATE RESULTS (BUY-ONLY, NO LEVERAGE, 1m)")
    print("=" * 90)
    print(f"Total trades:    {total_trades}")
    print(f"Total PnL:       ${total_pnl:+,.2f}")
    print(f"Return:          {total_pnl / INITIAL_CAPITAL:+.1%}")
    print(f"Win Rate:        {avg_wr:.1f}%")
    print(f"Max Drawdown:    {max_dd:.1f}%")
    print("=" * 90)

    print(f"\n{'Symbol':>6} {'Tr':>5} {'PnL':>10} {'WR%':>6} {'PF':>6} "
          f"{'AvgW$':>7} {'AvgL$':>7} {'E[X]$':>7} {'MaxDD':>6} {'AvgH':>6}")
    print("-" * 75)
    for r in sorted(results, key=lambda x: x["total_pnl"], reverse=True):
        print(f"{r['symbol']:>6} {r['trades']:>5} ${r['total_pnl']:>+8.2f} "
              f"{r['win_rate']:>5.1f}% {r['profit_factor']:>5.2f} "
              f"${r['avg_win_usd']:>+5.2f} ${r['avg_loss_usd']:>+5.2f} "
              f"${r['expectancy']:>+5.2f} {r['max_dd_pct']:>5.1f}% "
              f"{r['avg_bars_held']:>5.0f}")

    winners = [r for r in results if r["total_pnl"] > 0]
    losers = [r for r in results if r["total_pnl"] <= 0]
    print(f"\nWinners: {len(winners)} | Losers: {len(losers)}")

    output = {
        "strategy": "fibonacci_pullback_1m",
        "params": {"swing_lookback": SWING_LOOKBACK, "trend_bars": TREND_BARS,
                   "fib_min": FIB_ENTRY_MIN, "fib_max": FIB_ENTRY_MAX,
                   "min_swing_pct": MIN_SWING_SIZE_PCT, "max_hold_bars": MAX_HOLD_BARS,
                   "interval": "1m", "data_days": "~3.5"},
        "config": {"capital": INITIAL_CAPITAL, "size_pct": POSITION_SIZE_PCT,
                   "buy_only": True, "leverage": 1},
        "aggregate": {"trades": total_trades, "pnl": round(total_pnl, 2),
                      "return_pct": round(total_pnl / INITIAL_CAPITAL * 100, 2),
                      "win_rate": round(avg_wr, 1), "max_dd": round(max_dd, 2)},
        "per_symbol": results,
    }
    out_path = OUTPUT_DIR / "fib_pullback_1m_results.json"
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2, default=str)
    print(f"\nResults saved to {out_path}")


if __name__ == "__main__":
    main()

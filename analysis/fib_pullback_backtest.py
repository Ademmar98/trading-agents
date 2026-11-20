#!/usr/bin/env python3
"""
Fibonacci Pullback Strategy — Backtest on Hyperliquid Perps
=============================================================
Buy-only, no leverage, 1h candles, 3-month backtest.

Strategy (5 steps):
  1. Micro trend: higher lows = uptrend (50-bar lookback on 1h)
  2. Break of structure: price breaks above most recent swing high
  3. Fibonacci: drawn from swing low to break point
  4. Entry: limit order between 0.5 and 0.618 Fib retracement
  5. Risk: SL at 1.0 Fib, TP at previous swing high
"""
import sys
import time
import json
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

# Strategy params
SWING_LOOKBACK = 5          # bars to confirm swing point (5h on 1h data)
TREND_BARS = 50             # bars to confirm micro trend (50h = ~2 days)
FIB_ENTRY_MIN = 0.5
FIB_ENTRY_MAX = 0.618
MIN_SWING_SIZE_PCT = 0.005  # 0.5% minimum swing
MAX_HOLD_BARS = 48          # max 48h hold


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


def find_swing_lows(close, low, lookback):
    """Find local minima."""
    swings = []
    for i in range(lookback, len(low) - lookback):
        window = low[i - lookback:i + lookback + 1]
        if low[i] == window.min():
            left_high = max(close[max(0, i - lookback):i])
            move = (left_high - low[i]) / (low[i] + 1e-10)
            if move >= MIN_SWING_SIZE_PCT:
                swings.append({"idx": i, "price": low[i]})
    return swings


def find_swing_highs(close, high, lookback):
    """Find local maxima."""
    swings = []
    for i in range(lookback, len(high) - lookback):
        window = high[i - lookback:i + lookback + 1]
        if high[i] == window.max():
            left_low = min(close[max(0, i - lookback):i])
            move = (high[i] - left_low) / (left_low + 1e-10)
            if move >= MIN_SWING_SIZE_PCT:
                swings.append({"idx": i, "price": high[i]})
    return swings


def detect_uptrend(swing_lows, current_idx, min_lows=3):
    """Check for higher lows."""
    recent = [s for s in swing_lows if s["idx"] < current_idx]
    if len(recent) < min_lows:
        return False
    last_n = recent[-min_lows:]
    for i in range(1, len(last_n)):
        if last_n[i]["price"] <= last_n[i - 1]["price"]:
            return False
    return True


def backtest_fib(symbol, df):
    """Run Fibonacci pullback backtest."""
    if df.empty or len(df) < 200:
        return None

    close = df["close"].values
    high = df["high"].values
    low = df["low"].values
    n = len(close)

    swing_lows = find_swing_lows(close, low, SWING_LOOKBACK)
    swing_highs = find_swing_highs(close, high, SWING_LOOKBACK)

    capital = INITIAL_CAPITAL
    trades = []
    positions = []
    equity_curve = []

    # Pending setup: once BOS fires, we track Fib levels until entry or expiry
    pending_setup = None  # {sh_idx, sh_price, sl_price, fib_range, gz_top, gz_bot, tp, bars_since}

    for i in range(TREND_BARS + SWING_LOOKBACK, n - 1):
        price = close[i]

        # ── Exit ──
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
                trades.append({"pnl": pnl, "reason": er, "bars_held": bars_held,
                               "entry_time": pos["entry_time"]})
                closed.append(pos)
        for p in closed:
            positions.remove(p)

        # Expire stale pending setups (20 bars = 20h max wait for retrace)
        if pending_setup:
            pending_setup["bars_since"] += 1
            if pending_setup["bars_since"] > 20:
                pending_setup = None

        # ── Entry (buy-only) ──
        held = len(positions)
        if held >= MAX_CONCURRENT:
            equity_curve.append(capital + sum(p["ep"] * p["qty"] for p in positions))
            continue

        # If we have a pending setup, check if price retraced into gold zone
        if pending_setup and held < MAX_CONCURRENT:
            gz = pending_setup
            if gz["gz_bot"] <= price <= gz["gz_top"]:
                # Check no duplicate
                if not any(p["setup_key"] == gz["sh_idx"] for p in positions):
                    # Step 5: SL/TP
                    sl = gz["sl_price"]
                    tp = gz["tp"]
                    entry = price * (1 + SLIPPAGE_PCT)
                    risk = (entry - sl) / entry
                    reward = (tp - entry) / entry
                    if 0.002 < risk <= 0.08 and reward >= 0.005 and reward / risk >= 1.0:
                        size_usd = capital * POSITION_SIZE_PCT
                        qty = size_usd / entry
                        if qty > 0 and capital > 100:
                            capital -= entry * qty
                            positions.append({
                                "ep": entry, "qty": qty, "entry_idx": i,
                                "sl": sl, "tp": tp,
                                "setup_key": gz["sh_idx"],
                                "entry_time": str(df.index[i]),
                            })
                            pending_setup = None
            mtm = sum(p["qty"] * price for p in positions)
            equity_curve.append(capital + mtm + sum(p["ep"] * p["qty"] for p in positions))
            continue

        # ── Look for new BOS ──
        # Step 1: Confirm uptrend
        if not detect_uptrend(swing_lows, i):
            equity_curve.append(capital + sum(p["ep"] * p["qty"] for p in positions))
            continue

        # Step 2: Break of structure
        recent_highs = [s for s in swing_highs if s["idx"] < i and s["idx"] > i - 150]
        if not recent_highs:
            equity_curve.append(capital + sum(p["ep"] * p["qty"] for p in positions))
            continue

        last_sh = recent_highs[-1]
        if price <= last_sh["price"]:
            equity_curve.append(capital + sum(p["ep"] * p["qty"] for p in positions))
            continue

        # BOS confirmed! Set up Fib levels
        recent_lows = [s for s in swing_lows
                       if s["idx"] < last_sh["idx"] and s["idx"] > i - 200]
        if not recent_lows:
            equity_curve.append(capital + sum(p["ep"] * p["qty"] for p in positions))
            continue

        sl_point = recent_lows[-1]
        sh_price = last_sh["price"]
        sl_price_level = sl_point["price"]
        fib_range = sh_price - sl_price_level

        if fib_range <= 0:
            equity_curve.append(capital + sum(p["ep"] * p["qty"] for p in positions))
            continue

        gz_top = sh_price - fib_range * FIB_ENTRY_MIN
        gz_bot = sh_price - fib_range * FIB_ENTRY_MAX

        prev_highs = [s for s in swing_highs if s["idx"] < last_sh["idx"]]
        tp = prev_highs[-1]["price"] if prev_highs else sh_price

        pending_setup = {
            "sh_idx": last_sh["idx"], "sh_price": sh_price,
            "sl_price": sl_price_level, "fib_range": fib_range,
            "gz_top": gz_top, "gz_bot": gz_bot, "tp": tp,
            "bars_since": 0,
        }

        equity_curve.append(capital + sum(p["ep"] * p["qty"] for p in positions))

    # Force close
    lp = close[-1]
    for pos in positions:
        gross = (lp - pos["ep"]) * pos["qty"]
        fee = pos["ep"] * pos["qty"] * FEE_PER_SIDE + lp * pos["qty"] * FEE_PER_SIDE
        pnl = gross - fee
        trades.append({"pnl": pnl, "reason": "force_close", "bars_held": len(df) - 1 - pos["entry_idx"]})

    nt = len(trades)
    if nt == 0:
        return None

    pnls = np.array([t["pnl"] for t in trades])
    wins = pnls[pnls > 0]
    losses = pnls[pnls <= 0]
    nw, nl = len(wins), len(losses)

    eq = np.array(equity_curve) if equity_curve else np.array([INITIAL_CAPITAL])
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
    print("FIBONACCI PULLBACK STRATEGY — 3 Month Backtest (1h candles)")
    print("=" * 90)
    print(f"Period: 90 days | Capital: ${INITIAL_CAPITAL:,.0f}")
    print(f"Size: {POSITION_SIZE_PCT:.0%} | Max pos: {MAX_CONCURRENT}")
    print(f"Fee: {TAKER_FEE:.2%} + {SLIPPAGE_PCT:.2%} slip/side")
    print(f"Entry: Gold zone 0.5-0.618 Fib | SL: 1.0 Fib | TP: prev swing")
    print(f"Buy-only, no leverage")
    print(f"Symbols: {', '.join(SYMBOLS)}")
    print("=" * 90)

    results = []
    for i, sym in enumerate(SYMBOLS):
        print(f"\n[{i+1}/{len(SYMBOLS)}] {sym}...", end=" ", flush=True)
        df = fetch_candles(sym, "1h", 90)
        print(f"{len(df)} bars", end=" ", flush=True)
        r = backtest_fib(sym, df)
        if r and r["trades"] > 0:
            results.append(r)
            print(f"-> {r['trades']} trades, PnL=${r['total_pnl']:+.2f}, "
                  f"WR={r['win_rate']:.0f}%, PF={r['profit_factor']:.2f}")
        else:
            print("-> no trades")
        time.sleep(0.3)

    if not results:
        print("\nNo trades across all symbols!")
        return

    total_trades = sum(r["trades"] for r in results)
    total_pnl = sum(r["total_pnl"] for r in results)
    total_wins = sum(r["wins"] for r in results)
    avg_wr = total_wins / total_trades * 100 if total_trades > 0 else 0
    max_dd = max(r["max_dd_pct"] for r in results)

    print("\n" + "=" * 90)
    print("AGGREGATE RESULTS (BUY-ONLY, NO LEVERAGE)")
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
              f"{r['avg_bars_held']:>5.0f}h")

    winners = [r for r in results if r["total_pnl"] > 0]
    losers = [r for r in results if r["total_pnl"] <= 0]
    print(f"\nWinners: {len(winners)} | Losers: {len(losers)}")
    if winners:
        print(f"  Best:  {winners[0]['symbol']} ${winners[0]['total_pnl']:+.2f}")
    if losers:
        print(f"  Worst: {losers[-1]['symbol']} ${losers[-1]['total_pnl']:+.2f}")

    output = {
        "strategy": "fibonacci_pullback",
        "params": {"swing_lookback": SWING_LOOKBACK, "trend_bars": TREND_BARS,
                   "fib_min": FIB_ENTRY_MIN, "fib_max": FIB_ENTRY_MAX,
                   "min_swing_pct": MIN_SWING_SIZE_PCT, "max_hold_bars": MAX_HOLD_BARS},
        "config": {"capital": INITIAL_CAPITAL, "size_pct": POSITION_SIZE_PCT,
                   "buy_only": True, "leverage": 1, "days": 90},
        "aggregate": {"trades": total_trades, "pnl": round(total_pnl, 2),
                      "return_pct": round(total_pnl / INITIAL_CAPITAL * 100, 2),
                      "win_rate": round(avg_wr, 1), "max_dd": round(max_dd, 2)},
        "per_symbol": results,
    }
    out_path = OUTPUT_DIR / "fib_pullback_90day_results.json"
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2, default=str)
    print(f"\nResults saved to {out_path}")


if __name__ == "__main__":
    main()

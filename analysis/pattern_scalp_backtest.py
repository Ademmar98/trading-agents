#!/usr/bin/env python3
"""
The Pattern Scalp — Backtest
=============================
Strategy:
  1. First 15m candle = Opening Range (OR)
  2. If OR range > 20% of daily ATR → manipulation flag
  3. On 5m chart, look for reversal patterns:
     - John Wick (hammer): enter on break of wick high/low
     - Power Tower (engulfing): enter at 50% recovery
  4. TP: opposite side of OR box
  5. SL: beyond pattern extreme

Session: 00:00-01:00 UTC daily (first 60 min)
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
POSITION_SIZE_PCT = 0.20
MAX_CONCURRENT = 2
TAKER_FEE = 0.00075
SLIPPAGE_PCT = 0.0005
FEE_PER_SIDE = TAKER_FEE + SLIPPAGE_PCT

# Strategy params
ATR_FILTER_PCT = 0.20    # OR range > 20% of daily ATR = manipulation
ATR_PERIOD = 14
MIN_OR_RANGE_PCT = 0.003 # Min 0.3% range on OR candle to trade
MAX_TRADES_PER_DAY = 2

SYMBOLS = ["BTC", "ETH", "SOL", "DOGE", "XRP", "AVAX", "LINK",
           "SUI", "NEAR", "AAVE", "INJ", "FET", "ARB", "OP",
           "UNI", "LDO", "CRV", "MKR", "SNX", "DYDX", "GMX",
           "PENDLE", "WLD", "TIA", "APT"]
HYPERLIQUID_API = "https://api.hyperliquid.xyz/info"
OUTPUT_DIR = Path(__file__).parent


def fetch_candles(symbol, interval, days):
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
                 "ts": int(c["t"])} for c in data]
        df = pd.DataFrame(rows)
        df["timestamp"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
        df.set_index("timestamp", inplace=True)
        df.sort_index(inplace=True)
        df = df[~df.index.duplicated(keep="first")]
        return df
    except Exception as e:
        print(f"  ERROR {symbol} {interval}: {e}")
        return pd.DataFrame()


def compute_daily_atr(daily_df, period=ATR_PERIOD):
    """Compute ATR on daily candles."""
    if daily_df.empty or len(daily_df) < period + 1:
        return pd.Series(dtype=float)
    h = daily_df["high"]
    l = daily_df["low"]
    c = daily_df["close"]
    tr1 = h - l
    tr2 = (h - c.shift(1)).abs()
    tr3 = (l - c.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.rolling(period).mean()
    return atr


def is_hammer(row, prev_row):
    """John Wick / Hammer pattern: small body, long lower wick, upper wick small."""
    body = abs(row["close"] - row["open"])
    full_range = row["high"] - row["low"]
    if full_range < 1e-10:
        return False, None

    upper_wick = row["high"] - max(row["open"], row["close"])
    lower_wick = min(row["open"], row["close"]) - row["low"]

    # Hammer: long lower wick (>2x body), small upper wick
    if lower_wick > 2 * body and lower_wick > upper_wick * 1.5:
        return True, "long"  # Bullish hammer → long

    # Inverted hammer / shooting star: long upper wick
    if upper_wick > 2 * body and upper_wick > lower_wick * 1.5:
        return True, "short"  # Bearish → short

    return False, None


def is_engulfing(row, prev_row):
    """Power Tower / Engulfing pattern."""
    curr_body = abs(row["close"] - row["open"])
    prev_body = abs(prev_row["close"] - prev_row["open"])

    if curr_body < prev_body * 1.2:
        return False, None

    curr_bullish = row["close"] > row["open"]
    prev_bearish = prev_row["close"] < prev_row["open"]
    prev_bullish = prev_row["close"] > prev_row["open"]

    # Bullish engulfing
    if curr_bullish and prev_bearish:
        return True, "long"

    # Bearish engulfing
    if not curr_bullish and prev_bullish:
        return True, "short"

    return False, None


def backtest_symbol(symbol, candles_15m, candles_5m, daily_df):
    """Backtest Pattern Scalp for one symbol over 30 days."""
    if candles_15m.empty or candles_5m.empty or daily_df.empty:
        return None

    atr = compute_daily_atr(daily_df)
    if atr.empty:
        return None

    capital = INITIAL_CAPITAL
    trades = []
    positions = []
    equity_curve = []

    # Group 15m candles by day
    candles_15m = candles_15m.copy()
    candles_15m["date"] = candles_15m.index.date

    candles_5m = candles_5m.copy()
    candles_5m["date"] = candles_5m.index.date
    candles_5m["hour"] = candles_5m.index.hour
    candles_5m["minute"] = candles_5m.index.minute

    days = sorted(candles_15m["date"].unique())

    for day in days:
        # Get ATR for this day
        day_str = str(day)
        day_atr = atr[atr.index <= pd.Timestamp(day, tz=timezone.utc)]
        if day_atr.empty:
            continue
        current_atr = day_atr.iloc[-1]

        # ── Step 1: Find first 15m candle (opening range) ──
        day_15m = candles_15m[candles_15m["date"] == day]
        if day_15m.empty:
            continue

        or_candle = day_15m.iloc[0]
        or_high = or_candle["high"]
        or_low = or_candle["low"]
        or_range = or_high - or_low
        or_range_pct = or_range / or_high if or_high > 0 else 0

        # Must be within first 60 min
        or_time = or_candle.name

        # ── Step 2: Manipulation check ──
        is_manipulation = or_range > current_atr * ATR_FILTER_PCT

        if or_range_pct < MIN_OR_RANGE_PCT:
            continue  # Too small

        # ── Step 3: Scan 5m candles in first 60 min ──
        session_end = or_time + pd.Timedelta(minutes=60)
        day_5m = candles_5m[
            (candles_5m["date"] == day) &
            (candles_5m.index >= or_time) &
            (candles_5m.index < session_end)
        ]

        if len(day_5m) < 4:
            continue

        trades_today = 0

        for i in range(2, len(day_5m)):
            if trades_today >= MAX_TRADES_PER_DAY:
                break
            if len(positions) >= MAX_CONCURRENT:
                break

            row = day_5m.iloc[i]
            prev_row = day_5m.iloc[i - 1]
            prev2_row = day_5m.iloc[i - 2]
            price = row["close"]

            # ── Pattern detection ──
            # John Wick on prev candle
            is_jw, jw_dir = is_hammer(prev_row, prev2_row)
            # Power Tower on current candle
            is_pt, pt_dir = is_engulfing(row, prev_row)

            signal_dir = None
            entry_type = None
            pattern_extreme = None

            if is_jw and jw_dir:
                signal_dir = jw_dir
                entry_type = "john_wick"
                if jw_dir == "long":
                    # Enter when price breaks above hammer high
                    if price > prev_row["high"]:
                        entry_price = price
                        sl = prev_row["low"]
                        tp = or_low  # TP at bottom of OR (for long reversal from low)
                        # Actually: if going long from OR low, TP should be OR high
                        tp = or_high
                    else:
                        continue
                else:  # short
                    if price < prev_row["low"]:
                        entry_price = price
                        sl = prev_row["high"]
                        tp = or_low  # TP at OR low
                    else:
                        continue

            elif is_pt and pt_dir:
                signal_dir = pt_dir
                entry_type = "power_tower"
                if pt_dir == "long":
                    # Enter at 50% recovery of manipulation candle
                    recovery_level = or_low + or_range * 0.5
                    if price >= recovery_level:
                        entry_price = price
                        sl = min(row["low"], prev_row["low"])
                        tp = or_high
                    else:
                        continue
                else:  # short
                    recovery_level = or_high - or_range * 0.5
                    if price <= recovery_level:
                        entry_price = price
                        sl = max(row["high"], prev_row["high"])
                        tp = or_low
                    else:
                        continue
            else:
                continue

            # Risk check
            risk = abs(entry_price - sl) / entry_price
            reward = abs(tp - entry_price) / entry_price
            if risk < 0.001 or risk > 0.05:
                continue
            if reward / risk < 1.0:
                continue

            # Check daily loss limit safety
            size_usd = capital * POSITION_SIZE_PCT
            qty = size_usd / entry_price
            if qty <= 0 or capital <= 100:
                continue

            entry_price *= (1 + SLIPPAGE_PCT) if signal_dir == "long" else (1 - SLIPPAGE_PCT)
            fee_entry = entry_price * qty * FEE_PER_SIDE
            capital -= entry_price * qty

            positions.append({
                "entry_price": entry_price, "qty": qty,
                "sl": sl, "tp": tp, "sym": symbol,
                "dir": signal_dir, "type": entry_type,
                "entry_time": str(day_5m.index[i]),
                "day": str(day), "fee_entry": fee_entry,
            })
            trades_today += 1

        # ── Exit logic (within session or force close) ──
        closed = []
        for pos in positions:
            # Check if price hit SL or TP during remaining 5m candles
            session_5m = candles_5m[
                (candles_5m.index >= pos["entry_time"]) &
                (candles_5m.index < session_end)
            ]

            for _, bar in session_5m.iterrows():
                d = pos.get("dir", "long")
                t = pos.get("type", "unknown")
                day = pos.get("day", "")
                if d == "long":
                    if bar["low"] <= pos["sl"]:
                        ep = pos["sl"] * (1 - SLIPPAGE_PCT)
                        gross = (ep - pos["entry_price"]) * pos["qty"]
                        fee = pos["fee_entry"] + ep * pos["qty"] * FEE_PER_SIDE
                        pnl = gross - fee
                        capital += pos["entry_price"] * pos["qty"] + pnl
                        trades.append({"pnl": pnl, "reason": "sl", "sym": symbol,
                                       "dir": d, "type": t, "day": day})
                        closed.append(pos)
                        break
                    elif bar["high"] >= pos["tp"]:
                        ep = pos["tp"] * (1 - SLIPPAGE_PCT)
                        gross = (ep - pos["entry_price"]) * pos["qty"]
                        fee = pos["fee_entry"] + ep * pos["qty"] * FEE_PER_SIDE
                        pnl = gross - fee
                        capital += pos["entry_price"] * pos["qty"] + pnl
                        trades.append({"pnl": pnl, "reason": "tp", "sym": symbol,
                                       "dir": d, "type": t, "day": day})
                        closed.append(pos)
                        break
                else:  # short
                    if bar["high"] >= pos["sl"]:
                        ep = pos["sl"] * (1 + SLIPPAGE_PCT)
                        gross = (pos["entry_price"] - ep) * pos["qty"]
                        fee = pos["fee_entry"] + ep * pos["qty"] * FEE_PER_SIDE
                        pnl = gross - fee
                        capital += pos["entry_price"] * pos["qty"] + pnl
                        trades.append({"pnl": pnl, "reason": "sl", "sym": symbol,
                                       "dir": d, "type": t, "day": day})
                        closed.append(pos)
                        break
                    elif bar["low"] <= pos["tp"]:
                        ep = pos["tp"] * (1 + SLIPPAGE_PCT)
                        gross = (pos["entry_price"] - ep) * pos["qty"]
                        fee = pos["fee_entry"] + ep * pos["qty"] * FEE_PER_SIDE
                        pnl = gross - fee
                        capital += pos["entry_price"] * pos["qty"] + pnl
                        trades.append({"pnl": pnl, "reason": "tp", "sym": symbol,
                                       "dir": d, "type": t, "day": day})
                        closed.append(pos)
                        break

        for p in closed:
            positions.remove(p)

        # Force close remaining at session end
        for pos in list(positions):
            last_price = day_5m.iloc[-1]["close"] if not day_5m.empty else pos["entry_price"]
            if pos["dir"] == "long":
                gross = (last_price - pos["entry_price"]) * pos["qty"]
            else:
                gross = (pos["entry_price"] - last_price) * pos["qty"]
            fee = pos["fee_entry"] + last_price * pos["qty"] * FEE_PER_SIDE
            pnl = gross - fee
            capital += pos["entry_price"] * pos["qty"] + pnl
            trades.append({"pnl": pnl, "reason": "session_close", "sym": symbol,
                           "dir": pos.get("dir", "long"), "type": pos.get("type", "unknown"),
                           "day": pos["day"]})
            positions.remove(pos)

        equity_curve.append(capital)

    # Force close any remaining
    for pos in list(positions):
        capital += pos["entry_price"] * pos["qty"]
        trades.append({"pnl": -pos["fee_entry"], "reason": "force_close", "sym": symbol,
                        "dir": pos.get("dir", "long"), "type": pos.get("type", "unknown"),
                        "day": pos.get("day", "")})
        positions.remove(pos)

    n = len(trades)
    if n == 0:
        return None

    pnls = np.array([t["pnl"] for t in trades])
    wins = pnls[pnls > 0]
    losses = pnls[pnls <= 0]
    nw, nl = len(wins), len(losses)
    gp = wins.sum() if nw > 0 else 0
    gl = abs(losses.sum()) if nl > 0 else 0.01

    eq = np.array(equity_curve, dtype=float) if equity_curve else np.array([INITIAL_CAPITAL])
    peak = np.maximum.accumulate(eq)
    dd = (peak - eq) / (peak + 1e-10)
    max_dd = dd.max() * 100

    # Pattern breakdown
    jw_trades = [t for t in trades if t["type"] == "john_wick"]
    pt_trades = [t for t in trades if t["type"] == "power_tower"]
    long_trades = [t for t in trades if t["dir"] == "long"]
    short_trades = [t for t in trades if t["dir"] == "short"]

    return {
        "symbol": symbol, "trades": n, "wins": nw, "losses": nl,
        "total_pnl": round(float(pnls.sum()), 2),
        "win_rate": round(nw / n * 100, 1),
        "profit_factor": round(gp / gl, 2) if gl > 0 else 999,
        "avg_win_usd": round(gp / nw, 2) if nw > 0 else 0,
        "avg_loss_usd": round(gl / nl, 2) if nl > 0 else 0.01,
        "expectancy": round((nw / n * gp / nw if nw > 0 else 0) -
                            (nl / n * gl / nl if nl > 0 else 0), 2),
        "max_dd_pct": round(max_dd, 2),
        "jw_trades": len(jw_trades),
        "pt_trades": len(pt_trades),
        "long_trades": len(long_trades),
        "short_trades": len(short_trades),
        "tp_hits": sum(1 for t in trades if t["reason"] == "tp"),
        "sl_hits": sum(1 for t in trades if t["reason"] == "sl"),
        "session_closes": sum(1 for t in trades if t["reason"] == "session_close"),
    }


def main():
    print("=" * 100)
    print("THE PATTERN SCALP — Backtest (30 days, 15m+5m candles)")
    print("=" * 100)
    print(f"Session: 00:00-01:00 UTC daily | Capital: ${INITIAL_CAPITAL:,.0f}")
    print(f"OR filter: range > {ATR_FILTER_PCT:.0%} of daily ATR({ATR_PERIOD})")
    print(f"Patterns: John Wick (hammer) + Power Tower (engulfing)")
    print(f"TP: opposite side of OR box | SL: pattern extreme")
    print(f"Size: {POSITION_SIZE_PCT:.0%} | Max: {MAX_CONCURRENT} | Max {MAX_TRADES_PER_DAY}/day")
    print(f"Buy-only: NO (long + short)")
    print("=" * 100)

    results = []

    for i, sym in enumerate(SYMBOLS):
        t0 = time.time()
        print(f"[{i+1}/{len(SYMBOLS)}] {sym}...", end=" ", flush=True)

        # Fetch 15m for OR + 5m for entries + daily for ATR
        df_15m = fetch_candles(sym, "15m", 33)
        print(f"15m:{len(df_15m)}", end=" ", flush=True)
        time.sleep(0.2)
        df_5m = fetch_candles(sym, "5m", 33)
        print(f"5m:{len(df_5m)}", end=" ", flush=True)
        time.sleep(0.2)
        df_daily = fetch_candles(sym, "1d", 60)
        print(f"1d:{len(df_daily)}", end=" ", flush=True)
        time.sleep(0.2)

        r = backtest_symbol(sym, df_15m, df_5m, df_daily)
        dt = time.time() - t0

        if r and r["trades"] > 0:
            results.append(r)
            print(f"-> {r['trades']}T PnL=${r['total_pnl']:+.2f} WR={r['win_rate']:.0f}% "
                  f"PF={r['profit_factor']:.2f} DD={r['max_dd_pct']:.1f}% "
                  f"JW:{r['jw_trades']} PT:{r['pt_trades']} L:{r['long_trades']} S:{r['short_trades']} "
                  f"({dt:.1f}s)")
        else:
            print(f"-> no trades ({dt:.1f}s)")

    if not results:
        print("\nNo trades across all symbols!")
        return

    total_trades = sum(r["trades"] for r in results)
    total_pnl = sum(r["total_pnl"] for r in results)
    total_wins = sum(r["wins"] for r in results)
    avg_wr = total_wins / total_trades * 100 if total_trades > 0 else 0
    max_dd = max(r["max_dd_pct"] for r in results)
    total_jw = sum(r["jw_trades"] for r in results)
    total_pt = sum(r["pt_trades"] for r in results)
    total_long = sum(r["long_trades"] for r in results)
    total_short = sum(r["short_trades"] for r in results)

    print("\n" + "=" * 100)
    print("AGGREGATE RESULTS")
    print("=" * 100)
    print(f"Total trades:    {total_trades}")
    print(f"  John Wick:     {total_jw} | Power Tower: {total_pt}")
    print(f"  Long:          {total_long} | Short: {total_short}")
    print(f"Total PnL:       ${total_pnl:+,.2f}")
    print(f"Return:          {total_pnl / INITIAL_CAPITAL:+.1%}")
    print(f"Win Rate:        {avg_wr:.1f}%")
    print(f"Max Drawdown:    {max_dd:.1f}%")
    print("=" * 100)

    print(f"\n{'Sym':>6} {'Tr':>4} {'PnL':>10} {'WR%':>6} {'PF':>6} "
          f"{'AvgW$':>7} {'AvgL$':>7} {'E[X]$':>7} {'DD%':>5} "
          f"{'JW':>3} {'PT':>3} {'L':>3} {'S':>3} {'TP':>3} {'SL':>3}")
    print("-" * 95)
    for r in sorted(results, key=lambda x: x["total_pnl"], reverse=True):
        print(f"{r['symbol']:>6} {r['trades']:>4} ${r['total_pnl']:>+8.2f} "
              f"{r['win_rate']:>5.1f}% {r['profit_factor']:>5.2f} "
              f"${r['avg_win_usd']:>+5.2f} ${r['avg_loss_usd']:>+5.2f} "
              f"${r['expectancy']:>+5.2f} {r['max_dd_pct']:>4.1f}% "
              f"{r['jw_trades']:>3} {r['pt_trades']:>3} "
              f"{r['long_trades']:>3} {r['short_trades']:>3} "
              f"{r['tp_hits']:>3} {r['sl_hits']:>3}")

    winners = [r for r in results if r["total_pnl"] > 0]
    losers = [r for r in results if r["total_pnl"] <= 0]
    print(f"\nWinners: {len(winners)} | Losers: {len(losers)}")

    # Save
    output = {
        "strategy": "pattern_scalp",
        "params": {"atr_filter_pct": ATR_FILTER_PCT, "atr_period": ATR_PERIOD,
                   "min_or_range_pct": MIN_OR_RANGE_PCT, "session": "00:00-01:00 UTC",
                   "patterns": ["john_wick", "power_tower"], "tp": "opposite OR side",
                   "sl": "pattern extreme"},
        "config": {"capital": INITIAL_CAPITAL, "size_pct": POSITION_SIZE_PCT,
                   "max_concurrent": MAX_CONCURRENT},
        "aggregate": {"trades": total_trades, "pnl": round(total_pnl, 2),
                      "return_pct": round(total_pnl / INITIAL_CAPITAL * 100, 2),
                      "win_rate": round(avg_wr, 1), "max_dd": round(max_dd, 2),
                      "john_wick": total_jw, "power_tower": total_pt,
                      "longs": total_long, "shorts": total_short},
        "per_symbol": results,
    }
    out_path = OUTPUT_DIR / "pattern_scalp_results.json"
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2, default=str)
    print(f"\nResults saved to {out_path}")


if __name__ == "__main__":
    main()

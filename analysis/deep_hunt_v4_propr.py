#!/usr/bin/env python3
"""
Deep Hunting v4 — Strict Propr Challenge Simulation
====================================================
STOPS immediately when:
  - 6% max drawdown hit ($300 from peak equity) -> CHALLENGE FAILED
  - 3% daily loss hit ($150 in one day) -> all positions closed, no more trades that day

$5,000 capital, 30 days, 12 pairs, no leverage.
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
DAILY_LOSS_LIMIT = INITIAL_CAPITAL * 0.03   # $150
MAX_DD_LIMIT = INITIAL_CAPITAL * 0.06       # $300
TAKER_FEE = 0.00075
SLIPPAGE_PCT = 0.0005
FEE_PER_SIDE = TAKER_FEE + SLIPPAGE_PCT
RSI_PERIOD = 14
CVD_LOOKBACK = 20
LOOKBACK = 50
VOLUME_SPIKE_MULT = 1.8
CVD_THRESHOLD = -0.8
DIP_THRESHOLD = 0.05
RSI_OVERSOLD = 30

SYMBOLS = ["BTC", "ETH", "ATOM", "DYDX", "SOL", "AVAX", "BNB", "APE", "OP", "LTC",
           "ARB", "DOGE", "INJ", "SUI", "kPEPE", "CRV", "LDO", "LINK", "STX", "CFX",
           "GMX", "SNX", "XRP", "BCH", "APT", "AAVE", "COMP", "WLD", "YGG", "TRX",
           "kSHIB", "UNI", "SEI", "RUNE", "ZRO", "DOT", "BANANA", "TRB", "BIGTIME",
           "KAS", "BLUR", "TIA", "BSV", "ADA", "MINA", "POLYX", "GAS", "PENDLE", "FET",
           "NEAR", "MEME", "ORDI", "NEO", "ZEN", "FIL", "PYTH", "SUSHI", "IMX", "kBONK",
           "GMT", "SUPER", "JUP", "kLUNC", "RSR", "GALA", "JTO", "ACE", "WIF", "CAKE",
           "PEOPLE", "ENS", "ETC", "XAI", "MANTA", "UMA", "ONDO", "ALT", "ZETA", "DYM",
           "W", "STRK", "TAO", "AR", "kFLOKI", "BOME", "ETHFI", "ENA", "MNT", "TNSR",
           "SAGA", "MERL", "HBAR", "POPCAT", "EIGEN", "REZ", "NOT", "TURBO", "BRETT",
           "IO", "ZK", "RENDER", "POL", "CELO", "HMSTR", "kNEIRO", "GOAT", "MOODENG",
           "GRASS", "PURR", "PNUT", "XLM", "SAND", "IOTA", "ALGO", "HYPE", "ME", "MOVE",
           "VIRTUAL", "PENGU", "USUAL", "FARTCOIN", "AIXBT", "BIO", "GRIFFAIN", "SPX",
           "S", "MORPHO", "TRUMP", "MELANIA", "ANIME", "VINE", "VVV", "BERA", "LAYER",
           "KAITO", "NIL", "PAXG", "BABY", "WCT", "HYPER", "ZORA", "INIT", "NXPC",
           "SOPH", "RESOLV", "SYRUP", "PUMP", "PROVE", "XPL", "WLFI", "LINEA", "SKY",
           "ASTER", "AVNT", "STBL", "0G", "HEMI", "APEX", "2Z", "ZEC", "MON", "MET",
           "MEGA", "CC", "ICP", "AERO", "STABLE", "FOGO", "LIT", "XMR", "AXS", "DASH",
           "SKR", "AZTEC", "CHIP", "GRAM", "CASHCAT"]
HYPERLIQUID_API = "https://api.hyperliquid.xyz/info"
OUTPUT_DIR = Path(__file__).parent


def fetch_candles(symbol, interval="1h", days=30):
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
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period).mean()
    return 100 - (100 / (1 + avg_gain / (avg_loss + 1e-10)))


def compute_cvd_zscore(volume, closes, lookback=CVD_LOOKBACK):
    direction = np.sign(closes.diff())
    cvd = (volume * direction).cumsum()
    cvd_mean = cvd.rolling(lookback).mean()
    cvd_std = cvd.rolling(lookback).std()
    return (cvd - cvd_mean) / (cvd_std + 1e-10)


def equity_of(positions, prices):
    """Calculate total equity = free capital + margin locked + unrealized PnL"""
    margin_locked = sum(p["entry_price"] * p["qty"] for p in positions)
    unrealized = sum(prices[p["sym"]] * p["qty"] - p["entry_price"] * p["qty"]
                     for p in positions)
    return margin_locked + unrealized


def close_position(pos, exit_price, reason, capital, daily_pnl, trades):
    exit_price *= (1 - SLIPPAGE_PCT)
    gross = (exit_price - pos["entry_price"]) * pos["qty"]
    fee = pos["entry_price"] * pos["qty"] * FEE_PER_SIDE + exit_price * pos["qty"] * FEE_PER_SIDE
    pnl = gross - fee
    capital += pos["entry_price"] * pos["qty"] + pnl
    daily_pnl += pnl
    trades.append({"pnl": pnl, "reason": reason, "sym": pos["sym"],
                   "entry": pos["entry_price"], "exit": exit_price})
    return capital, daily_pnl


def backtest_propr(all_data, cfg):
    """Single portfolio-level sim with strict Propr enforcement."""
    # Precompute indicators
    processed = {}
    for sym, df in all_data.items():
        if df.empty or len(df) < 220:
            continue
        d = df.copy()
        d["rsi"] = compute_rsi(d["close"])
        d["cvd_z"] = compute_cvd_zscore(d["volume"], d["close"])
        d["vol_ma"] = d["volume"].rolling(20).mean()
        d["vol_spike"] = d["volume"] / (d["vol_ma"] + 1e-10)
        d["recent_high"] = d["high"].rolling(LOOKBACK).max()
        d["dip_pct"] = (d["recent_high"] - d["close"]) / d["recent_high"]
        d["signal"] = (
            (d["dip_pct"] >= DIP_THRESHOLD) &
            (d["rsi"] < RSI_OVERSOLD) &
            (d["vol_spike"] > VOLUME_SPIKE_MULT) &
            (d["cvd_z"] < CVD_THRESHOLD)
        )
        if cfg.get("trend_filter"):
            d["ema50"] = d["close"].ewm(span=50).mean()
            d["ema200"] = d["close"].ewm(span=200).mean()
            d["signal"] = d["signal"] & (d["ema50"] > d["ema200"])
        processed[sym] = d

    if not processed:
        return None

    sl_pct = cfg["sl_pct"]
    tp_pct = cfg["tp_pct"]
    pos_size_pct = cfg.get("pos_size_pct", 0.30)
    max_concurrent = cfg.get("max_concurrent", 3)
    trail_activation = cfg.get("trail_activation", 0.0)
    trail_distance = cfg.get("trail_distance", 0.015)
    cooldown = cfg.get("cooldown", 3)
    max_hold = cfg.get("max_hold", 24)

    capital = INITIAL_CAPITAL
    peak_equity = INITIAL_CAPITAL
    positions = []
    trades = []
    daily_pnl = 0.0
    current_day = None
    daily_trading_halted = False
    challenge_failed = False
    fail_reason = ""
    fail_bar = 0
    last_entry_bar = {s: -cooldown - 1 for s in processed}
    equity_curve = []
    daily_log = []

    ref_sym = list(processed.keys())[0]
    timestamps = processed[ref_sym].index
    start_bar = 220

    for bar_idx in range(start_bar, len(timestamps)):
        ts = timestamps[bar_idx]

        # Get current prices for all symbols
        prices = {}
        for sym in processed:
            if bar_idx < len(processed[sym]):
                prices[sym] = processed[sym].iloc[bar_idx]["close"]
            else:
                prices[sym] = processed[sym].iloc[-1]["close"]  # use last known

        # ── Daily reset ──
        day = ts.date()
        if current_day is None or day != current_day:
            if current_day is not None:
                daily_log.append({
                    "day": str(current_day),
                    "daily_pnl": round(daily_pnl, 2),
                    "equity": round(equity_of(positions, prices) + capital -
                                    sum(p["entry_price"] * p["qty"] for p in positions), 2),
                })
            daily_pnl = 0.0
            current_day = day
            daily_trading_halted = False

        # ── Calculate current equity ──
        margin_locked = sum(p["entry_price"] * p["qty"] for p in positions)
        unrealized = sum(prices[p["sym"]] * p["qty"] - p["entry_price"] * p["qty"]
                         for p in positions if p["sym"] in prices)
        total_equity = capital + margin_locked + unrealized
        # total_equity = free cash + what positions are worth
        # Actually: capital already has margin deducted. So:
        # total_equity = capital + sum(current_price * qty for each position)
        total_equity = capital + sum(prices[p["sym"]] * p["qty"]
                                     for p in positions if p["sym"] in prices)

        equity_curve.append(total_equity)

        # ── CHECK 1: Max drawdown ($300 / 6%) ──
        if total_equity > peak_equity:
            peak_equity = total_equity
        dd_amount = peak_equity - total_equity
        if dd_amount > MAX_DD_LIMIT:
            challenge_failed = True
            fail_reason = f"MAX DD HIT: ${dd_amount:.2f} > ${MAX_DD_LIMIT:.0f} ({dd_amount/INITIAL_CAPITAL:.1%})"
            fail_bar = bar_idx
            print(f"  !!! CHALLENGE FAILED at bar {bar_idx} ({ts}): {fail_reason}")
            # Force close all
            for pos in list(positions):
                if pos["sym"] in prices:
                    capital, daily_pnl = close_position(pos, prices[pos["sym"]],
                                                        "dd_breach", capital, daily_pnl, trades)
            positions = []
            break

        # ── CHECK 2: Daily loss limit ($150 / 3%) ──
        if daily_pnl <= -DAILY_LOSS_LIMIT:
            daily_trading_halted = True
            print(f"  !!! DAILY LIMIT at bar {bar_idx} ({ts}): daily_pnl=${daily_pnl:.2f}")
            # Close all positions
            for pos in list(positions):
                if pos["sym"] in prices:
                    capital, daily_pnl = close_position(pos, prices[pos["sym"]],
                                                        "daily_limit", capital, daily_pnl, trades)
            positions = []
            continue  # No more trades today

        if daily_trading_halted:
            equity_curve.append(total_equity)
            continue

        # ── Exit logic ──
        closed = []
        for pos in positions:
            sym = pos["sym"]
            if sym not in processed:
                continue
            if bar_idx >= len(processed[sym]):
                continue
            row = processed[sym].iloc[bar_idx]
            bars_held = bar_idx - pos["entry_bar"]

            # Update trailing
            if pos.get("trailing"):
                new_trail = row["high"] * (1 - trail_distance)
                if new_trail > pos["sl"]:
                    pos["sl"] = new_trail

            exit_price = exit_reason = None
            if row["low"] <= pos["sl"]:
                exit_price, exit_reason = pos["sl"], "stop_loss"
                if pos["sl"] > pos["entry_price"]:
                    exit_reason = "trail_stop"
            elif row["high"] >= pos["tp"]:
                exit_price, exit_reason = pos["tp"], "take_profit"
            elif bars_held >= max_hold:
                exit_price, exit_reason = row["close"], "time_exit"

            if exit_price:
                capital, daily_pnl = close_position(pos, exit_price, exit_reason,
                                                    capital, daily_pnl, trades)
                closed.append(pos)

        for p in closed:
            positions.remove(p)

        # ── Re-check daily limit after exits ──
        if daily_pnl <= -DAILY_LOSS_LIMIT:
            daily_trading_halted = True
            for pos in list(positions):
                if pos["sym"] in prices:
                    capital, daily_pnl = close_position(pos, prices[pos["sym"]],
                                                        "daily_limit", capital, daily_pnl, trades)
            positions = []
            continue

        # ── Entry ──
        held_syms = {p["sym"] for p in positions}
        for sym in processed:
            if len(positions) >= max_concurrent:
                break
            if sym in held_syms:
                continue
            if (bar_idx - last_entry_bar[sym]) < cooldown:
                continue
            if bar_idx >= len(processed[sym]):
                continue

            row = processed[sym].iloc[bar_idx]
            if not row["signal"]:
                continue

            entry_price = row["close"] * (1 + SLIPPAGE_PCT)
            size_usd = capital * pos_size_pct
            if size_usd <= 0 or capital <= 100:
                continue
            qty = size_usd / entry_price

            # Safety: worst-case loss from this trade
            worst_loss = entry_price * qty * sl_pct + entry_price * qty * FEE_PER_SIDE * 2
            if (daily_pnl - worst_loss) < -DAILY_LOSS_LIMIT:
                # Would breach daily limit on worst case — skip
                continue

            fee_entry = entry_price * qty * FEE_PER_SIDE
            capital -= entry_price * qty

            sl_price = entry_price * (1 - sl_pct)
            tp_price = entry_price * (1 + tp_pct)

            positions.append({
                "entry_price": entry_price, "qty": qty,
                "entry_bar": bar_idx, "sym": sym,
                "sl": sl_price, "tp": tp_price,
                "fee_entry": fee_entry,
                "trailing": False, "peak_price": row["close"],
            })
            last_entry_bar[sym] = bar_idx

        # Update trailing
        for pos in positions:
            sym = pos["sym"]
            if sym in processed and bar_idx < len(processed[sym]):
                price = processed[sym].iloc[bar_idx]["close"]
                if price > pos.get("peak_price", 0):
                    pos["peak_price"] = price
                    if not pos["trailing"] and trail_activation > 0:
                        if price > pos["entry_price"] * (1 + trail_activation):
                            pos["trailing"] = True
                            pos["sl"] = pos["entry_price"] * 1.001

    # ── Force close remaining ──
    if not challenge_failed and positions:
        last_bar = len(timestamps) - 1
        for pos in list(positions):
            sym = pos["sym"]
            if sym in processed:
                price = processed[sym].iloc[last_bar]["close"]
                capital, daily_pnl = close_position(pos, price, "force_close",
                                                    capital, daily_pnl, trades)

    # Record last day
    if current_day:
        daily_log.append({
            "day": str(current_day),
            "daily_pnl": round(daily_pnl, 2),
        })

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
    dd_arr = (peak - eq) / (peak + 1e-10)
    max_dd_pct = dd_arr.max() * 100
    max_dd_usd = (peak - eq).max()

    # Count daily limits hit
    daily_limits_hit = sum(1 for t in trades if t["reason"] == "daily_limit")
    dd_breaches = sum(1 for t in trades if t["reason"] == "dd_breach")

    # Per-symbol
    sym_stats = {}
    for t in trades:
        s = t["sym"]
        if s not in sym_stats:
            sym_stats[s] = {"pnl": 0, "trades": 0, "wins": 0, "losses": 0}
        sym_stats[s]["pnl"] += t["pnl"]
        sym_stats[s]["trades"] += 1
        if t["pnl"] > 0:
            sym_stats[s]["wins"] += 1
        else:
            sym_stats[s]["losses"] += 1

    # Reason breakdown
    reason_counts = {}
    for t in trades:
        r = t["reason"]
        reason_counts[r] = reason_counts.get(r, 0) + 1

    return {
        "trades": n, "wins": nw, "losses": nl,
        "total_pnl": round(float(pnls.sum()), 2),
        "final_capital": round(capital, 2),
        "return_pct": round((capital - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100, 2),
        "win_rate": round(nw / n * 100, 1),
        "profit_factor": round(gp / gl, 2) if gl > 0 else 999,
        "avg_win_usd": round(gp / nw, 2) if nw > 0 else 0,
        "avg_loss_usd": round(gl / nl, 2) if nl > 0 else 0.01,
        "expectancy": round((nw / n * gp / nw if nw > 0 else 0) -
                            (nl / n * gl / nl if nl > 0 else 0), 2),
        "max_dd_usd": round(float(max_dd_usd), 2),
        "max_dd_pct": round(max_dd_pct, 2),
        "challenge_failed": challenge_failed,
        "fail_reason": fail_reason,
        "daily_limits_hit": daily_limits_hit,
        "dd_breaches": dd_breaches,
        "reason_counts": reason_counts,
        "per_symbol": sym_stats,
        "daily_log": daily_log,
    }


CONFIGS = [
    # Conservative — 10% position, max 2 concurrent = 20% at risk
    {"name": "c10_2c_sl10_tp4_trail", "sl_pct": 0.10, "tp_pct": 0.04,
     "pos_size_pct": 0.10, "max_concurrent": 2,
     "trail_activation": 0.025, "trail_distance": 0.02, "max_hold": 36},
    {"name": "c10_2c_sl8_tp3_trail", "sl_pct": 0.08, "tp_pct": 0.03,
     "pos_size_pct": 0.10, "max_concurrent": 2,
     "trail_activation": 0.02, "trail_distance": 0.015, "max_hold": 24},

    # Single position — max safety
    {"name": "c10_1c_sl10_tp4_trail", "sl_pct": 0.10, "tp_pct": 0.04,
     "pos_size_pct": 0.10, "max_concurrent": 1,
     "trail_activation": 0.025, "trail_distance": 0.02, "max_hold": 36},
    {"name": "c05_1c_sl8_tp3_trail", "sl_pct": 0.08, "tp_pct": 0.03,
     "pos_size_pct": 0.05, "max_concurrent": 1,
     "trail_activation": 0.02, "trail_distance": 0.015, "max_hold": 24},

    # 7.5% position, 2 concurrent
    {"name": "c075_2c_sl10_tp4_trail", "sl_pct": 0.10, "tp_pct": 0.04,
     "pos_size_pct": 0.075, "max_concurrent": 2,
     "trail_activation": 0.025, "trail_distance": 0.02, "max_hold": 36},

    # Cooldown variants
    {"name": "c10_2c_cd4_sl10_tp4", "sl_pct": 0.10, "tp_pct": 0.04,
     "pos_size_pct": 0.10, "max_concurrent": 2, "cooldown": 4,
     "trail_activation": 0.025, "trail_distance": 0.02, "max_hold": 36},

    # No trailing — simpler
    {"name": "c10_2c_sl10_tp4_notrail", "sl_pct": 0.10, "tp_pct": 0.04,
     "pos_size_pct": 0.10, "max_concurrent": 2, "max_hold": 24},
]


def main():
    print("=" * 100)
    print("DEEP HUNTING v4 — STRICT PROPR CHALLENGE SIMULATION")
    print("=" * 100)
    print(f"PROPR RULES (ENFORCED):")
    print(f"  Daily loss limit:  ${DAILY_LOSS_LIMIT:.0f} (3% of ${INITIAL_CAPITAL:,.0f})")
    print(f"  Max drawdown:      ${MAX_DD_LIMIT:.0f} (6% of ${INITIAL_CAPITAL:,.0f})")
    print(f"  If daily hit  -> close all, no more trades that day")
    print(f"  If DD hit     -> CHALLENGE FAILED, simulation stops")
    print(f"Capital: ${INITIAL_CAPITAL:,.0f} | Period: 30 days | 12 pairs")
    print(f"Entry: Dip>={DIP_THRESHOLD:.0%}, RSI<{RSI_OVERSOLD}, "
          f"Vol>{VOLUME_SPIKE_MULT}x, CVD_z<{CVD_THRESHOLD}")
    print("=" * 100)

    # Fetch data
    print("\nFetching 30-day 1h candles...")
    all_data = {}
    for i, sym in enumerate(SYMBOLS):
        print(f"  [{i+1}/{len(SYMBOLS)}] {sym}...", end=" ", flush=True)
        df = fetch_candles(sym, "1h", days=30)
        all_data[sym] = df
        print(f"{len(df)} bars")
        time.sleep(0.3)

    # Run configs
    print(f"\nRunning {len(CONFIGS)} configurations...")
    print("NOTE: Position sizes are checked against daily loss limit before entry.\n")
    results = []

    for ci, cfg in enumerate(CONFIGS):
        name = cfg.get("name", f"cfg_{ci}")
        t0 = time.time()
        r = backtest_propr(all_data, cfg)
        dt = time.time() - t0

        if r:
            r["name"] = name
            r["cfg"] = {k: v for k, v in cfg.items() if k != "name"}
            r["elapsed"] = round(dt, 1)
            results.append(r)

            fail_tag = " *** FAILED ***" if r["challenge_failed"] else ""
            daily_tag = f" [daily_limits={r['daily_limits_hit']}]" if r['daily_limits_hit'] > 0 else ""
            print(f"  [{ci+1}/{len(CONFIGS)}] {name:40s} "
                  f"{r['trades']:3d}T PnL=${r['total_pnl']:>+8.2f} "
                  f"Final=${r['final_capital']:>8.2f} "
                  f"WR={r['win_rate']:5.1f}% PF={r['profit_factor']:5.2f} "
                  f"MaxDD=${r['max_dd_usd']:>6.0f}({r['max_dd_pct']:4.1f}%) "
                  f"({dt:.1f}s){fail_tag}{daily_tag}")
        else:
            print(f"  [{ci+1}/{len(CONFIGS)}] {name:40s} no trades ({dt:.1f}s)")

    if not results:
        print("\nNo results!")
        return

    # Sort by score: PnL - DD penalty (for prop firm)
    for r in results:
        dd_pen = max(0, r["max_dd_usd"] - MAX_DD_LIMIT * 0.5) * 2
        r["score"] = r["total_pnl"] - dd_pen + min(r["trades"], 50) * 1.5
    results.sort(key=lambda x: x["score"], reverse=True)

    # ── Summary ──
    passed = [r for r in results if not r["challenge_failed"]]
    failed = [r for r in results if r["challenge_failed"]]

    print("\n" + "=" * 100)
    print("ALL RESULTS (sorted by prop-firm-adjusted score)")
    print("=" * 100)
    print(f"{'#':>3} {'Name':40s} {'Tr':>4} {'PnL':>10} {'Final$':>10} "
          f"{'WR%':>6} {'PF':>6} {'MaxDD$':>8} {'DD%':>5} {'Failed':>7} {'Daily#':>6}")
    print("-" * 115)

    for i, r in enumerate(results):
        fail_tag = "YES" if r["challenge_failed"] else "no"
        marker = " <--" if i == 0 else ""
        print(f"{i+1:>3} {r['name']:40s} {r['trades']:>4} "
              f"${r['total_pnl']:>+8.2f} ${r['final_capital']:>8.2f} "
              f"{r['win_rate']:>5.1f}% {r['profit_factor']:>5.2f} "
              f"${r['max_dd_usd']:>6.0f} {r['max_dd_pct']:>4.1f}% "
              f"{fail_tag:>7} {r['daily_limits_hit']:>5}{marker}")

    print(f"\nSummary: {len(passed)} passed, {len(failed)} failed")

    if failed:
        print(f"\nFailed configs:")
        for r in failed:
            print(f"  {r['name']}: {r['fail_reason']}")

    # ── Best config detail ──
    viable = [r for r in results if not r["challenge_failed"]]
    if viable:
        best = viable[0]
        print("\n" + "=" * 100)
        print(f"BEST VIABLE: {best['name']}")
        print(f"SL={best['cfg']['sl_pct']:.0%} | TP={best['cfg']['tp_pct']:.0%} | "
              f"Size={best['cfg'].get('pos_size_pct', 0.30):.0%} | "
              f"MaxConc={best['cfg'].get('max_concurrent', 3)} | "
              f"Trail={best['cfg'].get('trail_activation', 'OFF')} | "
              f"MaxHold={best['cfg'].get('max_hold', 24)}")
        print("=" * 100)
        print(f"  Final Capital:    ${best['final_capital']:,.2f} "
              f"(${best['total_pnl']:+.2f}, {best['return_pct']:+.1f}%)")
        print(f"  Trades:           {best['trades']}")
        print(f"  Win Rate:         {best['win_rate']}%")
        print(f"  Profit Factor:    {best['profit_factor']}")
        print(f"  Max Drawdown:     ${best['max_dd_usd']:.2f} ({best['max_dd_pct']:.1f}%)")
        print(f"  Daily Limits Hit: {best['daily_limits_hit']}")
        print(f"  Challenge Failed: NO")
        print(f"  Reason breakdown: {best['reason_counts']}")

        print(f"\n  Per-symbol:")
        print(f"  {'Sym':>6} {'Tr':>4} {'W':>3} {'L':>3} {'PnL':>10}")
        print(f"  {'-'*30}")
        for sym, st in sorted(best["per_symbol"].items(),
                               key=lambda x: x[1]["pnl"], reverse=True):
            print(f"  {sym:>6} {st['trades']:>4} {st['wins']:>3} {st['losses']:>3} "
                  f"${st['pnl']:>+8.2f}")

        # Daily PnL log
        if best.get("daily_log"):
            print(f"\n  Daily PnL Log:")
            for dl in best["daily_log"][:35]:
                print(f"    {dl['day']}: ${dl['daily_pnl']:>+8.2f}")

    # Save
    output = {
        "challenge_rules": {
            "initial_capital": INITIAL_CAPITAL,
            "daily_loss_limit": DAILY_LOSS_LIMIT,
            "max_drawdown": MAX_DD_LIMIT,
        },
        "configs_tested": len(CONFIGS),
        "configs_passed": len(passed),
        "configs_failed": len(failed),
        "results": [{k: v for k, v in r.items()
                     if k not in ("per_symbol", "cfg", "daily_log")}
                    for r in results],
        "best_config": {k: v for k, v in viable[0].items()
                        if k not in ("per_symbol", "cfg", "daily_log")} if viable else None,
        "best_params": viable[0]["cfg"] if viable else None,
    }
    out_path = OUTPUT_DIR / "deep_hunt_v4_propr_strict.json"
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2, default=str)
    print(f"\nResults saved to {out_path}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Mass Backtest: Top 50 liquid crypto pairs x all strategies
Generates a comprehensive edge-hunting report.
"""
import sys
import os
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, stdev
import requests

# Fix Windows console encoding
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import TRADE_FEE_PCT, BACKTEST_SPREAD_PCT, INITIAL_BALANCE
from core.data_provider import fetch_binance_klines, _to_binance_symbol
from core.strategies import ALL_STRATEGIES, _ensure_family_merged
from core.pricing import round_sig

# ── Config ──
BACKTEST_BARS = 365  # ~1 year of daily bars
FEE_RATIO = (TRADE_FEE_PCT + BACKTEST_SPREAD_PCT) / 100.0
MAX_ACTIVE_POSITIONS = 3
RESULTS_DIR = Path(__file__).parent / "mass_backtest_results"
RESULTS_DIR.mkdir(exist_ok=True)


def fetch_top_50_by_volume():
    """Fetch top 50 USDT pairs by 24h quote volume from Binance."""
    print("Fetching top 50 pairs by 24h volume...")
    try:
        r = requests.get("https://api.binance.com/api/v3/ticker/24hr", timeout=15)
        data = r.json()
        usdt_pairs = [
            t for t in data
            if t["symbol"].endswith("USDT") and float(t.get("quoteVolume", 0)) > 0
        ]
        usdt_pairs.sort(key=lambda t: float(t["quoteVolume"]), reverse=True)
        top50 = []
        for t in usdt_pairs[:50]:
            sym = t["symbol"].replace("USDT", "/USD")
            vol = float(t["quoteVolume"])
            last = float(t["lastPrice"])
            top50.append({"symbol": sym, "volume_24h": vol, "price": last})
            print(f"  {sym:12s}  vol=${vol/1e6:,.0f}M  price=${last:,.4f}")
        return top50
    except Exception as e:
        print(f"Error fetching pairs: {e}")
        return []


def _calc_sl_tp(entry_price, side, volatility_pct, atr_pct=0, sl_mult=1.5, tp_mult=2.0):
    """Calculate SL/TP based on volatility."""
    vol_dec = (volatility_pct or 2) / 100.0
    atr_dec = max(atr_pct / 100.0, 0.005) if atr_pct > 0 else vol_dec
    sl_distance = max(atr_dec * sl_mult, vol_dec * sl_mult * 1.2)
    tp_distance = max(atr_dec * tp_mult, vol_dec * tp_mult * 0.8)
    if side == "BUY":
        sl = round_sig(entry_price * (1 - sl_distance))
        tp = round_sig(entry_price * (1 + tp_distance))
    else:
        sl = round_sig(entry_price * (1 + sl_distance))
        tp = round_sig(entry_price * (1 - tp_distance))
    return sl, tp


def _pos_value(pos, current_price):
    if pos["side"] == "BUY":
        return pos["qty"] * current_price
    return pos["qty"] * (2 * pos["entry"] - current_price)


def backtest_single_strategy(symbol, strategy_name, strategy_fn, ohlc, initial_capital=INITIAL_BALANCE):
    """Backtest a single strategy on a symbol."""
    if len(ohlc) < 50:
        return None

    cash = initial_capital
    positions = []
    trades = []
    equity_curve = []
    next_pos_id = 0

    for i in range(50, len(ohlc)):
        slice_data = ohlc[:i + 1]
        current = ohlc[i]
        high, low = current["high"], current["low"]
        close = current["close"]

        # Process existing positions
        remaining = []
        for pos in positions:
            side, entry, qty, sl, tp = pos["side"], pos["entry"], pos["qty"], pos["sl"], pos["tp"]
            exit_price = None
            reason = None

            hit_sl = (side == "BUY" and low <= sl) or (side == "SELL" and high >= sl)
            hit_tp = (side == "BUY" and high >= tp) or (side == "SELL" and low <= tp)

            if hit_sl:
                exit_price = sl
                reason = "SL"
            elif hit_tp:
                exit_price = tp
                reason = "TP"

            if exit_price:
                if side == "BUY":
                    pnl = (exit_price - entry) * qty
                else:
                    pnl = (entry - exit_price) * qty
                exit_fee = qty * exit_price * FEE_RATIO
                cash += qty * exit_price - exit_fee
                total_fee = exit_fee + qty * entry * FEE_RATIO
                net_pnl = pnl - total_fee
                trades.append({
                    "entry": entry, "exit": exit_price,
                    "pnl": net_pnl, "reason": reason,
                })
            else:
                remaining.append(pos)
        positions = remaining

        # Check for new signals
        if len(positions) < MAX_ACTIVE_POSITIONS:
            try:
                sig = strategy_fn(slice_data)
                if sig and sig.get("action") in ("BUY", "SELL"):
                    side = sig["action"]
                    qty = (cash * 15 / 100) / close  # 15% position size
                    if qty >= 0.001:
                        # Simple volatility estimate
                        recent = ohlc[max(0, i-20):i+1]
                        closes = [c["close"] for c in recent]
                        if len(closes) > 1:
                            returns = [(closes[j] - closes[j-1]) / closes[j-1] for j in range(1, len(closes))]
                            vol = stdev(returns) * 100 if len(returns) > 1 else 2.0
                        else:
                            vol = 2.0
                        sl, tp = _calc_sl_tp(close, side, vol)
                        cost = qty * close
                        entry_fee = cost * FEE_RATIO
                        total_cost = cost + entry_fee
                        if total_cost <= cash:
                            cash -= total_cost
                            next_pos_id += 1
                            positions.append({
                                "side": side, "entry": close, "qty": qty,
                                "sl": sl, "tp": tp, "pos_id": next_pos_id,
                            })
            except Exception:
                pass

        total_value = cash + sum(_pos_value(p, close) for p in positions)
        equity_curve.append(total_value)

    # Close remaining positions at market
    for pos in positions:
        exit_price = ohlc[-1]["close"]
        if pos["side"] == "BUY":
            pnl = (exit_price - pos["entry"]) * pos["qty"]
        else:
            pnl = (pos["entry"] - exit_price) * pos["qty"]
        exit_fee = pos["qty"] * exit_price * FEE_RATIO
        cash += pos["qty"] * exit_price - exit_fee
        trades.append({"entry": pos["entry"], "exit": exit_price, "pnl": pnl - exit_fee, "reason": "close"})

    # Compute metrics
    final_equity = equity_curve[-1] if equity_curve else initial_capital
    total_return = ((final_equity - initial_capital) / initial_capital) * 100
    total_trades = len(trades)
    winning = [t for t in trades if t["pnl"] > 0]
    losing = [t for t in trades if t["pnl"] < 0]
    win_rate = (len(winning) / total_trades * 100) if total_trades > 0 else 0
    avg_win = mean([t["pnl"] for t in winning]) if winning else 0
    avg_loss = mean([abs(t["pnl"]) for t in losing]) if losing else 0
    profit_factor = (sum(t["pnl"] for t in winning) / abs(sum(t["pnl"] for t in losing))
                     ) if losing and sum(t["pnl"] for t in losing) != 0 else float("inf")

    # Max drawdown
    peak = equity_curve[0] if equity_curve else initial_capital
    max_dd = 0
    for v in (equity_curve or []):
        if v > peak:
            peak = v
        dd = (peak - v) / peak * 100 if peak > 0 else 0
        if dd > max_dd:
            max_dd = dd

    # Sharpe
    returns = [equity_curve[i] - equity_curve[i - 1] for i in range(1, len(equity_curve))]
    sharpe = 0
    if len(returns) > 1 and stdev(returns) > 0:
        sharpe = (mean(returns) / stdev(returns)) * (365 ** 0.5)

    # Buy & hold benchmark
    benchmark_return = ((ohlc[-1]["close"] - ohlc[50]["close"]) / ohlc[50]["close"]) * 100

    return {
        "strategy": strategy_name,
        "symbol": symbol,
        "total_return": round(total_return, 2),
        "total_trades": total_trades,
        "win_rate": round(win_rate, 1),
        "profit_factor": round(profit_factor, 2) if profit_factor != float("inf") else None,
        "max_drawdown": round(max_dd, 2),
        "sharpe_ratio": round(sharpe, 2),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "benchmark_return": round(benchmark_return, 2),
        "beats_benchmark": total_return >= benchmark_return,
    }


def run_mass_backtest():
    """Run backtests on all strategies across top 50 pairs."""
    _ensure_family_merged()
    strategies = [(name, fn) for name, fn in ALL_STRATEGIES]

    print(f"\n{'='*80}")
    print(f"MASS BACKTEST: {len(strategies)} strategies x 50 pairs")
    print(f"Backtest period: {BACKTEST_BARS} daily bars (~1 year)")
    print(f"Fee per side: {FEE_RATIO*100:.2f}%")
    print(f"{'='*80}\n")

    # Fetch top 50 pairs
    pairs = fetch_top_50_by_volume()
    if not pairs:
        print("Failed to fetch pairs. Exiting.")
        return

    all_results = []
    pair_results = {}
    strategy_stats = {}

    for pair in pairs:
        symbol = pair["symbol"]
        print(f"\n{'─'*60}")
        print(f"Backtesting {symbol} (vol: ${pair['volume_24h']/1e6:,.0f}M)")
        print(f"{'─'*60}")

        # Fetch daily OHLC for this symbol
        ohlc = fetch_binance_klines(symbol, interval="1d", limit=BACKTEST_BARS + 100)
        if not ohlc or len(ohlc) < 50:
            print(f"  Skipped: insufficient data ({len(ohlc) if ohlc else 0} bars)")
            continue

        print(f"  Fetched {len(ohlc)} daily bars")
        pair_results[symbol] = []

        for strat_name, strat_fn in strategies:
            result = backtest_single_strategy(symbol, strat_name, strat_fn, ohlc)
            if result and result["total_trades"] >= 5:  # Minimum 5 trades
                all_results.append(result)
                pair_results[symbol].append(result)

                # Aggregate strategy stats
                if strat_name not in strategy_stats:
                    strategy_stats[strat_name] = {
                        "returns": [], "win_rates": [], "trades": [],
                        "sharpes": [], "drawdowns": [], "pf": [],
                        "beats_bh": 0, "total": 0,
                    }
                ss = strategy_stats[strat_name]
                ss["returns"].append(result["total_return"])
                ss["win_rates"].append(result["win_rate"])
                ss["trades"].append(result["total_trades"])
                ss["sharpes"].append(result["sharpe_ratio"])
                ss["drawdowns"].append(result["max_drawdown"])
                if result["profit_factor"] is not None:
                    ss["pf"].append(result["profit_factor"])
                ss["beats_bh"] += 1 if result["beats_benchmark"] else 0
                ss["total"] += 1

                status = "WIN" if result["total_return"] > 0 else "LOSE"
                print(f"  {strat_name:40s} {status} {result['total_return']:+6.1f}%  "
                      f"WR:{result['win_rate']:4.1f}%  N:{result['total_trades']:3d}  "
                      f"DD:{result['max_drawdown']:5.1f}%")

        time.sleep(0.1)  # Rate limit

    # ── Generate Report ──
    print(f"\n\n{'='*80}")
    print("SUMMARY REPORT")
    print(f"{'='*80}")

    # Strategy ranking by average return
    print(f"\n{'Strategy':45s} {'Avg Ret%':>8s} {'Avg WR%':>7s} {'Avg N':>5s} "
          f"{'Avg Sharpe':>10s} {'Avg DD%':>7s} {'Avg PF':>6s} {'Beats BH':>8s}")
    print("─" * 100)

    ranked = sorted(strategy_stats.items(), key=lambda x: mean(x[1]["returns"]), reverse=True)
    for name, stats in ranked:
        if stats["total"] == 0:
            continue
        avg_ret = mean(stats["returns"])
        avg_wr = mean(stats["win_rates"])
        avg_n = mean(stats["trades"])
        avg_sh = mean(stats["sharpes"])
        avg_dd = mean(stats["drawdowns"])
        avg_pf = mean(stats["pf"]) if stats["pf"] else 0
        beats = f"{stats['beats_bh']}/{stats['total']}"
        print(f"  {name:43s} {avg_ret:+7.1f}% {avg_wr:6.1f}% {avg_n:5.1f} "
              f"{avg_sh:10.2f} {avg_dd:6.1f}% {avg_pf:6.2f} {beats:>8s}")

    # Top 10 strategies
    print(f"\n{'='*80}")
    print("TOP 10 STRATEGIES (by avg return across all pairs)")
    print(f"{'='*80}")
    for i, (name, stats) in enumerate(ranked[:10], 1):
        if stats["total"] == 0:
            continue
        avg_ret = mean(stats["returns"])
        print(f"  {i:2d}. {name:40s} {avg_ret:+.1f}% avg return "
              f"(n={stats['total']} pairs)")

    # Bottom 10 strategies
    print(f"\n{'='*80}")
    print("BOTTOM 10 STRATEGIES (by avg return)")
    print(f"{'='*80}")
    non_zero = [(n, s) for n, s in ranked if s["total"] > 0]
    for i, (name, stats) in enumerate(non_zero[-10:], 1):
        avg_ret = mean(stats["returns"])
        print(f"  {i:2d}. {name:40s} {avg_ret:+.1f}% avg return")

    # Strategies with positive expectancy
    positive = [(n, s) for n, s in ranked if s["total"] > 0 and mean(s["returns"]) > 0]
    print(f"\n{'='*80}")
    print(f"STRATEGIES WITH POSITIVE EXPECTANCY: {len(positive)} / {len(strategy_stats)}")
    print(f"{'='*80}")
    for name, stats in positive:
        avg_ret = mean(stats["returns"])
        print(f"  ✓ {name:40s} {avg_ret:+.1f}% avg return")

    # Best per pair
    print(f"\n{'='*80}")
    print("BEST STRATEGY PER PAIR")
    print(f"{'='*80}")
    for symbol, results in pair_results.items():
        if not results:
            continue
        best = max(results, key=lambda r: r["total_return"])
        print(f"  {symbol:12s} → {best['strategy']:40s} {best['total_return']:+.1f}%")

    # Save results
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "config": {
            "bars": BACKTEST_BARS,
            "fee_ratio": FEE_RATIO,
            "pairs_tested": len(pairs),
            "strategies_tested": len(strategies),
        },
        "strategy_ranking": [
            {
                "strategy": name,
                "avg_return": round(mean(stats["returns"]), 2),
                "avg_win_rate": round(mean(stats["win_rates"]), 1),
                "avg_trades": round(mean(stats["trades"]), 1),
                "avg_sharpe": round(mean(stats["sharpes"]), 2),
                "avg_max_dd": round(mean(stats["drawdowns"]), 2),
                "avg_profit_factor": round(mean(stats["pf"]), 2) if stats["pf"] else None,
                "pairs_positive": stats["beats_bh"],
                "pairs_tested": stats["total"],
            }
            for name, stats in ranked if stats["total"] > 0
        ],
        "all_results": all_results,
    }

    out_file = RESULTS_DIR / f"mass_backtest_{datetime.now():%Y%m%d_%H%M%S}.json"
    with open(out_file, "w") as f:
        json.dump(report, f, indent=2)
    print(f"\nResults saved to {out_file}")

    return report


if __name__ == "__main__":
    run_mass_backtest()

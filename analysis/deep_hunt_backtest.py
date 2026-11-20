#!/usr/bin/env python3
"""
Deep Hunting Backtest — Buy deep dips on Hyperliquid perps (Propr pairs)
========================================================================
Strategy: Hunt for capitulation selling, enter long at extremes.

Entry conditions (all must be true):
  1. Price dropped >= DIP_THRESHOLD from the lookback high
  2. RSI(14) < RSI_OVERSOLD (default 25)
  3. Volume spike: current bar volume > VOLUME_SPIKE_MULT * mean(20)
  4. CVD z-score < CVD_THRESHOLD (heavy selling pressure = absorption)

Exit conditions:
  - Take profit at +TP_PCT from entry
  - Stop loss at -SL_PCT from entry
  - Time exit after MAX_HOLD_BARS if neither TP nor SL hit

Fees: 0.075% taker per side (Propr/Hyperliquid rate)
Data: Hyperliquid candle API, 1h bars, last 30 days
"""
import sys
import time
import json
import numpy as np
import pandas as pd
import requests
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

# ── Config ──────────────────────────────────────────────────────────────
INITIAL_CAPITAL = 5000.0
POSITION_SIZE_PCT = 0.30          # 30% of equity per trade
MAX_CONCURRENT = 3
TAKER_FEE = 0.00075               # 0.075% per side
SLIPPAGE_PCT = 0.0005             # 0.05% slippage per fill

# Strategy params
DIP_THRESHOLD = 0.04              # price must be 4%+ below lookback high
RSI_OVERSOLD = 25
RSI_PERIOD = 14
VOLUME_SPIKE_MULT = 1.8           # volume must be 1.8x average
CVD_THRESHOLD = -0.8              # CVD z-score below this
LOOKBACK = 50                     # bars for high/low lookback
CVD_LOOKBACK = 20

# Exits
SL_PCT = 0.025                    # 2.5% stop loss
TP_PCT = 0.05                     # 5% take profit
MAX_HOLD_BARS = 24                # max 24 hours (1h bars)

# Propr pairs (Hyperliquid symbols)
SYMBOLS = ["BTC", "ETH", "SOL", "DOGE", "XRP", "AVAX", "LINK",
           "SUI", "NEAR", "AAVE", "INJ", "FET"]

HYPERLIQUID_API = "https://api.hyperliquid.xyz/info"
OUTPUT_DIR = Path(__file__).parent
OUTPUT_FILE = OUTPUT_DIR / "deep_hunt_backtest_results.json"


# ── Data Fetching ───────────────────────────────────────────────────────
def fetch_hyperliquid_candles(symbol: str, interval: str = "1h",
                              days: int = 30) -> pd.DataFrame:
    """Fetch candles from Hyperliquid API."""
    now = datetime.now(timezone.utc)
    interval_ms = {"1m": 60000, "5m": 300000, "15m": 900000,
                   "1h": 3600000, "4h": 14400000}.get(interval, 3600000)
    end_time = int(now.timestamp() * 1000)
    start_time = end_time - (days * 24 * 3600 * 1000)

    try:
        resp = requests.post(HYPERLIQUID_API, json={
            "type": "candleSnapshot",
            "req": {
                "coin": symbol,
                "interval": interval,
                "startTime": start_time,
                "endTime": end_time,
            }
        }, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        if not data:
            return pd.DataFrame()

        rows = []
        for c in data:
            rows.append({
                "open": float(c.get("o", 0)),
                "high": float(c.get("h", 0)),
                "low": float(c.get("l", 0)),
                "close": float(c.get("c", 0)),
                "volume": float(c.get("v", 0)),
                "timestamp": pd.to_datetime(int(c.get("t", 0)), unit="ms", utc=True),
            })

        df = pd.DataFrame(rows)
        if not df.empty:
            df.set_index("timestamp", inplace=True)
            df.sort_index(inplace=True)
        return df

    except Exception as e:
        print(f"  ERROR fetching {symbol}: {e}")
        return pd.DataFrame()


# ── Indicators ──────────────────────────────────────────────────────────
def compute_rsi(closes: pd.Series, period: int = RSI_PERIOD) -> pd.Series:
    delta = closes.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta.where(delta < 0, 0.0))
    avg_gain = gain.ewm(alpha=1/period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1/period, min_periods=period).mean()
    rs = avg_gain / (avg_loss + 1e-10)
    return 100 - (100 / (1 + rs))


def compute_cvd_zscore(volume: pd.Series, closes: pd.Series,
                       lookback: int = CVD_LOOKBACK) -> pd.Series:
    """Approximate CVD z-score from kline volume."""
    direction = np.sign(closes.diff())
    cvd = (volume * direction).cumsum()
    cvd_mean = cvd.rolling(lookback).mean()
    cvd_std = cvd.rolling(lookback).std()
    return (cvd - cvd_mean) / (cvd_std + 1e-10)


# ── Strategy ────────────────────────────────────────────────────────────
def generate_signals(df: pd.DataFrame) -> pd.DataFrame:
    """Generate deep hunting entry signals."""
    df = df.copy()

    # Indicators
    df["rsi"] = compute_rsi(df["close"])
    df["cvd_z"] = compute_cvd_zscore(df["volume"], df["close"])
    df["vol_ma"] = df["volume"].rolling(20).mean()
    df["vol_spike"] = df["volume"] / (df["vol_ma"] + 1e-10)

    # Rolling high for dip detection
    df["recent_high"] = df["high"].rolling(LOOKBACK).max()
    df["dip_pct"] = (df["recent_high"] - df["close"]) / df["recent_high"]

    # Signal: all 4 conditions
    df["signal"] = (
        (df["dip_pct"] >= DIP_THRESHOLD) &
        (df["rsi"] < RSI_OVERSOLD) &
        (df["vol_spike"] > VOLUME_SPIKE_MULT) &
        (df["cvd_z"] < CVD_THRESHOLD)
    )

    return df


# ── Backtester ──────────────────────────────────────────────────────────
def backtest_symbol(symbol: str, df: pd.DataFrame) -> dict:
    """Run deep hunting backtest on a single symbol."""
    if df.empty or len(df) < LOOKBACK + 20:
        return {"symbol": symbol, "trades": [], "error": "insufficient data"}

    df = generate_signals(df)

    capital = INITIAL_CAPITAL
    positions = []  # list of {entry_price, qty, entry_bar, sl, tp}
    trades = []
    equity_curve = []

    for bar_idx in range(LOOKBACK + 20, len(df)):
        row = df.iloc[bar_idx]
        current_price = row["close"]

        # ── Check exits first ──
        closed = []
        for pos in positions:
            high = row["high"]
            low = row["low"]
            bars_held = bar_idx - pos["entry_bar"]

            exit_price = None
            exit_reason = None

            # Stop loss
            if low <= pos["sl"]:
                exit_price = pos["sl"]
                exit_reason = "stop_loss"
            # Take profit
            elif high >= pos["tp"]:
                exit_price = pos["tp"]
                exit_reason = "take_profit"
            # Time exit
            elif bars_held >= MAX_HOLD_BARS:
                exit_price = current_price
                exit_reason = "time_exit"

            if exit_price:
                # Apply slippage on exit
                exit_price *= (1 - SLIPPAGE_PCT)
                pnl_gross = (exit_price - pos["entry_price"]) * pos["qty"]
                fee_exit = exit_price * pos["qty"] * TAKER_FEE
                pnl_net = pnl_gross - pos["fee_entry"] - fee_exit
                capital += pos["qty"] * pos["entry_price"] + pnl_net

                trades.append({
                    "entry_price": pos["entry_price"],
                    "exit_price": exit_price,
                    "qty": pos["qty"],
                    "pnl": round(pnl_net, 2),
                    "pnl_pct": round(pnl_net / (pos["entry_price"] * pos["qty"]) * 100, 2),
                    "reason": exit_reason,
                    "bars_held": bars_held,
                    "entry_time": str(df.index[pos["entry_bar"]]),
                    "exit_time": str(df.index[bar_idx]),
                })
                closed.append(pos)

        for c in closed:
            positions.remove(c)

        # ── Check entry ──
        if row["signal"] and len(positions) < MAX_CONCURRENT:
            # Don't enter if already have a position in this symbol
            if not any(p["symbol"] == symbol for p in positions):
                entry_price = current_price * (1 + SLIPPAGE_PCT)
                size_usd = capital * POSITION_SIZE_PCT
                qty = size_usd / entry_price

                if qty > 0 and capital > 100:
                    fee_entry = entry_price * qty * TAKER_FEE
                    sl = entry_price * (1 - SL_PCT)
                    tp = entry_price * (1 + TP_PCT)
                    capital -= entry_price * qty

                    positions.append({
                        "entry_price": entry_price,
                        "qty": qty,
                        "entry_bar": bar_idx,
                        "sl": sl,
                        "tp": tp,
                        "fee_entry": fee_entry,
                        "symbol": symbol,
                    })

        # Mark-to-market
        mtm = sum(p["qty"] * current_price for p in positions if p["symbol"] == symbol)
        equity = capital + mtm + sum(
            p["qty"] * p["entry_price"] for p in positions if p["symbol"] == symbol
        )
        equity_curve.append({"bar": bar_idx, "equity": equity})

    # Force close remaining positions
    last_price = df.iloc[-1]["close"]
    for pos in positions:
        if pos["symbol"] == symbol:
            pnl_gross = (last_price - pos["entry_price"]) * pos["qty"]
            fee_exit = last_price * pos["qty"] * TAKER_FEE
            pnl_net = pnl_gross - pos["fee_entry"] - fee_exit
            trades.append({
                "entry_price": pos["entry_price"],
                "exit_price": last_price,
                "qty": pos["qty"],
                "pnl": round(pnl_net, 2),
                "pnl_pct": round(pnl_net / (pos["entry_price"] * pos["qty"]) * 100, 2),
                "reason": "force_close",
                "bars_held": len(df) - 1 - pos["entry_bar"],
                "entry_time": str(df.index[pos["entry_bar"]]),
                "exit_time": str(df.index[-1]),
            })

    # Compute stats
    if not trades:
        return {
            "symbol": symbol,
            "trades": 0,
            "total_pnl": 0,
            "win_rate": 0,
            "avg_pnl_pct": 0,
            "max_dd_pct": 0,
            "profit_factor": 0,
            "sharpe": 0,
            "trades_detail": [],
        }

    pnls = [t["pnl"] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    total_pnl = sum(pnls)

    # Max drawdown from equity curve
    if equity_curve:
        equities = [e["equity"] for e in equity_curve]
        peak = equities[0]
        max_dd = 0
        for eq in equities:
            if eq > peak:
                peak = eq
            dd = (peak - eq) / peak if peak > 0 else 0
            max_dd = max(max_dd, dd)
    else:
        max_dd = 0

    # Sharpe (annualized from 1h bars)
    if len(pnls) > 1:
        mean_ret = np.mean(pnls) / INITIAL_CAPITAL
        std_ret = np.std(pnls) / INITIAL_CAPITAL
        sharpe = (mean_ret / (std_ret + 1e-10)) * np.sqrt(365 * 24)
    else:
        sharpe = 0

    gross_profit = sum(wins) if wins else 0
    gross_loss = abs(sum(losses)) if losses else 0.01

    return {
        "symbol": symbol,
        "trades": len(trades),
        "total_pnl": round(total_pnl, 2),
        "win_rate": round(len(wins) / len(trades) * 100, 1),
        "avg_pnl_pct": round(np.mean([t["pnl_pct"] for t in trades]), 2),
        "max_dd_pct": round(max_dd * 100, 2),
        "profit_factor": round(gross_profit / gross_loss, 2),
        "sharpe": round(sharpe, 2),
        "avg_bars_held": round(np.mean([t["bars_held"] for t in trades]), 1),
        "trades_detail": trades,
    }


# ── Main ────────────────────────────────────────────────────────────────
def main():
    print("=" * 70)
    print("DEEP HUNTING BACKTEST — Hyperliquid Perps (Propr Pairs)")
    print("=" * 70)
    print(f"Period:    Last 30 days, 1h bars")
    print(f"Capital:   ${INITIAL_CAPITAL:,.0f}")
    print(f"Size:      {POSITION_SIZE_PCT:.0%} per trade")
    print(f"Max pos:   {MAX_CONCURRENT}")
    print(f"SL/TP:     {SL_PCT:.1%} / {TP_PCT:.1%}")
    print(f"Entry:     Dip>={DIP_THRESHOLD:.0%}, RSI<{RSI_OVERSOLD}, "
          f"Vol>{VOLUME_SPIKE_MULT}x, CVD_z<{CVD_THRESHOLD}")
    print(f"Fees:      {TAKER_FEE:.2%} taker + {SLIPPAGE_PCT:.2%} slip/side")
    print(f"Symbols:   {', '.join(SYMBOLS)}")
    print("=" * 70)

    results = []
    all_trades = []

    for i, symbol in enumerate(SYMBOLS):
        print(f"\n[{i+1}/{len(SYMBOLS)}] {symbol}...", end=" ", flush=True)
        df = fetch_hyperliquid_candles(symbol, "1h", days=30)
        print(f"({len(df)} bars)", end=" ", flush=True)

        result = backtest_symbol(symbol, df)
        results.append(result)
        all_trades.extend(result.get("trades_detail", []))

        if result["trades"] > 0:
            print(f"-> {result['trades']} trades, "
                  f"PnL=${result['total_pnl']:+.2f}, "
                  f"WR={result['win_rate']:.0f}%, "
                  f"PF={result['profit_factor']:.2f}")
        else:
            print("-> no signals")

        time.sleep(0.3)

    # ── Aggregate ──
    total_trades = sum(r["trades"] for r in results)
    total_pnl = sum(r["total_pnl"] for r in results)
    symbols_with_trades = [r for r in results if r["trades"] > 0]

    if symbols_with_trades:
        avg_wr = np.mean([r["win_rate"] for r in symbols_with_trades])
        avg_pf = np.mean([r["profit_factor"] for r in symbols_with_trades])
        avg_sharpe = np.mean([r["sharpe"] for r in symbols_with_trades])
        max_dd = max(r["max_dd_pct"] for r in results)
    else:
        avg_wr = avg_pf = avg_sharpe = max_dd = 0

    print("\n" + "=" * 70)
    print("AGGREGATE RESULTS")
    print("=" * 70)
    print(f"Total trades:    {total_trades}")
    print(f"Total PnL:       ${total_pnl:+,.2f}")
    print(f"Return:          {total_pnl / INITIAL_CAPITAL:+.1%}")
    print(f"Avg Win Rate:    {avg_wr:.1f}%")
    print(f"Avg Profit Factor: {avg_pf:.2f}")
    print(f"Avg Sharpe:      {avg_sharpe:.2f}")
    print(f"Max Drawdown:    {max_dd:.1f}%")
    print("=" * 70)

    # Per-symbol summary
    print(f"\n{'Symbol':>6} {'Trades':>7} {'PnL':>10} {'WR%':>6} {'PF':>6} {'Sharpe':>7} {'MaxDD%':>7}")
    print("-" * 55)
    for r in results:
        print(f"{r['symbol']:>6} {r['trades']:>7} ${r['total_pnl']:>+8.2f} "
              f"{r['win_rate']:>5.1f}% {r['profit_factor']:>5.2f} "
              f"{r['sharpe']:>6.2f} {r['max_dd_pct']:>6.1f}%")

    # ── Best/Worst trades ──
    if all_trades:
        sorted_trades = sorted(all_trades, key=lambda x: x["pnl"])
        print(f"\nTop 5 winning trades:")
        for t in sorted_trades[-5:]:
            print(f"  {t['entry_time'][:16]} | PnL=${t['pnl']:+.2f} ({t['pnl_pct']:+.1f}%) "
                  f"| {t['reason']} | {t['bars_held']}h")
        print(f"\nTop 5 losing trades:")
        for t in sorted_trades[:5]:
            print(f"  {t['entry_time'][:16]} | PnL=${t['pnl']:+.2f} ({t['pnl_pct']:+.1f}%) "
                  f"| {t['reason']} | {t['bars_held']}h")

    # ── Save ──
    output = {
        "config": {
            "dip_threshold": DIP_THRESHOLD,
            "rsi_oversold": RSI_OVERSOLD,
            "volume_spike_mult": VOLUME_SPIKE_MULT,
            "cvd_threshold": CVD_THRESHOLD,
            "sl_pct": SL_PCT,
            "tp_pct": TP_PCT,
            "max_hold_bars": MAX_HOLD_BARS,
            "position_size_pct": POSITION_SIZE_PCT,
            "initial_capital": INITIAL_CAPITAL,
            "period_days": 30,
            "symbols": SYMBOLS,
        },
        "aggregate": {
            "total_trades": total_trades,
            "total_pnl": round(total_pnl, 2),
            "return_pct": round(total_pnl / INITIAL_CAPITAL * 100, 2),
            "avg_win_rate": round(avg_wr, 1),
            "avg_profit_factor": round(avg_pf, 2),
            "avg_sharpe": round(avg_sharpe, 2),
            "max_drawdown_pct": round(max_dd, 2),
        },
        "per_symbol": [{k: v for k, v in r.items() if k != "trades_detail"}
                       for r in results],
        "trades": all_trades,
    }

    with open(OUTPUT_FILE, "w") as f:
        json.dump(output, f, indent=2, default=str)
    print(f"\nResults saved to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()

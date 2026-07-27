#!/usr/bin/env python3
"""
Deep Hunting v4 — 1-Month Config Sweep, Portfolio Engine
========================================================
Sweeps 20 exit/management configs over the same fixed dip entry
(dip >= 5% off the 50h high + RSI < 30 + volume > 1.8x + CVD z < -0.8).

The sweep runs on backtest_propr(): ONE $5,000 book shared across all symbols,
with the Propr daily-loss (3%) and max-drawdown (6%) rules enforced. It used to
call backtest_v4() once per symbol, which handed every symbol its own $5,000 —
so the aggregate PnL was 12 independent accounts, MAX_CONCURRENT never bound,
and the capital at risk was 12x the headline.

Two things only the portfolio engine can see, both of which it found:
  - At the original 30% position size, every config with a 10% stop is
    UNFUNDABLE: one trade risks 3.08% of the book and the daily loss limit is
    3%. The rules refuse the entry. Hence the sweep runs at 30% AND 20%.
  - Drawdown is measured on the actual book, not on 12 parallel ones.

backtest_v4() below is retained deliberately: deep_hunt_v4_rescore.py and the
equity-curve regression guard in data_integrity_audit.py both run against it.
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
POSITION_SIZE_PCT = 0.30
MAX_CONCURRENT = 3
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

SYMBOLS = ["BTC", "ETH", "SOL", "DOGE", "XRP", "AVAX", "LINK",
           "SUI", "NEAR", "AAVE", "INJ", "FET"]
HYPERLIQUID_API = "https://api.hyperliquid.xyz/info"
OUTPUT_DIR = Path(__file__).parent


def fetch_hyperliquid_candles(symbol, interval="1h", days=30):
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


def compute_atr(high, low, close, period=14):
    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def backtest_v4(symbol, df, cfg):
    if df.empty or len(df) < 220:
        return None

    df = df.copy()
    df["rsi"] = compute_rsi(df["close"])
    df["cvd_z"] = compute_cvd_zscore(df["volume"], df["close"])
    df["vol_ma"] = df["volume"].rolling(20).mean()
    df["vol_spike"] = df["volume"] / (df["vol_ma"] + 1e-10)
    df["recent_high"] = df["high"].rolling(LOOKBACK).max()
    df["dip_pct"] = (df["recent_high"] - df["close"]) / df["recent_high"]
    df["atr"] = compute_atr(df["high"], df["low"], df["close"])

    # Signal
    df["signal"] = (
        (df["dip_pct"] >= DIP_THRESHOLD) &
        (df["rsi"] < RSI_OVERSOLD) &
        (df["vol_spike"] > VOLUME_SPIKE_MULT) &
        (df["cvd_z"] < CVD_THRESHOLD)
    )

    # EMA trend filter
    if cfg.get("trend_filter"):
        df["ema50"] = df["close"].ewm(span=50).mean()
        df["ema200"] = df["close"].ewm(span=200).mean()
        df["signal"] = df["signal"] & (df["ema50"] > df["ema200"])

    sl_pct = cfg["sl_pct"]
    tp_pct = cfg["tp_pct"]
    trail_activation = cfg.get("trail_activation", 0.01)  # trail after 1% profit
    trail_distance = cfg.get("trail_distance", 0.015)  # trail 1.5% below high
    partial_tp_pct = cfg.get("partial_tp", 0.0)  # take partial at TP
    cooldown = cfg.get("cooldown", 3)  # bars between entries on same symbol
    confirm_bars = cfg.get("confirm_bars", 0)  # wait N bars after signal
    max_hold = cfg.get("max_hold", 24)

    capital = INITIAL_CAPITAL
    positions = []
    trades = []
    equity_curve = []
    last_entry_bar = -cooldown - 1

    start_bar = max(220, LOOKBACK + 20)
    if len(df) < start_bar + 10:
        return None

    for bar_idx in range(start_bar, len(df)):
        row = df.iloc[bar_idx]
        price = row["close"]
        high_i = row["high"]
        low_i = row["low"]

        # ── Exit logic ──
        closed = []
        for pos in positions:
            bars_held = bar_idx - pos["entry_bar"]
            exit_price = exit_reason = None

            # Update trailing stop
            if pos.get("trailing"):
                new_trail = high_i * (1 - trail_distance)
                if new_trail > pos["sl"]:
                    pos["sl"] = new_trail

            # Check SL
            if low_i <= pos["sl"]:
                exit_price, exit_reason = pos["sl"], "stop_loss"
                # Check if we hit trailing SL in profit
                if pos["sl"] > pos["entry_price"]:
                    exit_reason = "trail_stop"

            # Check TP
            elif high_i >= pos["tp"]:
                if partial_tp_pct > 0 and pos.get("full_size", True):
                    # Partial TP — close half, let rest ride
                    partial_qty = pos["qty"] * partial_tp_pct
                    partial_ep = pos["tp"] * (1 - SLIPPAGE_PCT)
                    gross = (partial_ep - pos["entry_price"]) * partial_qty
                    fee = pos["entry_price"] * partial_qty * FEE_PER_SIDE + partial_ep * partial_qty * FEE_PER_SIDE
                    pnl_partial = gross - fee
                    capital += pos["entry_price"] * partial_qty + pnl_partial
                    pos["qty"] -= partial_qty
                    pos["full_size"] = False
                    # Move SL to breakeven
                    pos["sl"] = pos["entry_price"] * 1.001
                    pos["trailing"] = True
                    trades.append({"pnl": pnl_partial, "reason": "partial_tp"})
                else:
                    exit_price, exit_reason = pos["tp"], "take_profit"

            # Time exit
            elif bars_held >= max_hold:
                exit_price, exit_reason = price, "time_exit"

            if exit_price:
                exit_price *= (1 - SLIPPAGE_PCT)
                gross = (exit_price - pos["entry_price"]) * pos["qty"]
                fee = pos["entry_price"] * pos["qty"] * FEE_PER_SIDE + exit_price * pos["qty"] * FEE_PER_SIDE
                pnl = gross - fee
                capital += pos["entry_price"] * pos["qty"] + pnl
                trades.append({"pnl": pnl, "reason": exit_reason})
                closed.append(pos)

        for p in closed:
            positions.remove(p)

        # ── Entry ──
        held = {p["symbol"] for p in positions}
        if (row["signal"] and symbol not in held
                and len(positions) < MAX_CONCURRENT
                and (bar_idx - last_entry_bar) >= cooldown):

            # Confirm: signal must have fired within confirm_bars
            if confirm_bars > 0:
                recent_signals = df["signal"].iloc[max(0, bar_idx - confirm_bars):bar_idx + 1]
                if not recent_signals.any():
                    equity_curve.append(capital + sum(p["qty"] * price for p in positions))
                    continue

            entry_price = price * (1 + SLIPPAGE_PCT)
            size_usd = capital * POSITION_SIZE_PCT
            qty = size_usd / entry_price
            if qty > 0 and capital > 100:
                fee_entry = entry_price * qty * FEE_PER_SIDE
                capital -= entry_price * qty

                # Adaptive SL based on ATR
                atr_val = row["atr"] if not np.isnan(row["atr"]) else price * sl_pct
                adaptive_sl = max(sl_pct, (atr_val * 2) / price) if cfg.get("adaptive_sl") else sl_pct

                sl_price = entry_price * (1 - adaptive_sl)
                tp_price = entry_price * (1 + tp_pct)

                positions.append({
                    "entry_price": entry_price, "qty": qty,
                    "entry_bar": bar_idx, "symbol": symbol,
                    "sl": sl_price, "tp": tp_price,
                    "fee_entry": fee_entry,
                    "trailing": False, "full_size": True,
                    "peak_price": price,
                })
                last_entry_bar = bar_idx

        # Update peak for trailing
        for pos in positions:
            if pos["symbol"] == symbol and price > pos.get("peak_price", 0):
                pos["peak_price"] = price
                # Activate trailing once in profit
                if not pos["trailing"] and price > pos["entry_price"] * (1 + trail_activation):
                    pos["trailing"] = True
                    pos["sl"] = pos["entry_price"] * 1.001  # breakeven

        # equity = free cash + what the open positions are worth NOW.
        # capital already had the entry cost deducted on entry, so adding it back
        # here (as this line used to) double-counts every open position and
        # invents a ~23pp drawdown on entry/exit. See analysis/deep_hunt_v4_rescore.py.
        mtm = sum(p["qty"] * price for p in positions if p["symbol"] == symbol)
        equity_curve.append(capital + mtm)

    # Force close
    last_price = df.iloc[-1]["close"]
    for pos in positions:
        if pos["symbol"] == symbol:
            gross = (last_price - pos["entry_price"]) * pos["qty"]
            fee = pos["entry_price"] * pos["qty"] * FEE_PER_SIDE + last_price * pos["qty"] * FEE_PER_SIDE
            pnl = gross - fee
            trades.append({"pnl": pnl, "reason": "force_close"})

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

    return {
        "symbol": symbol, "trades": n, "wins": nw, "losses": nl,
        "total_pnl": round(float(pnls.sum()), 2),
        "win_rate": round(nw / n * 100, 1),
        "profit_factor": round(gp / gl, 2) if gl > 0 else 999,
        "avg_win_usd": round(gp / nw, 2) if nw > 0 else 0,
        "avg_loss_usd": round(gl / nl, 2) if nl > 0 else 0.01,
        "expectancy": round((nw / n * gp / nw if nw > 0 else 0) - (nl / n * gl / nl if nl > 0 else 0), 2),
        "max_dd_pct": round(max_dd, 2),
        "reasons": {t["reason"]: trades.count(t) for t in trades if trades.count(t) > 0},
    }


# ── Config grid ──
CONFIGS = [
    # Baseline: v3 best
    {"name": "v3_best", "sl_pct": 0.10, "tp_pct": 0.02, "max_hold": 24},

    # Trailing stop configs
    {"name": "trail_1pct_act", "sl_pct": 0.10, "tp_pct": 0.02,
     "trail_activation": 0.01, "trail_distance": 0.015, "max_hold": 24},
    {"name": "trail_0p5_act", "sl_pct": 0.10, "tp_pct": 0.02,
     "trail_activation": 0.005, "trail_distance": 0.01, "max_hold": 24},
    {"name": "trail_2pct_act", "sl_pct": 0.10, "tp_pct": 0.03,
     "trail_activation": 0.02, "trail_distance": 0.02, "max_hold": 30},

    # Partial TP configs
    {"name": "partial_50", "sl_pct": 0.10, "tp_pct": 0.02,
     "partial_tp": 0.5, "max_hold": 24},
    {"name": "partial_50_trail", "sl_pct": 0.10, "tp_pct": 0.02,
     "partial_tp": 0.5, "trail_activation": 0.005, "trail_distance": 0.01, "max_hold": 24},

    # Quick scalp with trail
    {"name": "scalp_trail", "sl_pct": 0.05, "tp_pct": 0.02,
     "trail_activation": 0.01, "trail_distance": 0.01, "max_hold": 18},
    {"name": "scalp_wide_trail", "sl_pct": 0.08, "tp_pct": 0.025,
     "trail_activation": 0.015, "trail_distance": 0.012, "max_hold": 20},

    # Higher TP with trail
    {"name": "tp3_trail", "sl_pct": 0.10, "tp_pct": 0.03,
     "trail_activation": 0.02, "trail_distance": 0.015, "max_hold": 30},
    {"name": "tp4_trail", "sl_pct": 0.10, "tp_pct": 0.04,
     "trail_activation": 0.025, "trail_distance": 0.02, "max_hold": 36},

    # Trend filter on/off
    {"name": "trend_sl10_tp2", "sl_pct": 0.10, "tp_pct": 0.02,
     "trend_filter": True, "max_hold": 24},
    {"name": "trend_trail", "sl_pct": 0.10, "tp_pct": 0.02,
     "trend_filter": True, "trail_activation": 0.01, "trail_distance": 0.015, "max_hold": 24},

    # Cooldown variants
    {"name": "cd5_trail", "sl_pct": 0.10, "tp_pct": 0.02,
     "cooldown": 5, "trail_activation": 0.01, "trail_distance": 0.015, "max_hold": 24},
    {"name": "cd1_trail", "sl_pct": 0.10, "tp_pct": 0.02,
     "cooldown": 1, "trail_activation": 0.01, "trail_distance": 0.015, "max_hold": 24},

    # Confirm entry
    {"name": "confirm1_trail", "sl_pct": 0.10, "tp_pct": 0.02,
     "confirm_bars": 1, "trail_activation": 0.01, "trail_distance": 0.015, "max_hold": 24},
    {"name": "confirm2_trail", "sl_pct": 0.10, "tp_pct": 0.02,
     "confirm_bars": 2, "trail_activation": 0.01, "trail_distance": 0.015, "max_hold": 24},

    # Adaptive SL
    {"name": "adapt_sl_trail", "sl_pct": 0.08, "tp_pct": 0.02,
     "adaptive_sl": True, "trail_activation": 0.01, "trail_distance": 0.015, "max_hold": 24},

    # Combined: trend + trail + partial
    {"name": "combo_full", "sl_pct": 0.10, "tp_pct": 0.025,
     "trend_filter": True, "partial_tp": 0.5,
     "trail_activation": 0.015, "trail_distance": 0.015, "max_hold": 24},

    # Aggressive trail (let winners run)
    {"name": "agg_trail", "sl_pct": 0.12, "tp_pct": 0.02,
     "trail_activation": 0.01, "trail_distance": 0.02, "max_hold": 36},
    {"name": "agg_trail_tp3", "sl_pct": 0.12, "tp_pct": 0.03,
     "trail_activation": 0.02, "trail_distance": 0.02, "max_hold": 36},
]


# ── Portfolio conversion ──────────────────────────────────────────────────
# The sweep runs on backtest_propr(): ONE $5,000 book across all symbols, with
# the Propr daily-loss and max-drawdown rules enforced. It used to call
# backtest_v4() once per symbol, which gave every symbol its own $5,000 — so the
# aggregate was 12 independent accounts, MAX_CONCURRENT never bound, and the
# capital at risk was 12x what the headline implied.
#
# backtest_v4() above is kept: deep_hunt_v4_rescore.py and the equity-curve
# regression guard in data_integrity_audit.py both run against it.
#
# CONFIGS were written against backtest_v4's defaults, which are NOT
# backtest_propr's (trail_activation defaults to 0.01 there and 0.0 here). Every
# default is therefore materialised below, or 8 configs would silently lose the
# trailing stop they were actually tested with.
V4_DEFAULTS = {
    "trail_activation": 0.01, "trail_distance": 0.015,
    "cooldown": 3, "max_hold": 24,
    "pos_size_pct": POSITION_SIZE_PCT, "max_concurrent": MAX_CONCURRENT,
}
# backtest_propr implements none of these. Not silently dropped — flagged per row.
NOT_IN_PORTFOLIO_ENGINE = ("partial_tp", "adaptive_sl")

# 30% was the per-symbol sweep's size, and under Propr rules it is unfundable for
# any config with a 10% stop: 30% x 10% = 3.0% of the book before fees, and the
# daily loss limit is 3%. 20% is the size the walk-forward winner ships at.
SIZES = (0.30, 0.20)


def worst_case_pct(cfg):
    """Loss on one position if it goes straight to the stop, as a share of the
    book. This is the quantity backtest_propr checks before every entry."""
    return cfg["pos_size_pct"] * (cfg["sl_pct"] + 2 * FEE_PER_SIDE)


def align(all_data, min_bars=220):
    """One shared, gap-free timeline for every symbol.

    backtest_propr addresses every symbol with the SAME iloc index, taken from
    whichever symbol happens to be first. That is only safe if the indexes are
    identical, which the raw feed does not guarantee (symbols come back with
    5000-5004 bars and different start hours). Intersecting makes it true.

    The final bar is dropped: it is still forming, and dip_runner.py drops it
    live, so keeping it here would backtest a bar live trading never sees.
    """
    usable = {s: df for s, df in all_data.items() if len(df) >= min_bars}
    if not usable:
        return {}, None
    common = None
    for df in usable.values():
        common = df.index if common is None else common.intersection(df.index)
    common = common.sort_values()[:-1]
    return {s: df.loc[common] for s, df in usable.items()}, common


def main():
    print("=" * 104)
    print("DEEP HUNTING v4 — 1-MONTH SWEEP, PORTFOLIO ENGINE (backtest_propr)")
    print("=" * 104)
    print(f"Fixed entry: Dip>={DIP_THRESHOLD:.0%}, RSI<{RSI_OVERSOLD}, "
          f"Vol>{VOLUME_SPIKE_MULT}x, CVD_z<{CVD_THRESHOLD}")
    print(f"ONE book of ${INITIAL_CAPITAL:,.0f} across all symbols | "
          f"size {POSITION_SIZE_PCT:.0%} | max {MAX_CONCURRENT} concurrent")
    print(f"Propr rules ENFORCED: 3% daily loss halt, 6% max DD = challenge failed")
    print(f"Testing {len(CONFIGS)} configs")
    print("=" * 104)

    from analysis.deep_hunt_v4_propr import backtest_propr, DAILY_LOSS_LIMIT, MAX_DD_LIMIT

    print("\nFetching 30-day 1h candles...")
    raw = {}
    for i, sym in enumerate(SYMBOLS):
        df = fetch_hyperliquid_candles(sym, "1h", days=30)
        raw[sym] = df
        print(f"  [{i+1:2d}/{len(SYMBOLS)}] {sym:5s} {len(df)} bars")
        time.sleep(0.3)

    all_data, common = align(raw)
    if not all_data:
        print("no usable data")
        return
    dropped = [s for s in raw if s not in all_data]
    print(f"\nAligned to {len(common)} shared bars "
          f"({common[0]} -> {common[-1]}), forming bar dropped.")
    print(f"{len(all_data)} symbols usable" +
          (f", dropped for short history: {', '.join(dropped)}" if dropped else ""))

    results_by_size = {}
    for size in SIZES:
        print(f"\n{'=' * 104}\nSWEEP AT {size:.0%} POSITION SIZE\n{'=' * 104}")
        rows, blocked = [], []
        for ci, cfg in enumerate(CONFIGS):
            name = cfg.get("name", f"cfg_{ci}")
            effective = {**V4_DEFAULTS, **cfg, "pos_size_pct": size}
            ignored = [k for k in NOT_IN_PORTFOLIO_ENGINE if cfg.get(k)]

            # Pre-flight: backtest_propr refuses any entry whose worst case would
            # breach the 3% daily loss limit. Say so, rather than reporting "no
            # trades" and letting it read as "the signal never fired".
            wc = worst_case_pct(effective)
            if wc > DAILY_LOSS_LIMIT / INITIAL_CAPITAL:
                blocked.append((name, wc))
                print(f"  [{ci+1:2d}/{len(CONFIGS)}] {name:22s} UNFUNDABLE — one trade "
                      f"risks {wc:.2%} of the book, daily limit is "
                      f"{DAILY_LOSS_LIMIT / INITIAL_CAPITAL:.0%}")
                continue

            r = backtest_propr(all_data, effective)
            if not r:
                print(f"  [{ci+1:2d}/{len(CONFIGS)}] {name:22s} no trades (signal never fired)")
                continue

            dd_penalty = max(0, r["max_dd_pct"] - 3) * 15
            score = (r["total_pnl"] - dd_penalty + min(r["trades"], 60) * 1.0
                     + min(r["profit_factor"], 5) * 8)
            r = {**r, "name": name, "cfg": effective, "score": round(score, 2),
                 "dd_penalty": round(dd_penalty, 2), "ignored_params": ignored,
                 "worst_case_pct": round(wc * 100, 2)}
            rows.append(r)

            tags = ""
            if r["challenge_failed"]:
                tags += "  *** CHALLENGE FAILED ***"
            if r["daily_limits_hit"]:
                tags += f"  [daily_limit x{r['daily_limits_hit']}]"
            if ignored:
                tags += f"  [IGNORED: {', '.join(ignored)}]"
            print(f"  [{ci+1:2d}/{len(CONFIGS)}] {name:22s} -> {r['trades']:3d} trades "
                  f"PnL=${r['total_pnl']:>+8.2f} ({r['return_pct']:>+6.2f}%) "
                  f"WR={r['win_rate']:5.1f}% PF={r['profit_factor']:5.2f} "
                  f"DD={r['max_dd_pct']:5.2f}%{tags}")
        rows.sort(key=lambda x: x["score"], reverse=True)
        results_by_size[size] = {"rows": rows, "blocked": blocked}
        if blocked:
            print(f"\n  {len(blocked)}/{len(CONFIGS)} configs are unfundable at "
                  f"{size:.0%} size — a 10% stop on a {size:.0%} position is "
                  f"{size * 0.10:.1%} of\n  the book before fees, and the daily loss "
                  f"limit is 3%. They are not weak here; they\n  cannot legally open a "
                  f"position under the challenge rules.")

    # Headline whichever size actually funds the most of the sweep.
    size = max(SIZES, key=lambda s: len(results_by_size[s]["rows"]))
    config_results = results_by_size[size]["rows"]
    if not config_results:
        print("\nNo config produced a trade at any size.")
        return

    print("\n" + "=" * 104)
    print(f"ALL CONFIGURATIONS — one ${INITIAL_CAPITAL:,.0f} book at {size:.0%} size, "
          f"Propr rules enforced")
    print("=" * 104)
    print(f"{'#':>3} {'Name':22s} {'Tr':>4} {'PnL':>10} {'Ret%':>7} {'WR%':>6} "
          f"{'PF':>6} {'MaxDD$':>8} {'DD%':>6} {'Fail':>5} {'Daily':>6} {'Score':>9}")
    print("-" * 104)
    for i, r in enumerate(config_results):
        marker = " ***" if i == 0 and not r["challenge_failed"] else ""
        print(f"{i+1:>3} {r['name']:22s} {r['trades']:>4} "
              f"${r['total_pnl']:>+8.2f} {r['return_pct']:>+6.2f}% "
              f"{r['win_rate']:>5.1f}% {r['profit_factor']:>5.2f} "
              f"${r['max_dd_usd']:>7.2f} {r['max_dd_pct']:>5.2f}% "
              f"{'YES' if r['challenge_failed'] else 'no':>5} "
              f"{r['daily_limits_hit']:>6} {r['score']:>+8.2f}{marker}")

    skipped = [r for r in config_results if r["ignored_params"]]
    if skipped:
        print(f"\n  NOT FAITHFULLY TESTED — backtest_propr does not implement these, so "
              f"the rows below\n  are the config MINUS that feature, not the config:")
        for r in skipped:
            print(f"    {r['name']:22s} ignored: {', '.join(r['ignored_params'])}")
    print(f"\n  Note: confirm_bars is inert in backtest_v4 too (entry already requires "
          f"signal==True\n  on the bar, so the lookback window can never be empty), so "
          f"confirm* configs are\n  duplicates of trail_1pct_act in BOTH engines.")

    viable = [r for r in config_results if not r["challenge_failed"]]
    failed = [r for r in config_results if r["challenge_failed"]]
    print(f"\n  {len(viable)} configs survived the challenge, {len(failed)} failed "
          f"(6% = ${MAX_DD_LIMIT:.0f} from peak; daily halt at ${DAILY_LOSS_LIMIT:.0f})")

    best = viable[0] if viable else config_results[0]
    print("\n" + "=" * 104)
    print(f"BEST: {best['name']}"
          f"{'  (NOTE: no config survived)' if not viable else ''}")
    print(f"SL={best['cfg']['sl_pct']:.0%} | TP={best['cfg']['tp_pct']:.0%} | "
          f"size={best['cfg']['pos_size_pct']:.0%} | max_conc={best['cfg']['max_concurrent']} | "
          f"trail={best['cfg']['trail_activation']:.1%}/{best['cfg']['trail_distance']:.1%} | "
          f"hold={best['cfg']['max_hold']}h")
    print("=" * 104)
    print(f"  Final capital   ${best['final_capital']:,.2f} "
          f"(${best['total_pnl']:+.2f}, {best['return_pct']:+.2f}%)")
    print(f"  Trades          {best['trades']}  (WR {best['win_rate']}%, "
          f"PF {best['profit_factor']})")
    print(f"  Max drawdown    ${best['max_dd_usd']:.2f} ({best['max_dd_pct']:.2f}% "
          f"of ${INITIAL_CAPITAL:,.0f}) vs the 6% wall")
    print(f"  Exits           {best['reason_counts']}")

    print(f"\n  {'Symbol':>7} {'Tr':>4} {'W':>4} {'L':>4} {'PnL':>10}")
    print("  " + "-" * 34)
    for sym, st in sorted(best["per_symbol"].items(),
                          key=lambda x: x[1]["pnl"], reverse=True):
        print(f"  {sym:>7} {st['trades']:>4} {st['wins']:>4} {st['losses']:>4} "
              f"${st['pnl']:>+8.2f}")
    winners = [s for s, st in best["per_symbol"].items() if st["pnl"] > 0]
    print(f"\n  Profitable symbols: {len(winners)}/{len(best['per_symbol'])} traded "
          f"({', '.join(winners) if winners else 'none'})")

    output = {
        "strategy": "deep_hunting_v4",
        "engine": "backtest_propr (single portfolio, Propr rules enforced)",
        "capital": INITIAL_CAPITAL,
        "window": {"bars": len(common), "start": str(common[0]), "end": str(common[-1]),
                   "symbols": sorted(all_data)},
        "fixed_entry": {"dip_threshold": DIP_THRESHOLD, "rsi_oversold": RSI_OVERSOLD,
                        "volume_spike_mult": VOLUME_SPIKE_MULT, "cvd_threshold": CVD_THRESHOLD},
        "v4_defaults_applied": V4_DEFAULTS,
        "params_not_implemented_by_engine": list(NOT_IN_PORTFOLIO_ENGINE),
        "configs_tested": len(CONFIGS),
        "configs_survived": len(viable),
        "headline_position_size": size,
        "by_position_size": {
            f"{s:.0%}": {
                "funded": len(v["rows"]),
                "unfundable": [{"name": n, "worst_case_pct": round(w * 100, 2)}
                               for n, w in v["blocked"]],
                "results": [{k: val for k, val in r.items()
                             if k not in ("per_symbol", "daily_log")}
                            for r in v["rows"]],
            } for s, v in results_by_size.items()},
        "best_config": {k: v for k, v in best.items()
                        if k not in ("per_symbol", "daily_log")},
        "best_per_symbol": best["per_symbol"],
        "all_results": [{k: v for k, v in r.items()
                         if k not in ("per_symbol", "daily_log")}
                        for r in config_results],
    }
    out_path = OUTPUT_DIR / "deep_hunt_v4_30d_results.json"
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2, default=str)
    print(f"\nResults saved to {out_path}")


if __name__ == "__main__":
    main()

import time
from datetime import datetime, timezone
from statistics import stdev, mean

import requests

from config import WATCHED_SYMBOLS, INITIAL_BALANCE
from core.database import execute, fetchone, fetchall
from core.strategies import ALL_STRATEGIES, scan_symbol
from core.market import MarketData

BACKTEST_DAYS = 90
POSITION_SIZE_PCT = 25


def _to_binance_symbol(symbol):
    s = symbol.replace("/", "").upper()
    if s.endswith("USD") and not s.endswith("USDT"):
        return s + "T"
    return s


def fetch_klines(symbol, interval="1d", limit=100):
    bsym = _to_binance_symbol(symbol)
    try:
        r = requests.get("https://api.binance.com/api/v3/klines", params={
            "symbol": bsym, "interval": interval, "limit": limit
        }, timeout=10)
        data = r.json()
        return [{
            "date": datetime.fromtimestamp(k[0] / 1000, tz=timezone.utc).isoformat(),
            "open": float(k[1]), "high": float(k[2]), "low": float(k[3]),
            "close": float(k[4]), "volume": float(k[5]), "ts": k[0] // 1000
        } for k in data]
    except Exception:
        return []


def _calc_sl_tp(price, side, volatility_pct, sl_mult=1.5, tp_mult=3.0):
    vol_dec = (volatility_pct or 2) / 100.0
    if side == "BUY":
        sl = round(price * (1 - vol_dec * sl_mult), 5)
        tp = round(price * (1 + vol_dec * tp_mult), 5)
    else:
        sl = round(price * (1 + vol_dec * sl_mult), 5)
        tp = round(price * (1 - vol_dec * tp_mult), 5)
    return sl, tp


def backtest_symbol(symbol, days=BACKTEST_DAYS, initial_capital=INITIAL_BALANCE):
    ohlc = fetch_klines(symbol, interval="1d", limit=days + 50)
    if len(ohlc) < 50:
        return None

    cash = initial_capital
    position = None
    trades = []
    equity_curve = []
    market = MarketData()

    for i in range(50, len(ohlc)):
        slice_data = ohlc[:i + 1]
        current = ohlc[i]

        if position:
            high, low = current["high"], current["low"]
            side, entry, qty, sl, tp = position["side"], position["entry"], position["qty"], position["sl"], position["tp"]
            hit_sl = (side == "BUY" and low <= sl) or (side == "SELL" and high >= sl)
            hit_tp = (side == "BUY" and high >= tp) or (side == "SELL" and low <= tp)
            exit_price = None
            reason = None
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
                cash += qty * exit_price
                pnl_pct = (pnl / (entry * qty)) * 100 if entry * qty else 0
                trades.append({
                    "symbol": symbol, "side": side, "qty": qty,
                    "entry": entry, "exit": exit_price,
                    "pnl": round(pnl, 2), "pnl_pct": round(pnl_pct, 2),
                    "reason": reason, "bar": i,
                    "date": current["date"][:10],
                })
                position = None

        if not position and i % 5 == 0:
            signals = scan_symbol(slice_data)
            buy_signals = [s for s in signals if s["action"] == "BUY"]
            sell_signals = [s for s in signals if s["action"] == "SELL"]
            if buy_signals or sell_signals:
                hist = [{"close": c["close"]} for c in slice_data[-30:]]
                ind = market.compute_indicators(hist)
                vol = ind.get("volatility", 2)
                best = max(buy_signals + sell_signals, key=lambda s: s["confidence"])
                side = best["action"]
                qty = (cash * POSITION_SIZE_PCT / 100) / current["close"]
                if qty < 0.001:
                    qty = 0
                if qty > 0:
                    sl, tp = _calc_sl_tp(current["close"], side, vol)
                    cost = qty * current["close"]
                    if cost <= cash:
                        cash -= cost
                        position = {"side": side, "entry": current["close"], "qty": qty, "sl": sl, "tp": tp}
                        position["strategy"] = best.get("strategies", [best.get("strategy", "unknown")])[0]

        pos_value = position["qty"] * current["close"] if position else 0
        pos_side = position["side"] if position else None
        if pos_side == "SELL":
            pos_value = position["qty"] * (2 * position["entry"] - current["close"])
        equity_curve.append(cash + pos_value)

    if position:
        exit_price = ohlc[-1]["close"]
        if position["side"] == "BUY":
            pnl = (exit_price - position["entry"]) * position["qty"]
        else:
            pnl = (position["entry"] - exit_price) * position["qty"]
        cash += position["qty"] * exit_price
        trades.append({
            "symbol": symbol, "side": position["side"], "qty": position["qty"],
            "entry": position["entry"], "exit": exit_price,
            "pnl": round(pnl, 2), "pnl_pct": round((pnl / (position["entry"] * position["qty"])) * 100, 2),
            "reason": "open", "bar": len(ohlc),
        })
        position = None

    return _compute_metrics(symbol, trades, equity_curve, initial_capital)


def _compute_metrics(symbol, trades, equity_curve, initial_capital):
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
    peak = equity_curve[0]
    max_dd = 0
    for v in equity_curve:
        if v > peak:
            peak = v
        dd = (peak - v) / peak * 100 if peak > 0 else 0
        if dd > max_dd:
            max_dd = dd
    returns = [equity_curve[i] - equity_curve[i - 1] for i in range(1, len(equity_curve))]
    sharpe = 0
    if len(returns) > 1 and stdev(returns) > 0:
        sharpe = (mean(returns) / stdev(returns)) * (365 ** 0.5)

    return {
        "symbol": symbol,
        "total_return": round(total_return, 2),
        "final_equity": round(final_equity, 2),
        "total_trades": total_trades,
        "win_rate": round(win_rate, 1),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "profit_factor": round(profit_factor, 2) if profit_factor != float("inf") else None,
        "max_drawdown": round(max_dd, 2),
        "sharpe_ratio": round(sharpe, 2),
        "trades": trades[-20:],
    }


def run_all_backtests(symbols=None):
    symbols = symbols or [s for s in WATCHED_SYMBOLS if "/" in s][:5]
    results = []
    execute("DELETE FROM backtest_results")
    for sym in symbols:
        print(f"  Backtesting {sym}...")
        result = backtest_symbol(sym)
        if result:
            _save_backtest(result)
            results.append(result)
    return results


def _save_backtest(result):
    execute("""
        INSERT OR REPLACE INTO backtest_results
        (symbol, total_return, total_trades, win_rate, profit_factor, max_drawdown, sharpe_ratio, final_equity, avg_win, avg_loss, tested_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
    """, [result["symbol"], result["total_return"], result["total_trades"],
          result["win_rate"], result["profit_factor"], result["max_drawdown"],
          result["sharpe_ratio"], result["final_equity"], result["avg_win"],
          result["avg_loss"]])


def get_backtest_results():
    rows = fetchall("SELECT * FROM backtest_results ORDER BY total_return DESC")
    return [dict(r) for r in rows]

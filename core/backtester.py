from statistics import stdev, mean

from config import WATCHED_SYMBOLS, INITIAL_BALANCE, TRADE_FEE_PCT, MAX_POSITION_SIZE_PCT, BACKTEST_BARS, TRADING_TIMEFRAME
from core.database import execute, fetchone, fetchall, get_unprofitable_strategies
from core.strategies import ALL_STRATEGIES, scan_symbol
from core.market import MarketData


MAX_ACTIVE_POSITIONS = 3

_REGIME_PRICING = {
    "trending_up":   {"sl_mult": 1.5, "tp_mult": 2.5},
    "trending_down": {"sl_mult": 1.5, "tp_mult": 2.5},
    "trending":      {"sl_mult": 1.5, "tp_mult": 2.0},
    "volatile":      {"sl_mult": 2.0, "tp_mult": 2.5},
    "ranging":       {"sl_mult": 1.5, "tp_mult": 1.5},
}

_DEFAULT_PRICING = {"sl_mult": 1.5, "tp_mult": 2.0}


def fetch_klines(symbol, interval="1d", limit=100):
    from core.data_provider import fetch_ohlc
    return fetch_ohlc(symbol, interval=interval, limit=limit)


def _calc_sl_tp(entry_price, side, volatility_pct, atr_pct=0, sl_mult=2.5, tp_mult=3.5):
    vol_dec = (volatility_pct or 2) / 100.0
    atr_dec = max(atr_pct / 100.0, 0.005) if atr_pct > 0 else vol_dec
    sl_distance = max(atr_dec * sl_mult, vol_dec * sl_mult * 1.2)
    tp_distance = max(atr_dec * tp_mult, vol_dec * tp_mult * 0.8)
    if side == "BUY":
        sl = round(entry_price * (1 - sl_distance), 5)
        tp = round(entry_price * (1 + tp_distance), 5)
    else:
        sl = round(entry_price * (1 + sl_distance), 5)
        tp = round(entry_price * (1 - tp_distance), 5)
    return sl, tp


def _pos_value(pos, current_price):
    if pos["side"] == "BUY":
        return pos["qty"] * current_price
    return pos["qty"] * (2 * pos["entry"] - current_price)


def backtest_symbol(symbol, bars=BACKTEST_BARS, initial_capital=INITIAL_BALANCE):
    ohlc = fetch_klines(symbol, interval=TRADING_TIMEFRAME, limit=bars + 200) or []
    if len(ohlc) < 200:
        return None

    fee_ratio = TRADE_FEE_PCT / 100.0
    cash = initial_capital
    positions = []
    trades = []
    equity_curve = []
    market = MarketData()
    bad_strats = get_unprofitable_strategies()

    for i in range(200, len(ohlc)):
        slice_data = ohlc[:i + 1]
        current = ohlc[i]
        high, low = current["high"], current["low"]
        close = current["close"]

        remaining = []
        for pos in positions:
            side, entry, qty, sl, tp = pos["side"], pos["entry"], pos["qty"], pos["sl"], pos["tp"]
            exit_price = None
            reason = None

            # Breakeven: if price moved 1x initial SL distance in our favor, move SL to entry
            sl_distance = abs(entry - sl)
            if sl_distance > 0:
                if side == "BUY" and high >= entry + sl_distance and sl < entry:
                    sl = entry
                    pos["sl"] = entry
                elif side == "SELL" and low <= entry - sl_distance and sl > entry:
                    sl = entry
                    pos["sl"] = entry

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
                exit_fee = qty * exit_price * fee_ratio
                cash += qty * exit_price - exit_fee
                pnl_pct = (pnl / (entry * qty)) * 100 if entry * qty else 0
                trades.append({
                    "symbol": symbol, "side": side, "qty": qty,
                    "entry": entry, "exit": exit_price,
                    "pnl": round(pnl - exit_fee, 2), "pnl_pct": round(pnl_pct, 2),
                    "reason": reason, "bar": i,
                    "date": current["date"][:10],
                })
            else:
                remaining.append(pos)
        positions = remaining

        if len(positions) < MAX_ACTIVE_POSITIONS:
            signals = scan_symbol(slice_data, exclude_strategies=bad_strats)
            buy_signals = [s for s in signals if s["action"] == "BUY"]
            sell_signals = [s for s in signals if s["action"] == "SELL"]
            if buy_signals or sell_signals:
                ind = market.compute_indicators(slice_data[-30:])
                vol = ind.get("volatility", 2)
                atr_val = ind.get("atr", 0)
                atr_pct = (atr_val / close * 100) if atr_val and close > 0 else 0
                best = max(buy_signals + sell_signals, key=lambda s: s["confidence"])
                side = best["action"]
                qty = (cash * MAX_POSITION_SIZE_PCT / 100) / close
                if qty >= 0.001:
                    sl, tp = _calc_sl_tp(close, side, vol, atr_pct)
                    cost = qty * close
                    entry_fee = cost * fee_ratio
                    total_cost = cost + entry_fee
                    if total_cost <= cash:
                        cash -= total_cost
                        positions.append({
                            "side": side, "entry": close, "qty": qty, "sl": sl, "tp": tp,
                            "strategy": best.get("strategies", [best.get("strategy", "unknown")])[0]
                        })

        total_value = cash + sum(_pos_value(p, close) for p in positions)
        equity_curve.append(total_value)

    for pos in positions:
        exit_price = ohlc[-1]["close"]
        if pos["side"] == "BUY":
            pnl = (exit_price - pos["entry"]) * pos["qty"]
        else:
            pnl = (pos["entry"] - exit_price) * pos["qty"]
        exit_fee = pos["qty"] * exit_price * fee_ratio
        cash += pos["qty"] * exit_price - exit_fee
        trades.append({
            "symbol": symbol, "side": pos["side"], "qty": pos["qty"],
            "entry": pos["entry"], "exit": exit_price,
            "pnl": round(pnl - exit_fee, 2), "pnl_pct": round((pnl / (pos["entry"] * pos["qty"])) * 100, 2),
            "reason": "close", "bar": len(ohlc),
        })

    # Buy-and-hold over the same tested window: a strategy that trails simply
    # holding the asset isn't earning its fees.
    benchmark_return = ((ohlc[-1]["close"] - ohlc[200]["close"]) / ohlc[200]["close"]) * 100
    return _compute_metrics(symbol, trades, equity_curve, initial_capital, benchmark_return)


def _compute_metrics(symbol, trades, equity_curve, initial_capital, benchmark_return=0.0):
    final_equity = equity_curve[-1] if equity_curve else initial_capital
    if not equity_curve:
        return {"symbol": symbol, "total_return": 0, "final_equity": initial_capital,
                "total_trades": 0, "win_rate": 0, "avg_win": 0, "avg_loss": 0,
                "profit_factor": None, "max_drawdown": 0, "sharpe_ratio": 0, "trades": [],
                "benchmark_return": round(benchmark_return, 2),
                "beats_benchmark": 0 >= benchmark_return}
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
        "benchmark_return": round(benchmark_return, 2),
        "beats_benchmark": total_return >= benchmark_return,
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

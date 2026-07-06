from core.backtester import backtest_symbol, fetch_klines
from core.database import execute, fetchall, fetchone
from core.strategies import scan_symbol
from config import INITIAL_BALANCE

PARAM_GRID = {
    "sl_mult": [1.5, 2.0, 2.5],
    "tp_mult": [2.0, 3.0, 4.0],
    "position_size_pct": [20, 25, 30],
    "confidence_threshold": [0.4, 0.5, 0.6],
}

BACKTEST_DAYS = 90


def _backtest_with_params(symbol, sl_mult, tp_mult, pos_size, conf_thresh, days=BACKTEST_DAYS):
    ohlc = fetch_klines(symbol, interval="1d", limit=days + 50)
    if len(ohlc) < 50:
        return None

    cash = INITIAL_BALANCE
    position = None
    trades = []
    equity_curve = []

    for i in range(50, len(ohlc)):
        slice_data = ohlc[:i + 1]
        current = ohlc[i]

        if position:
            high, low = current["high"], current["low"]
            side, entry, qty, sl, tp = position["side"], position["entry"], position["qty"], position["sl"], position["tp"]
            hit_sl = (side == "BUY" and low <= sl) or (side == "SELL" and high >= sl)
            hit_tp = (side == "BUY" and high >= tp) or (side == "SELL" and low <= tp)
            if hit_sl or hit_tp:
                exit_price = sl if hit_sl else tp
                reason = "SL" if hit_sl else "TP"
                pnl = (exit_price - entry) * qty if side == "BUY" else (entry - exit_price) * qty
                cash += qty * exit_price
                trades.append({"pnl": round(pnl, 2)})
                position = None

        if not position and i % 5 == 0:
            signals = scan_symbol(slice_data)
            signals = [s for s in signals if s["confidence"] >= conf_thresh]
            if signals:
                best = max(signals, key=lambda s: s["confidence"])
                qty = (cash * pos_size / 100) / current["close"]
                if qty >= 0.001 and qty * current["close"] <= cash:
                    cost = qty * current["close"]
                    cash -= cost
                    sl_p = round(current["close"] * (1 - (1 / 100) * sl_mult)) if best["action"] == "BUY" else round(current["close"] * (1 + (1 / 100) * sl_mult))
                    tp_p = round(current["close"] * (1 + (1 / 100) * tp_mult)) if best["action"] == "BUY" else round(current["close"] * (1 - (1 / 100) * tp_mult))
                    # Use volatility for SL/TP
                    vol = (max(c["high"] for c in slice_data[-14:]) - min(c["low"] for c in slice_data[-14:])) / current["close"]
                    vol = max(vol, 0.005)
                    if best["action"] == "BUY":
                        sl_p = round(current["close"] * (1 - vol * sl_mult), 5)
                        tp_p = round(current["close"] * (1 + vol * tp_mult), 5)
                    else:
                        sl_p = round(current["close"] * (1 + vol * sl_mult), 5)
                        tp_p = round(current["close"] * (1 - vol * tp_mult), 5)
                    position = {"side": best["action"], "entry": current["close"], "qty": qty, "sl": sl_p, "tp": tp_p}

        pos_val = position["qty"] * current["close"] if position else 0
        if position and position["side"] == "SELL":
            pos_val = position["qty"] * (2 * position["entry"] - current["close"])
        equity_curve.append(cash + pos_val)

    if position:
        cash += position["qty"] * ohlc[-1]["close"]

    final_equity = equity_curve[-1] if equity_curve else cash
    total_return = ((final_equity - INITIAL_BALANCE) / INITIAL_BALANCE) * 100
    total_trades = len(trades)
    winning = [t for t in trades if t["pnl"] > 0]
    losing = [t for t in trades if t["pnl"] < 0]
    win_rate = (len(winning) / total_trades * 100) if total_trades > 0 else 0
    avg_win = sum(t["pnl"] for t in winning) / len(winning) if winning else 0
    avg_loss = sum(abs(t["pnl"]) for t in losing) / len(losing) if losing else 0
    gross_profit = sum(t["pnl"] for t in winning)
    gross_loss = abs(sum(t["pnl"] for t in losing))
    pf = (gross_profit / gross_loss) if gross_loss > 0 else (0 if gross_profit == 0 else float("inf"))
    peak = equity_curve[0]
    max_dd = 0
    for v in equity_curve:
        if v > peak:
            peak = v
        dd = (peak - v) / peak * 100 if peak > 0 else 0
        if dd > max_dd:
            max_dd = dd
    from statistics import mean, stdev
    returns = [equity_curve[i] - equity_curve[i - 1] for i in range(1, len(equity_curve))]
    sharpe = 0
    if len(returns) > 1 and stdev(returns) > 0:
        sharpe = (mean(returns) / stdev(returns)) * (365 ** 0.5)

    score = total_return * 0.3 + win_rate * 0.15 + (pf if pf != float("inf") else 3) * 0.2 + min(sharpe, 5) * 0.2 + (20 - min(max_dd, 20)) * 0.15

    return {
        "total_return": round(total_return, 2),
        "total_trades": total_trades,
        "win_rate": round(win_rate, 1),
        "profit_factor": round(pf, 2) if pf != float("inf") else None,
        "max_drawdown": round(max_dd, 2),
        "sharpe_ratio": round(sharpe, 2),
        "score": round(score, 2),
    }


def optimize_symbol(symbol, verbose=True):
    best = None
    best_params = None
    total = 1
    for sm in PARAM_GRID["sl_mult"]:
        for tm in PARAM_GRID["tp_mult"]:
            for ps in PARAM_GRID["position_size_pct"]:
                for ct in PARAM_GRID["confidence_threshold"]:
                    total += 1
    count = 0
    for sm in PARAM_GRID["sl_mult"]:
        for tm in PARAM_GRID["tp_mult"]:
            for ps in PARAM_GRID["position_size_pct"]:
                for ct in PARAM_GRID["confidence_threshold"]:
                    count += 1
                    result = _backtest_with_params(symbol, sm, tm, ps, ct)
                    if result and (best is None or result["score"] > best["score"]):
                        best = result
                        best_params = {"sl_mult": sm, "tp_mult": tm, "position_size_pct": ps, "confidence_threshold": ct}
                    if verbose and count % 20 == 0:
                        print(f"    {symbol}: {count}/{total} combos...", end="\r")
    if verbose:
        print(f"    {symbol}: done ({count} combos)")
    if best and best_params:
        _save_optimization(symbol, best, best_params)
        return {"symbol": symbol, "params": best_params, "result": best}
    return None


def _save_optimization(symbol, result, params):
    execute("""
        INSERT OR REPLACE INTO optimization_results
        (symbol, sl_mult, tp_mult, position_size_pct, confidence_threshold, total_return, total_trades, win_rate, profit_factor, max_drawdown, sharpe_ratio, score, optimized_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
    """, [symbol, params["sl_mult"], params["tp_mult"], params["position_size_pct"],
          params["confidence_threshold"], result["total_return"], result["total_trades"],
          result["win_rate"], result["profit_factor"], result["max_drawdown"],
          result["sharpe_ratio"], result["score"]])


def get_optimized_params(symbol):
    row = fetchone("SELECT * FROM optimization_results WHERE symbol=? ORDER BY score DESC LIMIT 1", [symbol])
    if row:
        return {
            "sl_mult": row["sl_mult"],
            "tp_mult": row["tp_mult"],
            "position_size_pct": row["position_size_pct"],
            "confidence_threshold": row["confidence_threshold"],
        }
    return {"sl_mult": 1.5, "tp_mult": 3.0, "position_size_pct": 25, "confidence_threshold": 0.0}


def run_all_optimizations(symbols):
    results = []
    for sym in symbols:
        print(f"  Optimizing {sym}...")
        r = optimize_symbol(sym)
        if r:
            results.append(r)
    return results


def get_optimization_results():
    rows = fetchall("SELECT * FROM optimization_results ORDER BY score DESC")
    return [dict(r) for r in rows]

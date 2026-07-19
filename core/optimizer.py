from core.backtester import backtest_symbol, fetch_klines
from core.pricing import round_sig
from core.database import execute, fetchall, fetchone
from core.strategies import scan_symbol
from config import INITIAL_BALANCE, TRADE_FEE_PCT, BACKTEST_BARS, TRADING_TIMEFRAME, BUY_ONLY

FEE_RATIO = TRADE_FEE_PCT / 100.0

PARAM_GRID = {
    "sl_mult": [0.3, 0.5, 0.8, 1.2, 1.5],
    "tp_mult": [0.6, 1.0, 1.5, 2.0, 2.5],
    "position_size_pct": [25, 35, 45],
    "confidence_threshold": [0.3, 0.4, 0.5],
}
# Walk-forward split: parameters are searched on the first fraction of the
# history and only adopted if they hold up on the unseen remainder. Scoring
# the search and the verdict on the same candles is how overfit params win.
TRAIN_FRACTION = 0.7


def _backtest_with_params(symbol, sl_mult, tp_mult, pos_size, conf_thresh, bars=BACKTEST_BARS,
                          ohlc=None, bar_range=None):
    if ohlc is None:
        ohlc = fetch_klines(symbol, interval=TRADING_TIMEFRAME, limit=bars + 200) or []
    if len(ohlc) < 200:
        return None
    start, end = 200, len(ohlc)
    if bar_range:
        # Fractions of the full history; indicators still warm up on all
        # candles before `start`, which is past data — no leakage.
        start = max(200, int(len(ohlc) * bar_range[0]))
        end = min(len(ohlc), int(len(ohlc) * bar_range[1]))
        if end - start < 50:
            return None

    cash = INITIAL_BALANCE
    position = None
    trades = []
    equity_curve = []

    for i in range(start, end):
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
                exit_fee = qty * exit_price * FEE_RATIO
                cash += qty * exit_price - exit_fee
                trades.append({"pnl": round(pnl, 2)})
                position = None

        if not position:
            signals = scan_symbol(slice_data)
            signals = [s for s in signals if s["confidence"] >= conf_thresh]
            if BUY_ONLY:
                signals = [s for s in signals if s["action"] == "BUY"]
            if signals and current["close"] > 0:
                best = max(signals, key=lambda s: s["confidence"])
                qty = (cash * pos_size / 100) / current["close"]
                if qty >= 0.001:
                    cost = qty * current["close"]
                    entry_fee = cost * FEE_RATIO
                    total_cost = cost + entry_fee
                    # Entry only when affordable — previously the position
                    # was created even when cash couldn't cover it.
                    if total_cost <= cash:
                        cash -= total_cost
                        vol = (max(c["high"] for c in slice_data[-14:]) - min(c["low"] for c in slice_data[-14:])) / current["close"]
                        vol = max(vol, 0.005)
                        if best["action"] == "BUY":
                            sl_p = round_sig(current["close"] * (1 - vol * sl_mult))
                            tp_p = round_sig(current["close"] * (1 + vol * tp_mult))
                        else:
                            sl_p = round_sig(current["close"] * (1 + vol * sl_mult))
                            tp_p = round_sig(current["close"] * (1 - vol * tp_mult))
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
    # Fetch once — every grid combo replays the same candles.
    ohlc = fetch_klines(symbol, interval=TRADING_TIMEFRAME, limit=BACKTEST_BARS + 200) or None
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
                    result = _backtest_with_params(symbol, sm, tm, ps, ct,
                                                   ohlc=ohlc, bar_range=(0.0, TRAIN_FRACTION))
                    if result and (best is None or result["score"] > best["score"]):
                        best = result
                        best_params = {"sl_mult": sm, "tp_mult": tm, "position_size_pct": ps, "confidence_threshold": ct}
                    if verbose and count % 20 == 0:
                        print(f"    {symbol}: {count}/{total} combos...", end="\r")
    if verbose:
        print(f"    {symbol}: done ({count} combos)")
    if not (best and best_params):
        return None
    # Walk-forward gate: the winning params must survive candles the grid
    # search never saw, or they are noise fit to the training window.
    validation = _backtest_with_params(
        symbol, best_params["sl_mult"], best_params["tp_mult"],
        best_params["position_size_pct"], best_params["confidence_threshold"],
        ohlc=ohlc, bar_range=(TRAIN_FRACTION, 1.0))
    pf = (validation or {}).get("profit_factor")
    adopted = bool(validation and validation["total_return"] > 0
                   and (pf is None or pf >= 1.0))
    if not adopted:
        if verbose:
            print(f"    {symbol}: best in-sample params failed out-of-sample validation — not adopted")
        return {"symbol": symbol, "params": best_params, "result": best,
                "validation": validation, "adopted": False}
    # Persist the out-of-sample metrics — those are the honest numbers.
    _save_optimization(symbol, validation, best_params)
    return {"symbol": symbol, "params": best_params, "result": validation,
            "validation": validation, "adopted": True}


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
    return {"sl_mult": 2.0, "tp_mult": 6.0, "position_size_pct": 25, "confidence_threshold": 0.0}


def test_single_param(param_name, current_value, increment, symbol=None, bars=BACKTEST_BARS):
    """Test a single param with ±increment delta, return best value.

    Uses WATCHED_SYMBOLS[0] if symbol not given.  Returns the value
    (current, +increment, or -increment) that maximises the backtest score
    together with the score dict.  Returns (current_value, None) on error.
    """
    from config import WATCHED_SYMBOLS
    sym = symbol or (WATCHED_SYMBOLS[0] if WATCHED_SYMBOLS else "BTC/USD")
    candidates = [current_value, current_value + increment, current_value - increment]

    ohlc = fetch_klines(sym, interval=TRADING_TIMEFRAME, limit=bars + 200) or None
    best_score = -1e9
    best_val = current_value
    best_result = None
    best_kwargs = None

    for val in candidates:
        # Build kwargs for _backtest_with_params from PARAM_GRID defaults,
        # overriding the param under test.
        kwargs = dict(
            sl_mult=PARAM_GRID["sl_mult"][len(PARAM_GRID["sl_mult"]) // 2],
            tp_mult=PARAM_GRID["tp_mult"][len(PARAM_GRID["tp_mult"]) // 2],
            pos_size=PARAM_GRID["position_size_pct"][len(PARAM_GRID["position_size_pct"]) // 2],
            conf_thresh=PARAM_GRID["confidence_threshold"][len(PARAM_GRID["confidence_threshold"]) // 2],
        )
        # Map param_name to the kwarg
        kwarg_map = {
            "SL_VOL_MULT": "sl_mult",
            "TP_VOL_MULT": "tp_mult",
            "POSITION_SIZE_PCT": "pos_size",
            "MAX_POSITION_SIZE_PCT": "pos_size",
            "RISK_PER_TRADE_PCT": "pos_size",  # approximated via position size
            "STOP_LOSS_PCT": "sl_mult",         # approximated via sl_mult
        }
        target_kwarg = kwarg_map.get(param_name)
        if target_kwarg:
            kwargs[target_kwarg] = val

        result = _backtest_with_params(sym, **kwargs, bars=bars,
                                       ohlc=ohlc, bar_range=(0.0, TRAIN_FRACTION))
        if result and result["score"] > best_score:
            best_score = result["score"]
            best_val = val
            best_result = result
            best_kwargs = kwargs

    # A proposed change (not the incumbent value) must also hold up on the
    # unseen validation window before the agent gets to suggest it.
    if best_val != current_value and best_kwargs:
        validation = _backtest_with_params(sym, **best_kwargs, bars=bars,
                                           ohlc=ohlc, bar_range=(TRAIN_FRACTION, 1.0))
        pf = (validation or {}).get("profit_factor")
        if not validation or validation["total_return"] <= 0 or (pf is not None and pf < 1.0):
            return current_value, None

    return best_val, best_result


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

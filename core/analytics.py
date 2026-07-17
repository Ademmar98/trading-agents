from statistics import mean, stdev
from datetime import datetime, timezone
from core.database import fetchall, execute


def compute_analytics():
    # Group by position so a scaled exit (partial_tp row + runner row) counts
    # as one trade with its net pnl — split rows would skew win rate,
    # expectancy, and the per-strategy stats.
    trades = fetchall("""
        SELECT MAX(t.symbol) AS symbol, MAX(t.strategy) AS strategy,
               MAX(t.reason) AS reason, SUM(t.pnl) AS pnl, SUM(t.qty) AS qty,
               MIN(t.opened_at) AS opened_at, MAX(t.closed_at) AS closed_at,
               MAX(p.side) AS side, MAX(p.entry_price) AS entry_price,
               MAX(p.quantity) AS quantity
        FROM trades t
        LEFT JOIN positions p ON t.position_id = p.id
        GROUP BY COALESCE(t.position_id, t.id)
        ORDER BY MAX(t.closed_at) DESC
    """)
    trades = [dict(r) for r in trades]

    if not trades:
        return _empty_analytics()

    total = len(trades)
    winning = [t for t in trades if t["pnl"] > 0]
    losing = [t for t in trades if t["pnl"] < 0]
    win_rate = (len(winning) / total * 100) if total > 0 else 0

    gross_profit = sum(t["pnl"] for t in winning)
    gross_loss = abs(sum(t["pnl"] for t in losing))
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else float("inf")

    avg_win = mean([t["pnl"] for t in winning]) if winning else 0
    avg_loss = mean([abs(t["pnl"]) for t in losing]) if losing else 0

    all_pnls = [t["pnl"] for t in trades]

    sharpe = 0
    if len(all_pnls) > 1 and stdev(all_pnls) > 0:
        avg_pnl = mean(all_pnls)
        sharpe = (avg_pnl / stdev(all_pnls)) * (365 ** 0.5)

    max_dd_pct, rolling_dd = _rolling_drawdown(all_pnls)

    total_pnl = sum(all_pnls)
    avg_pnl_per_trade = mean(all_pnls) if all_pnls else 0
    expectancy = (win_rate / 100 * avg_win) - ((1 - win_rate / 100) * avg_loss)

    strategy_stats = _compute_strategy_stats(trades)
    duration_stats = _trade_duration_stats(trades)
    var_95 = _value_at_risk(all_pnls, 0.95)

    result = {
        "total_trades": total,
        "win_rate": round(win_rate, 1),
        "profit_factor": round(profit_factor, 2) if profit_factor != float("inf") else None,
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "sharpe_ratio": round(sharpe, 2),
        "max_drawdown_pct": round(max_dd_pct * 100, 2),
        "total_pnl": round(total_pnl, 2),
        "avg_pnl": round(avg_pnl_per_trade, 2),
        "expectancy": round(expectancy, 2),
        "winning_trades": len(winning),
        "losing_trades": len(losing),
        "var_95": round(var_95, 2),
        "strategy_breakdown": strategy_stats,
        "trade_duration": duration_stats,
    }

    _save_analytics(result)
    return result


def _rolling_drawdown(pnls, initial_balance=None):
    """Max drawdown on the cumulative equity curve, as a fraction of peak
    equity. The old version compared individual trade pnls to the largest
    single win — a $10 win followed by a $2 win read as an 80% "drawdown",
    which poisoned the dashboard and the head-trader's context.

    pnls arrive newest-first (trades are ordered by closed_at DESC), so the
    curve is built over the reversed sequence."""
    if not pnls:
        return 0, []
    if initial_balance is None:
        from config import INITIAL_BALANCE
        initial_balance = INITIAL_BALANCE
    equity = initial_balance
    peak = equity
    max_dd_pct = 0.0
    rolling = []
    for pnl in reversed(pnls):
        equity += pnl
        if equity > peak:
            peak = equity
        dd_pct = (peak - equity) / peak if peak > 0 else 0.0
        rolling.append(dd_pct)
        if dd_pct > max_dd_pct:
            max_dd_pct = dd_pct
    return max_dd_pct, rolling


def _value_at_risk(pnls, confidence=0.95):
    if len(pnls) < 5:
        return 0
    sorted_pnls = sorted(pnls)
    idx = int((1 - confidence) * len(sorted_pnls))
    return abs(sorted_pnls[idx])


def _trade_duration_stats(trades):
    durations_h = []
    for t in trades:
        opened = t.get("opened_at") or t.get("open_time")
        closed = t.get("closed_at") or t.get("close_time")
        if not opened or not closed:
            continue
        try:
            ot = datetime.fromisoformat(opened)
            ct = datetime.fromisoformat(closed)
            hours = (ct - ot).total_seconds() / 3600
            durations_h.append(hours)
        except (ValueError, TypeError):
            pass
    if not durations_h:
        return {"avg_hours": 0, "min_hours": 0, "max_hours": 0, "count": 0}
    return {
        "avg_hours": round(mean(durations_h), 1),
        "min_hours": round(min(durations_h), 1),
        "max_hours": round(max(durations_h), 1),
        "count": len(durations_h),
    }


def _compute_strategy_stats(trades):
    by_strategy = {}
    for t in trades:
        # Combined signals tag the trade with every contributor pipe-joined
        # ("a|b") — score the trade under EACH contributing strategy so
        # co-contributors accumulate their own expectancy record.
        for strat_name in (t.get("strategy") or t.get("reason", "unknown")).split("|"):
            s = by_strategy.setdefault(strat_name, {"trades": 0, "won": 0, "pnl": 0,
                                                    "win_pnl": 0.0, "loss_pnl": 0.0})
            s["trades"] += 1
            s["pnl"] += t["pnl"]
            if t["pnl"] > 0:
                s["won"] += 1
                s["win_pnl"] += t["pnl"]
            elif t["pnl"] < 0:
                s["loss_pnl"] += abs(t["pnl"])

    result = []
    for strategy, s in sorted(by_strategy.items(), key=lambda x: x[1]["pnl"], reverse=True):
        n = s["trades"]
        losses = n - s["won"]
        result.append({
            "strategy": strategy,
            "trades": n,
            "win_rate": round((s["won"] / n) * 100, 1) if n else 0,
            "pnl": round(s["pnl"], 2),
            "expectancy": round(s["pnl"] / n, 2) if n else 0,
            "avg_win": round(s["win_pnl"] / s["won"], 2) if s["won"] else 0,
            "avg_loss": round(s["loss_pnl"] / losses, 2) if losses else 0,
        })
    return result


def _save_analytics(data):
    execute("DELETE FROM analytics")
    execute("""
        INSERT INTO analytics
        (total_trades, win_rate, profit_factor, sharpe_ratio, max_drawdown, total_pnl, expectancy, computed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
    """, [data["total_trades"], data["win_rate"], data["profit_factor"],
          data["sharpe_ratio"], data["max_drawdown_pct"], data["total_pnl"],
          data["expectancy"]])


def get_analytics():
    row = fetchall("SELECT * FROM analytics ORDER BY computed_at DESC LIMIT 1")
    if not row:
        return _empty_analytics()
    result = dict(row[0])
    result["strategy_breakdown"] = [dict(r) for r in fetchall(
        "SELECT * FROM strategy_stats ORDER BY pnl DESC"
    )]
    empty = _empty_analytics()
    for k in empty:
        result.setdefault(k, empty[k])
    if "max_drawdown" in result and "max_drawdown_pct" not in result:
        result["max_drawdown_pct"] = result["max_drawdown"]
    return result


def get_strategy_stats():
    rows = fetchall("SELECT * FROM strategy_stats ORDER BY pnl DESC")
    return [dict(r) for r in rows]


def _empty_analytics():
    return {
        "total_trades": 0, "win_rate": 0, "profit_factor": None,
        "avg_win": 0, "avg_loss": 0, "sharpe_ratio": 0,
        "max_drawdown_pct": 0, "max_drawdown": 0,
        "total_pnl": 0, "avg_pnl": 0,
        "expectancy": 0, "winning_trades": 0, "losing_trades": 0,
        "var_95": 0,
        "strategy_breakdown": [],
        "trade_duration": {"avg_hours": 0, "min_hours": 0, "max_hours": 0, "count": 0},
    }

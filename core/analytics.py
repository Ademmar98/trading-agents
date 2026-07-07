from statistics import mean, stdev
from datetime import datetime, timezone
from core.database import fetchall, execute


def compute_analytics():
    trades = fetchall("""
        SELECT t.*, p.side, p.entry_price, p.quantity
        FROM trades t
        LEFT JOIN positions p ON t.position_id = p.id
        ORDER BY t.closed_at DESC
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


def _rolling_drawdown(pnls):
    if not pnls:
        return 0, []
    max_dd_pct = 0
    peak = pnls[0]
    rolling = []
    for pnl in pnls:
        if pnl > peak:
            peak = pnl
        dd = peak - pnl
        dd_pct = dd / peak if peak > 0 else 0
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
        strat_name = t.get("strategy") or t.get("reason", "unknown")
        by_strategy.setdefault(strat_name, {"trades": 0, "won": 0, "pnl": 0})
        by_strategy[strat_name]["trades"] += 1
        by_strategy[strat_name]["pnl"] += t["pnl"]
        if t["pnl"] > 0:
            by_strategy[strat_name]["won"] += 1

    result = []
    for strategy, stats in sorted(by_strategy.items(), key=lambda x: x[1]["pnl"], reverse=True):
        result.append({
            "strategy": strategy,
            "trades": stats["trades"],
            "win_rate": round((stats["won"] / stats["trades"]) * 100, 1) if stats["trades"] > 0 else 0,
            "pnl": round(stats["pnl"], 2),
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
    execute("DELETE FROM strategy_stats")
    for s in data.get("strategy_breakdown", []):
        execute("""
            INSERT INTO strategy_stats (strategy, trades, win_rate, pnl)
            VALUES (?, ?, ?, ?)
        """, [s["strategy"], s["trades"], s["win_rate"], s["pnl"]])


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

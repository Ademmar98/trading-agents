from statistics import mean, stdev
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

    max_dd = 0
    peak = 0
    cumulative = 0
    for pnl in all_pnls:
        cumulative += pnl
        if cumulative > peak:
            peak = cumulative
        dd = peak - cumulative
        if dd > max_dd:
            max_dd = dd

    total_pnl = sum(all_pnls)
    avg_pnl_per_trade = mean(all_pnls) if all_pnls else 0
    expectancy = (win_rate / 100 * avg_win) - ((1 - win_rate / 100) * avg_loss)

    strategy_stats = _compute_strategy_stats(trades)

    result = {
        "total_trades": total,
        "win_rate": round(win_rate, 1),
        "profit_factor": round(profit_factor, 2) if profit_factor != float("inf") else None,
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "sharpe_ratio": round(sharpe, 2),
        "max_drawdown": round(max_dd, 2),
        "total_pnl": round(total_pnl, 2),
        "avg_pnl": round(avg_pnl_per_trade, 2),
        "expectancy": round(expectancy, 2),
        "winning_trades": len(winning),
        "losing_trades": len(losing),
        "strategy_breakdown": strategy_stats,
    }

    _save_analytics(result)
    return result


def _compute_strategy_stats(trades):
    by_strategy = {}
    for t in trades:
        reason = t.get("reason", "unknown")
        by_strategy.setdefault(reason, {"trades": 0, "won": 0, "pnl": 0})
        by_strategy[reason]["trades"] += 1
        by_strategy[reason]["pnl"] += t["pnl"]
        if t["pnl"] > 0:
            by_strategy[reason]["won"] += 1

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
          data["sharpe_ratio"], data["max_drawdown"], data["total_pnl"],
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
    return result


def get_strategy_stats():
    rows = fetchall("SELECT * FROM strategy_stats ORDER BY pnl DESC")
    return [dict(r) for r in rows]


def _empty_analytics():
    return {
        "total_trades": 0, "win_rate": 0, "profit_factor": None,
        "avg_win": 0, "avg_loss": 0, "sharpe_ratio": 0,
        "max_drawdown": 0, "total_pnl": 0, "avg_pnl": 0,
        "expectancy": 0, "winning_trades": 0, "losing_trades": 0,
        "strategy_breakdown": [],
    }

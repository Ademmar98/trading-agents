from datetime import datetime, timezone

from core.database import execute, fetchone, fetchall, get_meta, set_meta
from core.portfolio import load_portfolio


def _utc_today():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def snapshot_equity():
    """Record the current portfolio equity. Called once per trading cycle."""
    p = load_portfolio()
    execute(
        "INSERT INTO equity_history (equity, cash, positions_value, exposure_pct) "
        "VALUES (?, ?, ?, ?)",
        [round(p.equity, 2), round(p.cash, 2),
         round(p.positions_value, 2), round(p.exposure_pct, 2)],
    )
    return p.equity


def get_equity_history(limit=500):
    rows = fetchall(
        "SELECT equity, snapped_at FROM equity_history ORDER BY id DESC LIMIT ?",
        [limit],
    )
    return [dict(r) for r in reversed(rows)]


def day_start_equity(date_str=None):
    """Equity at the first snapshot of the given UTC day (default: today)."""
    row = fetchone(
        "SELECT equity FROM equity_history WHERE date(snapped_at) = ? "
        "ORDER BY id ASC LIMIT 1",
        [date_str or _utc_today()],
    )
    return row["equity"] if row else None


def daily_loss_pct():
    """Today's equity change in percent vs the day's first snapshot (negative = loss)."""
    start = day_start_equity()
    if not start:
        return 0.0
    p = load_portfolio()
    return (p.equity - start) / start * 100


def build_daily_summary(date_str):
    """Stats for the Telegram report covering one completed UTC day."""
    p = load_portfolio()
    rows = fetchall("SELECT pnl FROM trades WHERE date(closed_at) = ?", [date_str])
    pnls = [r["pnl"] for r in rows]
    wins = len([x for x in pnls if x > 0])
    start = day_start_equity(date_str)
    end_row = fetchone(
        "SELECT equity FROM equity_history WHERE date(snapped_at) = ? "
        "ORDER BY id DESC LIMIT 1",
        [date_str],
    )
    end = end_row["equity"] if end_row else None
    day_pnl_pct = ((end - start) / start * 100) if start and end else 0.0
    return {
        "date": date_str,
        "equity": round(p.equity, 2),
        "total_pnl_pct": round(p.total_pnl_pct, 2),
        "day_pnl_pct": round(day_pnl_pct, 2),
        "trades_closed": len(pnls),
        "pnl_closed": round(sum(pnls), 2),
        "win_rate": round(wins / len(pnls) * 100, 0) if pnls else 0,
        "open_positions": len(p.positions),
        "cash": round(p.cash, 2),
    }


def pop_completed_day():
    """Return the date of a just-completed UTC day exactly once, else None.

    The first call ever only arms the marker; afterwards each day rollover
    returns the previous day's date a single time.
    """
    today = _utc_today()
    last = get_meta("last_daily_summary")
    if last == today:
        return None
    set_meta("last_daily_summary", today)
    return last  # None on the very first run, else the completed day

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
    # Net per position within the day (scaled exits write partial + runner rows)
    rows = fetchall("""
        SELECT SUM(pnl) AS pnl FROM trades WHERE date(closed_at) = ?
        GROUP BY COALESCE(position_id, id)
    """, [date_str])
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
    # Net expectancy per trade — the "are we profitable-shaped yet?" number.
    # A signal with no edge nets ≈ -(round-trip cost) per trade; positive is
    # the bar. Computed for the day and all-time so the trend is visible.
    day_exp = (sum(pnls) / len(pnls)) if pnls else 0.0
    all_rows = fetchall("SELECT SUM(pnl) AS pnl FROM trades "
                        "GROUP BY COALESCE(position_id, id)")
    all_pnls = [r["pnl"] for r in all_rows if r["pnl"] is not None]
    all_exp = (sum(all_pnls) / len(all_pnls)) if all_pnls else 0.0
    all_n = len(all_pnls)
    return {
        "date": date_str,
        "equity": round(p.equity, 2),
        "total_pnl_pct": round(p.total_pnl_pct, 2),
        "day_pnl_pct": round(day_pnl_pct, 2),
        "trades_closed": len(pnls),
        "pnl_closed": round(sum(pnls), 2),
        "win_rate": round(wins / len(pnls) * 100, 0) if pnls else 0,
        "net_expectancy_usd": round(day_exp, 2),
        "net_expectancy_all_usd": round(all_exp, 2),
        "net_expectancy_all_n": all_n,
        "open_positions": len(p.positions),
        "cash": round(p.cash, 2),
    }


def check_goals(notifier):
    """Firm goals (reporting targets, not trade gates): +DAILY_PROFIT_TARGET
    MIN..MAX % per day and +TOTAL_PROFIT_TARGET MIN..MAX % of total capital.
    Pings Telegram once per day / once per milestone when first reached."""
    from config import (DAILY_PROFIT_TARGET_MIN, DAILY_PROFIT_TARGET_MAX,
                        TOTAL_PROFIT_TARGET_MIN, TOTAL_PROFIT_TARGET_MAX)
    today = _utc_today()
    day_pnl = daily_loss_pct()
    if day_pnl >= DAILY_PROFIT_TARGET_MIN and get_meta("goal_day_hit") != today:
        set_meta("goal_day_hit", today)
        notifier.send(
            f"🎯 Daily goal reached: {day_pnl:+.2f}% "
            f"(target +{DAILY_PROFIT_TARGET_MIN:g}% to +{DAILY_PROFIT_TARGET_MAX:g}%)")
    if day_pnl >= DAILY_PROFIT_TARGET_MAX and get_meta("goal_day_max_hit") != today:
        set_meta("goal_day_max_hit", today)
        notifier.send(
            f"🏆 Daily stretch goal hit: {day_pnl:+.2f}% — a day worth protecting")
    p = load_portfolio()
    total_pct = p.total_pnl_pct
    if total_pct >= TOTAL_PROFIT_TARGET_MIN and get_meta("goal_total_min") != "1":
        set_meta("goal_total_min", "1")
        notifier.send(
            f"🎯 Firm goal reached: {total_pct:+.2f}% of total capital "
            f"(target +{TOTAL_PROFIT_TARGET_MIN:g}% to +{TOTAL_PROFIT_TARGET_MAX:g}%)")
    if total_pct >= TOTAL_PROFIT_TARGET_MAX and get_meta("goal_total_max") != "1":
        set_meta("goal_total_max", "1")
        notifier.send(
            f"🏆 Firm stretch goal hit: {total_pct:+.2f}% — maximum target reached")


def pop_completed_day():
    """Return the date of a just-completed UTC day exactly once, else None.

    The first call ever only arms the marker; afterwards each day rollover
    returns the previous day's date a single time.

    NOTE: advances the marker immediately — if the caller then fails to
    generate the report, that day is lost. Prefer peek_completed_day() +
    mark_day_reported() for crash-safe generation.
    """
    today = _utc_today()
    last = get_meta("last_daily_summary")
    if last == today:
        return None
    set_meta("last_daily_summary", today)
    return last  # None on the very first run, else the completed day


def peek_completed_day():
    """Crash-safe variant: return the completed day WITHOUT advancing the
    marker. The caller generates the report and only then calls
    mark_day_reported() — a failed generation retries next cycle instead of
    silently losing the day. First call ever arms the marker and returns None."""
    today = _utc_today()
    last = get_meta("last_daily_summary")
    if last is None:
        set_meta("last_daily_summary", today)   # arm on first run
        return None
    if last == today:
        return None
    return last


def mark_day_reported():
    """Advance the day marker after a SUCCESSFUL report generation."""
    set_meta("last_daily_summary", _utc_today())


def stamp_cycle_heartbeat():
    """Record that a maintenance cycle completed — the liveness signal the
    watchdog and /api/health check from OUTSIDE the cycle thread."""
    set_meta("last_cycle_at", str(datetime.now(timezone.utc).timestamp()))


def cycle_age_seconds():
    """Seconds since the last completed maintenance cycle (None if never)."""
    raw = get_meta("last_cycle_at")
    if not raw:
        return None
    try:
        return max(0.0, datetime.now(timezone.utc).timestamp() - float(raw))
    except (TypeError, ValueError):
        return None

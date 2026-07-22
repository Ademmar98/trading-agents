"""Cycle-liveness guards + crash-safe daily-report marker (incident 2026-07-22)."""
import time

import pytest

from core.database import init_db, execute, set_meta, get_meta
from core.equity import (
    peek_completed_day, mark_day_reported, stamp_cycle_heartbeat,
    cycle_age_seconds, build_daily_summary, _utc_today,
)


@pytest.fixture(autouse=True)
def _clean():
    init_db()
    execute("DELETE FROM meta")
    execute("DELETE FROM trades")
    execute("DELETE FROM equity_history")
    yield
    execute("DELETE FROM meta")
    execute("DELETE FROM trades")
    execute("DELETE FROM equity_history")


# ── crash-safe day marker ──
def test_peek_arms_marker_first_call_then_none_same_day():
    assert peek_completed_day() is None          # first call arms
    assert get_meta("last_daily_summary") == _utc_today()
    assert peek_completed_day() is None          # same day -> nothing


def test_peek_does_not_advance_marker_until_marked():
    set_meta("last_daily_summary", "2026-07-20")  # yesterday
    day = peek_completed_day()
    assert day == "2026-07-20"
    # marker NOT advanced — a failed report must retry
    assert peek_completed_day() == "2026-07-20"
    mark_day_reported()                           # success
    assert peek_completed_day() is None           # now consumed


# ── cycle heartbeat / age ──
def test_cycle_age_none_before_first_heartbeat():
    assert cycle_age_seconds() is None


def test_cycle_age_small_after_heartbeat():
    stamp_cycle_heartbeat()
    age = cycle_age_seconds()
    assert age is not None and age < 5


def test_cycle_age_detects_stall():
    set_meta("last_cycle_at", str(time.time() - 3600))   # 1h ago
    age = cycle_age_seconds()
    assert 3500 < age < 3700


# ── net expectancy metric ──
def test_build_daily_summary_reports_net_expectancy():
    today = _utc_today()
    for pnl in (10.0, -4.0, -4.0):                # mean = +0.667
        execute("INSERT INTO trades (symbol, side, qty, entry_price, exit_price, "
                "pnl, pnl_pct, reason, closed_at) VALUES ('BTC/USD','BUY',1,100,101,"
                f"{pnl},1,'x','{today} 12:00:00')")
    s = build_daily_summary(today)
    assert s["trades_closed"] == 3
    assert s["net_expectancy_usd"] == pytest.approx(0.67, abs=0.01)
    assert s["net_expectancy_all_usd"] == pytest.approx(0.67, abs=0.01)
    assert s["net_expectancy_all_n"] == 3

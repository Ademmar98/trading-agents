"""Per-symbol concentration guards — re-entry cooldown + daily-loss cap.
Closes the sequential-concentration hole the 2026-07-23 meta-analysis exposed
(one alt traded 13x = 100%+ of the net loss)."""
from datetime import datetime, timezone, timedelta

import pytest

from core.database import init_db, execute
from core.risk import recent_stopout_cooldown, symbol_daily_loss


@pytest.fixture(autouse=True)
def _clean():
    init_db()
    execute("DELETE FROM trades")
    yield
    execute("DELETE FROM trades")


def _add(symbol, pnl, reason, closed_at):
    execute("INSERT INTO trades (symbol, side, qty, entry_price, exit_price, pnl, "
            "pnl_pct, reason, closed_at) VALUES (?, 'BUY', 1, 100, 100, ?, 0, ?, ?)",
            [symbol, pnl, reason, closed_at])


def _ago(mins):
    return (datetime.now(timezone.utc) - timedelta(minutes=mins)).strftime("%Y-%m-%d %H:%M:%S")


def _today(h=12, m=0):
    d = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return f"{d} {h:02d}:{m:02d}:00"


# ── re-entry cooldown ──
def test_cooldown_blocks_recent_stopout():
    _add("UNI/USD", -10, "stop_loss", _ago(5))
    assert recent_stopout_cooldown("UNI/USD", 45) is True


def test_cooldown_expires_after_window():
    _add("UNI/USD", -10, "stop_loss", _ago(60))
    assert recent_stopout_cooldown("UNI/USD", 45) is False


def test_cooldown_ignores_winners():
    _add("UNI/USD", +10, "take_profit", _ago(3))
    assert recent_stopout_cooldown("UNI/USD", 45) is False


def test_cooldown_only_on_stop_reason():
    _add("UNI/USD", -10, "manual_exit", _ago(3))
    assert recent_stopout_cooldown("UNI/USD", 45) is False


def test_cooldown_disabled_when_zero():
    _add("UNI/USD", -10, "stop_loss", _ago(1))
    assert recent_stopout_cooldown("UNI/USD", 0) is False


def test_cooldown_uses_latest_trade_only():
    _add("UNI/USD", -10, "stop_loss", _ago(30))   # older stop-out
    _add("UNI/USD", +5, "take_profit", _ago(2))    # newer win resets the clock
    assert recent_stopout_cooldown("UNI/USD", 45) is False


def test_cooldown_isolates_symbol():
    _add("UNI/USD", -10, "stop_loss", _ago(2))
    assert recent_stopout_cooldown("BTC/USD", 45) is False


# ── per-symbol daily loss ──
def test_daily_loss_sums_net_negative():
    _add("UNI/USD", -30, "stop_loss", _today(9))
    _add("UNI/USD", -20, "stop_loss", _today(11))
    _add("UNI/USD", +5, "take_profit", _today(13))
    assert symbol_daily_loss("UNI/USD") == pytest.approx(45.0)   # net -45 -> 45


def test_daily_loss_zero_when_profitable():
    _add("BTC/USD", +40, "take_profit", _today(10))
    _add("BTC/USD", -10, "stop_loss", _today(12))
    assert symbol_daily_loss("BTC/USD") == 0.0                   # net +30 -> 0


def test_daily_loss_isolates_symbol():
    _add("UNI/USD", -50, "stop_loss", _today(10))
    _add("BTC/USD", -5, "stop_loss", _today(10))
    assert symbol_daily_loss("UNI/USD") == pytest.approx(50.0)

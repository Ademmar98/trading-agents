"""Limit-fill microstructure diagnostics + circuit breakers."""
import time

import pytest

import core.fill_monitor as fm
from core.database import init_db, execute, fetchone
from core import pending_orders


@pytest.fixture(autouse=True)
def _clean():
    init_db()
    execute("DELETE FROM pending_orders")
    fm._SPREAD_EWMA.clear()
    fm._THROTTLE_UNTIL = 0.0
    yield
    execute("DELETE FROM pending_orders")
    fm._SPREAD_EWMA.clear()
    fm._THROTTLE_UNTIL = 0.0


# ── spread kill-switch ──
def test_spread_kill_switch_trips_on_blowout():
    # seed the rolling average with a tight spread, then a 5x-wider spread
    for _ in range(40):
        fm.spread_ok("BTC/USD", 100.0, 100.02)      # ~0.02% spread
    assert fm.spread_ok("BTC/USD", 100.0, 100.03)   # still near avg -> ok
    assert not fm.spread_ok("BTC/USD", 100.0, 100.30)  # ~0.3% >> 3x avg -> kill


def test_spread_ok_default_true_without_history():
    assert fm.spread_ok("NEW/USD", 10.0, 10.05) is True


# ── adverse-selection throttle ──
def test_offset_mult_widens_when_throttle_active():
    assert fm.offset_mult(1.0, now=1000) == 1.0
    fm._THROTTLE_UNTIL = 2000
    assert fm.offset_mult(1.0, now=1000) == pytest.approx(1.5)
    assert fm.offset_mult(1.0, now=3000) == 1.0        # window elapsed


def test_throttle_trips_after_three_knife_fills():
    for i in range(3):
        execute("INSERT INTO pending_orders (symbol, side, limit_price, quantity, "
                "status, fill_price, adverse_1m) VALUES (?, 'BUY', 100, 1, 'filled', 100, ?)",
                [f"S{i}/USD", -0.01])                   # -1% each = knives
    fm._maybe_trip_throttle(now=5000)
    assert fm.adverse_throttle_active(now=5000)
    assert not fm.adverse_throttle_active(now=5000 + fm.THROTTLE_WINDOW_S + 1)


def test_throttle_does_not_trip_on_good_fills():
    for i in range(3):
        execute("INSERT INTO pending_orders (symbol, side, limit_price, quantity, "
                "status, fill_price, adverse_1m) VALUES (?, 'BUY', 100, 1, 'filled', 100, 0.008)",
                [f"G{i}/USD"])                          # +0.8% = good
    fm._maybe_trip_throttle(now=5000)
    assert not fm.adverse_throttle_active(now=5000)


# ── adverse-selection scoring ──
def test_score_adverse_selection_records_1m_and_5m():
    execute("INSERT INTO pending_orders (symbol, side, limit_price, quantity, status, "
            "fill_price, filled_at, scored) VALUES ('ETH/USD','BUY',100,1,'filled',100,?,0)",
            [__import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()])
    oid = fetchone("SELECT id FROM pending_orders")["id"]
    now = time.time()
    # after 90s, price up 2% -> good entry
    fm.score_adverse_selection({"ETH/USD": {"price": 102.0}}, now=now + 90)
    assert fetchone("SELECT adverse_1m, scored FROM pending_orders")["adverse_1m"] == pytest.approx(0.02)
    # after 320s, price down to -1% -> knife tail
    fm.score_adverse_selection({"ETH/USD": {"price": 99.0}}, now=now + 320)
    row = fetchone("SELECT adverse_5m, scored FROM pending_orders")
    assert row["adverse_5m"] == pytest.approx(-0.01) and row["scored"] == 2


# ── cancel/replace on drift ──
def test_cancel_drifted_cancels_runaway_quote():
    pending_orders.place_limit("SOL/USD", 100.0, 1.0, ref_price=101.0, atr=2.0)
    # price now 104 = 4 above limit > 1.5x ATR(2)=3 -> cancel
    n = fm.cancel_drifted({"SOL/USD": {"price": 104.0}}, drift_mult=1.5)
    assert n == 1
    assert fetchone("SELECT status FROM pending_orders")["status"] == "cancelled"


def test_cancel_drifted_keeps_nearby_quote():
    pending_orders.place_limit("SOL/USD", 100.0, 1.0, ref_price=101.0, atr=2.0)
    assert fm.cancel_drifted({"SOL/USD": {"price": 102.0}}, drift_mult=1.5) == 0  # 2 < 3
    assert fetchone("SELECT status FROM pending_orders")["status"] == "pending"


# ── spread saved + diagnostics ──
def test_spread_saved_fee_plus_price_improvement():
    po = {"ref_price": 100.0, "fill_price": 99.0, "quantity": 2.0}
    # fee saved = 2*99*(taker-maker)/100 ; price improve = 2*(100-99)=2.0
    saved = fm.spread_saved_usd(po)
    assert saved > 2.0        # price improvement dominates + fee saving


def test_diagnostics_aggregates():
    now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
    execute("INSERT INTO pending_orders (symbol, side, limit_price, quantity, status, "
            "ref_price, fill_price, created_at, filled_at, adverse_1m) "
            "VALUES ('BTC/USD','BUY',100,1,'filled',101,100,?,?,0.01)",
            [now.isoformat(), now.isoformat()])
    execute("INSERT INTO pending_orders (symbol, side, limit_price, quantity, status, created_at) "
            "VALUES ('BTC/USD','BUY',100,1,'expired',?)", [now.isoformat()])
    d = fm.diagnostics(days=7)
    btc = d["per_symbol"][0]
    assert btc["total_quotes"] == 2 and btc["filled"] == 1
    assert btc["fill_rate_pct"] == 50.0
    assert btc["adverse_1m_pct"] == pytest.approx(1.0)
    assert d["totals"]["net_spread_saved_usd"] > 1.0

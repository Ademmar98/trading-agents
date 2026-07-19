"""Swing desk, money-based streak breaker, and firm goals."""
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import config as app_config
from core.database import init_db, execute
from core.memory import SharedMemory
from core.portfolio import Portfolio, save_portfolio
from core.swing import swing_signal


@pytest.fixture(autouse=True)
def sandbox(monkeypatch):
    tmp = Path(tempfile.mkdtemp(prefix="trading-test-"))
    monkeypatch.setattr(app_config, "DATA_DIR", tmp)
    monkeypatch.setenv("TRADING_DATA_DIR", str(tmp))
    init_db()
    yield
    import shutil
    shutil.rmtree(str(tmp), ignore_errors=True)


def _bars(closes, spread=1.0):
    return [{"open": c, "high": c + spread, "low": c - spread, "close": c,
             "volume": 100.0} for c in closes]


def _uptrend_daily(n=80, start=100.0, step=0.4, last_jump=0.0, spread=1.0):
    closes = [start + i * step for i in range(n)]
    closes[-1] += last_jump
    return _bars(closes, spread)


def _uptrend_4h(n=80, start=100.0, step=0.2):
    return _bars([start + i * step for i in range(n)], spread=0.5)


class TestSwingSignal:
    def test_breakout_detected_with_bounded_geometry(self):
        d1 = _uptrend_daily(last_jump=8.0)   # last close clears prior 20d highs
        sig = swing_signal("BTC/USD", d1, _uptrend_4h())
        assert sig is not None
        assert sig["action"] == "BUY"
        assert sig["strategy"] == "swing_breakout"
        assert 1.0 <= sig["sl_pct"] <= 25.0
        assert 3.0 <= sig["tp_pct"] <= 100.0
        assert sig["tp_pct"] == pytest.approx(
            min(max(sig["sl_pct"] * 3.0, 3.0), 100.0), abs=0.05)
        assert sig["stop_loss"] < sig["entry_price"] < sig["take_profit"]

    def test_downtrend_gives_no_signal(self):
        d1 = _bars([200 - i * 0.5 for i in range(80)])
        d1[-1]["close"] += 10  # breakout-shaped bar, but the trend is down
        assert swing_signal("BTC/USD", d1, _uptrend_4h()) is None

    def test_4h_misalignment_blocks(self):
        d1 = _uptrend_daily(last_jump=8.0)
        h4 = _bars([200 - i * 0.5 for i in range(80)])  # 4h below its EMA50
        assert swing_signal("BTC/USD", d1, h4) is None

    def test_huge_atr_clamped_to_25_and_75(self):
        # Wild daily ranges -> raw ATR% x 2 far above 25 -> clamped
        d1 = _uptrend_daily(last_jump=60.0, spread=25.0)
        sig = swing_signal("BTC/USD", d1, _uptrend_4h())
        assert sig is not None
        assert sig["sl_pct"] == 25.0
        assert sig["tp_pct"] == 75.0

    def test_tiny_atr_floored_to_1_and_3(self):
        d1 = _uptrend_daily(last_jump=2.0, spread=0.05)  # dust ranges
        sig = swing_signal("BTC/USD", d1, _uptrend_4h())
        assert sig is not None
        assert sig["sl_pct"] == 1.0
        assert sig["tp_pct"] == 3.0

    def test_insufficient_history_gives_none(self):
        assert swing_signal("BTC/USD", _uptrend_daily(n=30), _uptrend_4h()) is None


def _seed_exec(memory, strategies, entry=100.0, sl=78.0, tp=166.0):
    memory.write("analyses", "market_scan", {
        "all_analyses": {"BTC/USD": {"price": entry, "bid": entry - 0.01,
                                     "ask": entry + 0.01, "volatility": 1.0}},
        "timestamp": time.time(),
    })
    opp = {
        "symbol": "BTC/USD", "action": "BUY", "confidence": 0.7,
        "price": entry, "entry_price": entry, "stop_loss": sl,
        "take_profit": tp,
        "sl_pct": abs(entry - sl) / entry * 100,
        "tp_pct": abs(tp - entry) / entry * 100,
        "calculated_risk_pct": 0.5,
        "max_qty": 5.0, "reasons": [], "strategies": strategies,
        "indicators": {"volatility": 1.0},
    }
    memory.write("decisions", "pricing", {"pricing_map": {}, "timestamp": time.time()})
    memory.write("decisions", "compliance_gate", {
        "halted": False, "approved_opportunities": [opp], "timestamp": time.time(),
    })


class TestSwingExecution:
    def test_swing_wide_stop_passes_guard(self):
        from agents.execution_agent import ExecutionAgent
        save_portfolio(Portfolio(cash=10000.0, initial_balance=10000.0))
        _seed_exec(SharedMemory(), ["swing_breakout"])  # 22% SL, 66% TP
        plan = ExecutionAgent().run()
        assert len(plan["orders"]) == 1
        order = plan["orders"][0]
        assert order["stop_loss"] == pytest.approx(78.0)
        # risk-coupled sizing: 22% stop -> tiny quantity, dollar risk flat
        assert order["qty"] * (100.0 - 78.0) <= 10000 * 0.5 / 100 + 1e-6

    def test_scalp_with_swing_stop_still_rejected(self):
        from agents.execution_agent import ExecutionAgent
        save_portfolio(Portfolio(cash=10000.0, initial_balance=10000.0))
        _seed_exec(SharedMemory(), ["test"])  # same 22% SL, non-swing tag
        plan = ExecutionAgent().run()
        assert plan["orders"] == []
        assert "sanity bound" in " ".join(plan["rejected"][0]["execution_reasons"])


def _insert_trade(pos_id, pnl, closed_at):
    execute(
        "INSERT INTO trades (position_id, symbol, side, qty, entry_price, "
        "exit_price, pnl, pnl_pct, reason, closed_at) "
        "VALUES (?, 'BTC/USD', 'BUY', 1, 100, 99, ?, ?, 'SL', ?)",
        [pos_id, pnl, pnl, closed_at])


class TestStreakBreaker:
    def test_streak_past_1_2_pct_halts(self):
        from agents.compliance_agent import ComplianceAgent
        save_portfolio(Portfolio(cash=10000.0, initial_balance=10000.0))
        _insert_trade(1, -70.0, "2026-07-13 10:00:00")
        _insert_trade(2, -60.0, "2026-07-13 11:00:00")  # streak -130 = -1.3%
        memory = SharedMemory()
        memory.write("decisions", "portfolio_plan", {
            "approved_opportunities": [], "timestamp": time.time()})
        report = ComplianceAgent().run()
        assert report["halted"] is True
        assert any("streak breaker" in b for b in report["blockers"])

    def test_winner_resets_streak(self):
        from agents.compliance_agent import ComplianceAgent
        save_portfolio(Portfolio(cash=10000.0, initial_balance=10000.0))
        _insert_trade(1, -70.0, "2026-07-13 10:00:00")
        _insert_trade(2, 5.0, "2026-07-13 11:00:00")    # winner breaks the run
        _insert_trade(3, -60.0, "2026-07-13 12:00:00")  # streak only -60 = -0.6%
        memory = SharedMemory()
        memory.write("decisions", "portfolio_plan", {
            "approved_opportunities": [], "timestamp": time.time()})
        report = ComplianceAgent().run()
        assert report["halted"] is False

    def test_small_losses_do_not_halt(self):
        from agents.compliance_agent import ComplianceAgent
        save_portfolio(Portfolio(cash=10000.0, initial_balance=10000.0))
        for i, ts in enumerate(["10:00:00", "11:00:00", "12:00:00", "13:00:00"]):
            _insert_trade(i + 1, -5.0, f"2026-07-13 {ts}")  # 4 losses, -0.2% total
        memory = SharedMemory()
        memory.write("decisions", "portfolio_plan", {
            "approved_opportunities": [], "timestamp": time.time()})
        report = ComplianceAgent().run()
        assert report["halted"] is False


class TestGoals:
    def test_daily_goal_notifies_once(self):
        from core.equity import snapshot_equity, check_goals
        save_portfolio(Portfolio(cash=10000.0, initial_balance=10000.0))
        snapshot_equity()  # day-start anchor at 10,000
        save_portfolio(Portfolio(cash=10080.0, initial_balance=10000.0))  # +0.8%
        notifier = MagicMock()
        check_goals(notifier)
        assert any("Daily goal reached" in c.args[0]
                   for c in notifier.send.call_args_list)
        notifier.reset_mock()
        check_goals(notifier)  # same day -> no duplicate ping
        assert not any("Daily goal reached" in c.args[0]
                       for c in notifier.send.call_args_list)

    def test_total_goal_notifies_once(self):
        from core.equity import snapshot_equity, check_goals
        save_portfolio(Portfolio(cash=10000.0, initial_balance=10000.0))
        snapshot_equity()
        save_portfolio(Portfolio(cash=11200.0, initial_balance=10000.0))  # +12%
        notifier = MagicMock()
        check_goals(notifier)
        assert any("Firm goal reached" in c.args[0]
                   for c in notifier.send.call_args_list)
        notifier.reset_mock()
        check_goals(notifier)
        assert not any("Firm goal reached" in c.args[0]
                       for c in notifier.send.call_args_list)

    def test_no_ping_below_target(self):
        from core.equity import snapshot_equity, check_goals
        save_portfolio(Portfolio(cash=10000.0, initial_balance=10000.0))
        snapshot_equity()
        save_portfolio(Portfolio(cash=10020.0, initial_balance=10000.0))  # +0.2%
        notifier = MagicMock()
        check_goals(notifier)
        notifier.send.assert_not_called()

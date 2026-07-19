"""Tests for the 15-minute scalping stack: EMA trend filter, MACD crossover,
RSI guard, ATR-based SL/TP via the win-rate/R:R matrix, the pre-routing win
probability gate, and position sizing via the position-sizer skill formula.
"""
import tempfile
import time
from pathlib import Path

import pytest

import config as app_config
import core.scalp15 as scalp15
from core.database import init_db, execute
from core.memory import SharedMemory
from core.portfolio import Portfolio, save_portfolio


@pytest.fixture(autouse=True)
def sandbox_data_dir(monkeypatch):
    tmp = Path(tempfile.mkdtemp(prefix="trading-test-"))
    monkeypatch.setattr(app_config, "DATA_DIR", tmp)
    monkeypatch.setenv("TRADING_DATA_DIR", str(tmp))
    init_db()
    yield
    import shutil
    shutil.rmtree(str(tmp), ignore_errors=True)


def _bars(close=105.0, n=60):
    return [{"close": close, "high": close + 1, "low": close - 1} for _ in range(n)]


def _rig(monkeypatch, ema=100.0, hist=(-0.5, 0.4), rsi_v=55.0, atr_v=2.0):
    monkeypatch.setattr(scalp15, "ema_all", lambda v, p: [ema])
    monkeypatch.setattr(scalp15, "_macd_series", lambda c: list(hist))
    monkeypatch.setattr(scalp15, "rsi", lambda c, period=14: rsi_v)
    monkeypatch.setattr(scalp15, "atr", lambda h, l, c, period=14: atr_v)


class TestRRMatrix:
    def test_win_rate_matrix(self):
        assert scalp15.rr_for_win_prob(0.90) == 1.0   # high wp -> quick target
        assert scalp15.rr_for_win_prob(0.75) == 1.2
        assert scalp15.rr_for_win_prob(0.65) == 1.5
        assert scalp15.rr_for_win_prob(0.40) == 2.0   # weak wp must reach further


class TestWinProbability:
    def test_prior_without_history(self):
        assert scalp15.estimate_win_probability(False) == pytest.approx(0.5)
        assert scalp15.estimate_win_probability(True) == pytest.approx(0.6)

    def test_laplace_smoothed_history(self):
        execute("INSERT INTO strategy_stats (strategy, trades, win_rate, pnl) "
                "VALUES ('scalp_15m', 20, 80.0, 150.0)")
        # (16 wins + 1) / (20 + 2)
        assert scalp15.estimate_win_probability(False) == pytest.approx(17 / 22, abs=0.001)


class TestSignal:
    def test_long_when_all_three_align(self, monkeypatch):
        _rig(monkeypatch)  # price 105 > EMA 100, MACD crosses up, RSI 55
        sig = scalp15.scalp_15m_signal("BTC/USD", regime="trending_up", ohlc=_bars())
        assert sig["action"] == "BUY"
        # SL = 1.5 x ATR(2.0) = 3 below entry; wp 0.6 -> RR 1.5 -> TP 4.5 above
        assert sig["stop_loss"] == pytest.approx(102.0)
        assert sig["take_profit"] == pytest.approx(109.5)
        assert sig["win_prob"] == pytest.approx(0.6)
        assert sig["rr"] == 1.5

    def test_rsi_overbought_blocks_long(self, monkeypatch):
        _rig(monkeypatch, rsi_v=75.0)
        assert scalp15.scalp_15m_signal("BTC/USD", ohlc=_bars()) is None

    def test_trend_filter_blocks_counter_trend_long(self, monkeypatch):
        _rig(monkeypatch, ema=110.0)  # price 105 below EMA -> no longs
        assert scalp15.scalp_15m_signal("BTC/USD", ohlc=_bars()) is None

    def test_short_side_mirrors(self, monkeypatch):
        monkeypatch.setattr(scalp15, "BUY_ONLY", False)  # exercise the SELL mirror
        _rig(monkeypatch, ema=110.0, hist=(0.5, -0.4), rsi_v=45.0)
        sig = scalp15.scalp_15m_signal("BTC/USD", regime="ranging", ohlc=_bars())
        assert sig["action"] == "SELL"
        assert sig["stop_loss"] == pytest.approx(108.0)   # entry + 1.5xATR
        # wp 0.5 (no history, regime not aligned) -> RR 2.0 -> TP 6 below
        assert sig["take_profit"] == pytest.approx(99.0)

    def test_stale_macd_state_is_not_a_cross(self, monkeypatch):
        _rig(monkeypatch, hist=(0.4, 0.5))  # already positive: no fresh cross
        assert scalp15.scalp_15m_signal("BTC/USD", ohlc=_bars()) is None


class TestPositionSizerSkillFormula:
    def test_atr_based_size(self):
        # (10000 equity x 1% risk) / (2.0 ATR x 1.5) = 100 / 3
        assert scalp15.atr_position_size(10000, 2.0, 1.5, 1.0) == pytest.approx(100 / 3)

    def test_degenerate_inputs(self):
        assert scalp15.atr_position_size(10000, 0.0, 1.5, 1.0) == 0.0
        assert scalp15.atr_position_size(0, 2.0, 1.5, 1.0) == 0.0


def _seed_scalp_execution(memory, win_prob):
    memory.write("analyses", "market_scan", {
        "all_analyses": {"BTC/USD": {"price": 105.0, "bid": 104.99, "ask": 105.01,
                                     "volatility": 2.0}},
        "timestamp": time.time(),
    })
    opp = {
        "symbol": "BTC/USD", "action": "BUY", "confidence": min(win_prob, 0.95),
        "price": 105.0, "entry_price": 105.0, "stop_loss": 102.0,
        "take_profit": 109.5, "sl_pct": 2.86, "tp_pct": 4.29,
        "calculated_risk_pct": 1.0, "atr": 2.0, "win_prob": win_prob,
        "max_qty": 50.0, "reasons": [], "strategies": ["scalp_15m"],
        "indicators": {"volatility": 2.0},
    }
    memory.write("decisions", "pricing", {"pricing_map": {"BTC/USD": opp},
                                          "timestamp": time.time()})
    memory.write("decisions", "compliance_gate", {
        "halted": False, "approved_opportunities": [opp], "timestamp": time.time(),
    })


class TestExecutionGate:
    def test_gate_aborts_below_min_win_prob(self, monkeypatch):
        import agents.execution_agent as ea
        from agents.execution_agent import ExecutionAgent
        monkeypatch.setattr(ea, "SCALP_MIN_WIN_PROB", 0.92)
        memory = SharedMemory()
        save_portfolio(Portfolio(cash=10000.0, initial_balance=10000.0))
        _seed_scalp_execution(memory, win_prob=0.60)

        plan = ExecutionAgent().run()
        assert plan["orders"] == []
        assert any("win probability" in r.lower()
                   for r in plan["rejected"][0]["execution_reasons"])

    def test_passing_setup_sized_by_skill_formula(self, monkeypatch):
        import agents.execution_agent as ea
        monkeypatch.setattr(ea, "SCALP_MIN_WIN_PROB", 0.5)
        monkeypatch.setattr(ea, "session_risk_mult", lambda: 1.0)  # pin session
        monkeypatch.setattr(ea, "POSITION_SIZE_MULT", 1.0)  # isolate the skill formula
        monkeypatch.setattr(ea, "MAX_POSITION_SIZE_PCT", 100)  # cap tested elsewhere
        memory = SharedMemory()
        save_portfolio(Portfolio(cash=10000.0, initial_balance=10000.0))
        _seed_scalp_execution(memory, win_prob=0.60)

        plan = ea.ExecutionAgent().run()
        assert len(plan["orders"]) == 1
        # qty capped at (10000 x 1%) / (2.0 ATR x 1.5) = 33.33..., not the 50 max_qty
        assert plan["orders"][0]["qty"] == pytest.approx(100 / 3, abs=0.01)
        assert plan["orders"][0]["stop_loss"] == pytest.approx(102.0)
        assert plan["orders"][0]["take_profit"] == pytest.approx(109.5)

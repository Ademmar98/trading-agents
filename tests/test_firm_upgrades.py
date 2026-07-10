"""Tests for the firm-upgrade batch: backtester scaled-exit alignment,
correlation de-risking, the strategy scorecard, and data-source fallbacks.
"""
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

import pytest

import config as app_config
from core.correlation import pearson, daily_returns
from core.database import init_db
from core.memory import SharedMemory
from core.portfolio import Portfolio, Position, save_portfolio


@pytest.fixture(autouse=True)
def sandbox_data_dir(monkeypatch):
    tmp = Path(tempfile.mkdtemp(prefix="trading-test-"))
    monkeypatch.setattr(app_config, "DATA_DIR", tmp)
    monkeypatch.setenv("TRADING_DATA_DIR", str(tmp))
    init_db()
    yield
    import shutil
    shutil.rmtree(str(tmp), ignore_errors=True)


class TestCorrelation:
    def test_perfectly_correlated(self):
        a = [0.01, -0.02, 0.03, 0.01, -0.01, 0.02, -0.03, 0.01, 0.02, -0.01, 0.015]
        assert pearson(a, a) == pytest.approx(1.0)

    def test_anti_correlated(self):
        a = [0.01, -0.02, 0.03, 0.01, -0.01, 0.02, -0.03, 0.01, 0.02, -0.01, 0.015]
        b = [-x for x in a]
        assert pearson(a, b) == pytest.approx(-1.0)

    def test_insufficient_or_flat(self):
        assert pearson([0.01] * 5, [0.02] * 5) is None       # too few
        assert pearson([0.0] * 20, [0.01, -0.01] * 10) is None  # flat series

    def test_daily_returns(self):
        assert daily_returns([100, 110, 99]) == pytest.approx([0.1, -0.1])


class TestCorrelationDeRisk:
    def test_correlated_candidate_size_halved(self):
        from agents.risk_manager import RiskManager

        memory = SharedMemory()
        p = Portfolio(cash=10000.0, initial_balance=10000.0)
        p.positions["ETH/USD"] = Position(symbol="ETH/USD", entry_price=1800.0,
                                          quantity=1.0, current_price=1800.0)
        save_portfolio(p)

        rets = [0.01, -0.02, 0.03, 0.01, -0.01, 0.02, -0.03, 0.01, 0.02, -0.01, 0.015]
        memory.write("analyses", "market_scan", {
            "opportunities": [{
                "symbol": "SOL/USD", "action": "BUY", "confidence": 0.8,
                "price": 150.0, "reasons": [], "strategies": ["test"],
            }],
            "all_analyses": {
                "SOL/USD": {"price": 150.0, "returns_30d": rets},
                "ETH/USD": {"price": 1800.0, "returns_30d": rets},  # corr = 1.0
            },
            "timestamp": time.time(),
        })

        report = RiskManager().run()
        opp = report["approved_opportunities"][0]
        assert opp["risk_ok"] is True  # de-risked, not blocked
        uncorrelated_qty = min(10000 * 0.15, 10000 * 0.15) / 150.0
        assert opp["max_qty"] == pytest.approx(uncorrelated_qty * 0.5, rel=0.01)
        assert any("correlated" in r for r in report["risks"])

    def test_uncorrelated_candidate_untouched(self):
        from agents.risk_manager import RiskManager

        memory = SharedMemory()
        p = Portfolio(cash=10000.0, initial_balance=10000.0)
        p.positions["ETH/USD"] = Position(symbol="ETH/USD", entry_price=1800.0,
                                          quantity=1.0, current_price=1800.0)
        save_portfolio(p)

        a = [0.01, -0.02, 0.03, 0.01, -0.01, 0.02, -0.03, 0.01, 0.02, -0.01, 0.015]
        b = [0.02, 0.01, -0.01, -0.02, 0.03, -0.01, 0.01, -0.02, 0.01, 0.02, -0.015]
        memory.write("analyses", "market_scan", {
            "opportunities": [{
                "symbol": "SOL/USD", "action": "BUY", "confidence": 0.8,
                "price": 150.0, "reasons": [], "strategies": ["test"],
            }],
            "all_analyses": {
                "SOL/USD": {"price": 150.0, "returns_30d": a},
                "ETH/USD": {"price": 1800.0, "returns_30d": b},
            },
            "timestamp": time.time(),
        })

        report = RiskManager().run()
        opp = report["approved_opportunities"][0]
        assert not any("correlated" in r for r in report["risks"])
        assert opp["max_qty"] == pytest.approx(min(10000 * 0.15, 10000 * 0.15) / 150.0, rel=0.01)


class TestBacktesterScaledExits:
    def test_partial_then_runner_merged_as_one_trade(self):
        """A backtest position that scales out must produce a PARTIAL row and
        a runner row merged into one logical trade in the metrics."""
        from core.backtester import _merge_by_position, _compute_metrics

        trades = [
            {"symbol": "T", "side": "BUY", "qty": 0.5, "entry": 100.0, "exit": 107.5,
             "pnl": 3.5, "pnl_pct": 7.0, "reason": "PARTIAL", "bar": 10,
             "date": "2026-07-01", "pos_id": 1},
            {"symbol": "T", "side": "BUY", "qty": 0.5, "entry": 100.0, "exit": 100.3,
             "pnl": 0.05, "pnl_pct": 0.1, "reason": "SL", "bar": 15,
             "date": "2026-07-01", "pos_id": 1},
            {"symbol": "T", "side": "BUY", "qty": 1.0, "entry": 100.0, "exit": 95.0,
             "pnl": -5.2, "pnl_pct": -5.2, "reason": "SL", "bar": 20,
             "date": "2026-07-02", "pos_id": 2},
        ]
        merged = _merge_by_position(trades)
        assert len(merged) == 2
        assert merged[0]["pnl"] == pytest.approx(3.55)
        assert merged[0]["qty"] == pytest.approx(1.0)

        metrics = _compute_metrics("T", trades, [10000, 10003.55, 9998.35], 10000)
        assert metrics["total_trades"] == 2
        assert metrics["win_rate"] == 50.0

    def test_backtest_takes_partial_at_1_5R(self):
        """Synthetic tape: entry, rally through 1.5R, collapse to breakeven —
        must produce a PARTIAL exit and a runner stop, net positive."""
        from core import backtester

        base = [{"date": f"2026-01-01T{i:02d}", "open": 100.0, "high": 100.5,
                 "low": 99.5, "close": 100.0} for i in range(24)]
        tape = base * 9  # 216 warmup bars, flat
        tape = tape[:210]
        # entry bar, rally past partial target, then collapse
        tape += [
            {"date": "2026-02-01T00", "open": 100.0, "high": 100.5, "low": 99.5, "close": 100.0},  # entry at close 100
            {"date": "2026-02-01T01", "open": 100.0, "high": 112.0, "low": 99.9, "close": 111.0},  # rally: hits partial
            {"date": "2026-02-01T02", "open": 111.0, "high": 111.0, "low": 95.0, "close": 96.0},   # collapse: runner stops
        ]
        signals = [{"action": "BUY", "confidence": 0.9, "strategies": ["synthetic"]}]

        with patch.object(backtester, "fetch_klines", return_value=tape), \
             patch.object(backtester, "scan_symbol",
                          side_effect=lambda data, **kw: signals if len(data) == 211 else []), \
             patch.object(backtester, "get_unprofitable_strategies", return_value=[]):
            result = backtester.backtest_symbol("SYN/USD", bars=len(tape) - 200)

        assert result is not None
        # The PARTIAL row is folded into its runner by the per-position merge:
        # one logical trade whose pnl proves the scale-out happened. Without
        # the partial, the runner alone exits at breakeven+buffer for ~$0.
        assert result["total_trades"] == 1
        trade = result["trades"][-1]
        assert trade["qty"] == pytest.approx(15.0)   # both halves accounted
        assert trade["reason"] == "SL"               # runner's final exit
        assert trade["pnl"] > 20                     # banked 1.5R on half the size


class TestScorecard:
    def test_scorecard_verdicts(self):
        from core.analytics import _compute_strategy_stats

        trades = ([{"strategy": "good", "pnl": 5.0}] * 12 +
                  [{"strategy": "bad", "pnl": -2.0}] * 11 +
                  [{"strategy": "young", "pnl": 1.0}] * 3)
        rows = {r["strategy"]: r for r in _compute_strategy_stats(trades)}
        assert rows["good"]["expectancy"] == pytest.approx(5.0)
        assert rows["bad"]["expectancy"] == pytest.approx(-2.0)
        assert rows["good"]["avg_win"] == pytest.approx(5.0)
        assert rows["bad"]["avg_loss"] == pytest.approx(2.0)


class TestDataFallbacks:
    def test_twelvedata_symbol_mapping(self):
        from core.data_provider import _twelvedata_symbol
        assert _twelvedata_symbol("XAUUSD") == "XAU/USD"
        assert _twelvedata_symbol("XAGUSD") == "XAG/USD"
        assert _twelvedata_symbol("EURUSD") == "EUR/USD"
        assert _twelvedata_symbol("AAPL") == "AAPL"

    def test_fetchers_disabled_without_keys(self, monkeypatch):
        import core.data_provider as dp
        monkeypatch.setattr(dp, "TWELVEDATA_API_KEY", "")
        monkeypatch.setattr(dp, "MASSIVE_API_KEY", "")
        assert dp.fetch_twelvedata_ohlc("XAUUSD", "15m", 10) == []
        assert dp.fetch_massive_ohlc("AAPL", "15m", 10) == []


class TestRoundSig:
    def test_normal_prices_unchanged(self):
        from core.pricing import round_sig
        assert round_sig(102.0) == 102.0
        assert round_sig(62693.123456) == 62693.1

    def test_micro_prices_keep_geometry(self):
        """round(x, 5) turned a $0.00003 grid into 33% steps; six significant
        figures keep SL/TP distinct and proportional."""
        from core.pricing import round_sig, compute_pricing
        assert round_sig(0.0000312345678) == 0.0000312346
        p = compute_pricing("1000SATS/USD", "BUY", 0.0000312,
                            {"volatility": 2.0, "bid": 0.0000312, "ask": 0.0000312},
                            None, 0)
        assert 0 < p["stop_loss"] < p["entry_price"] < p["take_profit"]
        # SL distance must be near the intended ~3.6%, not a 33% grid step
        assert 1.0 < p["sl_pct"] < 10.0

    def test_zero_passthrough(self):
        from core.pricing import round_sig
        assert round_sig(0) == 0

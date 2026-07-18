"""Tests for the firm-upgrade batch: backtester scaled-exit alignment,
correlation gating, the strategy scorecard, and data-source fallbacks.
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
    def test_correlated_candidate_blocked(self):
        """A candidate >= MAX_PAIR_CORRELATION with an open position is
        BLOCKED (max_qty=0, risk_ok=False) — halving still let the same
        cluster through (post-mortem 2026-07-12)."""
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
        assert opp["risk_ok"] is False  # blocked, not merely de-risked
        assert opp["max_qty"] == 0
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

    def test_pipe_joined_contributors_scored_individually(self):
        """Combined-signal trades carry every contributor pipe-joined
        ("a|b"); each must build its own record (contribution, not allocation)."""
        from core.analytics import _compute_strategy_stats

        trades = ([{"strategy": "good|helper", "pnl": 5.0}] * 4 +
                  [{"strategy": "good", "pnl": 5.0}] * 8 +
                  [{"strategy": "helper", "pnl": -2.0}])
        rows = {r["strategy"]: r for r in _compute_strategy_stats(trades)}
        assert rows["good"]["trades"] == 12
        assert rows["helper"]["trades"] == 5
        assert rows["helper"]["pnl"] == pytest.approx(4 * 5.0 - 2.0)
        assert rows["helper"]["win_rate"] == pytest.approx(80.0)


class TestDataFallbacks:
    def test_cryptocom_timeframe_mapping(self):
        from core.data_provider import _CRYPTOCOM_TIMEFRAMES
        assert _CRYPTOCOM_TIMEFRAMES["15m"] == "M15"
        assert _CRYPTOCOM_TIMEFRAMES["1h"] == "H1"
        assert _CRYPTOCOM_TIMEFRAMES["1d"] == "D1"

    def test_non_crypto_symbol_gets_no_data(self):
        # Crypto-only firm: anything without a BASE/QUOTE pair returns nothing.
        from core.data_provider import fetch_ohlc, fetch_current_price, fetch_cryptocom_ohlc
        assert fetch_ohlc("AAPL", "15m", 10) == []
        assert fetch_current_price("XAUUSD") == 0
        assert fetch_cryptocom_ohlc("AAPL", "15m", 10) == []


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


class TestScalpGeometryCaps:
    def test_daily_range_volatility_capped(self):
        """23.6% daily-range volatility must not become a 23%+ stop — caps
        clamp to MAX_SL_PCT / MAX_TP_PCT (the original 29.5%-stop bug)."""
        from core.pricing import compute_pricing
        p = compute_pricing("BTC/USD", "BUY", 631.48,
                            {"volatility": 23.59, "bid": 631.4, "ask": 631.6},
                            "ranging", 0)
        assert p["sl_pct"] <= app_config.MAX_SL_PCT + 0.01
        assert p["tp_pct"] <= app_config.MAX_TP_PCT + 0.01

    def test_intraday_inputs_floored_at_fee_safe_stop(self):
        """Realistic 15m inputs (ATR ~0.3%) once produced sub-1% scalp stops
        whose whole 1R was eaten by the 0.3% round-trip cost — the MIN_SL_PCT
        fee floor now lifts them to 1.0% (geometry and R:R preserved)."""
        from core.pricing import compute_pricing
        p = compute_pricing("BTC/USD", "BUY", 60000.0,
                            {"volatility": 0.4, "bid": 59995.0, "ask": 60005.0},
                            "trending_up", 180.0)  # ATR = 0.3% -> raw stop 0.45%
        assert p["sl_pct"] == pytest.approx(app_config.MIN_SL_PCT, abs=0.05)
        assert p["tp_pct"] < 2.0
        assert p["stop_loss"] < p["entry_price"] < p["take_profit"]


class TestBootClamp:
    def test_wide_stops_clamped_at_init(self):
        """Positions with pre-fix daily-range stops self-heal on boot."""
        from core.database import execute, init_db, fetchone
        execute("""INSERT INTO positions (symbol, side, quantity, entry_price,
                   current_price, stop_loss, take_profit, peak_price, initial_risk)
                   VALUES ('OLD/USD', 'BUY', 1.0, 100.0, 100.0, 70.0, 160.0, 100.0, 30.0)""")
        init_db()  # migration runs the clamp
        row = fetchone("SELECT * FROM positions WHERE symbol='OLD/USD'")
        assert row["stop_loss"] == pytest.approx(100.0 * (1 - app_config.MAX_SL_PCT / 100))
        assert row["take_profit"] == pytest.approx(100.0 * (1 + app_config.MAX_TP_PCT / 100))
        assert row["initial_risk"] == pytest.approx(100.0 * app_config.MAX_SL_PCT / 100)

    def test_scalp_stops_untouched(self):
        from core.database import execute, init_db, fetchone
        execute("""INSERT INTO positions (symbol, side, quantity, entry_price,
                   current_price, stop_loss, take_profit, peak_price, initial_risk)
                   VALUES ('OK/USD', 'BUY', 1.0, 100.0, 100.0, 99.2, 101.6, 100.0, 0.8)""")
        init_db()
        row = fetchone("SELECT * FROM positions WHERE symbol='OK/USD'")
        assert row["stop_loss"] == 99.2
        assert row["take_profit"] == 101.6


class TestNoLeveragePolicy:
    """Halal requirement: gross notional may never exceed 1x equity —
    cash-only trading enforced at compliance, counting same-cycle approvals."""

    def _seed(self, memory, candidates):
        memory.write("decisions", "portfolio_plan", {
            "approved_opportunities": candidates, "timestamp": time.time(),
        })

    def _opp(self, symbol, price, qty):
        return {"symbol": symbol, "action": "BUY", "confidence": 0.9,
                "price": price, "max_qty": qty, "risk_ok": True,
                "reasons": [], "strategies": ["test"]}

    def test_blocks_entry_exceeding_equity(self):
        from agents.compliance_agent import ComplianceAgent
        from core.positions import PositionManager

        memory = SharedMemory()
        save_portfolio(Portfolio(cash=10000.0, initial_balance=10000.0))
        # $9,000 already deployed (no SL -> no heat interference)
        PositionManager().open_position("BTC/USD", "BUY", 0.15, 60000.0)
        # Candidate worth $2,000 would take gross notional to $11,000 > $10,000
        self._seed(memory, [self._opp("SOL/USD", 200.0, 10.0)])

        report = ComplianceAgent().run()
        assert report["approved_opportunities"] == []
        assert any("No-leverage policy" in r
                   for r in report["rejected_opportunities"][0]["compliance_reasons"])

    def test_allows_entry_within_equity(self):
        from agents.compliance_agent import ComplianceAgent
        from core.positions import PositionManager

        memory = SharedMemory()
        save_portfolio(Portfolio(cash=10000.0, initial_balance=10000.0))
        PositionManager().open_position("BTC/USD", "BUY", 0.15, 60000.0)  # $9,000
        self._seed(memory, [self._opp("SOL/USD", 200.0, 2.0)])  # $400 fits

        report = ComplianceAgent().run()
        assert len(report["approved_opportunities"]) == 1

    def test_same_cycle_approvals_consume_budget(self):
        from agents.compliance_agent import ComplianceAgent

        memory = SharedMemory()
        save_portfolio(Portfolio(cash=10000.0, initial_balance=10000.0))
        # Two $6,000 candidates: only the first fits under 1x equity
        self._seed(memory, [self._opp("BTC/USD", 60000.0, 0.1),
                            self._opp("ETH/USD", 3000.0, 2.0)])

        report = ComplianceAgent().run()
        assert len(report["approved_opportunities"]) == 1
        assert len(report["rejected_opportunities"]) == 1
        assert any("No-leverage policy" in r
                   for r in report["rejected_opportunities"][0]["compliance_reasons"])


class TestPeakDrawdownHalt:
    """The drawdown halt is anchored at PEAK equity (high-water mark from the
    equity snapshots), not the initial balance: a book that ran up then bled
    back past -10% from the peak must halt even while above its starting
    capital."""

    def _plan(self, memory):
        memory.write("decisions", "portfolio_plan", {
            "approved_opportunities": [], "timestamp": time.time(),
        })

    def test_halts_past_10pct_from_peak(self):
        from agents.compliance_agent import ComplianceAgent
        from core.database import execute

        memory = SharedMemory()
        self._plan(memory)
        # Equity $10,500 (above the $10k start) vs a $12,000 high-water mark
        # snapshotted two days ago -> -12.5% from peak. The old from-INITIAL
        # check saw +5% and waved it through.
        save_portfolio(Portfolio(cash=10500.0, initial_balance=10000.0))
        execute("INSERT INTO equity_history (equity, cash, positions_value, "
                "exposure_pct, snapped_at) VALUES (12000, 12000, 0, 0, "
                "datetime('now', '-2 days'))")

        report = ComplianceAgent().run()
        assert report["halted"] is True
        assert any("peak" in b for b in report["blockers"])

    def test_no_halt_within_10pct_of_peak(self):
        from agents.compliance_agent import ComplianceAgent

        memory = SharedMemory()
        self._plan(memory)
        save_portfolio(Portfolio(cash=10000.0, initial_balance=10000.0))

        report = ComplianceAgent().run()
        assert report["halted"] is False
        assert report["blockers"] == []


class TestScoutMode:
    """While the SMA200 dial says risk_off, scout mode (regime_scan.scout_mode
    + a small nonzero deployment_target) keeps approvals alive but clamps
    per-trade risk to SCOUT_RISK_PER_TRADE_PCT so the test cycle collects
    forward stats at trivial cost."""

    def _seed(self, memory, risk_pct=0.5, deploy=0.10, scout=True):
        memory.write("decisions", "portfolio_plan", {
            "approved_opportunities": [{
                "symbol": "BTC/USD", "action": "BUY", "confidence": 0.9,
                "price": 50000.0, "max_qty": 0.01, "risk_ok": True,
                "calculated_risk_pct": risk_pct,
                "stop_loss": 49000.0, "take_profit": 52000.0,
                "sizing_notes": [],
            }],
            "timestamp": time.time(),
        })
        memory.write("analyses", "regime_scan", {
            "firm_regime": "risk_off", "deployment_target": deploy,
            "scout_mode": scout, "symbols": {}, "timestamp": time.time()})
        memory.write("analyses", "market_scan", {
            "all_analyses": {}, "bellwether_moves": {}, "timestamp": time.time()})
        memory.write("reports", "health", {
            "halted": False, "issues": [], "timestamp": time.time()})
        memory.write("decisions", "risk_assessment", {
            "verdict": "low", "risks": [], "timestamp": time.time()})
        save_portfolio(Portfolio(cash=10000.0, initial_balance=10000.0))

    def test_scout_clamps_risk_and_approves(self):
        from agents.compliance_agent import ComplianceAgent
        from config import SCOUT_RISK_PER_TRADE_PCT
        memory = SharedMemory()
        self._seed(memory, risk_pct=0.5)

        report = ComplianceAgent().run()
        assert len(report["approved_opportunities"]) == 1
        opp = report["approved_opportunities"][0]
        assert opp["scout"] is True
        # 0.5% risk clamped to 0.1% -> qty x 0.2
        assert opp["max_qty"] == pytest.approx(0.01 * SCOUT_RISK_PER_TRADE_PCT / 0.5)
        assert any("Scout" in n for n in opp["sizing_notes"])

    def test_no_scout_means_full_cash_rejection(self):
        """Same candidate, scout off and 0% deployment -> regime cap rejects."""
        from agents.compliance_agent import ComplianceAgent
        memory = SharedMemory()
        self._seed(memory, deploy=0.0, scout=False)

        report = ComplianceAgent().run()
        assert report["approved_opportunities"] == []
        assert any("deployment cap" in r
                   for r in report["rejected_opportunities"][0]["compliance_reasons"])

    def test_regime_agent_sets_scout_floor(self, monkeypatch):
        """risk_off + SCOUT_MODE_ENABLED -> deployment_target = SCOUT_MAX_DEPLOY_PCT/100
        and scout_mode=True in the regime_scan report."""
        import agents.regime_agent as ra

        # risk_off tape: 260 daily bars, last close below SMA200
        closes = [100.0] * 259 + [90.0]
        bars = [{"open": c, "high": c * 1.01, "low": c * 0.99, "close": c,
                 "volume": 1000.0, "ts": 1} for c in closes]  # ts=1 -> all closed
        monkeypatch.setattr(ra.MarketData, "get_ohlc",
                            lambda self, s, days=100, interval=None: bars)
        monkeypatch.setattr(ra, "SCOUT_MODE_ENABLED", True)
        monkeypatch.setattr(ra, "SCOUT_MAX_DEPLOY_PCT", 10.0)

        memory = SharedMemory()
        report = ra.RegimeAgent().run()
        assert report["firm_regime"] == "risk_off"
        assert report["scout_mode"] is True
        assert report["deployment_target"] == pytest.approx(0.10)

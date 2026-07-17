"""Tests for the BUY-only conversion, multi-timeframe scalp stack,
microstructure/VWAP, BUY-limit orders, the news agent, and the open-trade
steward.
"""
import tempfile
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

import config as app_config
from core.database import init_db, execute, fetchone
from core.memory import SharedMemory
from core.portfolio import Portfolio, save_portfolio


@pytest.fixture(autouse=True)
def sandbox(monkeypatch):
    tmp = Path(tempfile.mkdtemp(prefix="trading-test-"))
    monkeypatch.setattr(app_config, "DATA_DIR", tmp)
    monkeypatch.setenv("TRADING_DATA_DIR", str(tmp))
    init_db()
    yield
    import shutil
    shutil.rmtree(str(tmp), ignore_errors=True)


def _rig(monkeypatch, ema=100.0, hist=(0.5, -0.4), rsi_v=45.0, atr_v=2.0):
    """Default rig triggers a SELL (down-cross below EMA)."""
    import core.scalp15 as s
    monkeypatch.setattr(s, "ema_all", lambda v, p: [ema])
    monkeypatch.setattr(s, "_macd_series", lambda c: list(hist))
    monkeypatch.setattr(s, "rsi", lambda c, period=14: rsi_v)
    monkeypatch.setattr(s, "atr", lambda h, l, c, period=14: atr_v)


def _bars(n=60, close=95.0):
    return [{"close": close, "high": close + 1, "low": close - 1} for _ in range(n)]


class TestBuyOnly:
    def test_scalp_blocks_sell_when_buy_only(self, monkeypatch):
        import core.scalp15 as s
        monkeypatch.setattr(s, "BUY_ONLY", True)
        _rig(monkeypatch)  # would be a SELL setup
        assert s.scalp_signal("BTC/USD", ohlc=_bars(), timeframe="15m") is None

    def test_scalp_allows_sell_when_disabled(self, monkeypatch):
        import core.scalp15 as s
        monkeypatch.setattr(s, "BUY_ONLY", False)
        _rig(monkeypatch)
        sig = s.scalp_signal("BTC/USD", ohlc=_bars(), timeframe="15m")
        assert sig["action"] == "SELL"


class TestMultiTimeframe:
    def test_timeframe_tag_in_signal(self, monkeypatch):
        import core.scalp15 as s
        monkeypatch.setattr(s, "BUY_ONLY", False)
        # up-cross above EMA -> BUY
        _rig(monkeypatch, ema=90.0, hist=(-0.5, 0.4), rsi_v=55.0)
        for tf in ("1m", "5m", "1h"):
            sig = s.scalp_signal("BTC/USD", ohlc=_bars(close=95.0), timeframe=tf)
            assert sig["timeframe"] == tf
            assert sig["strategy"] == f"scalp_{tf}"
            assert any(f"scalp_{tf}" in r for r in sig["reasons"])

    def test_win_prob_reads_per_tf_strategy(self, monkeypatch):
        import core.scalp15 as s
        execute("INSERT INTO strategy_stats (strategy, trades, win_rate, pnl) "
                "VALUES ('scalp_1m', 18, 80.0, 90.0)")
        # 14.4 wins +1 / 20 -> ~0.77
        assert s.estimate_win_probability(False, "scalp_1m") == pytest.approx(15.4 / 20, abs=0.01)
        assert s.estimate_win_probability(False, "scalp_4h") == pytest.approx(0.5)


class TestVWAPandMicrostructure:
    def test_vwap_volume_weighted(self):
        from core.microstructure import vwap
        bars = [{"high": 11, "low": 9, "close": 10, "volume": 100},
                {"high": 21, "low": 19, "close": 20, "volume": 300}]
        # (10*100 + 20*300) / 400 = 17.5
        assert vwap(bars) == pytest.approx(17.5, rel=0.001)

    def test_vwap_no_volume_falls_back_to_average(self):
        from core.microstructure import vwap
        bars = [{"high": 11, "low": 9, "close": 10},
                {"high": 21, "low": 19, "close": 20}]
        assert vwap(bars) == pytest.approx(15.0, rel=0.001)

    def test_signals_fail_open(self, monkeypatch):
        import core.microstructure as m
        monkeypatch.setattr(m.requests, "get", MagicMock(side_effect=OSError))
        assert m.book_imbalance("BTC/USD") is None
        assert m.funding_rate("BTC/USD") is None


class TestBuyLimitOrders:
    def test_limit_fills_when_price_touches(self):
        from core import pending_orders
        pid = pending_orders.place_limit("BTC/USD", 60000.0, 0.1, sl=58000, tp=63000)
        assert pid > 0
        # price above the limit: no fill
        assert pending_orders.check_fills({"BTC/USD": {"price": 60500}}, lambda s: False) == []
        # price touches the limit: fill
        fills = pending_orders.check_fills({"BTC/USD": {"price": 59900}}, lambda s: False)
        assert len(fills) == 1 and fills[0]["symbol"] == "BTC/USD"
        assert fetchone("SELECT status FROM pending_orders WHERE id=?", [pid])["status"] == "filled"

    def test_limit_cancelled_if_position_exists(self):
        from core import pending_orders
        pid = pending_orders.place_limit("ETH/USD", 3000.0, 1.0)
        pending_orders.check_fills({"ETH/USD": {"price": 2900}}, lambda s: True)
        assert fetchone("SELECT status FROM pending_orders WHERE id=?", [pid])["status"] == "cancelled"

    def test_expired_limit_not_filled(self):
        from core import pending_orders
        pid = pending_orders.place_limit("SOL/USD", 150.0, 1.0, ttl_min=-1)  # already expired
        fills = pending_orders.check_fills({"SOL/USD": {"price": 140}}, lambda s: False)
        assert fills == []
        assert fetchone("SELECT status FROM pending_orders WHERE id=?", [pid])["status"] == "expired"


class TestNewsAgent:
    def test_sentiment_scoring(self):
        from agents.news_agent import _score_text
        assert _score_text("Bitcoin surges to record high on ETF approval") > 0
        assert _score_text("Solana crashes after exploit and lawsuit") < 0
        assert _score_text("The market opened today") == 0

    def test_news_scan_tags_symbols(self, monkeypatch):
        import agents.news_agent as na
        monkeypatch.setattr(na, "NEWS_AGENT_ENABLED", True)
        monkeypatch.setattr(na, "_fetch_feed",
                            lambda url: ["Bitcoin surges to record high"] if "coindesk" in url else [])
        agent = na.NewsAgent()
        agent.memory = SharedMemory()
        report = agent.run()
        assert report is not None
        assert report["symbols"]["BTC/USD"]["score"] > 0

    def test_throttled(self, monkeypatch):
        import agents.news_agent as na
        from core.database import set_meta
        monkeypatch.setattr(na, "NEWS_AGENT_ENABLED", True)
        set_meta("news_last_run", str(time.time()))
        agent = na.NewsAgent(); agent.memory = SharedMemory()
        assert agent.run() is None


class TestOpenTradeSteward:
    def test_adjust_levels_only_tightens_stop(self):
        from core.positions import PositionManager
        pm = PositionManager()
        pid = pm.open_position("BTC/USD", "BUY", 1.0, 100.0, sl=95.0, tp=110.0)
        # widening the stop is rejected
        assert pm.adjust_levels(pid, new_sl=90.0) is False
        # tightening is accepted
        assert pm.adjust_levels(pid, new_sl=98.0) is True
        assert fetchone("SELECT stop_loss FROM positions WHERE id=?", [pid])["stop_loss"] == 98.0

    def test_adjust_levels_extends_tp(self):
        from core.positions import PositionManager
        pm = PositionManager()
        pid = pm.open_position("BTC/USD", "BUY", 1.0, 100.0, sl=95.0, tp=110.0)
        assert pm.adjust_levels(pid, new_tp=115.0) is True
        assert fetchone("SELECT take_profit FROM positions WHERE id=?", [pid])["take_profit"] == 115.0

    def test_steward_tightens_on_bearish(self, monkeypatch):
        from agents.analyst import ResearchAnalyst
        from core.positions import PositionManager
        save_portfolio(Portfolio(cash=10000.0, initial_balance=10000.0))
        pm = PositionManager()
        pid = pm.open_position("BTC/USD", "BUY", 1.0, 100.0, sl=95.0, tp=110.0)
        with patch("agents.analyst.MarketData"):
            agent = ResearchAnalyst()
        agent.memory = SharedMemory()
        analyses = {"BTC/USD": {"price": 106.0, "atr": 2.0, "trend": "bearish", "rsi_14": 80}}
        agent._steward_open_trades(analyses)
        # bearish trend + RSI>75 = 2 votes, in profit -> SL tightened toward price
        new_sl = fetchone("SELECT stop_loss FROM positions WHERE id=?", [pid])["stop_loss"]
        assert new_sl > 95.0


class TestCorrelatedSelloffDefenses:
    """Post-mortem 2026-07-12: six alt longs stopped together on one BTC dip."""

    def test_group_counting(self):
        from core.risk import count_group_positions, symbol_group
        assert symbol_group("AAVE/USD") == "crypto_alts"
        assert symbol_group("BTC/USD") == "crypto_majors"
        held = ["AAVE/USD", "UNI/USD", "BTC/USD", "FOO/USD"]
        assert count_group_positions("ADA/USD", held) == 2   # two alts held
        assert count_group_positions("ETH/USD", held) == 1   # one major held
        assert count_group_positions("FOO/USD", held) == 0   # ungrouped symbol

    def test_session_multiplier(self):
        from datetime import datetime, timezone
        from core.risk import session_risk_mult
        asian = datetime(2026, 7, 12, 3, 0, tzinfo=timezone.utc)
        euro = datetime(2026, 7, 12, 10, 0, tzinfo=timezone.utc)
        us = datetime(2026, 7, 12, 16, 0, tzinfo=timezone.utc)
        late = datetime(2026, 7, 12, 23, 0, tzinfo=timezone.utc)
        assert session_risk_mult(asian) == 0.5
        assert session_risk_mult(euro) == 0.8
        assert session_risk_mult(us) == 1.0
        assert session_risk_mult(late) == 0.5

    def test_macro_dip_alert(self):
        from core.risk import macro_dip_alert
        assert macro_dip_alert("crypto", {"crypto": -1.4}) is True
        assert macro_dip_alert("crypto", {"crypto": -0.6}) is False
        assert macro_dip_alert("crypto", {}) is False
        assert macro_dip_alert("unknown", {"crypto": -2.0}) is False

    def test_vol_aware_stop_atr_first(self, monkeypatch):
        import core.risk as risk
        monkeypatch.setattr(risk, "MAX_SL_PCT", 5.0)
        monkeypatch.setattr(risk, "MIN_SL_PCT", 0.3)
        from core.risk import vol_aware_stop_loss
        # normal: ATR decides (0.8% ATR x 1.5 = 1.2%), NOT the cap
        assert vol_aware_stop_loss(0.8, 1.5) == pytest.approx(1.2)
        # absurd volatility: sanity ceiling
        assert vol_aware_stop_loss(9.0, 1.5) == 5.0
        # micro noise: floor
        assert vol_aware_stop_loss(0.05, 1.5) == pytest.approx(0.3)
        assert vol_aware_stop_loss(0, 1.5) is None

    def test_compliance_group_guard_blocks_third_alt(self, monkeypatch):
        import agents.compliance_agent as ca
        from core.positions import PositionManager
        save_portfolio(Portfolio(cash=10000.0, initial_balance=10000.0))
        pm = PositionManager()
        pm.open_position("AAVE/USD", "BUY", 0.1, 100.0)
        pm.open_position("UNI/USD", "BUY", 1.0, 10.0)
        memory = SharedMemory()
        memory.write("analyses", "market_scan", {"bellwether_moves": {}, "timestamp": time.time()})
        memory.write("decisions", "portfolio_plan", {
            "approved_opportunities": [{
                "symbol": "ADA/USD", "action": "BUY", "confidence": 0.7,
                "price": 0.5, "max_qty": 100.0, "risk_ok": True,
                "reasons": [], "strategies": ["test"],
            }],
            "timestamp": time.time(),
        })
        report = ca.ComplianceAgent().run()
        assert report["approved_opportunities"] == []
        assert any("Correlation group" in r
                   for r in report["rejected_opportunities"][0]["compliance_reasons"])

    def test_macro_dip_interlock_blocks_cluster(self):
        import agents.compliance_agent as ca
        save_portfolio(Portfolio(cash=10000.0, initial_balance=10000.0))
        memory = SharedMemory()
        memory.write("analyses", "market_scan", {
            "bellwether_moves": {"crypto": -1.5}, "timestamp": time.time()})
        memory.write("decisions", "portfolio_plan", {
            "approved_opportunities": [{
                "symbol": "SOL/USD", "action": "BUY", "confidence": 0.7,
                "price": 150.0, "max_qty": 1.0, "risk_ok": True,
                "reasons": [], "strategies": ["test"],
            }],
            "timestamp": time.time(),
        })
        report = ca.ComplianceAgent().run()
        assert report["approved_opportunities"] == []
        assert any("Macro dip interlock" in r
                   for r in report["rejected_opportunities"][0]["compliance_reasons"])

    def test_pricing_uses_atr_not_cap(self, monkeypatch):
        import core.risk as risk
        from config import MIN_SL_PCT
        monkeypatch.setattr(risk, "MAX_SL_PCT", 5.0)
        from core.pricing import compute_pricing
        # ATR 0.5% x trending sl_mult 1.5 = 0.75% stop — nowhere near the 5%
        # cap, but below the MIN_SL_PCT noise floor, so the floor binds.
        p = compute_pricing("BTC/USD", "BUY", 60000.0,
                            {"volatility": 4.0, "bid": 59995.0, "ask": 60005.0},
                            "trending_up", 300.0)
        assert p["sl_pct"] == pytest.approx(max(0.75, MIN_SL_PCT), abs=0.05)
        assert p["sl_pct"] < 2.0  # the old formula would have clamped at the cap

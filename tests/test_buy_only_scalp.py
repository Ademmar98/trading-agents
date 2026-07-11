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

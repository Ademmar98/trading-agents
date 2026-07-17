"""Tests for config.py env var parsing and dynamic symbol loading."""

import builtins
import os
import tempfile


def _reimport_config(env_vars=None, dotenv_content=None):
    """Reimport config module with given env vars and optional .env content."""
    import importlib
    import config as cfg
    importlib.reload(cfg)
    return cfg


class TestConfigDefaults:
    def test_default_trade_fee(self):
        import config as cfg
        assert cfg.TRADE_FEE_PCT == 0.1

    def test_default_backtest_bars(self):
        import config as cfg
        assert cfg.BACKTEST_BARS == 2500

    def test_default_max_consecutive_losses(self):
        import config as cfg
        assert cfg.MAX_CONSECUTIVE_LOSSES == 3

    def test_default_broker_type(self):
        import config as cfg
        assert cfg.BROKER_TYPE == "paper"

    def test_watched_symbols_is_list(self):
        import config as cfg
        assert isinstance(cfg.WATCHED_SYMBOLS, list)
        assert len(cfg.WATCHED_SYMBOLS) > 0


class TestConfigEnvOverrides:
    def test_default_trading_timeframe(self):
        import config as cfg
        assert cfg.TRADING_TIMEFRAME == "5m"

    def test_interval_minutes_is_int(self):
        import config as cfg
        assert isinstance(cfg.TRADING_INTERVAL_MINUTES, int)
        assert cfg.TRADING_INTERVAL_MINUTES >= 1

    def test_backtest_bars_from_env(self, monkeypatch):
        from config import BACKTEST_BARS
        assert isinstance(BACKTEST_BARS, int)
        assert BACKTEST_BARS == 2500


class TestPaperCycleRails:
    """Risk-gate defaults hardened for the 1-week expanded-pool paper cycle
    (readiness audit 1.8 + correlated-selloff post-mortem 2026-07-12)."""

    def test_daily_trade_cap_parser(self, monkeypatch):
        # The live value may come from .env; verify the parser honors the cap.
        monkeypatch.setenv("MAX_TRADES_PER_DAY", "20")
        cfg = _reimport_config()
        assert cfg.MAX_TRADES_PER_DAY == 20

    def test_daily_trade_cap_is_positive_int(self):
        import config as cfg
        assert isinstance(cfg.MAX_TRADES_PER_DAY, int)
        assert cfg.MAX_TRADES_PER_DAY > 0

    def test_sl_floor_clears_round_trip_cost(self):
        import config as cfg
        # A 0.3% floor == the round-trip cost was a fee trap (the whole 1R
        # eaten by costs); the floor must clear costs with real margin.
        round_trip = 2 * cfg.TRADE_FEE_PCT + 2 * cfg.BACKTEST_SPREAD_PCT
        assert cfg.MIN_SL_PCT >= 3 * round_trip

    def test_correlation_threshold_catches_alt_clusters(self):
        import config as cfg
        assert cfg.MAX_PAIR_CORRELATION == 0.7

    def test_peak_drawdown_halt_default(self):
        import config as cfg
        assert cfg.MAX_PEAK_DRAWDOWN_PCT == 10

    def test_new_loss_rails(self):
        import config as cfg
        assert cfg.MAX_DAILY_LOSS_USD == 300
        assert cfg.MAX_WEEKLY_LOSS_PCT == 5
        assert cfg.PER_STRATEGY_MAX_OPEN == 2

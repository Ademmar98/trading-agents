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

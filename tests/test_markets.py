"""Crypto-only firm: symbol classification, 24/7 market hours, the crypto
watchlist, and SL/TP trigger coverage from scan prices when a symbol is
absent from the websocket feed.

Stocks/metals/forex were removed 2026-07-14 — this file asserts the
crypto-only behavior that replaced them.
"""
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

import config as app_config
from core.market import classify_symbol, is_market_open
from core.database import init_db
from core.memory import SharedMemory
from core.portfolio import Portfolio, save_portfolio, load_portfolio


@pytest.fixture(autouse=True)
def sandbox_data_dir(monkeypatch):
    tmp = Path(tempfile.mkdtemp(prefix="trading-test-"))
    monkeypatch.setattr(app_config, "DATA_DIR", tmp)
    monkeypatch.setenv("TRADING_DATA_DIR", str(tmp))
    init_db()
    yield
    import shutil
    shutil.rmtree(str(tmp), ignore_errors=True)


def test_everything_classifies_as_crypto():
    assert classify_symbol("BTC/USD") == "crypto"
    assert classify_symbol("ETH/USD") == "crypto"
    # even a stray legacy ticker collapses to the single crypto cluster
    assert classify_symbol("AAPL") == "crypto"


def test_crypto_always_open():
    saturday = datetime(2026, 7, 11, 15, 0, tzinfo=timezone.utc)
    wed_night = datetime(2026, 7, 8, 3, 0, tzinfo=timezone.utc)
    assert is_market_open("BTC/USD", saturday)
    assert is_market_open("BTC/USD", wed_night)
    assert is_market_open("ETH/USD")  # no time arg -> now, still open


def test_watchlist_is_crypto_only():
    assert all("/" in s for s in app_config.WATCHED_SYMBOLS)
    assert "BTC/USD" in app_config.WATCHED_SYMBOLS
    assert "AAPL" not in app_config.WATCHED_SYMBOLS
    assert "XAUUSD" not in app_config.WATCHED_SYMBOLS
    assert app_config.MARKET_TYPE == "crypto"


def test_no_stock_metal_correlation_groups():
    assert set(app_config.CORRELATION_GROUPS) == {"crypto_alts", "crypto_majors"}
    assert list(app_config.MACRO_BELLWETHERS) == ["crypto"]


def test_trigger_check_uses_market_scan_when_ws_silent(monkeypatch):
    """A position whose symbol isn't on the websocket feed must still hit its
    SL from the market-scan prices merged in by process_price_triggers."""
    import main
    from core.broker import PaperBroker
    from core.notifier import Notifier

    main.memory = SharedMemory()
    monkeypatch.setattr(main, "notifier", Notifier("", ""))
    save_portfolio(Portfolio(cash=10000.0, initial_balance=10000.0))

    broker = PaperBroker()
    order = broker.place_order("TRX/USD", "BUY", 5.0, 200.0, sl=192.0, tp=220.0)
    assert order["status"] == "filled"
    main.pos_mgr.open_position("TRX/USD", "BUY", 5.0, 200.0, sl=192.0, tp=220.0)

    main.memory.write("analyses", "market_scan", {
        "all_analyses": {"TRX/USD": {"price": 190.0}},
        "timestamp": time.time(),
    })

    # Empty websocket prices: the merge from market_scan must still trigger the SL
    triggered = main.process_price_triggers({})
    assert len(triggered) == 1
    assert triggered[0]["symbol"] == "TRX/USD"
    assert triggered[0]["reason"] == "stop_loss"
    p = load_portfolio()
    assert "TRX/USD" not in p.positions
    fee = app_config.TRADE_FEE_PCT / 100.0
    expected = 10000.0 - 1000.0 * (1 + fee) + 950.0 * (1 - fee)
    assert p.cash == pytest.approx(expected)

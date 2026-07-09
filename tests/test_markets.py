"""Tests for multi-market support: stocks and metals alongside crypto.

Covers Yahoo symbol mapping (metals need the =X suffix), symbol
classification, market-hours gating, the compliance closed-market check, and
SL/TP trigger coverage for symbols absent from the crypto websocket feed.
"""
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

import config as app_config
from core.data_provider import _yahoo_symbol
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


def test_yahoo_symbol_mapping():
    assert _yahoo_symbol("BTC/USD") == "BTC-USD"
    assert _yahoo_symbol("XAUUSD") == "XAUUSD=X"
    assert _yahoo_symbol("XAGUSD") == "XAGUSD=X"
    assert _yahoo_symbol("AAPL") == "AAPL"
    assert _yahoo_symbol("GOOGL") == "GOOGL"


def test_classify_symbol():
    assert classify_symbol("BTC/USD") == "crypto"
    assert classify_symbol("XAUUSD") == "forex"
    assert classify_symbol("EURUSD") == "forex"
    assert classify_symbol("AAPL") == "stock"
    assert classify_symbol("GOOGL") == "stock"


# 2026-07-08 is a Wednesday, 2026-07-10 Friday, 2026-07-11 Saturday, 2026-07-12 Sunday
WED_NOON_ET = datetime(2026, 7, 8, 15, 0, tzinfo=timezone.utc)   # 11:00 EDT
WED_NIGHT = datetime(2026, 7, 8, 3, 0, tzinfo=timezone.utc)      # 23:00 EDT prev day
WED_AFTER_CLOSE = datetime(2026, 7, 8, 20, 30, tzinfo=timezone.utc)  # 16:30 EDT
SATURDAY = datetime(2026, 7, 11, 15, 0, tzinfo=timezone.utc)
SUNDAY_EARLY = datetime(2026, 7, 12, 21, 0, tzinfo=timezone.utc)
SUNDAY_LATE = datetime(2026, 7, 12, 23, 0, tzinfo=timezone.utc)
FRIDAY_LATE = datetime(2026, 7, 10, 22, 0, tzinfo=timezone.utc)


def test_crypto_always_open():
    assert is_market_open("BTC/USD", SATURDAY)
    assert is_market_open("BTC/USD", WED_NIGHT)


def test_stock_market_hours():
    assert is_market_open("AAPL", WED_NOON_ET)
    assert not is_market_open("AAPL", WED_NIGHT)
    assert not is_market_open("AAPL", WED_AFTER_CLOSE)
    assert not is_market_open("AAPL", SATURDAY)


def test_metals_market_hours():
    assert is_market_open("XAUUSD", WED_NIGHT)       # 24h on weekdays
    assert is_market_open("XAUUSD", WED_NOON_ET)
    assert not is_market_open("XAUUSD", SATURDAY)
    assert not is_market_open("XAUUSD", SUNDAY_EARLY)
    assert is_market_open("XAUUSD", SUNDAY_LATE)     # reopens Sun 22:00 UTC
    assert not is_market_open("XAUUSD", FRIDAY_LATE)  # closes Fri 21:00 UTC


def test_compliance_rejects_closed_market(monkeypatch):
    import agents.compliance_agent as ca

    monkeypatch.setattr(ca, "is_market_open", lambda s: False)
    memory = SharedMemory()
    save_portfolio(Portfolio(cash=10000.0, initial_balance=10000.0))
    memory.write("decisions", "portfolio_plan", {
        "approved_opportunities": [{
            "symbol": "AAPL", "action": "BUY", "confidence": 0.9,
            "price": 200.0, "max_qty": 5.0, "risk_ok": True,
            "reasons": [], "strategies": ["test"],
        }],
        "timestamp": time.time(),
    })

    report = ca.ComplianceAgent().run()
    assert report["approved_opportunities"] == []
    rejected = report["rejected_opportunities"][0]
    assert "Market closed for this symbol" in rejected["compliance_reasons"]


def test_trigger_check_uses_market_scan_for_non_crypto(monkeypatch):
    """A stock position must hit its SL from scan prices when the websocket
    feed (crypto-only) has no quote for it."""
    import main
    from core.broker import PaperBroker
    from core.notifier import Notifier

    main.memory = SharedMemory()
    monkeypatch.setattr(main, "notifier", Notifier("", ""))
    save_portfolio(Portfolio(cash=10000.0, initial_balance=10000.0))

    broker = PaperBroker()
    order = broker.place_order("AAPL", "BUY", 5.0, 200.0, sl=192.0, tp=220.0)
    assert order["status"] == "filled"
    main.pos_mgr.open_position("AAPL", "BUY", 5.0, 200.0, sl=192.0, tp=220.0)

    main.memory.write("analyses", "market_scan", {
        "all_analyses": {"AAPL": {"price": 190.0}},
        "timestamp": time.time(),
    })

    # Empty websocket prices: the merge from market_scan must still trigger the SL
    triggered = main.process_price_triggers({})
    assert len(triggered) == 1
    assert triggered[0]["symbol"] == "AAPL"
    assert triggered[0]["reason"] == "stop_loss"
    p = load_portfolio()
    assert "AAPL" not in p.positions
    fee = app_config.TRADE_FEE_PCT / 100.0
    expected = 10000.0 - 1000.0 * (1 + fee) + 950.0 * (1 - fee)
    assert p.cash == pytest.approx(expected)


def test_market_type_assembles_watchlist():
    """MARKET_TYPE=both merges crypto + stocks + metals; crypto stays default."""
    code = (
        "import config; "
        "assert 'AAPL' in config.WATCHED_SYMBOLS, 'stocks missing'; "
        "assert 'XAUUSD' in config.WATCHED_SYMBOLS, 'metals missing'; "
        "assert 'BTC/USD' in config.WATCHED_SYMBOLS, 'crypto missing'"
    )
    import os
    env = {**os.environ, "MARKET_TYPE": "both", "WATCHED_SYMBOLS": ""}
    r = subprocess.run([sys.executable, "-c", code], env=env,
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr

    code_crypto = (
        "import config; "
        "assert 'AAPL' not in config.WATCHED_SYMBOLS; "
        "assert 'BTC/USD' in config.WATCHED_SYMBOLS"
    )
    env = {**os.environ, "MARKET_TYPE": "crypto", "WATCHED_SYMBOLS": ""}
    r = subprocess.run([sys.executable, "-c", code_crypto], env=env,
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr

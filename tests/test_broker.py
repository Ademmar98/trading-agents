import os
import tempfile
from pathlib import Path

import pytest

import config as app_config
from core.broker import PaperBroker
from core.portfolio import Portfolio, save_portfolio, load_portfolio
from core.positions import PositionManager
from core.database import init_db


@pytest.fixture(autouse=True)
def sandbox_data_dir(monkeypatch):
    tmp = Path(tempfile.mkdtemp(prefix="trading-test-"))
    monkeypatch.setattr(app_config, "DATA_DIR", tmp)
    monkeypatch.setenv("TRADING_DATA_DIR", str(tmp))
    init_db()
    yield
    import shutil
    shutil.rmtree(str(tmp), ignore_errors=True)


def test_place_buy_order():
    save_portfolio(Portfolio(cash=10000.0, initial_balance=10000.0))
    broker = PaperBroker()
    order = broker.place_order("BTC/USD", "BUY", 0.1, 60000.0)
    assert order["status"] == "filled"
    assert order["side"] == "BUY"
    assert order["quantity"] == 0.1
    portfolio = load_portfolio()
    assert portfolio.cash < 10000.0
    assert "BTC/USD" in portfolio.positions


def test_place_sell_order_without_position():
    save_portfolio(Portfolio(cash=10000.0, initial_balance=10000.0))
    broker = PaperBroker()
    order = broker.place_order("ETH/USD", "SELL", 1.0, 1800.0)
    assert order["status"] == "filled"
    assert order["action"] == "SHORT"
    portfolio = load_portfolio()
    assert portfolio.positions["ETH/USD"].quantity < 0


def test_buy_insufficient_funds():
    save_portfolio(Portfolio(cash=100.0, initial_balance=10000.0))
    broker = PaperBroker()
    order = broker.place_order("BTC/USD", "BUY", 0.1, 60000.0)
    assert order["status"] == "rejected"


def test_close_long_position():
    save_portfolio(Portfolio(cash=10000.0, initial_balance=10000.0))
    broker = PaperBroker()
    broker.place_order("BTC/USD", "BUY", 0.1, 60000.0)
    portfolio = load_portfolio()
    assert "BTC/USD" in portfolio.positions
    broker.place_order("BTC/USD", "SELL", 0.1, 61000.0)
    portfolio = load_portfolio()
    assert "BTC/USD" not in portfolio.positions
    assert portfolio.cash > 10000.0


def test_close_short_position():
    save_portfolio(Portfolio(cash=10000.0, initial_balance=10000.0))
    broker = PaperBroker()
    broker.place_order("ETH/USD", "SELL", 1.0, 1800.0)
    portfolio = load_portfolio()
    assert portfolio.positions["ETH/USD"].quantity < 0
    broker.place_order("ETH/USD", "BUY", 1.0, 1750.0)
    portfolio = load_portfolio()
    assert "ETH/USD" not in portfolio.positions


def test_check_stop_loss_triggered():
    save_portfolio(Portfolio(cash=10000.0, initial_balance=10000.0))
    broker = PaperBroker()
    broker.place_order("BTC/USD", "BUY", 0.1, 60000.0, sl=57000.0, tp=66000.0)
    triggered = broker.check_stop_losses({"BTC/USD": {"price": 56000.0}})
    assert len(triggered) > 0
    assert triggered[0]["trigger"] == "stop_loss"


def test_check_stop_loss_not_triggered():
    save_portfolio(Portfolio(cash=10000.0, initial_balance=10000.0))
    broker = PaperBroker()
    broker.place_order("BTC/USD", "BUY", 0.1, 60000.0)
    triggered = broker.check_stop_losses({"BTC/USD": {"price": 59000.0}})
    assert len(triggered) == 0


def test_get_status():
    save_portfolio(Portfolio(cash=8000.0, initial_balance=10000.0))
    broker = PaperBroker()
    status = broker.get_status()
    assert status["cash"] == 8000.0
    assert status["equity"] == 8000.0

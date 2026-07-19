import os
import tempfile
from pathlib import Path

import pytest

import config as app_config
from core.broker import PaperBroker, SLIPPAGE_PCT
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


def test_get_status():
    save_portfolio(Portfolio(cash=8000.0, initial_balance=10000.0))
    broker = PaperBroker()
    status = broker.get_status()
    assert status["cash"] == 8000.0
    assert status["equity"] == 8000.0


def test_buy_fill_slips_adverse_to_quote():
    """Audit fix: a market BUY pays the quote (ask) PLUS adverse slippage —
    the old zero-slippage fill at the caller's price was a fantasy."""
    save_portfolio(Portfolio(cash=10000.0, initial_balance=10000.0))
    broker = PaperBroker()
    order = broker.place_order("BTC/USD", "BUY", 0.1, 60000.0)
    expected_fill = 60000.0 * (1 + SLIPPAGE_PCT / 100.0)
    assert order["price"] == pytest.approx(expected_fill)
    assert order["requested_price"] == 60000.0
    portfolio = load_portfolio()
    assert portfolio.positions["BTC/USD"].entry_price == pytest.approx(expected_fill)
    fee = app_config.TRADE_FEE_PCT / 100.0
    assert portfolio.cash == pytest.approx(10000.0 - 0.1 * expected_fill * (1 + fee))


def test_sell_fill_slips_adverse_to_quote():
    """A market SELL hits the quote (bid) MINUS adverse slippage."""
    save_portfolio(Portfolio(cash=10000.0, initial_balance=10000.0))
    broker = PaperBroker()
    order = broker.place_order("ETH/USD", "SELL", 1.0, 1800.0)
    expected_fill = 1800.0 * (1 - SLIPPAGE_PCT / 100.0)
    assert order["price"] == pytest.approx(expected_fill)
    portfolio = load_portfolio()
    pos = portfolio.positions["ETH/USD"]
    assert pos.entry_price == pytest.approx(expected_fill)
    assert pos.quantity < 0


def test_limit_fills_only_on_trade_through():
    """Audit fix: a resting BUY limit must NOT fill on a mere touch — the
    market has to trade THROUGH the limit (queue-ahead realism)."""
    from core import pending_orders
    from core.pending_orders import LIMIT_FILL_THROUGH_PCT
    pid = pending_orders.place_limit("BTC/USD", 60000.0, 0.1)
    # Touch (price == limit) and a hair below the limit: no fill.
    assert pending_orders.check_fills({"BTC/USD": {"price": 60000.0}}, lambda s: False) == []
    assert pending_orders.check_fills({"BTC/USD": {"price": 59990.0}}, lambda s: False) == []
    # Through the limit by more than LIMIT_FILL_THROUGH_PCT: filled at limit.
    through = 60000.0 * (1 - 2 * LIMIT_FILL_THROUGH_PCT / 100.0)
    fills = pending_orders.check_fills({"BTC/USD": {"price": through}}, lambda s: False)
    assert len(fills) == 1 and fills[0]["id"] == pid

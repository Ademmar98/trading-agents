"""ATR-offset limit-entry mode (LIMIT_ENTRY_ATR_MULT) in the trader."""
import pytest

import agents.trader as trader_mod
from agents.trader import Trader
from core import pending_orders
from core.database import init_db, execute
from core.memory import SharedMemory
from core.portfolio import Portfolio, save_portfolio
from core.positions import PositionManager


@pytest.fixture(autouse=True)
def _clean():
    init_db()
    for t in ("pending_orders", "positions", "trades"):
        execute(f"DELETE FROM {t}")
    save_portfolio(Portfolio(cash=100000.0, initial_balance=100000.0))
    yield
    for t in ("pending_orders", "positions", "trades"):
        execute(f"DELETE FROM {t}")


def _seed(memory, atr=1000.0, price=60000.0):
    memory.write("analyses", "market_scan", {
        "all_analyses": {"BTC/USD": {"price": price, "ask": price, "bid": price}}})
    memory.write("orders", "execution_plan", {"status": "ready", "orders": [{
        "symbol": "BTC/USD", "action": "BUY", "qty": 0.5, "price": price,
        "stop_loss": 57000.0, "take_profit": 66000.0, "strategies": ["test"],
        "atr": atr, "execution_ok": True, "plan_id": ""}]})


def test_atr_limit_rests_order_with_shifted_geometry(monkeypatch):
    monkeypatch.setattr(trader_mod, "LIMIT_ENTRY_ATR_MULT", 1.0)
    _seed(SharedMemory())
    Trader().run()
    pend = pending_orders.open_pending("BTC/USD")
    assert len(pend) == 1
    po = pend[0]
    assert po["limit_price"] == pytest.approx(59000.0)   # 60000 - 1.0 x 1000 ATR
    assert po["stop_loss"] == pytest.approx(56000.0)     # shifted down by 1000
    assert po["take_profit"] == pytest.approx(65000.0)   # shifted down by 1000
    assert not PositionManager().has_position("BTC/USD")  # no market fill


def test_wider_mult_rests_deeper(monkeypatch):
    monkeypatch.setattr(trader_mod, "LIMIT_ENTRY_ATR_MULT", 2.0)
    _seed(SharedMemory())
    Trader().run()
    po = pending_orders.open_pending("BTC/USD")[0]
    assert po["limit_price"] == pytest.approx(58000.0)   # 60000 - 2.0 x 1000


def test_off_by_default_places_market(monkeypatch):
    monkeypatch.setattr(trader_mod, "LIMIT_ENTRY_ATR_MULT", 0.0)
    _seed(SharedMemory())
    Trader().run()
    assert pending_orders.open_pending("BTC/USD") == []      # no ATR limit rested
    assert PositionManager().has_position("BTC/USD")         # market order filled


def test_no_atr_falls_through_to_market(monkeypatch):
    monkeypatch.setattr(trader_mod, "LIMIT_ENTRY_ATR_MULT", 1.0)
    m = SharedMemory()
    _seed(m, atr=0.0)                                        # no ATR available
    Trader().run()
    assert pending_orders.open_pending("BTC/USD") == []
    assert PositionManager().has_position("BTC/USD")

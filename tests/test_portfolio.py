from pathlib import Path

import pytest

from core.portfolio import Portfolio, Position, save_portfolio, load_portfolio, apply_fill
import config


def _clear_portfolio():
    f = config.DATA_DIR / "reports" / "portfolio.json"
    if f.exists():
        f.unlink()


class TestPosition:
    def test_long_position_pnl(self):
        pos = Position(symbol="BTC/USD", entry_price=50000, quantity=1.0, current_price=55000)
        pos.pnl = (55000 - 50000) * 1.0
        pos.pnl_pct = (55000 - 50000) / 50000 * 100
        assert pos.pnl == 5000.0
        assert pos.pnl_pct == 10.0

    def test_short_position_pnl(self):
        pos = Position(symbol="BTC/USD", entry_price=50000, quantity=-1.0, current_price=45000)
        pos.pnl = (50000 - 45000) * 1.0
        pos.pnl_pct = (50000 - 45000) / 50000 * 100
        assert pos.pnl == 5000.0
        assert pos.pnl_pct == 10.0


class TestPortfolio:
    def test_empty_portfolio(self):
        p = Portfolio()
        assert p.cash == 0.0
        assert p.equity == 0.0
        assert p.positions == {}

    def test_initial_balance(self):
        p = Portfolio(cash=10000, initial_balance=10000)
        assert p.cash == 10000
        assert p.total_pnl == 0.0
        assert p.total_pnl_pct == 0.0

    def test_equity_with_position(self):
        p = Portfolio(cash=5000, initial_balance=10000)
        p.positions["BTC/USD"] = Position(
            symbol="BTC/USD", entry_price=50000, quantity=0.1, current_price=55000
        )
        p.positions["BTC/USD"].pnl = 500.0
        assert p.positions_value == 5500.0
        assert p.equity == 10500.0
        assert p.total_pnl == 500.0

    def test_exposure_pct(self):
        p = Portfolio(cash=5000, initial_balance=10000)
        p.positions["BTC/USD"] = Position(
            symbol="BTC/USD", entry_price=50000, quantity=0.1, current_price=50000
        )
        assert p.exposure_pct == 50.0

    def test_update_price_long(self):
        p = Portfolio(cash=5000, initial_balance=10000)
        p.positions["BTC/USD"] = Position(
            symbol="BTC/USD", entry_price=50000, quantity=0.1, current_price=50000
        )
        p.update_price("BTC/USD", 55000)
        assert p.positions["BTC/USD"].current_price == 55000
        assert p.positions["BTC/USD"].pnl == 500.0

    def test_update_price_short(self):
        p = Portfolio(cash=5000, initial_balance=10000)
        p.positions["BTC/USD"] = Position(
            symbol="BTC/USD", entry_price=50000, quantity=-0.1, current_price=50000
        )
        p.update_price("BTC/USD", 45000)
        assert p.positions["BTC/USD"].pnl == 500.0


class TestSaveLoadPortfolio:
    def test_save_and_load(self):
        _clear_portfolio()
        p = Portfolio(cash=5000, initial_balance=10000)
        p.positions["BTC/USD"] = Position(
            symbol="BTC/USD", entry_price=50000, quantity=0.1, current_price=55000
        )
        save_portfolio(p)

        loaded = load_portfolio()
        assert loaded.cash == 5000
        assert loaded.initial_balance == 10000
        assert "BTC/USD" in loaded.positions
        assert loaded.positions["BTC/USD"].quantity == 0.1

    def test_load_empty(self):
        _clear_portfolio()
        p = load_portfolio()
        assert isinstance(p, Portfolio)
        assert p.cash == 0.0


class TestApplyFill:
    def test_buy_fill(self):
        p = Portfolio(cash=10000, initial_balance=10000)
        apply_fill(p, "BTC/USD", "BUY", 0.1, 50000)
        assert p.cash == 5000.0
        assert "BTC/USD" in p.positions
        assert p.positions["BTC/USD"].quantity == 0.1

    def test_sell_fill(self):
        p = Portfolio(cash=5000, initial_balance=10000)
        p.positions["BTC/USD"] = Position(
            symbol="BTC/USD", entry_price=50000, quantity=0.1, current_price=50000
        )
        apply_fill(p, "BTC/USD", "SELL", 0.05, 55000)
        assert p.cash == 7750.0
        assert p.positions["BTC/USD"].quantity == 0.05

    def test_sell_full_close(self):
        p = Portfolio(cash=5000, initial_balance=10000)
        p.positions["BTC/USD"] = Position(
            symbol="BTC/USD", entry_price=50000, quantity=0.1, current_price=50000
        )
        apply_fill(p, "BTC/USD", "SELL", 0.1, 55000)
        assert p.cash == 10500.0
        assert "BTC/USD" not in p.positions

    def test_sell_without_position_noop(self):
        p = Portfolio(cash=10000, initial_balance=10000)
        apply_fill(p, "BTC/USD", "SELL", 0.1, 50000)
        assert p.cash == 10000.0

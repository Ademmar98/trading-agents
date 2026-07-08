from unittest.mock import MagicMock, patch

from core.reconcile import reconcile_with_exchange


class _FakePos:
    def __init__(self, qty):
        self.quantity = qty


class _FakePortfolio:
    def __init__(self, cash=10000, positions=None):
        self.cash = cash
        self.positions = positions or {}


def test_broker_exception():
    broker = MagicMock()
    broker.get_balances.side_effect = Exception("API error")

    with patch("core.reconcile.load_portfolio") as mock_load:
        mock_load.return_value = _FakePortfolio()
        result = reconcile_with_exchange(broker)
        assert result is None


def test_no_balances():
    broker = MagicMock()
    broker.get_balances.return_value = {}

    with patch("core.reconcile.load_portfolio") as mock_load:
        mock_load.return_value = _FakePortfolio()
        result = reconcile_with_exchange(broker)
        assert result is None


def test_no_positions():
    broker = MagicMock()
    broker.get_balances.return_value = {"USDT": 10000}

    with patch("core.reconcile.load_portfolio") as mock_load:
        mock_load.return_value = _FakePortfolio()
        result = reconcile_with_exchange(broker)
        assert result is not None
        assert result["ledger_cash"] == 10000


def test_with_positions_fully_backed():
    broker = MagicMock()
    broker.get_balances.return_value = {"BTC": 0.5, "USDT": 5000}

    portfolio = _FakePortfolio(cash=5000, positions={
        "BTC/USD": _FakePos(0.5),
    })

    with patch("core.reconcile.load_portfolio", return_value=portfolio):
        result = reconcile_with_exchange(broker)
        assert result["drifted_positions"] == 0
        assert len(result["positions"]) == 1


def test_with_drifted_position():
    broker = MagicMock()
    broker.get_balances.return_value = {"BTC": 0.3, "USDT": 5000}

    portfolio = _FakePortfolio(cash=5000, positions={
        "BTC/USD": _FakePos(0.5),
    })

    with patch("core.reconcile.load_portfolio", return_value=portfolio):
        result = reconcile_with_exchange(broker)
        assert result["drifted_positions"] >= 1

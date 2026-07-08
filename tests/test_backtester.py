import pytest

from core.backtester import _calc_sl_tp, _compute_metrics
from core.data_provider import _to_binance_symbol


def test_to_binance_symbol():
    assert _to_binance_symbol("BTC/USD") == "BTCUSDT"
    assert _to_binance_symbol("ETH/USDT") == "ETHUSDT"
    assert _to_binance_symbol("SOL/USD") == "SOLUSDT"


@pytest.mark.parametrize("side,price,vol,sl_mult,tp_mult", [
    ("BUY", 60000.0, 2.0, 2.0, 6.0),
    ("SELL", 1800.0, 3.0, 2.0, 6.0),
    ("BUY", 100.0, 1.5, 1.5, 5.0),
])
def test_calc_sl_tp(side, price, vol, sl_mult, tp_mult):
    sl, tp = _calc_sl_tp(price, side, vol, sl_mult, tp_mult)
    assert sl > 0
    assert tp > 0
    if side == "BUY":
        assert sl < price < tp
    else:
        assert tp < price < sl


def test_compute_metrics_empty_trades():
    result = _compute_metrics("TEST", [], [], 10000.0)
    assert result["symbol"] == "TEST"
    assert result["total_trades"] == 0
    assert result["win_rate"] == 0


def test_compute_metrics_with_trades():
    trades = [
        {"pnl": 100, "symbol": "TEST", "side": "BUY", "qty": 1, "entry": 100, "exit": 110, "pnl_pct": 10, "reason": "TP", "bar": 10, "date": "2024-01-01"},
        {"pnl": -50, "symbol": "TEST", "side": "BUY", "qty": 1, "entry": 100, "exit": 95, "pnl_pct": -5, "reason": "SL", "bar": 20, "date": "2024-01-02"},
    ]
    equity_curve = [10000.0, 10100.0, 10050.0]
    result = _compute_metrics("TEST", trades, equity_curve, 10000.0)
    assert result["total_trades"] == 2
    assert result["win_rate"] == 50.0
    assert result["total_return"] > 0
    assert result["avg_win"] == 100
    assert result["avg_loss"] == 50


def test_compute_metrics_winning_trades():
    trades = [
        {"pnl": 200, "symbol": "TEST", "side": "BUY", "qty": 1, "entry": 100, "exit": 120, "pnl_pct": 20, "reason": "TP", "bar": 5, "date": "2024-01-01"},
        {"pnl": 150, "symbol": "TEST", "side": "SELL", "qty": 1, "entry": 100, "exit": 85, "pnl_pct": 15, "reason": "TP", "bar": 15, "date": "2024-01-02"},
    ]
    equity_curve = [10000.0, 10200.0, 10350.0]
    result = _compute_metrics("TEST", trades, equity_curve, 10000.0)
    assert result["total_return"] == 3.5
    assert result["profit_factor"] is None

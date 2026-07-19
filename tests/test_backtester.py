from unittest.mock import patch
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


def test_pos_value_buy():
    from core.backtester import _pos_value
    pos = {"side": "BUY", "qty": 1.0, "entry": 100}
    assert _pos_value(pos, 110) == 110.0
    assert _pos_value(pos, 90) == 90.0


def test_pos_value_sell():
    from core.backtester import _pos_value
    pos = {"side": "SELL", "qty": 1.0, "entry": 100}
    assert _pos_value(pos, 90) == 110.0
    assert _pos_value(pos, 110) == 90.0


def test_save_and_get_backtest_results():
    from core.database import init_db, execute, fetchall
    init_db()
    execute("DELETE FROM backtest_results")

    from core.backtester import _save_backtest, get_backtest_results
    result = {
        "symbol": "TEST/USD", "total_return": 5.0, "total_trades": 3,
        "win_rate": 66.7, "profit_factor": 2.0, "max_drawdown": 3.0,
        "sharpe_ratio": 1.5, "final_equity": 10500, "avg_win": 150.0,
        "avg_loss": 50.0, "trades": [],
    }
    _save_backtest(result)
    results = get_backtest_results()
    assert any(r["symbol"] == "TEST/USD" for r in results)


def test_run_all_backtests_empty_symbols():
    from core.backtester import run_all_backtests
    with patch("core.backtester.backtest_symbol", return_value=None):
        results = run_all_backtests(symbols=[])
        assert results == []


def test_run_all_backtests_with_results():
    from core.backtester import run_all_backtests
    mock_result = {
        "symbol": "TEST/USD", "total_return": 5.0, "total_trades": 2,
        "win_rate": 50.0, "profit_factor": 2.0, "max_drawdown": 3.0,
        "sharpe_ratio": 1.5, "final_equity": 10500, "avg_win": 100,
        "avg_loss": 50, "trades": [],
    }
    with patch("core.backtester.backtest_symbol", return_value=mock_result):
        with patch("core.backtester._save_backtest") as mock_save:
            results = run_all_backtests(symbols=["TEST/USD"])
            assert len(results) == 1
            mock_save.assert_called_once_with(mock_result)


def test_backtest_symbol_short_data():
    from core.backtester import backtest_symbol
    with patch("core.backtester.fetch_klines", return_value=[]):
        result = backtest_symbol("TEST/USD")
        assert result is None


def test_backtest_symbol_runs():
    from core.backtester import backtest_symbol
    ohlc = [{"high": 100 + i, "low": 99 + i, "close": 99.5 + i, "date": f"2024-01-{d:02d}"}
            for i, d in enumerate(range(1, 301))]
    with patch("core.backtester.fetch_klines", return_value=ohlc):
        with patch("core.backtester.scan_symbol", return_value=[]):
            result = backtest_symbol("TEST/USD", initial_capital=10000)
            assert result is not None
            assert result["symbol"] == "TEST/USD"
            assert result["total_trades"] == 0


def test_compute_metrics_empty_equity():
    from core.backtester import _compute_metrics
    result = _compute_metrics("TEST", [], [], 10000.0)
    assert result["total_return"] == 0
    assert result["final_equity"] == 10000.0
    assert result["profit_factor"] is None


def test_fetch_klines():
    from core.backtester import fetch_klines
    with patch("core.data_provider.fetch_ohlc", return_value=[{"close": 100}]):
        result = fetch_klines("TEST/USD")
        assert len(result) == 1


def test_backtest_symbol_with_signals():
    from core.backtester import backtest_symbol
    ohlc = [{"high": 100 + i, "low": 99 + i, "close": 99.5 + i, "date": f"2024-01-{d:02d}"}
            for i, d in enumerate(range(1, 301))]
    signals = [{"action": "BUY", "confidence": 0.8, "strategies": ["test_strat"], "strategy": "test_strat"}]
    with patch("core.backtester.fetch_klines", return_value=ohlc), \
         patch("core.backtester.scan_symbol", return_value=signals), \
         patch("core.backtester.MarketData") as MockMD:
        md_instance = MockMD.return_value
        md_instance.compute_indicators.return_value = {"volatility": 1.5, "atr": 0.5}
        result = backtest_symbol("TEST/USD", initial_capital=10000)
        assert result is not None
        assert result["symbol"] == "TEST/USD"


def _flat_bars(n, price, spread=0.5):
    return [{"high": price + spread, "low": price - spread, "close": price, "date": "2024-01-01"}
            for _ in range(n)]


def _run_scripted_backtest(ohlc, side="SELL"):
    """Backtest scripted bars: one signal on the first tradable bar, nothing after.
    BUY_ONLY is patched off: these tests pin the side-aware _exit_credit math,
    which only executes when short simulation is enabled."""
    from core.backtester import backtest_symbol
    signal = [{"action": side, "confidence": 0.9, "strategies": ["test_strat"], "strategy": "test_strat"}]
    calls = iter([signal])
    with patch("core.backtester.fetch_klines", return_value=ohlc), \
         patch("core.backtester.scan_symbol", side_effect=lambda *a, **k: next(calls, [])), \
         patch("core.backtester.get_unprofitable_strategies", return_value=[]), \
         patch("core.backtester.BUY_ONLY", False), \
         patch("core.backtester.MarketData") as MockMD:
        MockMD.return_value.compute_indicators.return_value = {"volatility": 2, "atr": 0}
        return backtest_symbol("TEST/USD", initial_capital=10000)


def test_exit_credit_short_partial_qty():
    from core.backtester import _exit_credit
    assert _exit_credit("SELL", 100, 0.5, 90) == 55.0
    assert _exit_credit("BUY", 100, 0.5, 90) == 45.0


def test_short_tp_exit_credits_side_aware_cash():
    # Short entered at 100 (vol=2 -> SL 106 / TP 93), price falls through the TP.
    # Regression: the exit used to credit qty*exit_price, so a winning short
    # DRAINED cash and final_equity dropped below the starting capital.
    ohlc = _flat_bars(201, 100.0)
    ohlc.append({"high": 99.0, "low": 95.0, "close": 96.0, "date": "2024-01-02"})
    ohlc.append({"high": 96.0, "low": 92.0, "close": 93.0, "date": "2024-01-03"})
    ohlc += _flat_bars(2, 93.0)

    result = _run_scripted_backtest(ohlc)

    assert result["total_trades"] == 1
    trade = result["trades"][0]
    assert trade["side"] == "SELL"
    assert trade["reason"] == "TP"
    assert trade["pnl"] > 0
    # Recorded pnl nets out BOTH fees, so it maps 1:1 onto the cash delta.
    assert result["final_equity"] > 10000
    assert result["final_equity"] == pytest.approx(10000 + trade["pnl"], abs=0.02)


def test_short_end_of_data_close_side_aware():
    # Short entered at 100, price settles at 95.5 — between TP (93) and SL (106) —
    # so the position rides to the end of data and is closed there. final_equity
    # is marked to the last close (the close-out fee lands after the last equity
    # point); a profitable short must end above the starting capital.
    ohlc = _flat_bars(201, 100.0)
    ohlc += [{"high": 96.0, "low": 95.0, "close": 95.5, "date": "2024-01-02"} for _ in range(5)]

    result = _run_scripted_backtest(ohlc)

    assert result["total_trades"] == 1
    trade = result["trades"][0]
    assert trade["side"] == "SELL"
    assert trade["reason"] == "close"
    assert trade["pnl"] > 0
    from config import TRADE_FEE_PCT, BACKTEST_SPREAD_PCT
    entry_fee = trade["qty"] * trade["entry"] * (TRADE_FEE_PCT + BACKTEST_SPREAD_PCT) / 100.0
    expected = 10000 - entry_fee + (trade["entry"] - trade["exit"]) * trade["qty"]
    assert result["final_equity"] > 10000
    assert result["final_equity"] == pytest.approx(expected, abs=0.02)

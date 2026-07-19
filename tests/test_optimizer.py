from unittest.mock import patch, MagicMock

from core.optimizer import (
    _save_optimization, get_optimized_params, optimize_symbol,
    run_all_optimizations, get_optimization_results,
    PARAM_GRID,
)


class TestBacktestWithParams:
    def test_returns_none_for_short_data(self):
        with patch("core.optimizer.fetch_klines", return_value=[]):
            from core.optimizer import _backtest_with_params
            result = _backtest_with_params("TEST/USD", 2.0, 4.0, 15, 0.5)
            assert result is None


class TestSaveOptimization:
    def test_inserts_row(self):
        with patch("core.optimizer.execute") as mock_exec:
            _save_optimization("TEST/USD", {
                "total_return": 5.0, "total_trades": 3, "win_rate": 66.7,
                "profit_factor": 2.0, "max_drawdown": 3.0, "sharpe_ratio": 1.5,
                "score": 80.0,
            }, {"sl_mult": 2.0, "tp_mult": 4.0, "position_size_pct": 15, "confidence_threshold": 0.5})
            mock_exec.assert_called_once()
            assert "INSERT OR REPLACE INTO optimization_results" in mock_exec.call_args[0][0]


class TestGetOptimizedParams:
    def test_returns_saved_params(self):
        with patch("core.optimizer.fetchone", return_value={
            "sl_mult": 2.5, "tp_mult": 5.0, "position_size_pct": 20, "confidence_threshold": 0.6,
        }):
            result = get_optimized_params("TEST/USD")
            assert result["sl_mult"] == 2.5
            assert result["tp_mult"] == 5.0
            assert result["position_size_pct"] == 20
            assert result["confidence_threshold"] == 0.6

    def test_returns_defaults_when_not_found(self):
        with patch("core.optimizer.fetchone", return_value=None):
            result = get_optimized_params("TEST/USD")
            assert result["sl_mult"] == 2.0
            assert result["tp_mult"] == 6.0
            assert result["position_size_pct"] == 25
            assert result["confidence_threshold"] == 0.0


class TestOptimizeSymbol:
    def test_aborts_when_no_valid_results(self):
        with patch("core.optimizer.fetch_klines", return_value=[]), \
             patch("core.optimizer._backtest_with_params", return_value=None):
            result = optimize_symbol("TEST/USD", verbose=False)
            assert result is None

    def test_returns_best_params_when_validation_passes(self):
        def mock_backtest(sym, sm, tm, ps, ct, bars=None, ohlc=None, bar_range=None):
            return {"score": sm + tm + ps, "total_return": 5.0, "total_trades": 3,
                    "win_rate": 66.7, "profit_factor": 2.0, "max_drawdown": 3.0,
                    "sharpe_ratio": 1.5}
        with patch("core.optimizer.fetch_klines", return_value=[]), \
             patch("core.optimizer._backtest_with_params", side_effect=mock_backtest):
            with patch("core.optimizer._save_optimization"):
                result = optimize_symbol("TEST/USD", verbose=False)
                assert result is not None
                assert "params" in result
                assert "result" in result
                assert result["adopted"] is True

    def test_verbose_prints(self):
        def mock_backtest(sym, sm, tm, ps, ct, bars=None, ohlc=None, bar_range=None):
            return {"score": 10, "total_return": 5.0, "total_trades": 3,
                    "win_rate": 66.7, "profit_factor": 2.0, "max_drawdown": 3.0,
                    "sharpe_ratio": 1.5}
        with patch("core.optimizer.fetch_klines", return_value=[]), \
             patch("core.optimizer._backtest_with_params", side_effect=mock_backtest):
            with patch("core.optimizer._save_optimization"):
                result = optimize_symbol("TEST/USD", verbose=True)
                assert result is not None

    def test_saves_validation_metrics_not_train(self):
        """The persisted numbers must come from the out-of-sample window."""
        def mock_backtest(sym, sm, tm, ps, ct, bars=None, ohlc=None, bar_range=None):
            if bar_range and bar_range[0] > 0:  # the validation call
                return {"score": 55, "total_return": 4.0, "total_trades": 4,
                        "win_rate": 75.0, "profit_factor": 1.5, "max_drawdown": 2.0,
                        "sharpe_ratio": 1.2}
            return {"score": sm + tm + ps, "total_return": 12.0, "total_trades": 8,
                    "win_rate": 80.0, "profit_factor": 3.0, "max_drawdown": 1.5,
                    "sharpe_ratio": 2.5}
        with patch("core.optimizer.fetch_klines", return_value=[]), \
             patch("core.optimizer._backtest_with_params", side_effect=mock_backtest):
            with patch("core.optimizer._save_optimization") as mock_save:
                result = optimize_symbol("TEST/USD", verbose=False)
                assert mock_save.called
                saved_result = mock_save.call_args[0][1]
                assert saved_result["score"] == 55
                assert result["adopted"] is True

    def test_rejects_params_that_fail_out_of_sample(self):
        """Great in-sample, bleeding out-of-sample = overfit; must not adopt."""
        def mock_backtest(sym, sm, tm, ps, ct, bars=None, ohlc=None, bar_range=None):
            if bar_range and bar_range[0] > 0:  # the validation call
                return {"score": -5, "total_return": -3.0, "total_trades": 4,
                        "win_rate": 25.0, "profit_factor": 0.6, "max_drawdown": 6.0,
                        "sharpe_ratio": -1.0}
            return {"score": 50, "total_return": 20.0, "total_trades": 8,
                    "win_rate": 85.0, "profit_factor": 4.0, "max_drawdown": 1.0,
                    "sharpe_ratio": 3.0}
        with patch("core.optimizer.fetch_klines", return_value=[]), \
             patch("core.optimizer._backtest_with_params", side_effect=mock_backtest):
            with patch("core.optimizer._save_optimization") as mock_save:
                result = optimize_symbol("TEST/USD", verbose=False)
                assert not mock_save.called
                assert result["adopted"] is False


class TestRunAllOptimizations:
    def test_runs_for_each_symbol(self):
        with patch("core.optimizer.optimize_symbol", return_value=None):
            result = run_all_optimizations(["BTC/USD"])
            assert result == []

    def test_returns_results(self):
        with patch("core.optimizer.optimize_symbol", return_value={"symbol": "BTC/USD", "params": {}, "result": {}}):
            result = run_all_optimizations(["BTC/USD"])
            assert len(result) == 1


class TestGetOptimizationResults:
    def test_returns_rows(self):
        with patch("core.optimizer.fetchall", return_value=[{"symbol": "BTC/USD", "score": 80.0}]):
            result = get_optimization_results()
            assert len(result) == 1
            assert result[0]["symbol"] == "BTC/USD"


def _make_ohlc(count=220, base_close=100, volatility=2):
    import math
    ohlc = []
    for i in range(count):
        c = base_close + math.sin(i * 0.5) * volatility
        ohlc.append({
            "high": round(c + volatility * 0.5, 2),
            "low": round(c - volatility * 0.5, 2),
            "close": round(c, 2),
            "date": f"2024-01-{i + 1:02d}",
        })
    return ohlc


class TestBacktestWithParamsFull:
    def test_buy_hits_sl(self):
        ohlc = _make_ohlc()
        signals = [{"action": "BUY", "confidence": 0.8}]
        with patch("core.optimizer.fetch_klines", return_value=ohlc), \
             patch("core.optimizer.scan_symbol", return_value=signals):
            from core.optimizer import _backtest_with_params
            result = _backtest_with_params("TEST/USD", sl_mult=0.1, tp_mult=10.0, pos_size=15, conf_thresh=0.5)
            assert result is not None
            assert result["total_trades"] >= 0
            assert "total_return" in result

    def test_buy_hits_tp(self):
        ohlc = _make_ohlc(base_close=100, volatility=1)
        signals = [{"action": "BUY", "confidence": 0.8}]
        with patch("core.optimizer.fetch_klines", return_value=ohlc), \
             patch("core.optimizer.scan_symbol", return_value=signals):
            from core.optimizer import _backtest_with_params
            result = _backtest_with_params("TEST/USD", sl_mult=0.01, tp_mult=0.5, pos_size=15, conf_thresh=0.5)
            assert result is not None

    def test_sell_hits_sl(self):
        ohlc = _make_ohlc(count=220, base_close=100, volatility=3)
        signals = [{"action": "SELL", "confidence": 0.9}]
        with patch("core.optimizer.fetch_klines", return_value=ohlc), \
             patch("core.optimizer.scan_symbol", return_value=signals):
            from core.optimizer import _backtest_with_params
            result = _backtest_with_params("TEST/USD", sl_mult=0.1, tp_mult=10.0, pos_size=15, conf_thresh=0.5)
            assert result is not None


class TestSingleParam:
    def test_returns_best_value(self):
        def mock_backtest(sym, **kwargs):
            val = kwargs.get("sl_mult", 2.0)
            return {"score": val * 10, "total_return": 5.0, "total_trades": 3,
                    "win_rate": 66.7, "profit_factor": 2.0, "max_drawdown": 3.0,
                    "sharpe_ratio": 1.5}
        from core.optimizer import test_single_param
        with patch("core.optimizer.fetch_klines", return_value=[]), \
             patch("core.optimizer._backtest_with_params", side_effect=mock_backtest):
            best_val, result = test_single_param("SL_VOL_MULT", 2.0, 0.5)
            assert best_val is not None
            assert result is not None
            assert result["score"] > 0

    def test_proposal_rejected_when_validation_fails(self):
        """A parameter change that wins in-sample but loses out-of-sample
        must be dropped (returns the incumbent value with no result)."""
        def mock_backtest(sym, **kwargs):
            if kwargs.get("bar_range") and kwargs["bar_range"][0] > 0:
                return {"score": 1, "total_return": -2.0, "total_trades": 3,
                        "win_rate": 33.0, "profit_factor": 0.5, "max_drawdown": 5.0,
                        "sharpe_ratio": -0.5}
            val = kwargs.get("sl_mult", 2.0)
            return {"score": val * 10, "total_return": 5.0, "total_trades": 3,
                    "win_rate": 66.7, "profit_factor": 2.0, "max_drawdown": 3.0,
                    "sharpe_ratio": 1.5}
        from core.optimizer import test_single_param
        with patch("core.optimizer.fetch_klines", return_value=[]), \
             patch("core.optimizer._backtest_with_params", side_effect=mock_backtest):
            best_val, result = test_single_param("SL_VOL_MULT", 2.0, 0.5)
            assert best_val == 2.0
            assert result is None

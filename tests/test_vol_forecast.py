"""GARCH(1,1) vol forecaster and the position-size throttle."""
import numpy as np
import pytest

from core.vol_forecast import (
    garch11_forecast_daily, _forecast_from_closes, forecast_vol_ann,
    vol_throttle, clear_cache,
)


@pytest.fixture(autouse=True)
def _clear():
    clear_cache()
    yield
    clear_cache()


def _closes(n=520, daily_vol=0.03, seed=0):
    rng = np.random.default_rng(seed)
    rets = rng.normal(0, daily_vol, n)
    px = 100.0 * np.cumprod(1 + rets)
    return [100.0] + list(px)


class TestForecaster:
    def test_short_series_returns_none(self):
        assert garch11_forecast_daily([0.01] * 50) is None
        assert _forecast_from_closes([100.0] * 50) is None

    def test_forecast_in_sane_ballpark(self):
        # 3% daily vol -> ~3% * sqrt(365) * 100 ≈ 57% annualized
        v = _forecast_from_closes(_closes(daily_vol=0.03, seed=1))
        assert 35.0 < v < 85.0

    def test_higher_input_vol_gives_higher_forecast(self):
        lo = _forecast_from_closes(_closes(daily_vol=0.02, seed=2))
        hi = _forecast_from_closes(_closes(daily_vol=0.05, seed=2))
        assert hi > lo

    def test_deterministic(self):
        c = _closes(seed=3)
        assert _forecast_from_closes(c) == _forecast_from_closes(c)


class TestThrottle:
    def test_no_throttle_when_calm(self):
        c = _closes(daily_vol=0.015, seed=4)          # ~29% annualized
        assert vol_throttle("X", 50.0, closes=c, fetch=False) == 1.0

    def test_throttles_down_when_stormy(self):
        c = _closes(daily_vol=0.05, seed=5)           # ~95% annualized
        t = vol_throttle("Y", 40.0, closes=c, fetch=False)
        assert 0.25 <= t < 1.0

    def test_clips_to_floor(self):
        c = _closes(daily_vol=0.09, seed=6)           # extreme vol
        t = vol_throttle("Z", 20.0, floor=0.3, closes=c, fetch=False)
        assert t == pytest.approx(0.3, abs=1e-9)

    def test_never_exceeds_one(self):
        c = _closes(daily_vol=0.005, seed=7)          # very calm
        assert vol_throttle("Q", 200.0, closes=c, fetch=False) == 1.0

    def test_fail_safe_when_no_forecast(self):
        assert vol_throttle("W", 50.0, closes=[100.0, 101.0], fetch=False) == 1.0

    def test_zero_target_is_no_throttle(self):
        c = _closes(daily_vol=0.05, seed=8)
        assert vol_throttle("V", 0.0, closes=c, fetch=False) == 1.0

    def test_cache_avoids_refetch(self, monkeypatch):
        import core.data_provider as dp
        monkeypatch.setattr(dp, "fetch_ohlc",
                            lambda *a, **k: (_ for _ in ()).throw(AssertionError("refetched")))
        c = _closes(seed=9)
        v1 = forecast_vol_ann("CACHED", closes=c, fetch=False)
        # second call would fetch (and raise) if the cache were missed
        v2 = forecast_vol_ann("CACHED", closes=None, fetch=True)
        assert v1 == v2 and v1 > 0

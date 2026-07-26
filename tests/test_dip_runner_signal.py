"""
The live dip_runner must fire on exactly the bars the backtest fired on.

If signal_for() and the backtested signal column disagree, the runner is not
trading the strategy that survived walk-forward -- which is the only reason
this strategy is in the repo at all.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.exchange.propr.dip_runner import (  # noqa: E402
    signal_for, rsi, cvd_zscore,
    LOOKBACK, DIP_THRESHOLD, RSI_OVERSOLD, VOLUME_SPIKE_MULT, CVD_THRESHOLD,
)


def synthetic(n=400, seed=7):
    """Deterministic OHLCV built to actually satisfy all four clauses at once:
    a long quiet drift, then a sustained high-volume selloff (which drives the
    dip past 5%, RSI under 30, volume above 1.8x, and CVD z-score below -0.8).
    """
    rng = np.random.default_rng(seed)
    close = 100 * np.cumprod(1 + rng.normal(0.0004, 0.004, n))
    vol = np.abs(rng.normal(1000, 120, n)) + 200.0

    # Sustained selloff over the final stretch, if the series is long enough.
    crash_len = min(30, max(0, n - 260))
    if crash_len:
        s = n - crash_len
        close[s:] = close[s - 1] * np.cumprod(np.full(crash_len, 0.985))
        vol[s:] *= 3.2

    high = close * 1.002
    low = close * 0.998
    idx = pd.date_range("2026-01-01", periods=n, freq="h", tz="UTC")
    return pd.DataFrame({"open": close, "high": high, "low": low,
                         "close": close, "volume": vol}, index=idx)


def backtest_signal_column(df):
    """The vectorised signal exactly as analysis/deep_hunt_v4_propr.py builds it."""
    d = df.copy()
    d["rsi"] = rsi(d["close"])
    d["cvd_z"] = cvd_zscore(d["volume"], d["close"])
    d["vol_spike"] = d["volume"] / (d["volume"].rolling(20).mean() + 1e-10)
    d["recent_high"] = d["high"].rolling(LOOKBACK).max()
    d["dip_pct"] = (d["recent_high"] - d["close"]) / d["recent_high"]
    return ((d["dip_pct"] >= DIP_THRESHOLD) & (d["rsi"] < RSI_OVERSOLD) &
            (d["vol_spike"] > VOLUME_SPIKE_MULT) & (d["cvd_z"] < CVD_THRESHOLD))


def test_live_signal_matches_backtest_bar_for_bar():
    df = synthetic()
    expected = backtest_signal_column(df)

    mismatches, fired = [], 0
    for i in range(260, len(df)):
        window = df.iloc[:i + 1]           # everything up to and incl. bar i
        got, _ = signal_for(window)
        want = bool(expected.iloc[i])
        fired += want
        if got != want:
            mismatches.append((i, got, want))

    assert fired > 0, "test data never triggers the signal — it proves nothing"
    assert not mismatches, (
        f"live signal diverges from the backtest on {len(mismatches)} bars "
        f"(first: {mismatches[:3]})")


def test_short_history_never_fires():
    """Under the 220-bar minimum the backtest skips a symbol entirely."""
    got, feats = signal_for(synthetic(n=100))
    assert got is False and feats == {}


def test_none_and_empty_are_safe():
    assert signal_for(None) == (False, {})
    assert signal_for(pd.DataFrame()) == (False, {})


def test_each_condition_is_load_bearing():
    """Every clause must be able to veto the entry on its own."""
    df = synthetic()
    expected = backtest_signal_column(df)
    hits = [i for i in range(260, len(df)) if expected.iloc[i]]
    assert hits, "no firing bar to test against"
    i = hits[0]
    base = df.iloc[:i + 1]
    assert signal_for(base)[0] is True

    # Kill the dip: lift the close to the recent high.
    d = base.copy()
    d.iloc[-1, d.columns.get_loc("close")] = d["high"].rolling(LOOKBACK).max().iloc[-1]
    assert signal_for(d)[0] is False, "dip condition is not load-bearing"

    # Kill the volume spike.
    d = base.copy()
    d.iloc[-1, d.columns.get_loc("volume")] = 1.0
    assert signal_for(d)[0] is False, "volume condition is not load-bearing"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

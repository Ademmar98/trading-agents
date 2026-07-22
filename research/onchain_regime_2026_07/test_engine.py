"""Validation tests for onchain_regime_strategy — run: python test_engine.py

  1. test_no_lookahead : the STRICTEST guard. entry[t] computed on the full
     series must EQUAL entry[t] recomputed on data truncated at t. If any bar
     differs, a future value leaked into a past decision. (This is the exact
     class of bug that faked a 3.5 profit-factor in research/swing_htf_2026_07.)
  2. test_win_path     : a filled long that trends up must register TP1->TP2 wins.
"""
import numpy as np, pandas as pd
import onchain_regime_strategy as M


def _synth(n=500):
    b = M.synthetic_bundle(n_hours=n)
    return b


def test_no_lookahead():
    cfg = M.Config()
    b = _synth(500)
    full = M.build_signals(b, cfg)["entry"]
    # recompute entry[t] using ONLY data through t, for a sample of bars
    bad = 0
    for t in range(300, 500, 17):            # sampled bars in the valid region
        sub = {
            "price": b["price"].iloc[:t + 1], "funding": b["funding"].iloc[:t + 1],
            "delta": b["delta"].iloc[:t + 1], "onchain": b["onchain"].iloc[:t + 1],
        }
        e_t = M.build_signals(sub, cfg)["entry"].iloc[t]
        if bool(e_t) != bool(full.iloc[t]):
            bad += 1
    assert bad == 0, f"LOOKAHEAD LEAK on {bad} sampled bars"
    print("  test_no_lookahead: PASS (no future data leaked into any sampled decision)")


def test_win_path():
    cfg = M.Config()
    n = 400
    idx = pd.date_range("2022-01-01", periods=n, freq="h", tz="UTC")
    close = 1000 * np.cumprod(1 + np.full(n, 0.004))
    openp = np.r_[close[0], close[:-1]]
    price = pd.DataFrame({"open": openp, "high": close * 1.001,
                          "low": np.minimum(openp, close) * 0.997, "close": close,
                          "volume": 100.0}, index=idx)
    df = pd.DataFrame(index=idx)
    df["close"], df["high"], df["low"] = close, price["high"], price["low"]
    df["atr"] = M.atr(price["high"], price["low"], price["close"], cfg.atr_period)
    df["dch_upper"] = M.donchian_4h_on_1h(price, cfg.donchian_period)
    df["dch_upper_prev"] = df["dch_upper"].shift(1)
    df["ref_prev"] = df["close"].shift(1)
    df["atr_prev"] = df["atr"].shift(1)
    df["regime_cash"] = False
    df["entry"] = True
    res = M.backtest(df, cfg)
    tr = res["trades"]
    assert len(tr) > 0 and (tr.pnl > 0).any(), "WIN-PATH BROKEN: no winning trades on a clean uptrend"
    print(f"  test_win_path: PASS ({len(tr)} trades, WR {(tr.pnl>0).mean()*100:.0f}%, "
          f"avg {tr.ret_pct.mean():.2f}%/trade)")


if __name__ == "__main__":
    print("Running validation tests...")
    test_no_lookahead()
    test_win_path()
    print("ALL TESTS PASSED")

"""Tests for core/strats/ict_smc_priceaction.py.

Synthetic OHLC series: flat featureless noise front section (no confirmed
swings, no displacement, no gaps), then handcrafted pattern bars. Each top
strategy must fire on its constructed pattern and the WHOLE family must stay
silent on pure noise. Last bar is always treated as CLOSED.
"""
import core.strats.ict_smc_priceaction as m

BASE_TS = 1_700_000_000


def _bar(i, o, h, l, c, vol=1000.0):
    return {"date": "2025-01-01", "open": o, "high": h, "low": l,
            "close": c, "volume": vol, "ts": BASE_TS + i * 3600}


def _noise(n):
    """Alternating micro-bars: no strict k=3 swings, no inside bars, no
    FVG gaps, no displacement (body 0.1 / range 0.4)."""
    bars = []
    for k in range(n):
        if k % 2 == 0:
            bars.append(_bar(k, 100.0, 100.2, 99.8, 100.1))
        else:
            bars.append(_bar(k, 100.1, 100.1, 99.7, 100.0))
    return bars


def _series(pattern, noise_len=40):
    """Pattern rows are (o,h,l,c); indices continue after the noise."""
    return _noise(noise_len) + [_bar(noise_len + j, *row) for j, row in enumerate(pattern)]


def _assert_sig(sig, action, tag):
    assert sig is not None, f"expected {tag} {action} signal"
    assert sig["action"] == action
    assert sig["strategy"] == tag
    assert 0 < sig["confidence"] <= 1
    assert isinstance(sig["reasons"], list) and sig["reasons"]


# ---------------------------------------------------------------------------
# Firing tests — top catalog strategies on constructed patterns
# ---------------------------------------------------------------------------

def test_fvg_retrace_fires():
    # displacement middle bar (41), bullish FVG at 42 (zone 101.0-102.6),
    # drift up, last bar closes back inside the unfilled gap.
    ohlc = _series([
        (100.0, 101.0, 99.9, 100.8),    # 40
        (100.8, 103.3, 100.7, 103.0),   # 41 displacement
        (103.0, 104.2, 102.6, 104.0),   # 42 FVG bar
        (104.0, 104.8, 103.7, 104.5),   # 43
        (104.3, 104.4, 102.2, 102.4),   # 44 retrace into zone
    ])
    _assert_sig(m.strat_fvg_retrace(ohlc), "BUY", "ict_pa_fvg_retrace")


def test_liquidity_sweep_reversal_fires():
    # confirmed swing low 98.0 at bar 45; sweep bar wicks 97.8, closes back
    # above in top 50%; confirmation bar closes above sweep high.
    ohlc = _series([
        (99.8, 100.0, 98.0, 99.5),      # 45 swing low
        (99.5, 99.9, 99.0, 99.6),
        (99.6, 99.9, 99.1, 99.5),
        (99.5, 99.8, 99.0, 99.4),
        (99.4, 99.7, 99.1, 99.5),
        (99.4, 99.6, 97.8, 99.3),       # 50 sweep (close_pos 0.83)
        (99.3, 100.0, 99.2, 99.8),      # 51 confirmation
    ], noise_len=45)
    _assert_sig(m.strat_liquidity_sweep_reversal(ohlc), "BUY",
                "ict_pa_liquidity_sweep")


def test_liquidity_sweep_reversal_short_mirror():
    # confirmed swing high 102.0; sweep wicks above, closes back below in
    # bottom 50%; confirmation closes below sweep low.
    ohlc = _series([
        (100.2, 102.0, 100.0, 101.0),   # 45 swing high
        (101.0, 101.2, 100.4, 100.6),
        (100.6, 101.0, 100.3, 100.5),
        (100.5, 100.9, 100.2, 100.4),
        (100.4, 100.8, 100.1, 100.3),
        (100.3, 102.3, 100.2, 100.5),   # 50 sweep of the high
        (100.5, 100.7, 99.6, 99.9),     # 51 confirmation
    ], noise_len=45)
    _assert_sig(m.strat_liquidity_sweep_reversal(ohlc), "SELL",
                "ict_pa_liquidity_sweep")


def test_bos_pullback_fires():
    # established uptrend: HH 102->103, HL 99->100; displacement BOS close
    # above 103 at bar 52; last bar pulls back into the BOS 50% body.
    ohlc = _series([
        (100.1, 102.0, 99.9, 101.0),    # 40 swing high 1
        (101.0, 101.5, 100.3, 100.5),
        (100.5, 101.2, 99.8, 99.9),
        (99.9, 100.4, 99.0, 100.2),     # 43 swing low 1
        (100.2, 101.0, 99.7, 100.8),
        (100.8, 101.6, 100.4, 101.4),
        (101.4, 103.0, 101.2, 102.0),   # 46 swing high 2 (higher)
        (102.0, 102.6, 101.0, 101.3),
        (101.3, 101.9, 100.2, 100.6),
        (100.6, 101.1, 100.0, 100.9),   # 49 swing low 2 (higher)
        (100.9, 101.8, 100.5, 101.5),
        (101.5, 102.4, 101.1, 102.2),
        (102.2, 104.8, 102.1, 104.5),   # 52 BOS displacement
        (104.5, 104.9, 103.8, 104.2),
        (104.2, 104.3, 103.2, 103.5),   # 54 pullback into BOS 50%
    ])
    _assert_sig(m.strat_bos_pullback(ohlc), "BUY", "ict_pa_bos_pullback")


def test_choch_reversal_fires():
    # downtrend: LH 108->106, LL 102->100; sweep of 100 at bar 52;
    # displacement close above 106 (CHoCH) at 53; last bar retests and holds.
    ohlc = _series([
        (104.0, 108.0, 103.5, 107.0),   # 40 swing high 1
        (107.0, 107.2, 104.0, 104.5),
        (104.5, 105.0, 102.6, 103.0),
        (103.0, 103.5, 102.0, 102.8),   # 43 swing low 1
        (102.8, 104.0, 102.4, 103.6),
        (103.6, 104.8, 103.2, 104.5),
        (104.5, 106.0, 104.2, 105.0),   # 46 swing high 2 (lower)
        (105.0, 105.2, 102.0, 102.8),
        (102.8, 103.0, 100.0, 100.8),   # 48 swing low 2 (lower, confirmed by 51)
        (100.8, 101.4, 100.3, 101.0),
        (101.0, 101.3, 100.4, 100.7),
        (100.7, 101.0, 100.2, 100.5),
        (100.5, 100.8, 100.1, 100.6),
        (100.6, 100.9, 99.2, 100.5),    # 53 sweep of last LL
        (100.5, 107.4, 100.4, 107.0),   # 54 CHoCH displacement
        (107.0, 107.2, 105.8, 106.3),   # 55 retest holds above 106
    ])
    _assert_sig(m.strat_choch_reversal(ohlc), "BUY", "ict_pa_choch_reversal")


def test_turtle_soup_fires():
    # equal lows 98.00 / 98.03 seven bars apart; shallow raid to 97.85 closes
    # back above the level on the last bar.
    ohlc = _series([
        (99.8, 100.0, 98.00, 99.5),     # 45 equal low 1
        (99.5, 99.9, 98.8, 99.4),
        (99.4, 99.8, 99.0, 99.5),
        (99.5, 99.9, 99.1, 99.6),
        (99.6, 100.0, 99.2, 99.7),
        (99.7, 100.1, 99.3, 99.8),
        (99.8, 100.1, 99.0, 99.4),
        (99.4, 99.8, 98.03, 99.3),      # 52 equal low 2
        (99.3, 99.7, 98.9, 99.4),
        (99.4, 99.8, 99.0, 99.5),
        (99.5, 99.8, 98.9, 99.4),
        (99.4, 99.5, 97.85, 98.5),      # 56 raid + reclaim
    ], noise_len=45)
    _assert_sig(m.strat_turtle_soup(ohlc), "BUY", "ict_pa_turtle_soup")


def test_order_block_retest_fires():
    # swing high 103 at 44; bearish OB at 47; bullish displacement breaks 103
    # at 48; unmitigated drift; last bar dips into the OB zone (first retest).
    ohlc = _series([
        (100.1, 103.0, 99.9, 102.0),    # 44 swing high
        (102.0, 102.4, 101.4, 101.8),
        (101.8, 102.2, 101.2, 101.6),
        (102.0, 102.5, 101.6, 101.7),   # 47 bearish OB (zone 101.6-102.5)
        (101.7, 104.9, 101.6, 104.6),   # 48 displacement break
        (104.6, 105.2, 103.8, 104.8),
        (104.8, 105.0, 103.5, 103.9),
        (103.9, 104.2, 103.1, 103.8),
        (103.8, 103.9, 101.9, 102.4),   # 52 first retest of zone
    ], noise_len=44)
    _assert_sig(m.strat_order_block_retest(ohlc), "BUY", "ict_pa_order_block")


def test_ote_fires():
    # impulse leg 98.0 -> 104.6 with displacement at 46; retrace into the
    # 0.62-0.79 zone (99.386-100.508) on the last close, leg intact.
    ohlc = _series([
        (99.9, 100.2, 98.0, 99.5),      # 43 swing low (leg origin)
        (99.5, 100.4, 99.2, 100.2),
        (100.2, 101.4, 100.0, 101.2),
        (101.2, 103.2, 101.1, 103.0),   # 46 displacement in leg
        (103.0, 103.8, 102.6, 103.5),
        (103.5, 104.6, 103.2, 104.2),   # 48 swing high (leg end)
        (104.2, 104.3, 102.4, 102.8),
        (102.8, 103.0, 101.2, 101.6),
        (101.6, 101.9, 100.4, 100.9),
        (100.9, 101.2, 99.9, 100.2),    # 52 close inside OTE zone
    ], noise_len=43)
    _assert_sig(m.strat_ote(ohlc), "BUY", "ict_pa_ote")


def test_engulfing_at_structure_fires():
    # downtrend (close < SMA50), swing low 95.0 at 50; bullish engulfing of
    # the prior body printing right at that swing low.
    ohlc = _series([
        (99.5, 99.7, 95.0, 96.5),       # 50 swing low
        (96.5, 97.4, 96.2, 97.2),
        (97.2, 97.5, 96.8, 97.0),       # 52 prior bearish body
        (96.9, 97.6, 95.1, 97.4),       # 53 bullish engulfing at the level
    ], noise_len=50)
    _assert_sig(m.strat_engulfing_structure(ohlc), "BUY", "ict_pa_engulfing")


def test_pin_bar_fires():
    # hammer at the 98.0 swing low (lower wick 0.85 >= 2x body, no nose);
    # next bar breaks the pin high.
    ohlc = _series([
        (99.5, 99.7, 98.0, 99.3),       # 50 swing low
        (99.3, 99.6, 98.9, 99.4),
        (99.4, 99.6, 99.0, 99.3),
        (98.9, 98.95, 98.05, 98.95),    # 53 hammer at the level
        (98.95, 99.4, 98.9, 99.2),      # 54 break of pin high
    ], noise_len=50)
    _assert_sig(m.strat_pin_bar_rejection(ohlc), "BUY", "ict_pa_pin_bar")


def test_morning_star_fires():
    # strong bearish c1, small stalled c2 in c1's lower quarter (crypto
    # no-gap), bullish c3 closing >= 50% into c1's body.
    ohlc = _series([
        (102.0, 102.2, 99.8, 100.0),    # 50 c1
        (100.1, 100.3, 99.7, 99.9),     # 51 c2 small
        (99.9, 101.4, 99.8, 101.2),     # 52 c3
    ], noise_len=50)
    _assert_sig(m.strat_morning_evening_star(ohlc), "BUY",
                "ict_pa_morning_star")


# ---------------------------------------------------------------------------
# Silence / robustness tests
# ---------------------------------------------------------------------------

def test_all_silent_on_featureless_noise():
    ohlc = _noise(80)
    for tag, fn in m.ICT_SMC_PRICEACTION_STRATEGIES:
        assert fn(ohlc) is None, f"{tag} fired on featureless noise"


def test_session_strategies_skip_without_ts():
    ohlc = _noise(70)
    for c in ohlc:
        c.pop("ts")
    assert m.strat_judas_swing(ohlc) is None
    assert m.strat_power_of_three(ohlc) is None
    assert m.strat_silver_bullet(ohlc) is None
    assert m.strat_killzone_orb(ohlc) is None


def test_short_and_empty_inputs():
    assert m.scan_ict_smc_priceaction([]) == []
    tiny = _noise(10)
    for tag, fn in m.ICT_SMC_PRICEACTION_STRATEGIES:
        assert fn(tiny) is None


def test_registry_and_family_scan():
    # 26 OHLC-compatible catalog entries (SMT excluded: needs multi-symbol).
    assert len(m.ICT_SMC_PRICEACTION_STRATEGIES) == 26
    assert len({t for t, _ in m.ICT_SMC_PRICEACTION_STRATEGIES}) == 26
    assert "SMT" in m.__doc__  # skip is documented in the module docstring
    ohlc = _series([
        (100.0, 101.0, 99.9, 100.8),
        (100.8, 103.3, 100.7, 103.0),
        (103.0, 104.2, 102.6, 104.0),
        (104.0, 104.8, 103.7, 104.5),
        (104.3, 104.4, 102.2, 102.4),
    ])
    signals = m.scan_ict_smc_priceaction(ohlc)
    tags = {s["strategy"] for s in signals}
    assert "ict_pa_fvg_retrace" in tags
    for s in signals:
        assert s["action"] in ("BUY", "SELL")
        assert 0 < s["confidence"] <= 1
        assert s["reasons"]

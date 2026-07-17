"""Mean-reversion strategy family — synthetic OHLC fire/silence proofs.

Constructions are closed-bar series (the scan contract guarantees the last
bar is CLOSED) built so indicator values land decisively beyond each
catalog threshold (research/mean_reversion.md). Every fire test asserts the
shared return-dict convention from core/strategies.py scan_symbol:
{"action", "confidence", "reasons", "strategy": <unique per-tag name>};
silence tests run the same functions over perfectly regular uptrends and
downtrends, where a mean-reversion entry must NOT trigger.
"""
import pytest

from core.strats import mean_reversion as mr

TS0 = 1_700_000_000  # fixed anchor; session logic is not exercised by default builders


# ------------------------------------------------------------ builders

def _mk(closes, vol=100.0, ts0=TS0, step=3600):
    """Closes -> OHLCV dicts (open = prev close, tight symmetric wicks)."""
    bars = []
    for i, c in enumerate(closes):
        o = closes[i - 1] if i else c
        bars.append({"open": o, "high": max(o, c) * 1.0005,
                     "low": min(o, c) * 0.9995, "close": c,
                     "volume": vol, "ts": ts0 + i * step})
    return bars


def _mk_custom(specs, vol=100.0, ts0=TS0, step=3600):
    """(open, high, low, close) tuples -> OHLCV dicts."""
    return [{"open": o, "high": h, "low": l, "close": c, "volume": vol,
             "ts": ts0 + i * step} for i, (o, h, l, c) in enumerate(specs)]


def _no_ts(bars):
    return [{k: v for k, v in b.items() if k != "ts"} for b in bars]


def _wiggle(n, base=100.0, amp=0.05):
    """Flat alternating closes — RSI(14) baseline ~50, tiny stdev."""
    return [base + (amp if i % 2 else -amp) for i in range(n)]


def _trend(n=260, start=100.0, step=0.05, direction=1):
    """Perfectly regular drift. Bar width = 1.5x step so IBS sits mid-range
    (0.625 up / 0.375 down), |z| ~1.7 < 2, stretch ~2.4 < 2.5, pct-from-SMA50
    ~1.2% < 5%, %b inside (0,1), RSI(2) pinned at 100/0 with no level
    crossings -> every MR strategy in this module must stay silent."""
    w = 1.5 * step
    bars, price = [], start
    for i in range(n):
        o = price
        c = o + direction * step
        h = (c + w) if direction > 0 else (o + w)
        l = (o - w) if direction > 0 else (c - w)
        bars.append({"open": o, "high": h, "low": l, "close": c,
                     "volume": 100.0, "ts": TS0 + i * 3600})
        price = c
    return bars


def _pullback(n=240, start=100.0, drift=0.0015, drops=(0.01, 0.01, 0.01, 0.01)):
    """Gentle uptrend (close ends well above the lagging SMA200) + sharp
    consecutive down closes -> RSI(2) collapses to ~1 while the trend
    filter still passes."""
    closes = [start]
    for _ in range(n - 1):
        closes.append(closes[-1] * (1 + drift))
    for d in drops:
        closes.append(closes[-1] * (1 - d))
    return _mk(closes)


def _rally(n=240, start=200.0, drift=-0.0015, gains=(0.01, 0.01, 0.01, 0.01)):
    """Mirror of _pullback: downtrend (close below SMA200) + sharp up closes
    -> RSI(2) ~99."""
    closes = [start]
    for _ in range(n - 1):
        closes.append(closes[-1] * (1 + drift))
    for g in gains:
        closes.append(closes[-1] * (1 + g))
    return _mk(closes)


def _assert_sig(sig, action, tag):
    assert sig is not None, f"{tag} did not fire"
    assert sig["action"] == action
    assert sig["strategy"] == tag
    assert 0.5 <= sig["confidence"] <= 0.85
    assert sig["reasons"] and any(tag in r for r in sig["reasons"])


# ------------------------------------------------------------ 1-4: RSI(2)/Connors family

class TestRSI2ConnorsFamily:
    def test_classic_buy_sell(self):
        _assert_sig(mr.rsi2_connors_classic(_pullback()), "BUY", "mr_rsi2_connors_classic")
        _assert_sig(mr.rsi2_connors_classic(_rally()), "SELL", "mr_rsi2_connors_classic")

    def test_aggressive10_buy_sell(self):
        _assert_sig(mr.rsi2_connors_aggressive10(_pullback()), "BUY", "mr_rsi2_connors_aggressive10")
        _assert_sig(mr.rsi2_connors_aggressive10(_rally()), "SELL", "mr_rsi2_connors_aggressive10")

    def test_triple_capitulation_buy_sell(self):
        # 4 consecutive RSI(2) closes beyond the band >= the 3-bar streak
        _assert_sig(mr.rsi2_triple_capitulation(_pullback()), "BUY", "mr_rsi2_triple_capitulation")
        _assert_sig(mr.rsi2_triple_capitulation(_rally()), "SELL", "mr_rsi2_triple_capitulation")

    def test_scalein_tps_first_tranche_cross(self):
        # Single-bar panic: RSI(2) 100 -> ~13 crosses the first tranche (20)
        sig = mr.rsi2_scalein_tps(_pullback(drops=(0.01,)))
        _assert_sig(sig, "BUY", "mr_rsi2_scalein_tps")
        assert "tranche 1/4" in sig["reasons"][0]
        sig = mr.rsi2_scalein_tps(_rally(gains=(0.01,)))
        _assert_sig(sig, "SELL", "mr_rsi2_scalein_tps")
        assert "tranche 1/4" in sig["reasons"][0]


# ------------------------------------------------------------ 5: RSI14 fade

class TestRSI14ClassicFade:
    @staticmethod
    def _oversold_cross():
        # RSI(14) ~11 after 8 down bars, then one +2.5% bar lifts it to ~35
        closes = _wiggle(60)
        for _ in range(8):
            closes.append(closes[-1] * 0.992)
        closes.append(closes[-1] * 1.025)
        return _mk(closes)

    @staticmethod
    def _overbought_cross():
        closes = _wiggle(60)
        for _ in range(8):
            closes.append(closes[-1] * 1.008)
        closes.append(closes[-1] * 0.975)
        return _mk(closes)

    def test_buy_on_cross_back_up(self):
        _assert_sig(mr.rsi14_classic_fade(self._oversold_cross()), "BUY", "mr_rsi14_classic_fade")

    def test_sell_on_cross_back_down(self):
        _assert_sig(mr.rsi14_classic_fade(self._overbought_cross()), "SELL", "mr_rsi14_classic_fade")


# ------------------------------------------------------------ 6: ConnorsRSI

class TestConnorsRSI:
    def test_buy_sell(self):
        # 4-bar pullback: streak -4, RSI(3) ~1, %rank 0 -> CRSI ~1 < 10
        _assert_sig(mr.connors_rsi_crsi(_pullback()), "BUY", "mr_connors_rsi_crsi")
        _assert_sig(mr.connors_rsi_crsi(_rally()), "SELL", "mr_connors_rsi_crsi")


# ------------------------------------------------------------ 7-9: Bollinger family

class TestBollinger:
    def test_pctb_reversal_buy_sell(self):
        # close below lower band on t, back inside on t+1 (and mirror)
        _assert_sig(mr.bollinger_pctb_reversal(_mk(_wiggle(60, amp=0.3) + [96.5, 99.6])),
                    "BUY", "mr_bollinger_pctb_reversal")
        _assert_sig(mr.bollinger_pctb_reversal(_mk(_wiggle(60, amp=0.3) + [103.5, 100.4])),
                    "SELL", "mr_bollinger_pctb_reversal")

    def test_wickfade_buy_sell(self):
        base = [(c, c + 0.05, c - 0.05, c) for c in _wiggle(59, amp=0.3)]
        buy = _mk_custom(base + [(99.2, 99.98, 98.9, 99.95)])   # wick through lower, bull close inside
        sell = _mk_custom(base + [(100.8, 101.1, 100.05, 100.05)])  # wick through upper, bear close inside
        _assert_sig(mr.bollinger_bandtouch_wickfade(buy), "BUY", "mr_bollinger_bandtouch_wickfade")
        _assert_sig(mr.bollinger_bandtouch_wickfade(sell), "SELL", "mr_bollinger_bandtouch_wickfade")

    def test_midband_return_buy_sell(self):
        base = [(c, c + 0.05, c - 0.05, c) for c in _wiggle(40, amp=0.3)]
        # deep band breach 2 bars back, RSI(14) ~33, last close over prev high
        buy = _mk_custom(base + [
            (99.7, 99.75, 88.2, 88.3),
            (88.3, 88.35, 88.05, 88.1),
            (88.1, 89.75, 88.0, 89.7),
        ])
        sell = _mk_custom(base + [
            (99.7, 112.4, 99.65, 112.3),
            (112.3, 112.55, 112.2, 112.5),
            (112.5, 112.6, 110.85, 110.9),
        ])
        _assert_sig(mr.bollinger_midband_return(buy), "BUY", "mr_bollinger_midband_return")
        _assert_sig(mr.bollinger_midband_return(sell), "SELL", "mr_bollinger_midband_return")


# ------------------------------------------------------------ 10: Stochastic cross fade

class TestStochExtremeCrossFade:
    @staticmethod
    def _pinned(closes_tail):
        """Pin raw %K of the last 6 bars by fixing the 14-bar window's HH/LL
        with a spike bar exactly 14 bars back (high 110 / low 100)."""
        specs = [(c, c + 0.2, c - 0.2, c) for c in _wiggle(46, 105.0, 0.2)]
        specs.append((105.0, 110.0, 100.0, 105.0))       # window-pinning spike
        specs += [(105.0, 105.2, 104.8, 105.0)] * 7      # neutral filler
        specs += [(c, c + 0.1, c - 0.1, c) for c in closes_tail]
        return _mk_custom(specs)

    def test_buy_cross_in_oversold(self):
        # raw %K [12,10,6,8,11,18] -> %K crosses above %D while %K ~12 < 20
        bars = self._pinned([101.2, 101.0, 100.6, 100.8, 101.1, 101.8])
        _assert_sig(mr.stoch_extreme_crossfade(bars), "BUY", "mr_stoch_extreme_crossfade")

    def test_sell_cross_in_overbought(self):
        # raw %K [88,90,94,92,89,82] -> %K crosses below %D while %K ~88 > 80
        bars = self._pinned([108.8, 109.0, 109.4, 109.2, 108.9, 108.2])
        _assert_sig(mr.stoch_extreme_crossfade(bars), "SELL", "mr_stoch_extreme_crossfade")


# ------------------------------------------------------------ extras: IBS / ZScore / Keltner / RubberBand

class TestExtras:
    def test_ibs_classic_buy_sell(self):
        base = [(100.0, 100.1, 99.9, 100.0)] * 30
        buy = _mk_custom(base + [(100.4, 100.5, 99.0, 99.1)])    # IBS ~0.07
        sell = _mk_custom(base + [(99.6, 101.0, 99.5, 100.9)])   # IBS ~0.93
        _assert_sig(mr.ibs_classic_daily(buy), "BUY", "mr_ibs_classic_daily")
        _assert_sig(mr.ibs_classic_daily(sell), "SELL", "mr_ibs_classic_daily")

    def test_ibs_trendfiltered_buy(self):
        # uptrend above SMA50 + capitulation close near bar low on 2x volume
        bars = _trend(n=59, step=0.3)
        bars.append({"open": 117.5, "high": 117.6, "low": 114.0, "close": 114.2,
                     "volume": 200.0, "ts": TS0 + 59 * 3600})
        _assert_sig(mr.ibs_trendfiltered(bars), "BUY", "mr_ibs_trendfiltered")

    def test_zscore_buy_sell(self):
        # one-bar 4.5% break puts z ~ -7 (single-outlier bound sqrt(n-1));
        # the next bar's smaller break keeps z < -2 while ticking back up
        _assert_sig(mr.zscore_price_reversion(_mk(_wiggle(49, amp=0.1) + [95.5, 96.2])),
                    "BUY", "mr_zscore_price_reversion")
        _assert_sig(mr.zscore_price_reversion(_mk(_wiggle(49, amp=0.1) + [104.5, 103.8])),
                    "SELL", "mr_zscore_price_reversion")

    def test_keltner_midline_buy_sell(self):
        base = [(100.0, 100.1, 99.9, 100.0)] * 30
        buy = _mk_custom(base + [
            (100.0, 100.05, 98.9, 99.0),    # close below lower Keltner
            (99.0, 99.9, 98.95, 99.85),     # close back above it
        ])
        sell = _mk_custom(base + [
            (100.0, 101.1, 99.95, 101.0),   # close above upper Keltner
            (101.0, 101.05, 100.1, 100.15),  # close back below it
        ])
        _assert_sig(mr.keltner_midline_reversion(buy), "BUY", "mr_keltner_midline_reversion")
        _assert_sig(mr.keltner_midline_reversion(sell), "SELL", "mr_keltner_midline_reversion")

    def test_rubberband_sma_stretch_buy_sell(self):
        base = [(100.0, 100.1, 99.9, 100.0)] * 19
        buy = _mk_custom(base + [(98.6, 99.1, 98.5, 99.0)])      # ~-3.2 ATR stretch, bull bar
        sell = _mk_custom(base + [(101.4, 101.5, 100.9, 101.0)])  # ~+3.2 ATR stretch, bear bar
        _assert_sig(mr.rubberband_sma_stretch(buy), "BUY", "mr_rubberband_sma_stretch")
        _assert_sig(mr.rubberband_sma_stretch(sell), "SELL", "mr_rubberband_sma_stretch")

    def test_rubberband_pct_from_ma_buy_sell(self):
        # steeper base drift so a 4x3% break lands >5% beyond the lagging SMA50
        _assert_sig(mr.rubberband_pct_from_ma(_pullback(drift=0.0025, drops=(0.03,) * 4)),
                    "BUY", "mr_rubberband_pct_from_ma")
        _assert_sig(mr.rubberband_pct_from_ma(_rally(drift=-0.0025, gains=(0.03,) * 4)),
                    "SELL", "mr_rubberband_pct_from_ma")


# ------------------------------------------------------------ silence on trends

FIRE_TESTED = [
    mr.rsi2_connors_classic,
    mr.rsi2_connors_aggressive10,
    mr.rsi2_triple_capitulation,
    mr.rsi2_scalein_tps,
    mr.rsi14_classic_fade,
    mr.connors_rsi_crsi,
    mr.bollinger_pctb_reversal,
    mr.bollinger_bandtouch_wickfade,
    mr.bollinger_midband_return,
    mr.stoch_extreme_crossfade,
    mr.ibs_classic_daily,
    mr.ibs_trendfiltered,
    mr.zscore_price_reversion,
    mr.keltner_midline_reversion,
    mr.rubberband_sma_stretch,
    mr.rubberband_pct_from_ma,
]


@pytest.mark.parametrize("fn", FIRE_TESTED, ids=lambda f: f.__name__)
def test_silent_on_steady_uptrend(fn):
    assert fn(_trend(direction=1)) is None


@pytest.mark.parametrize("fn", FIRE_TESTED, ids=lambda f: f.__name__)
def test_silent_on_steady_downtrend(fn):
    assert fn(_trend(direction=-1)) is None


# ------------------------------------------------------------ contract / registry meta

class TestContract:
    def test_registry_covers_catalog(self):
        # 29 catalog entries - 3 documented skips = 26 implemented
        assert len(mr.MEAN_REVERSION_STRATEGIES) == 26
        names = [n for n, _ in mr.MEAN_REVERSION_STRATEGIES]
        fns = [f for _, f in mr.MEAN_REVERSION_STRATEGIES]
        assert len(set(names)) == 26 and len({f.__name__ for f in fns}) == 26
        assert all(n.startswith("MR - ") for n in names)
        assert all(callable(f) for f in fns)

    def test_skipped_entries_documented(self):
        assert set(mr.SKIPPED_CATALOG_ENTRIES) == {
            "Pairs_Cointegration_ZScore",
            "Perp_Basis_Reversion",
            "Funding_Rate_Extreme_Fade",
        }
        for entry in mr.SKIPPED_CATALOG_ENTRIES:
            assert entry in mr.__doc__

    def test_insufficient_history_returns_none(self):
        assert mr.rsi2_connors_classic(_mk([100.0] * 50)) is None
        assert mr.stoch_extreme_crossfade(_mk([100.0] * 10)) is None
        assert mr.ou_halflife_spread_reversion(_mk([100.0] * 60)) is None

    def test_session_functions_require_ts(self):
        bars = _no_ts(_trend(n=40))
        assert mr.vwap_atr_deviation_fade(bars) is None
        assert mr.vwap_stdband_reversion(bars) is None
        assert mr.overnight_session_gap_fade(bars) is None
        assert mr.forex_weekend_gap_fill(bars) is None
        assert mr.overnight_crypto_session_reversion(bars) is None

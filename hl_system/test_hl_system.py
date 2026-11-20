#!/usr/bin/env python3
"""
Self-check for the non-trivial logic. No network, no SDK credentials.

    python -m hl_system.test_hl_system

Covers the paths where a silent bug costs money: order quantisation (a wrong
price is a rejected or crossing order), ATR (wrong size), the drawdown state
machine (the thing that ends the account), and each signal module's trigger.
"""
from __future__ import annotations

import tempfile
import time
from pathlib import Path

from .config import ApiConfig, ExecConfig, RiskConfig, SignalConfig, SystemConfig
from .data_engine import (AssetMeta, BookLevel, Candle, OrderBook, Trade,
                          round_price, round_size)
from .execution import _tick_for, passive_price
from .risk_manager import RiskManager, RiskState, atr
from .signals import (Direction, FundingZScoreSignal, MicrostructureSignal,
                      VwapReversionSignal, _trend_score, vwap_with_bands)

PERP = AssetMeta("BTC", sz_decimals=5, is_spot=False)          # max 1 price decimal
ALT = AssetMeta("REQ", sz_decimals=1, is_spot=False)           # max 5 price decimals
SPOT = AssetMeta("PURR/USDC", sz_decimals=2, is_spot=True)     # max 6 price decimals


def _cfg(tmp: Path) -> SystemConfig:
    return SystemConfig(
        api=ApiConfig(secret_key="0x" + "1" * 64, account_address="0x" + "a" * 40),
        risk=RiskConfig(), signals=SignalConfig(), execution=ExecConfig(),
        state_path=tmp / "state.json", log_path=tmp / "t.log", dry_run=True,
    )


# ------------------------------------------------------- quantisation ------


def test_rounding() -> None:
    assert PERP.max_price_decimals == 1
    assert ALT.max_price_decimals == 5
    assert SPOT.max_price_decimals == 6

    # 5 significant figures is the binding rule at high prices
    assert round_price(64321.789, PERP, is_buy=True) == 64322.0 or \
           round_price(64321.789, PERP, is_buy=True) == 64321.0
    p = round_price(1234.5678, PERP, is_buy=True)
    assert p <= 1234.5678 and len(f"{p:g}".replace(".", "").lstrip("0")) <= 5, p

    # directional: a passive buy never rounds UP (would risk crossing)
    assert round_price(100.6789, ALT, is_buy=True) <= 100.6789
    assert round_price(100.6789, ALT, is_buy=False) >= 100.6789

    # decimal cap respected
    assert round_price(0.0123456789, ALT, is_buy=True) == round(
        round_price(0.0123456789, ALT, is_buy=True), ALT.max_price_decimals)

    # every output must survive float_to_wire (the SDK raises otherwise)
    from hyperliquid.utils.signing import float_to_wire
    for px in (0.00012345, 1.23456, 99.9999, 1234.5, 64321.7, 105_000.4):
        for buy in (True, False):
            float_to_wire(round_price(px, ALT, buy))
            float_to_wire(round_price(px, PERP, buy))

    # sizes round DOWN so a cap is never exceeded
    assert round_size(1.999999, PERP) <= 1.999999
    assert round_size(1.28, ALT) == 1.2
    assert round_size(0.004, ALT) == 0.0
    print("  ok  rounding / quantisation")


def test_tick_and_passive_price() -> None:
    assert _tick_for(64000.0, PERP) == 10.0 ** (4 - 4)      # 5 sig figs -> 1.0
    assert _tick_for(0.5, ALT) == 10.0 ** -5

    book = OrderBook(ts=0,
                     bids=[BookLevel(100.0, 5, 1), BookLevel(99.9, 5, 1)],
                     asks=[BookLevel(100.1, 5, 1), BookLevel(100.2, 5, 1)])
    bid_px = passive_price(book, is_buy=True, meta=ALT, offset_ticks=0)
    ask_px = passive_price(book, is_buy=False, meta=ALT, offset_ticks=0)
    assert bid_px is not None and ask_px is not None
    # A post-only order must never sit on the wrong side of the spread.
    assert bid_px < book.asks[0].px, (bid_px, book.asks[0].px)
    assert ask_px > book.bids[0].px, (ask_px, book.bids[0].px)
    # stepping away makes it strictly more passive
    deeper = passive_price(book, True, ALT, offset_ticks=5)
    assert deeper < bid_px
    assert passive_price(OrderBook(0, [], []), True, ALT) is None
    print("  ok  tick size / passive price never crosses")


# ------------------------------------------------------------- ATR ---------


def test_atr_uses_true_range() -> None:
    # A gapping series: close-to-close understates range, true range does not.
    candles = []
    close = 100.0
    for i in range(30):
        gap = 5.0 if i % 2 else -5.0
        o = close + gap
        c = Candle(ts=i * 86400000, open=o, high=o + 2, low=o - 2, close=o, volume=1.0)
        candles.append(c)
        close = c.close
    a = atr(candles, 14)
    assert a is not None
    close_to_close = sum(abs(candles[i].close - candles[i - 1].close)
                         for i in range(1, 15)) / 14
    # true range includes the gap AND the bar's own range, so it must exceed
    # a naive |close - prev_close| average here
    assert a > close_to_close, (a, close_to_close)
    assert atr(candles[:5], 14) is None        # refuses to guess on short history
    print(f"  ok  ATR true-range ({a:.3f} > close-to-close {close_to_close:.3f})")


# ------------------------------------------------------ risk machine -------


def test_drawdown_tiers() -> None:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        cfg = _cfg(tmp)
        rm = RiskManager(cfg, cfg.state_path)

        rm.update(10_000.0)
        assert rm.state is RiskState.NORMAL and rm.peak_equity == 10_000.0
        assert rm.size_multiplier == 1.0

        # -3.5% in one step: no drawdown TIER yet, but the daily gate must fire
        rm.update(9_650.0)
        assert rm.state is RiskState.FROZEN, rm.state
        assert not rm.can_open(0.0, 0)[0]
        rm.frozen_until = 0.0                    # clear it to test the tiers alone

        rm.update(9_580.0)                       # -4.2% -> REDUCED
        assert rm.state is RiskState.REDUCED, rm.state
        assert rm.size_multiplier == 0.5

        # With no daily freeze active, REDUCED still trades — at half size.
        rm.frozen_until = 0.0
        ok, why = rm.can_open(0.0, 0)
        assert ok, why

        # ...but an active freeze must still block, even though the tier state
        # says REDUCED. These are orthogonal gates; an earlier version stored
        # both in `state` and crossing the 4% tier silently cleared the freeze.
        rm.frozen_until = time.time() + 3600
        assert rm.state is RiskState.REDUCED
        assert not rm.can_open(0.0, 0)[0], \
            "tier state must not cancel an active daily freeze"
        rm.frozen_until = 0.0

        rm.update(9_510.0)                       # -4.9% -> LIQUIDATING
        assert rm.state is RiskState.LIQUIDATING, rm.state
        assert rm.should_flatten
        assert not rm.can_open(0.0, 0)[0]

        rm.update(9_400.0)                       # -6.0% -> HARD LOCK
        assert rm.state is RiskState.HALTED and rm.hard_locked
        assert rm.size_multiplier == 0.0
        assert not rm.can_open(0.0, 0)[0]

        # the lock must survive a restart, even if equity fully recovers
        rm2 = RiskManager(cfg, cfg.state_path)
        assert rm2.hard_locked and rm2.state is RiskState.HALTED
        rm2.update(20_000.0)
        assert rm2.state is RiskState.HALTED, "hard lock must not be undone by recovery"
        assert not rm2.can_open(0.0, 0)[0]
    print("  ok  drawdown tiers 4.0 / 4.8 / 5.0 + lock persists restart")


def test_daily_freeze_and_gates() -> None:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        cfg = _cfg(tmp)
        rm = RiskManager(cfg, cfg.state_path)
        rm.update(10_000.0)
        rm._equity_history[0] = (time.time() - 3600, 10_000.0)
        rm.update(9_820.0)                        # -1.8% on the day
        assert rm.state is RiskState.FROZEN, rm.state
        ok, why = rm.can_open(0.0, 0)
        assert not ok and "freeze" in why, why

        # gates that must fail closed regardless of drawdown
        rm.frozen_until = 0.0
        rm.state = RiskState.NORMAL
        assert not rm.can_open(1.0, 0)[0], "gross exposure at cap must block"
        assert not rm.can_open(0.0, cfg.risk.max_concurrent_positions)[0]
    print("  ok  daily-loss freeze + exposure/position gates")


def test_sizing() -> None:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        cfg = _cfg(tmp)
        rm = RiskManager(cfg, cfg.state_path)
        rm.update(10_000.0)

        daily = [Candle(i * 86400000, 100, 102, 98, 100, 1.0) for i in range(40)]
        a = atr(daily, 14)
        assert a is not None

        size, why = rm.position_size("SOL", 100.0, daily, ALT, conviction=1.0)
        assert size > 0, why
        # alt cap: 3% of 10k = $300 => 3 units at $100
        assert size * 100.0 <= 10_000 * cfg.risk.max_exposure_alt + 1e-9, why

        big, _ = rm.position_size("BTC", 100.0, daily, PERP, conviction=1.0)
        assert big * 100.0 <= 10_000 * cfg.risk.max_exposure_major + 1e-9
        assert big > size, "majors get a larger cap than alts"

        half, _ = rm.position_size("SOL", 100.0, daily, ALT, conviction=0.5)
        assert abs(half - size * 0.5) < 1e-9, "conviction must scale linearly"

        rm.state = RiskState.REDUCED
        red, _ = rm.position_size("SOL", 100.0, daily, ALT, conviction=1.0)
        assert abs(red - size * 0.5) < 1e-9, "REDUCED must halve size"

        rm.state = RiskState.HALTED
        assert rm.position_size("SOL", 100.0, daily, ALT)[0] == 0.0

        rm.state = RiskState.NORMAL
        assert rm.position_size("SOL", 100.0, daily[:3], ALT)[0] == 0.0  # no ATR
    print("  ok  volatility-parity sizing + caps + reduction")


# ---------------------------------------------------------- signals --------


class _FakeData:
    def __init__(self):
        self.trades, self.books, self.candles, self.funding = {}, {}, {}, {}
        self.assets, self.mids = {}, {}


def test_vwap_bands() -> None:
    flat = [Candle(i, 100, 100, 100, 100, 10.0) for i in range(50)]
    v, lo, hi = vwap_with_bands(flat, 2.0)
    assert abs(v - 100) < 1e-9 and abs(lo - 100) < 1e-9   # zero variance -> zero band
    mixed = [Candle(i, 100, 110, 90, 100, 10.0) for i in range(25)] + \
            [Candle(i, 200, 210, 190, 200, 10.0) for i in range(25, 50)]
    v2, lo2, hi2 = vwap_with_bands(mixed, 2.0)
    assert lo2 < v2 < hi2 and 140 < v2 < 160, v2
    assert vwap_with_bands([], 2.0) == (0.0, 0.0, 0.0)
    print(f"  ok  VWAP bands (vwap={v2:.1f}, -2s={lo2:.1f}, +2s={hi2:.1f})")


def test_module_b_zscore() -> None:
    cfg = SignalConfig()
    m = FundingZScoreSignal(cfg)
    assert m.zscore([0.0001] * 10) is None, "must refuse a partial window"
    assert m.zscore([0.0001] * 100) is None, "zero variance -> no score"

    import random
    random.seed(7)
    base = [random.gauss(0.0001, 0.00002) for _ in range(80)]
    z_low = m.zscore(base + [0.0001 - 5 * 0.00002])
    z_high = m.zscore(base + [0.0001 + 5 * 0.00002])
    assert z_low is not None and z_low < -2.5, z_low
    assert z_high is not None and z_high > 2.5, z_high

    d = _FakeData()
    d.funding["X"] = [(i * 3600000, r) for i, r in enumerate(base + [0.0001 - 5 * 0.00002])]
    s = m.evaluate("X", d)
    assert s is not None and s.direction is Direction.LONG, s
    d.funding["X"] = [(i * 3600000, r) for i, r in enumerate(base + [0.0001 + 5 * 0.00002])]
    s = m.evaluate("X", d)
    assert s is not None and s.direction is Direction.EXIT, s
    print(f"  ok  Module B z-score (long at {z_low:.2f}, exit at {z_high:.2f})")


def test_module_a_cvd() -> None:
    cfg = SignalConfig()
    m = MicrostructureSignal(cfg)
    d = _FakeData()

    # price grinding DOWN while aggressive BUYING accumulates = absorption
    trades, t0 = [], 1_700_000_000_000
    step = 900_000
    for b in range(20):
        px = 100.0 - b * 0.1
        for k in range(6):
            trades.append(Trade(t0 + b * step + k * 1000, px, 1.0, "B"))
        trades.append(Trade(t0 + b * step + 7000, px, 0.4, "A"))
    d.trades["X"] = trades
    # a bid wall: bid depth >> ask depth
    d.books["X"] = OrderBook(
        ts=t0, bids=[BookLevel(99.0, 100, 5)] * 10, asks=[BookLevel(99.1, 5, 2)] * 10)

    s = m.evaluate("X", d)
    assert s is not None and s.direction is Direction.LONG, s
    assert s.meta["cvd_z"] > 0 and s.meta["px_z"] < 0, s.meta

    # same tape, no wall -> no signal (the wall is what makes it a signal)
    d.books["X"] = OrderBook(ts=t0, bids=[BookLevel(99.0, 10, 5)] * 10,
                             asks=[BookLevel(99.1, 10, 2)] * 10)
    assert m.evaluate("X", d) is None
    print(f"  ok  Module A CVD divergence at wall (cvd_z={s.meta['cvd_z']:+.2f})")


def test_module_c_vwap_touch() -> None:
    cfg = SignalConfig()
    m = VwapReversionSignal(cfg)
    d = _FakeData()

    bars = [Candle(i * 3600000, 100, 101, 99, 100, 10.0) for i in range(200)]
    # final bar: deep flush below the lower band on 5x volume
    bars.append(Candle(200 * 3600000, 100, 100, 88, 90, 60.0))
    d.candles[("X", cfg.c_interval)] = bars
    d.candles[("X", cfg.c_confirm_interval)] = [
        Candle(i * 14400000, 100, 101, 99, 100, 10.0) for i in range(20)]

    s = m.evaluate("X", d)
    assert s is not None and s.direction is Direction.LONG, s
    assert s.meta["target"] > 90, s.meta
    assert s.meta["vol_ratio"] >= cfg.c_volume_expansion

    # identical touch, but on NORMAL volume -> no entry
    bars[-1] = Candle(200 * 3600000, 100, 100, 88, 90, 10.0)
    d.candles[("X", cfg.c_interval)] = bars
    assert m.evaluate("X", d) is None
    print(f"  ok  Module C -2sigma touch + volume expansion "
          f"(target={s.meta['target']:.2f})")


def test_trend_score_edges() -> None:
    assert _trend_score([]) == 0.0
    assert _trend_score([1.0, 2.0]) == 0.0
    assert _trend_score([5.0] * 20) == 0.0            # flat: no trend, no blow-up
    assert _trend_score(list(range(20))) > 0
    assert _trend_score(list(range(20))[::-1]) < 0
    print("  ok  slope z-score edge cases")


def test_leverage_invariant() -> None:
    ok = False
    try:
        RiskConfig(max_gross_exposure=1.5)
    except ValueError:
        ok = True
    assert ok, "max_gross_exposure > 1.0 must be rejected (leverage)"

    ok = False
    try:
        RiskConfig(dd_reduce_pct=4.9, dd_liquidate_pct=4.8, dd_hard_lock_pct=5.0)
    except ValueError:
        ok = True
    assert ok, "non-monotonic drawdown tiers must be rejected"

    ok = False
    try:
        SystemConfig(api=ApiConfig("0x1", "0xa"), execution=ExecConfig(leverage=3))
    except ValueError:
        ok = True
    assert ok, "leverage != 1 must be rejected"
    print("  ok  zero-leverage + tier-ordering invariants enforced")


def main() -> int:
    print("hl_system self-check")
    tests = [test_rounding, test_tick_and_passive_price, test_atr_uses_true_range,
             test_drawdown_tiers, test_daily_freeze_and_gates, test_sizing,
             test_vwap_bands, test_module_b_zscore, test_module_a_cvd,
             test_module_c_vwap_touch, test_trend_score_edges,
             test_leverage_invariant]
    failed = 0
    for t in tests:
        try:
            t()
        except AssertionError as e:
            print(f"  FAIL {t.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"  ERROR {t.__name__}: {type(e).__name__}: {e}")
            failed += 1
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

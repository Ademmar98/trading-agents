"""Fuzz tests: run every strategy against random OHLC data to catch edge cases."""
import random
from core.strategies import ALL_STRATEGIES, scan_symbol


def _random_ohlc(length: int, vol_max: float = 1000.0) -> list:
    price = 100.0
    ohlc = []
    for _ in range(length):
        change = random.gauss(0, 2)
        close = max(price + change, 1.0)
        high = max(close, price) * (1 + random.random() * 0.05)
        low = min(close, price) * (1 - random.random() * 0.05)
        ohlc.append({
            "open": price,
            "high": high,
            "low": low,
            "close": close,
            "volume": random.random() * vol_max,
            "ts": 0,
        })
        price = close
    return ohlc


def _random_constant_ohlc(length: int) -> list:
    price = 100.0
    ohlc = []
    for _ in range(length):
        ohlc.append({
            "open": price, "high": price + 0.01, "low": price - 0.01, "close": price,
            "volume": 100, "ts": 0,
        })
    return ohlc


def _random_spiky_ohlc(length: int) -> list:
    price = 100.0
    ohlc = []
    for _ in range(length):
        spike = random.choice([-50, -20, -10, 10, 20, 50])
        close = max(price + spike, 0.5)
        ohlc.append({
            "open": price, "high": max(price, close) * 1.1, "low": min(price, close) * 0.9,
            "close": close, "volume": random.random() * 10000, "ts": 0,
        })
        price = close
    return ohlc


def _random_zerodiv_ohlc(length: int) -> list:
    return [{"open": 0, "high": 0, "low": 0, "close": 0, "volume": 0, "ts": 0} for _ in range(length)]


def test_all_strategies_random_data():
    ohlc = _random_ohlc(150)
    for name, func in ALL_STRATEGIES:
        try:
            result = func(ohlc)
        except Exception as e:
            raise AssertionError(f"{name} crashed on random data: {e}")


def test_all_strategies_constant_data():
    ohlc = _random_constant_ohlc(100)
    for name, func in ALL_STRATEGIES:
        try:
            func(ohlc)
        except ZeroDivisionError:
            raise AssertionError(f"{name} ZeroDivisionError on constant data")
        except Exception:
            pass


def test_all_strategies_spiky_data():
    ohlc = _random_spiky_ohlc(150)
    for name, func in ALL_STRATEGIES:
        try:
            func(ohlc)
        except Exception as e:
            raise AssertionError(f"{name} crashed on spiky data: {e}")


def test_all_strategies_zero_data():
    ohlc = _random_zerodiv_ohlc(60)
    for name, func in ALL_STRATEGIES:
        try:
            func(ohlc)
        except ZeroDivisionError:
            raise AssertionError(f"{name} ZeroDivisionError on zero data")
        except Exception:
            pass


def test_all_strategies_short_data():
    for name, func in ALL_STRATEGIES:
        try:
            func([])
        except Exception:
            pass


def test_scan_symbol_various_lengths():
    for length in [0, 1, 5, 10, 20, 30, 50, 100]:
        ohlc = _random_ohlc(length)
        try:
            scan_symbol(ohlc)
        except Exception as e:
            raise AssertionError(f"scan_symbol crashed with {length} candles: {e}")


def test_scan_symbol_with_regime():
    ohlc = _random_ohlc(100)
    for regime in ["trending_up", "trending_down", "ranging", "volatile", None]:
        try:
            scan_symbol(ohlc, regime=regime)
        except Exception as e:
            raise AssertionError(f"scan_symbol crashed with regime={regime}: {e}")

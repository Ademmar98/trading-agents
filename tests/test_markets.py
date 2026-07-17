"""Crypto-only firm: symbol classification, 24/7 market hours, the crypto
watchlist, and SL/TP trigger coverage from scan prices when a symbol is
absent from the websocket feed.

Stocks/metals/forex were removed 2026-07-14 — this file asserts the
crypto-only behavior that replaced them.
"""
import logging
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import config as app_config
import core.data_provider as dp
from core.market import classify_symbol, is_market_open
from core.database import init_db, execute
from core.memory import SharedMemory
from core.portfolio import Portfolio, save_portfolio, load_portfolio


def _age_open_positions(seconds=3600):
    """Backdate opened_at so the MIN_HOLD_BARS minimum-hold guard (no exits
    less than 1 bar after entry) does not block the trigger checks below.
    3600s >> one 5m bar. Same pattern as tests/test_exits.py."""
    past = (datetime.now(timezone.utc) - timedelta(seconds=seconds)).strftime("%Y-%m-%d %H:%M:%S")
    execute("UPDATE positions SET opened_at=? WHERE status='open'", [past])


@pytest.fixture(autouse=True)
def sandbox_data_dir(monkeypatch):
    tmp = Path(tempfile.mkdtemp(prefix="trading-test-"))
    monkeypatch.setattr(app_config, "DATA_DIR", tmp)
    monkeypatch.setenv("TRADING_DATA_DIR", str(tmp))
    init_db()
    yield
    import shutil
    shutil.rmtree(str(tmp), ignore_errors=True)


def test_everything_classifies_as_crypto():
    assert classify_symbol("BTC/USD") == "crypto"
    assert classify_symbol("ETH/USD") == "crypto"
    # even a stray legacy ticker collapses to the single crypto cluster
    assert classify_symbol("AAPL") == "crypto"


def test_crypto_always_open():
    saturday = datetime(2026, 7, 11, 15, 0, tzinfo=timezone.utc)
    wed_night = datetime(2026, 7, 8, 3, 0, tzinfo=timezone.utc)
    assert is_market_open("BTC/USD", saturday)
    assert is_market_open("BTC/USD", wed_night)
    assert is_market_open("ETH/USD")  # no time arg -> now, still open


def test_watchlist_is_crypto_only():
    assert all("/" in s for s in app_config.WATCHED_SYMBOLS)
    assert "BTC/USD" in app_config.WATCHED_SYMBOLS
    assert "AAPL" not in app_config.WATCHED_SYMBOLS
    assert "XAUUSD" not in app_config.WATCHED_SYMBOLS
    assert app_config.MARKET_TYPE == "crypto"


def test_no_stock_metal_correlation_groups():
    assert set(app_config.CORRELATION_GROUPS) == {"crypto_alts", "crypto_majors"}
    assert list(app_config.MACRO_BELLWETHERS) == ["crypto"]


def test_trigger_check_uses_market_scan_when_ws_silent(monkeypatch):
    """A position whose symbol isn't on the websocket feed must still hit its
    SL from the market-scan prices merged in by process_price_triggers."""
    import main
    from core.broker import PaperBroker
    from core.notifier import Notifier

    main.memory = SharedMemory()
    monkeypatch.setattr(main, "notifier", Notifier("", ""))
    save_portfolio(Portfolio(cash=10000.0, initial_balance=10000.0))

    broker = PaperBroker()
    order = broker.place_order("TRX/USD", "BUY", 5.0, 200.0, sl=192.0, tp=220.0)
    assert order["status"] == "filled"
    main.pos_mgr.open_position("TRX/USD", "BUY", 5.0, 200.0, sl=192.0, tp=220.0)
    _age_open_positions()

    main.memory.write("analyses", "market_scan", {
        "all_analyses": {"TRX/USD": {"price": 190.0}},
        "timestamp": time.time(),
    })

    # Empty websocket prices: the merge from market_scan must still trigger the SL
    triggered = main.process_price_triggers({})
    assert len(triggered) == 1
    assert triggered[0]["symbol"] == "TRX/USD"
    assert triggered[0]["reason"] == "stop_loss"
    p = load_portfolio()
    assert "TRX/USD" not in p.positions
    fee = app_config.TRADE_FEE_PCT / 100.0
    # Slippage model (both layers adverse): entry fills at quote x (1+slip);
    # the stop-market exit fills at min(price, SL) x (1-slip) in the ledger
    # and the broker's exit order slips once more on top of that price.
    from core.broker import SLIPPAGE_PCT
    slip = SLIPPAGE_PCT / 100.0
    buy_cost = 5.0 * 200.0 * (1 + slip)
    exit_fill = 190.0 * (1 - slip) * (1 - slip)
    exit_proceeds = 5.0 * exit_fill
    expected = 10000.0 - buy_cost * (1 + fee) + exit_proceeds * (1 - fee)
    assert p.cash == pytest.approx(expected)


# --- Data integrity: closed bars only, real depth, gap detection, 'ts' ----
# Regression coverage for the fetch-level audit fixes in core/data_provider.


class _Resp:
    def __init__(self, data):
        self._data = data

    def json(self):
        return self._data


def _binance_kline(open_ms, close_ms, price=100.0):
    return [open_ms, str(price), str(price + 1), str(price - 1), str(price),
            "12.5", close_ms, "0", 0, "0", "0", "0"]


class TestClosedBarsOnly:
    def test_binance_drops_forming_bar(self, monkeypatch):
        day = 86_400_000
        now_ms = int(time.time() * 1000)
        last_open = (now_ms // day) * day - day  # open of last CLOSED daily
        klines = [_binance_kline(last_open - i * day, last_open - i * day + day - 1)
                  for i in (2, 1, 0)]
        klines.append(_binance_kline(last_open + day, last_open + 2 * day - 1))  # forming
        monkeypatch.setattr(dp.requests, "get", lambda *a, **k: _Resp(klines))
        bars = dp.fetch_binance_klines("BTC/USD", "1d", 10)
        assert len(bars) == 3
        assert all(b["ts"] + 86400 <= int(time.time()) for b in bars)
        assert bars[-1]["ts"] == last_open // 1000

    def test_cryptocom_drops_forming_bar(self, monkeypatch):
        day = 86_400_000
        now_ms = int(time.time() * 1000)
        last_open = (now_ms // day) * day - day
        data = [{"t": last_open - i * day, "o": "1", "h": "2", "l": "0.5",
                 "c": "1.5", "v": "10"} for i in (2, 1, 0)]
        data.append({"t": last_open + day, "o": "1", "h": "2", "l": "0.5",
                     "c": "1.5", "v": "10"})  # forming daily
        monkeypatch.setattr(dp.requests, "get",
                            lambda *a, **k: _Resp({"result": {"data": data}}))
        bars = dp.fetch_cryptocom_ohlc("BTC/USD", "1d", 10)
        assert len(bars) == 3
        assert all(b["ts"] + 86400 <= int(time.time()) for b in bars)


class TestPaginationDepth:
    def test_binance_paginates_past_1000_bar_cap(self, monkeypatch):
        hour = 3_600_000
        now_ms = int(time.time() * 1000)
        last_open = (now_ms // hour) * hour - hour  # last CLOSED hourly
        opens = [last_open - (1499 - i) * hour for i in range(1500)]
        all_klines = [_binance_kline(o, o + hour - 1) for o in opens]
        calls = []

        def fake_get(url, params=None, **kw):
            calls.append(dict(params))
            limit = params.get("limit", 500)
            end = params.get("endTime")
            page = [k for k in all_klines if end is None or k[0] <= end]
            return _Resp(page[-limit:])

        monkeypatch.setattr(dp.requests, "get", fake_get)
        bars = dp.fetch_binance_klines("BTC/USD", "1h", 1500)
        assert len(calls) == 2  # 1000 + 500
        assert len(bars) == 1500
        assert bars[0]["ts"] == opens[0] // 1000
        assert bars[-1]["ts"] == last_open // 1000
        # strictly ascending, contiguous, no duplicates
        tss = [b["ts"] for b in bars]
        assert tss == sorted(set(tss))

    def test_cryptocom_paginates_past_300_bar_cap(self, monkeypatch):
        hour = 3_600_000
        now_ms = int(time.time() * 1000)
        last_open = (now_ms // hour) * hour - hour
        opens = [last_open - (799 - i) * hour for i in range(800)]
        all_bars = [{"t": o, "o": "1", "h": "2", "l": "0.5", "c": "1.5", "v": "10"}
                    for o in opens]
        calls = []

        def fake_get(url, params=None, **kw):
            calls.append(dict(params))
            count = params.get("count", 300)
            end = params.get("end_ts")
            page = [b for b in all_bars if end is None or b["t"] <= end]
            return _Resp({"result": {"data": page[-count:]}})

        monkeypatch.setattr(dp.requests, "get", fake_get)
        bars = dp.fetch_cryptocom_ohlc("BTC/USD", "1h", 800)
        assert len(calls) == 3  # 300 + 300 + 200
        assert len(bars) == 800
        assert bars[0]["ts"] == opens[0] // 1000
        assert bars[-1]["ts"] == last_open // 1000


class TestGapDetectionAndTs:
    def test_gap_between_bars_logs_warning(self, monkeypatch, caplog):
        day = 86_400_000
        base = ((int(time.time()) // 86400) - 10) * 86400 * 1000  # well past, aligned
        opens = [base, base + day, base + 3 * day]  # one daily bar missing
        klines = [_binance_kline(o, o + day - 1) for o in opens]
        monkeypatch.setattr(dp.requests, "get", lambda *a, **k: _Resp(klines))
        with caplog.at_level(logging.WARNING, logger="data_provider"):
            bars = dp.fetch_binance_klines("BTC/USD", "1d", 10)
        assert len(bars) == 3
        assert any("gap" in r.getMessage().lower() for r in caplog.records)

    def test_contiguous_bars_log_no_gap_warning(self, monkeypatch, caplog):
        day = 86_400_000
        base = ((int(time.time()) // 86400) - 10) * 86400 * 1000
        opens = [base + i * day for i in range(4)]
        klines = [_binance_kline(o, o + day - 1) for o in opens]
        monkeypatch.setattr(dp.requests, "get", lambda *a, **k: _Resp(klines))
        with caplog.at_level(logging.WARNING, logger="data_provider"):
            bars = dp.fetch_binance_klines("BTC/USD", "1d", 10)
        assert len(bars) == 4
        assert not any("gap" in r.getMessage().lower() for r in caplog.records)

    def test_every_candle_carries_unix_ts(self, monkeypatch):
        day = 86_400_000
        base = ((int(time.time()) // 86400) - 10) * 86400 * 1000
        klines = [_binance_kline(base + i * day, base + (i + 1) * day - 1)
                  for i in range(3)]
        monkeypatch.setattr(dp.requests, "get", lambda *a, **k: _Resp(klines))
        bars = dp.fetch_binance_klines("BTC/USD", "1d", 10)
        assert all(isinstance(b["ts"], int) and b["ts"] > 0 for b in bars)
        # ts must match the ISO date field (session strategies rely on it)
        for b in bars:
            assert datetime.fromtimestamp(b["ts"], tz=timezone.utc).isoformat() == b["date"]

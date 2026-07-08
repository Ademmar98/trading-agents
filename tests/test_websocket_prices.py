import time
from unittest.mock import patch, MagicMock

from core.websocket_prices import (
    _to_binance_symbol, update_price, get_price, get_all_prices,
    start, stop, LIVE_PRICES,
)
import core.websocket_prices as wsp


class TestToBinanceSymbol:
    def test_usd_gets_usdt(self):
        assert _to_binance_symbol("BTC/USD") == "BTCUSDT"
        assert _to_binance_symbol("SOL/USD") == "SOLUSDT"

    def test_usdt_stays_usdt(self):
        assert _to_binance_symbol("ETH/USDT") == "ETHUSDT"

    def test_handles_upper(self):
        assert _to_binance_symbol("btc/usd") == "BTCUSDT"


class TestUpdatePrice:
    def test_sets_values(self):
        update_price("TEST/USD", 100.0, bid=99.5, ask=100.5, change=1.0, volume=1000)
        entry = get_price("TEST/USD")
        assert entry is not None
        assert entry["price"] == 100.0
        assert entry["bid"] == 99.5
        assert entry["ask"] == 100.5
        assert entry["change_24h"] == 1.0
        assert entry["volume_24h"] == 1000
        assert entry["type"] == "crypto"

    def test_overwrites(self):
        update_price("OVERWRITE/USD", 100.0)
        update_price("OVERWRITE/USD", 200.0)
        entry = get_price("OVERWRITE/USD")
        assert entry["price"] == 200.0

    def test_does_not_affect_others(self):
        update_price("AAA/USD", 10.0)
        entry = get_price("BBB/USD")
        assert entry is None


class TestGetPrice:
    def test_returns_none_for_missing(self):
        assert get_price("NONEXISTENT/USD") is None


class TestGetAllPrices:
    def test_returns_all(self):
        all_p = get_all_prices()
        assert isinstance(all_p, dict)
        assert "AAA/USD" in all_p


class TestStartStop:
    def test_start_sets_running(self):
        stop()
        assert wsp._running is False
        with patch("core.websocket_prices._poll_loop"):
            start()
            assert wsp._running is True
            stop()
            assert wsp._running is False

    def test_start_idempotent(self):
        with patch("core.websocket_prices._poll_loop"):
            start()
            start()
            stop()


class TestPollLoop:
    def test_polls_and_updates(self):
        stop()
        ticker_data = [
            {"symbol": "BTCUSDT", "lastPrice": "50000", "bidPrice": "49900",
             "askPrice": "50100", "priceChangePercent": "1.5", "quoteVolume": "1000000"},
        ]
        with patch("core.websocket_prices.WATCHED_SYMBOLS", ["BTC/USD"]):
            with patch("requests.get") as mock_get:
                mock_get.return_value.json.return_value = ticker_data
                mock_get.return_value.__enter__.return_value = mock_get.return_value
                wsp._running = True
                import threading
                t = threading.Thread(target=wsp._poll_loop, daemon=True)
                t.start()
                import time
                time.sleep(0.5)
                wsp._running = False
                t.join(timeout=2)
                price = get_price("BTC/USD")
                assert price is not None
                assert price["price"] == 50000.0

    def test_exception_swallowed(self):
        stop()
        with patch("core.websocket_prices.WATCHED_SYMBOLS", ["BTC/USD"]):
            with patch("requests.get", side_effect=Exception("fail")):
                wsp._running = True
                import threading
                t = threading.Thread(target=wsp._poll_loop, daemon=True)
                t.start()
                import time
                time.sleep(0.5)
                wsp._running = False
                t.join(timeout=2)

    def test_not_list_response(self):
        stop()
        with patch("core.websocket_prices.WATCHED_SYMBOLS", ["BTC/USD"]):
            with patch("requests.get") as mock_get:
                mock_get.return_value.json.return_value = {"error": True}
                wsp._running = True
                import threading
                t = threading.Thread(target=wsp._poll_loop, daemon=True)
                t.start()
                import time
                time.sleep(0.5)
                wsp._running = False
                t.join(timeout=2)

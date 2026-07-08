import time
from unittest.mock import MagicMock, patch

from core.price_stream import _update_price, get_latest_prices


class TestUpdatePrice:
    def test_updates_memory(self):
        mem = MagicMock()
        mem.read.return_value = None

        with patch("core.price_stream.SharedMemory", return_value=mem):
            _update_price("BTCUSDT", 50000.0, 1700000000000)

            write_calls = mem.write.call_args_list
            assert len(write_calls) >= 1
            prefix, key, data = write_calls[-1][0]
            assert prefix == "reports"
            assert key == "price_stream"
            assert "BTC/USD" in data["prices"]
            assert data["prices"]["BTC/USD"]["price"] == 50000.0

    def test_merges_existing(self):
        mem = MagicMock()
        existing = {"prices": {"ETH/USD": {"price": 3000, "timestamp": 100}}, "_updated": 100}
        mem.read.return_value = existing

        with patch("core.price_stream.SharedMemory", return_value=mem):
            _update_price("BTCUSDT", 50000.0, 1700000000000)

            write_calls = mem.write.call_args_list
            data = write_calls[-1][0][2]
            assert "BTC/USD" in data["prices"]
            assert "ETH/USD" in data["prices"]

    def test_handles_usdt_suffix(self):
        mem = MagicMock()
        mem.read.return_value = None

        with patch("core.price_stream.SharedMemory", return_value=mem):
            _update_price("SOLUSDT", 150.0, 1700000000000)
            write_calls = mem.write.call_args_list
            data = write_calls[-1][0][2]
            assert "SOL/USD" in data["prices"]


class TestGetLatestPrices:
    def test_returns_empty_when_no_data(self):
        mem = MagicMock()
        mem.read.return_value = None

        with patch("core.price_stream.SharedMemory", return_value=mem):
            result = get_latest_prices()
            assert result == {}

    def test_returns_prices_when_fresh(self):
        mem = MagicMock()
        mem.read.return_value = {
            "prices": {"BTC/USD": {"price": 50000, "timestamp": time.time()}},
            "_updated": time.time(),
        }

        with patch("core.price_stream.SharedMemory", return_value=mem):
            result = get_latest_prices()
            assert "BTC/USD" in result
            assert result["BTC/USD"]["price"] == 50000

    def test_returns_empty_when_stale(self):
        mem = MagicMock()
        mem.read.return_value = {
            "prices": {"BTC/USD": {"price": 50000, "timestamp": 0}},
            "_updated": time.time() - 300,
        }

        with patch("core.price_stream.SharedMemory", return_value=mem):
            result = get_latest_prices()
            assert result == {}


class TestStartStop:
    def test_start_sets_running(self):
        import core.price_stream as ps

        ps.stop()

        with patch("core.price_stream._binance_stream"):
            ps.start(["BTC/USD"])
            assert ps._RUNNING is True
            ps.stop()
            assert ps._RUNNING is False

    def test_stop_clears_running(self):
        import core.price_stream as ps

        ps.stop()
        assert ps._RUNNING is False

    def test_start_stop_doesnt_crash(self):
        import core.price_stream as ps

        ps.stop()
        with patch("core.price_stream._binance_stream"):
            ps.start([])
            ps.stop()

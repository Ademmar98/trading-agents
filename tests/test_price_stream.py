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

    def test_start_idempotent(self):
        import core.price_stream as ps
        ps.stop()
        with patch("core.price_stream._binance_stream"):
            ps.start(["BTC/USD"])
            ps.start(["BTC/USD"])
            ps.stop()
            assert ps._RUNNING is False


class TestBinanceStream:
    def test_no_websockets_import(self):
        import core.price_stream as ps
        import builtins as bltn
        ps.stop()
        real_import = bltn.__import__
        def _mock_import(name, *args, **kwargs):
            if name == "websockets":
                raise ImportError
            return real_import(name, *args, **kwargs)
        with patch("builtins.__import__", side_effect=_mock_import):
            ps._binance_stream(["BTC/USD"])

    def test_too_many_symbols(self):
        import core.price_stream as ps
        ps.stop()
        ps._binance_stream([f"SYM{i}/USD" for i in range(300)])

    def test_start_stops_thread(self):
        import core.price_stream as ps
        ps.stop()
        ps._RUNNING = False
        with patch("core.price_stream._binance_stream"):
            ps.start(["BTC/USD"])
            assert ps._STREAM_THREAD is not None
            ps.stop()
            ps._STREAM_THREAD.join(timeout=2)

import os
import tempfile
import json
import time
from pathlib import Path

import pytest

from core.memory import SharedMemory


def _clear_memory(mem):
    for d in mem.dirs.values():
        for f in d.glob("*"):
            f.unlink()


class TestSharedMemory:
    def test_write_and_read(self):
        mem = SharedMemory()
        _clear_memory(mem)
        mem.write("analyses", "test_analysis", {"price": 50000, "signal": "BUY"})
        result = mem.read("analyses", "test_analysis")
        assert result is not None
        assert result["price"] == 50000
        assert "_timestamp" in result

    def test_read_nonexistent(self):
        mem = SharedMemory()
        assert mem.read("analyses", "nonexistent") is None

    def test_read_latest(self):
        mem = SharedMemory()
        _clear_memory(mem)
        mem.write("analyses", "first", {"seq": 1})
        time.sleep(0.01)
        mem.write("analyses", "second", {"seq": 2})
        latest = mem.read_latest("analyses")
        assert latest is not None
        assert latest["seq"] == 2

    def test_read_latest_returns_none_when_empty(self):
        mem = SharedMemory()
        _clear_memory(mem)
        result = mem.read("reports", "nonexistent")
        assert result is None

    def test_log_and_get_recent(self):
        mem = SharedMemory()
        _clear_memory(mem)
        mem.log("analyst", "started analysis")
        mem.log("trader", "executed order")
        logs = mem.get_recent_logs(10)
        assert len(logs) >= 2
        assert logs[-1]["agent"] == "trader"

    def test_log_error(self):
        mem = SharedMemory()
        _clear_memory(mem)
        mem.log_error("system", "connection failed", "traceback details")
        errors = mem.get_recent_errors(10)
        assert len(errors) >= 1
        assert errors[-1]["source"] == "system"

    def test_list_files_after_clear(self):
        mem = SharedMemory()
        _clear_memory(mem)
        mem.write("decisions", "decision_1", {"decision": "buy"})
        mem.write("decisions", "decision_2", {"decision": "sell"})
        files = mem.list_files("decisions")
        assert len(files) == 2

    def test_portfolio_roundtrip(self):
        mem = SharedMemory()
        data = {"cash": 5000, "positions": {"BTC/USD": 0.1}}
        mem.write_portfolio(data)
        result = mem.read_portfolio()
        assert result["cash"] == 5000

    def test_read_portfolio_default(self):
        mem = SharedMemory()
        result = mem.read_portfolio()
        assert isinstance(result, dict)

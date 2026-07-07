import json
import os
import threading
import pytest
from http.server import ThreadingHTTPServer
from urllib.request import urlopen, Request
from urllib.error import HTTPError

from core.webserver import DashboardHandler
from core.database import init_db, execute


PORT = 19876
BASE = f"http://127.0.0.1:{PORT}"


@pytest.fixture(scope="module", autouse=True)
def _setup():
    init_db()
    execute("DELETE FROM positions")
    execute("DELETE FROM trades")
    execute("DELETE FROM equity_history")
    execute("DELETE FROM analytics")
    execute("DELETE FROM strategy_stats")
    os.environ["TRADING_DATA_DIR"] = str(__import__("pathlib").Path(__file__).parent.parent / "data")
    yield


@pytest.fixture(scope="module", autouse=True)
def _server():
    server = ThreadingHTTPServer(("127.0.0.1", PORT), DashboardHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    yield
    server.shutdown()


def _get(path):
    with urlopen(BASE + path) as r:
        return json.loads(r.read())


def test_summary():
    data = _get("/api/summary")
    assert "equity" in data
    assert "cash" in data
    assert "total_pnl" in data
    assert "open_positions" in data


def test_positions_empty():
    data = _get("/api/positions")
    assert isinstance(data, list)


def test_trades_empty():
    data = _get("/api/trades")
    assert isinstance(data, list)


def test_equity():
    data = _get("/api/equity")
    assert isinstance(data, list)
    assert len(data) >= 1


def test_activity():
    data = _get("/api/activity")
    assert isinstance(data, list)


def test_errors():
    data = _get("/api/errors")
    assert isinstance(data, list)


def test_opportunities():
    data = _get("/api/opportunities")
    assert isinstance(data, list)


def test_regime():
    data = _get("/api/regime")
    assert isinstance(data, dict)


def test_risk():
    data = _get("/api/risk")
    assert isinstance(data, dict)


def test_health():
    data = _get("/api/health")
    assert isinstance(data, dict)


def test_config():
    data = _get("/api/config")
    assert "broker" in data
    assert "interval_minutes" in data
    assert "watched_symbols" in data


def test_market_prices():
    data = _get("/api/market-prices")
    assert isinstance(data, dict)


def test_trade_journal():
    data = _get("/api/trade-journal")
    assert isinstance(data, list)


def test_strategy_stats():
    data = _get("/api/strategy-stats")
    assert isinstance(data, list)


def test_plans():
    data = _get("/api/plans")
    assert isinstance(data, list)


def test_backtests():
    data = _get("/api/backtests")
    assert isinstance(data, list)


def test_index():
    with urlopen(BASE + "/") as r:
        html = r.read().decode()
        assert "<!DOCTYPE html>" in html


def test_404():
    try:
        _get("/api/nonexistent")
        assert False, "expected 404"
    except HTTPError as e:
        assert e.code == 404


def test_trade_journal_content():
    execute("""
        INSERT INTO trades (symbol, side, qty, entry_price, exit_price, pnl, pnl_pct, reason, closed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
    """, ["TEST/USD", "BUY", 1, 100, 110, 10, 10, "test"])
    data = _get("/api/trade-journal")
    symbols = [t["symbol"] for t in data]
    assert "TEST/USD" in symbols
    execute("DELETE FROM trades WHERE symbol='TEST/USD'")

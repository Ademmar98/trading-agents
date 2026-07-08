import json
import os
import pytest
from fastapi.testclient import TestClient

from core.webserver import app
from core.database import init_db, execute


@pytest.fixture(scope="module", autouse=True)
def _setup():
    init_db()
    execute("DELETE FROM positions")
    execute("DELETE FROM trades")
    execute("DELETE FROM equity_history")
    execute("DELETE FROM analytics")
    execute("DELETE FROM strategy_stats")
    yield


@pytest.fixture
def client():
    return TestClient(app)


def test_summary(client):
    data = client.get("/api/summary").json()
    assert "equity" in data
    assert "cash" in data
    assert "total_pnl" in data
    assert "open_positions" in data


def test_positions_empty(client):
    data = client.get("/api/positions").json()
    assert isinstance(data, list)


def test_trades_empty(client):
    data = client.get("/api/trades").json()
    assert isinstance(data, list)


def test_equity(client):
    data = client.get("/api/equity").json()
    assert isinstance(data, list)
    assert len(data) >= 1


def test_activity(client):
    data = client.get("/api/activity").json()
    assert isinstance(data, list)


def test_errors(client):
    data = client.get("/api/errors").json()
    assert isinstance(data, list)


def test_opportunities(client):
    data = client.get("/api/opportunities").json()
    assert isinstance(data, list)


def test_regime(client):
    data = client.get("/api/regime").json()
    assert isinstance(data, dict)


def test_risk(client):
    data = client.get("/api/risk").json()
    assert isinstance(data, dict)


def test_health(client):
    data = client.get("/api/health").json()
    assert isinstance(data, dict)


def test_config(client):
    data = client.get("/api/config").json()
    assert "broker" in data
    assert "interval_minutes" in data
    assert "watched_symbols" in data


def test_market_prices(client):
    data = client.get("/api/market-prices").json()
    assert isinstance(data, dict)


def test_trade_journal(client):
    data = client.get("/api/trade-journal").json()
    assert isinstance(data, list)


def test_strategy_stats(client):
    data = client.get("/api/strategy-stats").json()
    assert isinstance(data, list)


def test_plans(client):
    data = client.get("/api/plans").json()
    assert isinstance(data, list)


def test_backtests(client):
    data = client.get("/api/backtests").json()
    assert isinstance(data, list)


def test_index(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "<!DOCTYPE html>" in r.text


def test_index_html(client):
    r = client.get("/index.html")
    assert r.status_code == 200
    assert "<!DOCTYPE html>" in r.text


def test_404(client):
    r = client.get("/api/nonexistent")
    assert r.status_code == 404


def test_openapi_json(client):
    r = client.get("/openapi.json")
    assert r.status_code == 200
    spec = r.json()
    assert "paths" in spec
    assert "/health" in spec["paths"]
    assert "/api/summary" in spec["paths"]


def test_docs_redirect(client):
    r = client.get("/docs", follow_redirects=True)
    assert r.status_code == 200
    assert "swagger" in r.text.lower()


def test_trade_journal_content(client):
    execute("""
        INSERT INTO trades (symbol, side, qty, entry_price, exit_price, pnl, pnl_pct, reason, closed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
    """, ["TEST/USD", "BUY", 1, 100, 110, 10, 10, "test"])
    data = client.get("/api/trade-journal").json()
    symbols = [t["symbol"] for t in data]
    assert "TEST/USD" in symbols
    execute("DELETE FROM trades WHERE symbol='TEST/USD'")

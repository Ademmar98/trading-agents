import pytest

from core.database import init_db, execute, fetchone, fetchall, get_meta, set_meta


def setup_method():
    init_db()
    execute("DELETE FROM positions")
    execute("DELETE FROM trades")
    execute("DELETE FROM meta")


class TestDatabase:
    def test_init_creates_tables(self):
        init_db()
        tables = fetchall("SELECT name FROM sqlite_master WHERE type='table'")
        names = {r["name"] for r in tables}
        assert "positions" in names
        assert "trades" in names
        assert "analytics" in names
        assert "equity_history" in names

    def test_insert_and_fetch_position(self):
        execute("DELETE FROM positions")
        execute(
            "INSERT INTO positions (symbol, side, quantity, entry_price, current_price, status) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ["BTC/USD", "BUY", 0.1, 50000, 51000, "open"],
        )
        row = fetchone("SELECT * FROM positions WHERE symbol=?", ["BTC/USD"])
        assert row["quantity"] == 0.1

    def test_insert_and_fetch_trade(self):
        execute("DELETE FROM trades")
        execute(
            "INSERT INTO trades (symbol, side, qty, entry_price, exit_price, pnl, pnl_pct, reason) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ["ETH/USD", "BUY", 1.0, 1800, 1900, 100, 5.55, "TP"],
        )
        rows = fetchall("SELECT * FROM trades")
        assert len(rows) >= 1
        assert rows[-1]["pnl"] == 100.0

    def test_meta_roundtrip(self):
        set_meta("test_key", "test_value")
        assert get_meta("test_key") == "test_value"

    def test_meta_default(self):
        assert get_meta("nonexistent", "default") == "default"

    def test_multiple_positions(self):
        execute("DELETE FROM positions")
        for sym in ["BTC/USD", "ETH/USD", "SOL/USD"]:
            execute(
                "INSERT INTO positions (symbol, side, quantity, entry_price, current_price, status) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                [sym, "BUY", 0.1, 50000, 51000, "open"],
            )
        rows = fetchall("SELECT symbol FROM positions ORDER BY symbol")
        assert len(rows) == 3
        assert rows[0]["symbol"] == "BTC/USD"

    def test_empty_fetchall(self):
        execute("DELETE FROM trades")
        rows = fetchall("SELECT * FROM trades")
        assert rows == []

    def test_empty_fetchone(self):
        row = fetchone("SELECT * FROM trades WHERE id=?", [999])
        assert row is None

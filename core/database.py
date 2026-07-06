import sqlite3
from pathlib import Path

from config import DATA_DIR


DB_PATH = Path(DATA_DIR) / "trading.db"


def get_connection():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def execute(sql, params=None):
    with get_connection() as conn:
        cur = conn.execute(sql, params or [])
        conn.commit()
        return cur


def fetchone(sql, params=None):
    with get_connection() as conn:
        return conn.execute(sql, params or []).fetchone()


def fetchall(sql, params=None):
    with get_connection() as conn:
        return conn.execute(sql, params or []).fetchall()


def init_db():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with get_connection() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                quantity REAL NOT NULL,
                entry_price REAL NOT NULL,
                current_price REAL NOT NULL,
                stop_loss REAL DEFAULT 0,
                take_profit REAL DEFAULT 0,
                pnl REAL DEFAULT 0,
                pnl_pct REAL DEFAULT 0,
                status TEXT DEFAULT 'open',
                opened_at TEXT DEFAULT (datetime('now')),
                closed_at TEXT,
                updated_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                position_id INTEGER,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                qty REAL NOT NULL,
                entry_price REAL NOT NULL,
                exit_price REAL NOT NULL,
                pnl REAL NOT NULL,
                pnl_pct REAL NOT NULL,
                reason TEXT,
                opened_at TEXT,
                closed_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS analytics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                total_trades INTEGER DEFAULT 0,
                win_rate REAL DEFAULT 0,
                total_pnl REAL DEFAULT 0,
                profit_factor REAL,
                sharpe_ratio REAL DEFAULT 0,
                max_drawdown REAL DEFAULT 0,
                expectancy REAL DEFAULT 0,
                computed_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS strategy_stats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                strategy TEXT NOT NULL,
                trades INTEGER DEFAULT 0,
                win_rate REAL DEFAULT 0,
                pnl REAL DEFAULT 0,
                computed_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS backtest_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                total_return REAL DEFAULT 0,
                total_trades INTEGER DEFAULT 0,
                win_rate REAL DEFAULT 0,
                profit_factor REAL,
                sharpe_ratio REAL DEFAULT 0,
                max_drawdown REAL DEFAULT 0,
                final_equity REAL DEFAULT 0,
                avg_win REAL DEFAULT 0,
                avg_loss REAL DEFAULT 0,
                tested_at TEXT,
                computed_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS equity_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                equity REAL NOT NULL,
                cash REAL DEFAULT 0,
                positions_value REAL DEFAULT 0,
                exposure_pct REAL DEFAULT 0,
                snapped_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT
            );

            CREATE TABLE IF NOT EXISTS optimization_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                params TEXT,
                sl_mult REAL,
                tp_mult REAL,
                position_size_pct REAL,
                confidence_threshold REAL,
                total_return REAL DEFAULT 0,
                total_trades INTEGER DEFAULT 0,
                win_rate REAL DEFAULT 0,
                profit_factor REAL,
                max_drawdown REAL DEFAULT 0,
                sharpe_ratio REAL DEFAULT 0,
                score REAL DEFAULT 0,
                optimized_at TEXT,
                computed_at TEXT DEFAULT (datetime('now'))
            );
            """
        )
        _migrate(conn)


def _ensure_columns(conn, table, columns):
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    for name, decl in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")


def _migrate(conn):
    # Databases created by older schemas (e.g. the Railway volume) get the
    # columns newer inserts expect; CREATE IF NOT EXISTS never alters them.
    _ensure_columns(conn, "backtest_results", {
        "final_equity": "REAL DEFAULT 0",
        "avg_win": "REAL DEFAULT 0",
        "avg_loss": "REAL DEFAULT 0",
        "tested_at": "TEXT",
    })
    _ensure_columns(conn, "positions", {
        "peak_price": "REAL DEFAULT 0",
    })
    _ensure_columns(conn, "optimization_results", {
        "sl_mult": "REAL",
        "tp_mult": "REAL",
        "position_size_pct": "REAL",
        "confidence_threshold": "REAL",
        "total_return": "REAL DEFAULT 0",
        "total_trades": "INTEGER DEFAULT 0",
        "win_rate": "REAL DEFAULT 0",
        "profit_factor": "REAL",
        "max_drawdown": "REAL DEFAULT 0",
        "sharpe_ratio": "REAL DEFAULT 0",
        "optimized_at": "TEXT",
    })


def get_meta(key, default=None):
    row = fetchone("SELECT value FROM meta WHERE key=?", [key])
    return row["value"] if row else default


def set_meta(key, value):
    execute("INSERT INTO meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value", [key, str(value)])

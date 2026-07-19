import sqlite3
from contextlib import contextmanager
from pathlib import Path

import config


def _db_path():
    return Path(config.DATA_DIR) / "trading.db"


@contextmanager
def get_connection():
    """Context manager that creates, yields, and reliably closes a SQLite connection."""
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_db_path()))
    # Concurrent agents write from worker threads; without a busy timeout a
    # colliding write raises "database is locked" instead of waiting.
    conn.execute("PRAGMA busy_timeout=5000")
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


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
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
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

            CREATE TABLE IF NOT EXISTS trade_plans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                plan_id TEXT NOT NULL UNIQUE,
                timestamp TEXT NOT NULL DEFAULT (datetime('now')),
                symbol TEXT NOT NULL,
                direction TEXT NOT NULL,
                entry_price REAL NOT NULL,
                stop_loss REAL NOT NULL,
                take_profit REAL NOT NULL,
                position_size_usd REAL,
                position_size_units REAL,
                confidence REAL,
                strategy TEXT,
                regime TEXT,
                rationale TEXT,
                risk_reward_ratio REAL,
                status TEXT DEFAULT 'created'
            );

            CREATE TABLE IF NOT EXISTS agent_state (
                agent TEXT PRIMARY KEY,
                state TEXT NOT NULL DEFAULT '{}',
                updated_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS agent_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                msg_id TEXT NOT NULL,
                correlation_id TEXT,
                topic TEXT NOT NULL,
                sender TEXT NOT NULL,
                recipient TEXT,
                payload TEXT NOT NULL DEFAULT '{}',
                created_at TEXT DEFAULT (datetime('now'))
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
    _ensure_columns(conn, "trades", {
        "strategy": "TEXT DEFAULT ''",
    })
    _ensure_columns(conn, "positions", {
        "strategy": "TEXT DEFAULT ''",
    })
    _ensure_columns(conn, "strategy_stats", {
        "avg_pnl": "REAL DEFAULT 0",
        "sharpe": "REAL DEFAULT 0",
    })
    try:
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_strategy_stats_name ON strategy_stats(strategy)")
    except Exception:
        pass


def save_plan(plan: dict):
    execute("""
        INSERT OR REPLACE INTO trade_plans
            (plan_id, timestamp, symbol, direction, entry_price, stop_loss,
             take_profit, position_size_usd, position_size_units, confidence,
             strategy, regime, rationale, risk_reward_ratio, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, [
        plan.get("plan_id", ""),
        plan.get("timestamp", ""),
        plan.get("symbol", ""),
        plan.get("direction", plan.get("action", "")),
        plan.get("entry_price", plan.get("price", 0)),
        plan.get("stop_loss", 0),
        plan.get("take_profit", 0),
        plan.get("position_size_usd", 0),
        plan.get("position_size_units", plan.get("qty", 0)),
        plan.get("confidence"),
        plan.get("strategy", ""),
        plan.get("regime", ""),
        plan.get("rationale", ""),
        plan.get("risk_reward_ratio"),
        plan.get("status", "created"),
    ])


def update_plan_status(plan_id: str, status: str):
    execute("UPDATE trade_plans SET status=? WHERE plan_id=?", [status, plan_id])


def save_strategy_stats(stats: dict):
    """Upsert per-strategy performance stats."""
    for strat_name, s in stats.items():
        execute("""
            INSERT INTO strategy_stats (strategy, trades, win_rate, pnl, avg_pnl)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(strategy) DO UPDATE SET
                trades=excluded.trades, win_rate=excluded.win_rate,
                pnl=excluded.pnl, avg_pnl=excluded.avg_pnl,
                computed_at=datetime('now')
        """, [strat_name, s.get("trades", 0), s.get("win_rate", 0.0),
              s.get("pnl", 0.0), s.get("avg_pnl", 0.0)])


def get_strategy_stats_list():
    rows = fetchall("SELECT strategy, trades, win_rate, pnl, avg_pnl FROM strategy_stats ORDER BY pnl DESC")
    return [dict(r) for r in rows]


def get_unprofitable_strategies(min_trades=3, max_win_rate=40):
    # Drop a strategy when its win rate is poor OR it loses money overall —
    # a 45% win rate with bad R:R still bleeds, and win rate alone missed that.
    rows = fetchall(
        "SELECT strategy FROM strategy_stats WHERE trades >= ? AND (win_rate <= ? OR pnl < 0)",
        [min_trades, max_win_rate],
    )
    return [r["strategy"] for r in rows]


def get_plans(limit=50):
    rows = fetchall(
        "SELECT * FROM trade_plans ORDER BY timestamp DESC LIMIT ?", [limit]
    )
    return [dict(r) for r in rows]


def get_agent_state(agent):
    """Load an agent's persistent state dict (empty dict when none saved)."""
    import json
    row = fetchone("SELECT state FROM agent_state WHERE agent=?", [agent])
    if not row:
        return {}
    try:
        return json.loads(row["state"])
    except (ValueError, TypeError):
        return {}


def set_agent_state(agent, state: dict):
    import json
    execute("""
        INSERT INTO agent_state (agent, state, updated_at)
        VALUES (?, ?, datetime('now'))
        ON CONFLICT(agent) DO UPDATE SET
            state=excluded.state, updated_at=datetime('now')
    """, [agent, json.dumps(state, default=str)])


def save_message(msg_id, topic, sender, payload, correlation_id=None, recipient=None):
    import json
    execute("""
        INSERT INTO agent_messages (msg_id, correlation_id, topic, sender, recipient, payload)
        VALUES (?, ?, ?, ?, ?, ?)
    """, [msg_id, correlation_id, topic, sender, recipient,
          json.dumps(payload, default=str)])


def get_message_thread(correlation_id, limit=200):
    """Full transcript of one deliberation, oldest first."""
    import json
    rows = fetchall("""
        SELECT * FROM agent_messages WHERE correlation_id=?
        ORDER BY id ASC LIMIT ?
    """, [correlation_id, limit])
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["payload"] = json.loads(d["payload"])
        except (ValueError, TypeError):
            pass
        out.append(d)
    return out


def get_recent_deliberations(limit=20):
    """Latest deliberation threads: correlation_id + verdict topic if reached."""
    rows = fetchall("""
        SELECT correlation_id,
               MIN(created_at) AS started_at,
               COUNT(*) AS messages,
               MAX(CASE WHEN topic LIKE '%.verdict' THEN payload END) AS verdict
        FROM agent_messages
        WHERE correlation_id IS NOT NULL
        GROUP BY correlation_id
        ORDER BY MIN(id) DESC LIMIT ?
    """, [limit])
    return [dict(r) for r in rows]


def get_meta(key, default=None):
    row = fetchone("SELECT value FROM meta WHERE key=?", [key])
    return row["value"] if row else default


def set_meta(key, value):
    execute("INSERT INTO meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value", [key, str(value)])

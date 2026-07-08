import base64
import json
import os
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from config import BASE_DIR, BROKER_TYPE, BINANCE_USE_TESTNET, DATA_DIR
from core.database import fetchall, fetchone, get_plans, get_strategy_stats_list, get_meta
from core.memory import SharedMemory
from core import websocket_prices

WEB_DIR = BASE_DIR / "web"

memory = SharedMemory()


def get_market_prices():
    return websocket_prices.get_all_prices()


def _live_pnl_stats():
    """Aggregate closed-trade P&L directly from SQLite (no portfolio.json)."""
    row = fetchone("SELECT COUNT(*) AS cnt, SUM(pnl) AS total FROM trades")
    cnt = row["cnt"] if row else 0
    total = row["total"] if row and row["total"] else 0
    wins = fetchone("SELECT COUNT(*) AS w FROM trades WHERE pnl > 0")
    win_cnt = wins["w"] if wins else 0
    return cnt, total, win_cnt


def _live_positions_summary():
    """Return open position count, total value, and exposure from SQLite."""
    rows = fetchall(
        "SELECT quantity, entry_price, current_price FROM positions WHERE status='open'"
    )
    count = len(rows)
    pos_value = sum(r["current_price"] * r["quantity"] for r in rows) if rows else 0
    return count, pos_value


def _live_cash():
    """Return the latest known cash balance from equity_history (fallback 0)."""
    row = fetchone(
        "SELECT cash FROM equity_history ORDER BY id DESC LIMIT 1"
    )
    return row["cash"] if row else 0


def get_summary():
    trade_cnt, closed_pnl, win_cnt = _live_pnl_stats()
    pos_cnt, pos_value = _live_positions_summary()
    cash = _live_cash()
    init_bal = float(get_meta("initial_balance", "0"))
    equity = cash + pos_value
    exposure = (pos_value / equity * 100) if equity > 0 else 0
    total_pnl_pct = ((equity - init_bal) / init_bal * 100) if init_bal > 0 else 0
    return {
        "equity": round(equity, 2),
        "cash": round(cash, 2),
        "initial_balance": init_bal,
        "total_pnl": round(closed_pnl, 2),
        "total_pnl_pct": round(total_pnl_pct, 2),
        "portfolio_pnl": round(equity - init_bal, 2),
        "portfolio_pnl_pct": round(total_pnl_pct, 2),
        "exposure_pct": round(exposure, 2),
        "open_positions": pos_cnt,
        "total_trades": trade_cnt,
        "win_rate": (win_cnt / trade_cnt * 100) if trade_cnt else 0,
        "broker": BROKER_TYPE,
        "testnet": BINANCE_USE_TESTNET,
    }


def get_positions():
    rows = fetchall(
        "SELECT symbol, side, quantity, entry_price, current_price, stop_loss, "
        "take_profit, pnl, pnl_pct, opened_at FROM positions "
        "WHERE status = 'open' ORDER BY opened_at DESC"
    )
    return [dict(r) for r in rows]


def get_trades(limit=100):
    rows = fetchall(
        "SELECT symbol, side, qty, entry_price, exit_price, pnl, pnl_pct, "
        "reason, closed_at FROM trades ORDER BY closed_at DESC LIMIT ?",
        [limit],
    )
    return [dict(r) for r in rows]


def get_trade_journal():
    rows = fetchall(
        "SELECT symbol, side, pnl, pnl_pct, strategy, closed_at FROM trades "
        "ORDER BY closed_at ASC LIMIT 200"
    )
    return [dict(r) for r in rows]


def get_equity_curve():
    init_bal = float(get_meta("initial_balance", "0"))
    snaps = fetchall(
        "SELECT equity, snapped_at FROM equity_history ORDER BY id ASC LIMIT 1000"
    )
    if snaps:
        points = [{"t": "start", "equity": init_bal}]
        points += [{"t": r["snapped_at"], "equity": r["equity"]} for r in snaps]
        cash = _live_cash()
        pos_cnt, pos_value = _live_positions_summary()
        points.append({"t": "now", "equity": round(cash + pos_value, 2)})
        return points
    rows = fetchall("SELECT closed_at, pnl FROM trades ORDER BY closed_at ASC")
    points = [{"t": "start", "equity": init_bal}]
    cumulative = init_bal
    for r in rows:
        cumulative += r["pnl"]
        points.append({"t": r["closed_at"], "equity": round(cumulative, 2)})
    points.append({"t": "now", "equity": round(cumulative, 2)})
    return points


def get_activity(n=30):
    return memory.get_recent_logs(n)


ERROR_KEYWORDS = ("error", "fail", "exception", "not connected", "timeout",
                  "invalid", "denied", "rejected", "insufficient")


def get_errors(n=50):
    entries = [dict(e) for e in memory.get_recent_errors(n)]
    for log in memory.get_recent_logs(200):
        msg = str(log.get("message", "")).lower()
        if any(k in msg for k in ERROR_KEYWORDS):
            entries.append({
                "source": log.get("agent", "system"),
                "message": log.get("message", ""),
                "trace": "",
                "time": log.get("time", 0),
            })
    entries.sort(key=lambda e: e.get("time", 0), reverse=True)
    seen = set()
    unique = []
    for e in entries:
        key = (int(e.get("time", 0)), e.get("message", ""))
        if key not in seen:
            seen.add(key)
            unique.append(e)
    return unique[:n]


_CACHE_BUST = ("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")


class DashboardHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _send(self, status, content_type, body):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header(*_CACHE_BUST)
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, data):
        self._send(200, "application/json", json.dumps(data, default=str).encode())

    def get_opportunities(self):
        analysis = memory.read("analyses", "market_scan")
        if not analysis:
            return []
        return analysis.get("opportunities", [])

    def get_regime(self):
        regime = memory.read("analyses", "regime_scan")
        if not regime:
            return {}
        return regime

    def get_backtests(self):
        from core.backtester import get_backtest_results
        return get_backtest_results()

    def get_optimizations(self):
        from core.optimizer import get_optimization_results
        return get_optimization_results()

    def get_risk(self):
        risk = memory.read("decisions", "risk_assessment")
        return risk or {}

    def get_health(self):
        health = memory.read("reports", "health")
        return health or {}

    def get_sentiment(self):
        sentiment = memory.read("analyses", "sentiment_scan")
        return sentiment or {}

    def get_pricing(self):
        pricing = memory.read("decisions", "pricing")
        return pricing or {}

    def get_config(self):
        from config import BROKER_TYPE, TRADING_INTERVAL_MINUTES, WATCHED_SYMBOLS, INITIAL_BALANCE, BINANCE_USE_TESTNET, DATA_DIR
        return {
            "broker": BROKER_TYPE,
            "interval_minutes": TRADING_INTERVAL_MINUTES,
            "watched_symbols": len(WATCHED_SYMBOLS),
            "initial_capital": INITIAL_BALANCE,
            "testnet": BINANCE_USE_TESTNET,
            "data_dir": str(DATA_DIR),
        }

    def do_GET(self):
        try:
            path = self.path.split("?")[0]
            if path == "/health":
                self._send(200, "application/json", b'{"status":"ok"}')
            elif path in ("/", "/index.html"):
                page = (WEB_DIR / "index.html").read_bytes()
                self._send(200, "text/html; charset=utf-8", page)
            elif path == "/api/summary":
                self._json(get_summary())
            elif path == "/api/positions":
                self._json(get_positions())
            elif path == "/api/trades":
                self._json(get_trades())
            elif path == "/api/equity":
                self._json(get_equity_curve())
            elif path == "/api/activity":
                self._json(get_activity())
            elif path == "/api/errors":
                self._json(get_errors())
            elif path == "/api/plans":
                self._json(get_plans())
            elif path == "/api/market-prices":
                self._json(get_market_prices())
            elif path == "/api/strategy-stats":
                self._json(get_strategy_stats_list())
            elif path == "/api/trade-journal":
                self._json(get_trade_journal())
            elif path == "/api/opportunities":
                self._json(self.get_opportunities())
            elif path == "/api/regime":
                self._json(self.get_regime())
            elif path == "/api/backtests":
                self._json(self.get_backtests())
            elif path == "/api/optimizations":
                self._json(self.get_optimizations())
            elif path == "/api/risk":
                self._json(self.get_risk())
            elif path == "/api/health":
                self._json(self.get_health())
            elif path == "/api/sentiment":
                self._json(self.get_sentiment())
            elif path == "/api/pricing":
                self._json(self.get_pricing())
            elif path == "/api/config":
                self._json(self.get_config())
            else:
                self._send(404, "text/plain", b"not found")
        except Exception as e:
            try:
                memory.log_error("webserver", f"{self.path}: {e}", traceback.format_exc())
            except Exception:
                pass
            self._send(500, "application/json", json.dumps({"error": str(e)}).encode())


def start_webserver():
    port = int(os.getenv("PORT", "8000"))
    server = ThreadingHTTPServer(("0.0.0.0", port), DashboardHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return port

import base64
import json
import os
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from config import BASE_DIR, BROKER_TYPE, BINANCE_USE_TESTNET
from core.database import fetchall, get_plans, get_strategy_stats_list
from core.portfolio import load_portfolio
from core.memory import SharedMemory
from core import websocket_prices

WEB_DIR = BASE_DIR / "web"

memory = SharedMemory()


def get_market_prices():
    prices = websocket_prices.get_all_prices()
    return prices


def get_summary():
    p = load_portfolio()
    trades = fetchall("SELECT pnl FROM trades")
    pnls = [r["pnl"] for r in trades]
    total_trades = len(pnls)
    wins = len([x for x in pnls if x > 0])
    closed_pnl = sum(pnls)
    return {
        "equity": round(p.equity, 2),
        "cash": round(p.cash, 2),
        "initial_balance": p.initial_balance,
        "total_pnl": round(closed_pnl, 2),
        "total_pnl_pct": round((closed_pnl / p.initial_balance * 100) if p.initial_balance else 0, 2),
        "portfolio_pnl": round(p.total_pnl, 2),
        "portfolio_pnl_pct": round(p.total_pnl_pct, 2),
        "exposure_pct": round(p.exposure_pct, 2),
        "open_positions": len(p.positions),
        "total_trades": total_trades,
        "win_rate": (wins / total_trades * 100) if total_trades else 0,
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
    p = load_portfolio()
    # Prefer real per-cycle snapshots (they include open-position value);
    # fall back to reconstructing from closed trades for older databases
    snaps = fetchall(
        "SELECT equity, snapped_at FROM equity_history ORDER BY id ASC LIMIT 1000"
    )
    if snaps:
        points = [{"t": "start", "equity": p.initial_balance}]
        points += [{"t": r["snapped_at"], "equity": r["equity"]} for r in snaps]
        points.append({"t": "now", "equity": round(p.equity, 2)})
        return points
    rows = fetchall("SELECT closed_at, pnl FROM trades ORDER BY closed_at ASC")
    points = [{"t": "start", "equity": p.initial_balance}]
    cumulative = p.initial_balance
    for r in rows:
        cumulative += r["pnl"]
        points.append({"t": r["closed_at"], "equity": round(cumulative, 2)})
    points.append({"t": "now", "equity": round(p.equity, 2)})
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


class DashboardHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _send(self, status, content_type, body):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
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

    def get_config(self):
        from config import BROKER_TYPE, TRADING_INTERVAL_MINUTES, WATCHED_SYMBOLS, INITIAL_BALANCE, BINANCE_USE_TESTNET
        return {
            "broker": BROKER_TYPE,
            "interval_minutes": TRADING_INTERVAL_MINUTES,
            "watched_symbols": len(WATCHED_SYMBOLS),
            "initial_capital": INITIAL_BALANCE,
            "testnet": BINANCE_USE_TESTNET,
        }

    def do_GET(self):
        try:
            if self.path in ("/", "/index.html"):
                page = (WEB_DIR / "index.html").read_bytes()
                self._send(200, "text/html; charset=utf-8", page)
            elif self.path == "/api/summary":
                self._json(get_summary())
            elif self.path == "/api/positions":
                self._json(get_positions())
            elif self.path == "/api/trades":
                self._json(get_trades())
            elif self.path == "/api/equity":
                self._json(get_equity_curve())
            elif self.path == "/api/activity":
                self._json(get_activity())
            elif self.path == "/api/errors":
                self._json(get_errors())
            elif self.path == "/api/plans":
                self._json(get_plans())
            elif self.path == "/api/market-prices":
                self._json(get_market_prices())
            elif self.path == "/api/strategy-stats":
                self._json(get_strategy_stats_list())
            elif self.path == "/api/trade-journal":
                self._json(get_trade_journal())
            elif self.path == "/api/opportunities":
                self._json(self.get_opportunities())
            elif self.path == "/api/regime":
                self._json(self.get_regime())
            elif self.path == "/api/backtests":
                self._json(self.get_backtests())
            elif self.path == "/api/optimizations":
                self._json(self.get_optimizations())
            elif self.path == "/api/risk":
                self._json(self.get_risk())
            elif self.path == "/api/health":
                self._json(self.get_health())
            elif self.path == "/api/sentiment":
                self._json(self.get_sentiment())
            elif self.path == "/api/config":
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

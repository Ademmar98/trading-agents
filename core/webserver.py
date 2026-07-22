import json
import os
import threading
import traceback

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
import uvicorn

from config import BASE_DIR, BROKER_TYPE, BINANCE_USE_TESTNET, DATA_DIR
from core.database import fetchall, fetchone, get_plans, get_strategy_stats_list, get_meta
from core.memory import SharedMemory
from core import websocket_prices

WEB_DIR = BASE_DIR / "web"

memory = SharedMemory()


def get_market_prices():
    return websocket_prices.get_all_prices()


def _live_pnl_stats():
    # One logical trade per position (scaled exits write partial + runner rows)
    row = fetchone("""
        SELECT COUNT(*) AS cnt, SUM(pnl) AS total,
               SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) AS w
        FROM (SELECT SUM(pnl) AS pnl FROM trades GROUP BY COALESCE(position_id, id))
    """)
    cnt = row["cnt"] if row else 0
    total = row["total"] if row and row["total"] else 0
    win_cnt = row["w"] if row and row["w"] else 0
    return cnt, total, win_cnt


def _live_positions_summary():
    rows = fetchall(
        "SELECT quantity, entry_price, current_price FROM positions WHERE status='open'"
    )
    count = len(rows)
    pos_value = sum(r["current_price"] * r["quantity"] for r in rows) if rows else 0
    return count, pos_value


def _live_cash():
    row = fetchone("SELECT cash FROM equity_history ORDER BY id DESC LIMIT 1")
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
        "goals": _goal_progress(total_pnl_pct),
    }


def _goal_progress(total_pnl_pct):
    """Firm goals for the dashboard: daily +0.5-3%, total +10-50%."""
    from config import (DAILY_PROFIT_TARGET_MIN, DAILY_PROFIT_TARGET_MAX,
                        TOTAL_PROFIT_TARGET_MIN, TOTAL_PROFIT_TARGET_MAX)
    try:
        from core.equity import daily_loss_pct
        day_pnl = round(daily_loss_pct(), 2)
    except Exception:
        day_pnl = 0.0
    return {
        "day_pnl_pct": day_pnl,
        "daily_target": [DAILY_PROFIT_TARGET_MIN, DAILY_PROFIT_TARGET_MAX],
        "daily_hit": day_pnl >= DAILY_PROFIT_TARGET_MIN,
        "total_pnl_pct": round(total_pnl_pct, 2),
        "total_target": [TOTAL_PROFIT_TARGET_MIN, TOTAL_PROFIT_TARGET_MAX],
        "total_hit": total_pnl_pct >= TOTAL_PROFIT_TARGET_MIN,
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


# -- FastAPI app --

app = FastAPI()


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/")
@app.get("/index.html")
def index():
    page = WEB_DIR / "index.html"
    if not page.exists():
        raise HTTPException(404)
    return FileResponse(str(page), headers={
        "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
        "Pragma": "no-cache",
        "Expires": "0",
    })


@app.get("/api/summary")
def api_summary():
    return get_summary()


@app.get("/api/positions")
def api_positions():
    return get_positions()


@app.get("/api/trades")
def api_trades():
    return get_trades()


@app.get("/api/equity")
def api_equity():
    return get_equity_curve()


@app.get("/api/activity")
def api_activity():
    return get_activity()


@app.get("/api/errors")
def api_errors():
    return get_errors()


@app.get("/api/plans")
def api_plans():
    return get_plans()


@app.get("/api/market-prices")
def api_market_prices():
    return get_market_prices()


@app.get("/api/strategy-stats")
def api_strategy_stats():
    return get_strategy_stats_list()


@app.get("/api/trade-journal")
def api_trade_journal():
    return get_trade_journal()


@app.get("/api/opportunities")
def api_opportunities():
    analysis = memory.read("analyses", "market_scan")
    if not analysis:
        return []
    return analysis.get("opportunities", [])


@app.get("/api/regime")
def api_regime():
    regime = memory.read("analyses", "regime_scan")
    return regime or {}


@app.get("/api/backtests")
def api_backtests():
    from core.backtester import get_backtest_results
    return get_backtest_results()


@app.get("/api/optimizations")
def api_optimizations():
    from core.optimizer import get_optimization_results
    return get_optimization_results()


@app.get("/api/risk")
def api_risk():
    risk = memory.read("decisions", "risk_assessment")
    return risk or {}


@app.get("/api/health")
def api_health():
    # The stored health report is written by an agent INSIDE the cycle thread,
    # so it goes stale during a freeze (incident 2026-07-22: it kept reporting
    # "ok" through a 10.5h stall). Overlay a LIVE cycle-age check computed here
    # in the webserver thread, which keeps running even when the cycle hangs.
    health_data = dict(memory.read("reports", "health") or {})
    try:
        from core.equity import cycle_age_seconds
        from config import CYCLE_STALL_AFTER_MIN, TRADING_INTERVAL_MINUTES
        age = cycle_age_seconds()
        threshold = max(CYCLE_STALL_AFTER_MIN * 60, 3 * TRADING_INTERVAL_MINUTES * 60)
        health_data["cycle_age_s"] = round(age, 0) if age is not None else None
        stalled = age is not None and age > threshold
        health_data["cycle_stalled"] = stalled
        if stalled:
            health_data["status"] = "stalled"
            health_data["halted"] = True
            health_data.setdefault("issues", [])
            health_data["issues"] = list(health_data.get("issues", [])) + [
                f"cycle frozen — no completed cycle for {int(age // 60)} min"]
    except Exception:
        pass
    return health_data


@app.get("/api/sentiment")
def api_sentiment():
    sentiment = memory.read("analyses", "sentiment_scan")
    return sentiment or {}


@app.get("/api/head-trader")
def api_head_trader():
    report = memory.read("reports", "head_trader")
    return report or {}


@app.get("/api/daily-pnl")
def api_daily_pnl():
    """Realized PnL per UTC day (one logical trade per position)."""
    rows = fetchall("""
        SELECT date(closed_at) AS day, COUNT(*) AS trades,
               ROUND(SUM(pnl), 2) AS pnl,
               SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) AS wins
        FROM (SELECT SUM(pnl) AS pnl, MAX(closed_at) AS closed_at
              FROM trades GROUP BY COALESCE(position_id, id))
        GROUP BY day ORDER BY day DESC LIMIT 30
    """)
    return {"days": [dict(r) for r in rows]}


@app.get("/api/scorecard")
def api_scorecard():
    """Per-strategy live scorecard with a verdict per strategy."""
    from core.analytics import compute_analytics
    a = compute_analytics()
    rows = a.get("strategy_breakdown", []) or []
    for r in rows:
        n = r.get("trades", 0)
        if n < 10:
            r["verdict"] = "building sample"
        elif r.get("expectancy", 0) < 0:
            r["verdict"] = "bleeding"
        else:
            r["verdict"] = "earning"
    return {"strategies": rows}


@app.get("/api/pricing")
def api_pricing():
    pricing = memory.read("decisions", "pricing")
    return pricing or {}


@app.get("/api/fill-diagnostics")
def api_fill_diagnostics(days: int = 7):
    """Limit-fill microstructure metrics: fill rate, time-to-fill, adverse
    selection, net spread saved (see core.fill_monitor)."""
    from core.fill_monitor import diagnostics
    return diagnostics(days=days)


@app.get("/api/daily-report")
def api_daily_report(date: str | None = None):
    """Latest daily desk report, or a specific one via ?date=YYYY-MM-DD."""
    from core.daily_report import load_report
    report = load_report(date)
    if report is None:
        raise HTTPException(status_code=404, detail="no daily report available")
    return report


@app.get("/api/daily-reports")
def api_daily_reports():
    from core.daily_report import list_report_dates
    return {"dates": list_report_dates()}


@app.get("/api/config")
def api_config():
    from config import BROKER_TYPE, TRADING_INTERVAL_MINUTES, WATCHED_SYMBOLS, INITIAL_BALANCE, BINANCE_USE_TESTNET, DATA_DIR
    return {
        "broker": BROKER_TYPE,
        "interval_minutes": TRADING_INTERVAL_MINUTES,
        "watched_symbols": len(WATCHED_SYMBOLS),
        "initial_capital": INITIAL_BALANCE,
        "testnet": BINANCE_USE_TESTNET,
        "data_dir": str(DATA_DIR),
    }


@app.exception_handler(Exception)
def catch_all(request, exc):
    try:
        memory.log_error("webserver", f"{request.url.path}: {exc}", traceback.format_exc())
    except Exception:
        pass
    return JSONResponse(status_code=500, content={"error": str(exc)})


def start_webserver():
    port = int(os.getenv("PORT", "8000"))
    if port == 0:
        return None
    config = uvicorn.Config(app, host="0.0.0.0", port=port, log_level="warning")
    server = uvicorn.Server(config)

    ready = threading.Event()
    orig_startup = server.startup

    async def _on_startup(sockets=None):
        await orig_startup(sockets)
        if server.started:
            ready.set()

    server.startup = _on_startup
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    ready.wait(timeout=10)
    return port if server.started else None

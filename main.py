#!/usr/bin/env python3
import os
import socket
import sys
import time
import threading
import traceback
from datetime import datetime, timezone
from pathlib import Path

# Headless mode: no dashboard, for running on a server (use --headless or HEADLESS=true)
HEADLESS = "--headless" in sys.argv or os.getenv("HEADLESS", "").lower() == "true"

# Reset flag: delete all data and start fresh
RESET = "--reset" in sys.argv

from config import DATA_DIR, INITIAL_BALANCE, TRADING_INTERVAL_MINUTES, BROKER_TYPE, BINANCE_API_KEY, BINANCE_API_SECRET, BINANCE_USE_TESTNET, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, WATCHED_SYMBOLS, LOCK_PORT
from core.broker import PaperBroker
from core.binance_broker import BinanceBroker
from core.portfolio import load_portfolio, save_portfolio, Portfolio
from core.memory import SharedMemory
from core.database import init_db, fetchall, set_meta
from core.positions import PositionManager
from core import pending_orders, websocket_prices
from core.notifier import Notifier
from core.analytics import get_analytics, get_strategy_stats
from core.webserver import start_webserver, get_market_prices
from core.dashboard import make_layout
from core.backtester import run_all_backtests, get_backtest_results, backtest_symbol
from core.equity import snapshot_equity, build_daily_summary, pop_completed_day, check_goals
from core.reconcile import reconcile_with_exchange
from agents.orchestrator import Orchestrator
from agents.analyst import ResearchAnalyst
from agents.sentiment_agent import SentimentAgent
from agents.news_agent import NewsAgent
from agents.regime_agent import RegimeAgent

from agents.risk_manager import RiskManager
from agents.position_sizer import PositionSizer
from agents.portfolio_manager import PortfolioManagerAgent
from agents.compliance_agent import ComplianceAgent
from agents.execution_agent import ExecutionAgent
from agents.trader import Trader
from agents.auditor import Auditor
from agents.head_trader import HeadTrader
from agents.optimizer_agent import OptimizerAgent
from agents.health_monitor import HealthMonitor

from rich.console import Console
from rich.live import Live

# ── Console instance: Rich UI for TTY, plain print for headless/Docker ──
if HEADLESS:
    import re as _re, builtins as _builtins
    class _Console:
        _strip = _re.compile(r"\[/?[\w#]+(?: [^\]]+)?\]")
        def print(self, *args, **kwargs):
            text = " ".join(str(a) for a in args) if args else ""
            plain = self._strip.sub("", text)
            if plain.strip():
                _builtins.print(plain, flush=True)
        def clear(self): pass
    console = _Console()
else:
    console = Console()

# Lazy — initialized inside main() so --reset can run before any dirs are created
memory = None


def _thread_excepthook(args):
    memory.log_error(
        "thread",
        f"{args.exc_type.__name__}: {args.exc_value}",
        "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback)),
    )


threading.excepthook = _thread_excepthook
live_broker = None
pos_mgr = PositionManager()
notifier = Notifier(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)


_lock_socket = None


def acquire_instance_lock():
    """Bind a localhost port as a process-wide mutex. A second bot instance
    fails the bind and must exit — two copies would place duplicate trades.
    The OS releases the port automatically even on a hard crash."""
    global _lock_socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", LOCK_PORT))
        s.listen(1)
    except OSError:
        return False
    _lock_socket = s
    return True


def make_broker():
    if BROKER_TYPE == "binance":
        return BinanceBroker(BINANCE_API_KEY, BINANCE_API_SECRET, BINANCE_USE_TESTNET)
    return PaperBroker()


_BROKER_RETRY = {}


def make_broker_with_retry():
    """Return broker. Fall back to PaperBroker when the live broker is disconnected."""
    global _BROKER_RETRY
    now = time.time()
    last_attempt = _BROKER_RETRY.get("last", 0)
    attempt = _BROKER_RETRY.get("count", 0)
    backoff = min(60, 2 ** attempt)
    if now - last_attempt < backoff and BROKER_TYPE != "paper":
        cached = _BROKER_RETRY.get("cached")
        if cached and hasattr(cached, "connected") and not cached.connected:
            return _paper_fallback_broker()
        return cached or PaperBroker()
    b = make_broker()
    _BROKER_RETRY["last"] = now
    if BROKER_TYPE != "paper" and hasattr(b, "connected") and not b.connected:
        _BROKER_RETRY["count"] = attempt + 1
        _BROKER_RETRY["cached"] = b
        memory.log("system", f"Broker {BROKER_TYPE} disconnected, using paper fallback (retry attempt {attempt+1} in {backoff}s)")
        return _paper_fallback_broker()
    _BROKER_RETRY["count"] = 0
    _BROKER_RETRY["cached"] = b
    return b


def _paper_fallback_broker():
    """Return a PaperBroker instance for fallback when live broker is down."""
    pb = PaperBroker()
    pb.connected = True
    return pb


def _rebalance_positions():
    """Close worst underwater position if better opportunities exist.

    NOTE — Intentional gate-free exception: this path calls
    broker.place_order() directly without routing through RiskManager,
    ComplianceAgent, or ExecutionAgent.  The bypass is deliberate because
    this only *closes* an existing losing position — it never opens a new
    entry.  The new-entry gate chain (RiskManager → PositionSizer →
    PortfolioManager → Compliance → Execution) is designed for vetting
    opening orders, not for cutting losses.  Protection layered here:
      - Triggered only by Auditor's needs_rebalance flag (Auditor runs
        after all other gates in the pipeline).
      - Position must be >5 % underwater.
      - Only fires when a >75 % confidence opportunity exists to deploy
        the freed capital.
      - Only the single worst position is closed per invocation.
    """
    try:
        audit = memory.read("reports", "audit") or {}
        if not audit.get("needs_rebalance"):
            return
        positions = fetchall("SELECT * FROM positions WHERE status='open' ORDER BY pnl ASC")
        if len(positions) < 1:
            return
        worst = positions[0]
        pnl_pct = worst["pnl_pct"]
        if pnl_pct > -5:
            return
        opportunities = memory.read("analyses", "market_scan") or {}
        opps = opportunities.get("opportunities", [])
        high_conf = [o for o in opps if o.get("confidence", 0) > 0.75]
        if not high_conf:
            return
        broker = make_broker_with_retry()
        close_side = "SELL" if worst["side"] == "BUY" else "BUY"
        order = broker.place_order(worst["symbol"], close_side, worst["quantity"], worst["current_price"])
        pos_mgr.close_position(worst["id"], worst["current_price"], reason="rebalance")
        memory.log("system", f"Rebalance closed {worst['symbol']} ${worst['pnl']:+.2f} for {high_conf[0]['symbol']}")
        notifier.send(
            f"Rebalance: closed {worst['symbol']} ({worst['pnl']:+.2f}%) to deploy into {high_conf[0]['symbol']}"
        )
    except Exception as e:
        memory.log_error("rebalance", str(e))


# The Trader consumes orders/execution_plan, which only exists if the whole
# chain runs: analysis -> sentiment/regime -> risk -> portfolio -> compliance -> execution
CYCLE_AGENTS = (
    Orchestrator,
    HealthMonitor,
    SentimentAgent,
    NewsAgent,  # self-throttled RSS scan; no-op inside NEWS_INTERVAL_MIN
    RegimeAgent,
    ResearchAnalyst,
    RiskManager,
    PositionSizer,
    PortfolioManagerAgent,
    ComplianceAgent,
    ExecutionAgent,
    Trader,
    Auditor,
    HeadTrader,  # self-throttled LLM review; no-op without HERMES_API_KEY
)


_cycle_count = 0
_trigger_lock = threading.Lock()


def process_price_triggers(prices):
    """Check SL/TP/trailing triggers and close each one through the broker.

    Every caller of pos_mgr.update_prices() must go through here: update_prices()
    closes the SQLite position, and without the matching broker order the sale
    proceeds are never credited back to cash — the position's full value would
    silently vanish from equity. The lock keeps the cycle thread and the
    monitor loop from racing on the same trigger.
    """
    with _trigger_lock:
        # The websocket feed only streams Binance crypto; stock/metal symbols
        # would otherwise never have their SL/TP checked. Fill the gaps from
        # the latest market scan (refreshed every cycle).
        merged = dict(prices)
        scan = ((memory.read("analyses", "market_scan") or {}) if memory else {}).get("all_analyses", {}) or {}
        for sym, d in scan.items():
            if sym not in merged and isinstance(d, dict) and d.get("price"):
                merged[sym] = {"price": d["price"]}
        triggered = pos_mgr.update_prices(merged)
        fills = pending_orders.check_fills(merged, pos_mgr.has_position)
        if not triggered and not fills:
            return []
        broker = make_broker_with_retry()
        for tr in triggered:
            close_side = "SELL" if tr["side"] == "BUY" else "BUY"
            order = broker.place_order(tr["symbol"], close_side, tr["qty"], tr["exit_price"])
            memory.log("system", f"{tr['reason']}: {tr['symbol']} ${tr['pnl']:+.2f} (exit {order.get('status', '?')})")
            notifier.on_sl_tp(tr)
        for po in fills:
            order = broker.place_order(po["symbol"], "BUY", po["quantity"], po["limit_price"],
                                       sl=po["stop_loss"], tp=po["take_profit"])
            if order.get("status") == "filled":
                pos_mgr.open_position(po["symbol"], "BUY", order.get("quantity") or po["quantity"],
                                      order.get("price") or po["limit_price"],
                                      sl=po["stop_loss"], tp=po["take_profit"],
                                      strategy=po.get("strategy", ""))
                memory.log("system", f"limit filled: BUY {po['symbol']} x{po['quantity']:g} @ ${po['limit_price']}")
                notifier.on_trade({"symbol": po["symbol"], "side": "BUY",
                                   "qty": po["quantity"], "price": po["limit_price"],
                                   "stop_loss": po["stop_loss"], "take_profit": po["take_profit"],
                                   "status": "filled"})
        return triggered


def run_cycle():
    global _cycle_count
    _cycle_count += 1
    try:
        for agent_cls in CYCLE_AGENTS:
            agent_cls().run()

        # Always run: the websocket feed is Binance-only and geo-blocked on
        # some hosts (US VPS) — the trigger processor falls back to the
        # analyst's scan prices internally, so an empty feed must not skip it.
        process_price_triggers(websocket_prices.get_all_prices() or {})

        _rebalance_positions()
        snapshot_equity()
        try:
            check_goals(notifier)
        except Exception as e:
            memory.log("system", f"Goal check warning: {e}")
        completed_day = pop_completed_day()
        if completed_day:
            notifier.daily_summary(build_daily_summary(completed_day))
        if _cycle_count % 30 == 0:
            notifier.portfolio_snapshot(pos_mgr.get_positions_summary())
        sync_position_stores()
    except Exception as e:
        memory.log("system", f"Cycle error: {e}")
        memory.log_error("cycle", str(e), traceback.format_exc())
        notifier.on_error(str(e))


def sync_position_stores():
    """Synchronize SQLite positions/trades into portfolio.json.

    SQLite (pos_mgr) is the authoritative source for positions and trades.
    portfolio.json keeps cash and initial_balance, which have no SQLite equivalent.
    """
    try:
        from core.portfolio import Position, load_portfolio, save_portfolio
        p = load_portfolio()
        sql_positions = pos_mgr.get_open_positions()
        p.positions = {}
        for sp in sql_positions:
            p.positions[sp["symbol"]] = Position(
                symbol=sp["symbol"], entry_price=sp["entry_price"],
                quantity=sp["quantity"], current_price=sp["current_price"],
                pnl=sp["pnl"], pnl_pct=sp["pnl_pct"],
            )
        sql_trades = pos_mgr.get_recent_trades(50)
        p.trades = sql_trades
        save_portfolio(p)
    except Exception as e:
        memory.log("system", f"Position sync warning: {e}")


def main():
    global live_broker, memory

    # Lock check before --reset so we don't delete a live DB out from under another instance
    if not acquire_instance_lock():
        console.print(f"[bold red]Another instance already holds lock port {LOCK_PORT} — "
                      "exiting to prevent duplicate trading.[/bold red]")
        if memory:
            memory.log("system", "Startup aborted: another instance is already running")
        sys.exit(1)

    if RESET:
        import shutil
        console.print("[bold yellow]--reset: wiping all data...[/bold yellow]")
        # Force-close any lingering SQLite connections
        try:
            from core.database import get_connection
            with get_connection() as conn:
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except Exception:
            pass
        db_file = DATA_DIR / "trading.db"
        # Backup existing database before deletion
        if db_file.exists():
            from shutil import copy2
            backup_dir = DATA_DIR.parent / "data_backups"
            backup_dir.mkdir(parents=True, exist_ok=True)
            backup = backup_dir / f"trading.db.backup-{datetime.now():%Y%m%d_%H%M%S}"
            try:
                copy2(db_file, backup)
                console.print(f"[dim]Backed up {db_file.name} -> {backup}[/dim]")
            except Exception as e:
                console.print(f"[dim]Backup skipped: {e}[/dim]")
        # Force GC to release Windows SQLite file locks before deletion
        import gc as _gc
        _gc.collect()
        time.sleep(0.5)
        for ext in ("", "-wal", "-shm", "-journal"):
            p = Path(str(db_file) + ext)
            try:
                if p.exists():
                    p.unlink()
            except PermissionError:
                pass
        try:
            if DATA_DIR.exists():
                shutil.rmtree(DATA_DIR)
                console.print(f"[dim]Removed {DATA_DIR}[/dim]")
        except PermissionError as e:
            console.print(f"[bold red]Failed to remove {DATA_DIR}: {e}[/bold red]")
            console.print("[yellow]Close any other programs using this directory and retry.[/yellow]")
            sys.exit(1)
        # Recreate subdirectories so subsequent SharedMemory writes don't crash
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        for sub in ("analyses", "decisions", "orders", "reports", "logs"):
            (DATA_DIR / sub).mkdir(parents=True, exist_ok=True)
        # Fresh portfolio from INITIAL_BALANCE
        from core.portfolio import Portfolio as _P, save_portfolio as _sp
        _sp(_P(cash=INITIAL_BALANCE, initial_balance=INITIAL_BALANCE))
        console.print(f"[bold green]Data reset complete. Fresh ${INITIAL_BALANCE:,.0f} capital initialized.[/bold green]")

    # Lazy — initialized inside main() so --reset can run before any dirs are created
    if memory is None:
        memory = SharedMemory()

    console.clear()
    console.print("[bold cyan]Trading Agent Firm[/bold cyan]")

    # Startup diagnostics
    console.print(f"[dim]DATA_DIR: {DATA_DIR}[/dim]")
    console.print(f"[dim]BROKER_TYPE: {BROKER_TYPE}[/dim]")
    console.print(f"[dim]PLATFORM: {sys.platform}[/dim]")
    memory.log("system", f"startup: DATA_DIR={DATA_DIR} BROKER_TYPE={BROKER_TYPE} platform={sys.platform}")

    init_db()
    console.print("[dim]Database initialized[/dim]")

    ws_testnet = BROKER_TYPE == "binance" and BINANCE_USE_TESTNET
    websocket_prices.start(testnet=ws_testnet)
    console.print("[dim]WebSocket price feed started[/dim]")

    web_port = start_webserver()
    if web_port:
        console.print(f"[dim]Dashboard running on port {web_port}[/dim]")
    else:
        console.print("[dim]Dashboard unavailable[/dim]")

    if BROKER_TYPE == "binance":
        live_broker = BinanceBroker(BINANCE_API_KEY, BINANCE_API_SECRET, BINANCE_USE_TESTNET)
        if live_broker.connected:
            memory.log("system", "Binance testnet connected")
        else:
            memory.log("system", "Binance not connected — using paper fallback")
    portfolio = load_portfolio()
    if portfolio.initial_balance == 0:
        portfolio.initial_balance = INITIAL_BALANCE
        portfolio.cash = INITIAL_BALANCE
        save_portfolio(portfolio)
    init_cap = portfolio.initial_balance
    set_meta("initial_balance", str(init_cap))
    console.print(f"[dim]Initial capital: ${init_cap:,.2f}[/dim]\n")

    if live_broker and live_broker.connected and hasattr(live_broker, "get_balances"):
        recon = reconcile_with_exchange(live_broker)
        if recon:
            console.print(f"[dim]Reconciliation: {recon['drifted_positions']} of "
                          f"{len(recon['positions'])} tracked positions drift from exchange[/dim]")

    snapshot_equity()
    sync_position_stores()

    for pos in pos_mgr.get_open_positions():
        memory.log("system", f"Restored position: {pos['side']} {pos['quantity']} {pos['symbol']} @ ${pos['entry_price']:.5f}")
    existing = pos_mgr.get_positions_summary()
    if existing["count"] > 0:
        console.print(f"[yellow]Restored {existing['count']} open position(s)[/yellow]")

    if notifier._enabled:
        notifier.start_polling()
        if RESET:
            notifier.send(f"[Trading Agent Firm - Fresh Test Initialized with ${INITIAL_BALANCE:,.0f} USD Capital]")
        else:
            notifier.send("[Trading Agent Firm started]")

    console.print("[dim]Running backtests on key symbols...[/dim]")
    try:
        bt_symbols = [s for s in WATCHED_SYMBOLS if "/" in s][:10]
        bt_results = run_all_backtests(bt_symbols)
        console.print(f"[dim]Backtested {len(bt_results)} symbols[/dim]")
        for r in bt_results[:10]:
            c = "green" if r["total_return"] >= 0 else "red"
            console.print(f"  {r['symbol']:8s}  [{c}]{r['total_return']:+.1f}%[/{c}]  "
                          f"{r['total_trades']}t  WR:{r['win_rate']:.0f}%  "
                          f"S:{r['sharpe_ratio']}  DD:{r['max_drawdown']:.1f}%")
    except Exception as e:
        # Backtests are informational — never let them kill the bot
        memory.log("system", f"Startup backtests failed: {e}")
        memory.log_error("backtester", str(e), traceback.format_exc())

    def cycle_loop():
        while True:
            run_cycle()
            time.sleep(TRADING_INTERVAL_MINUTES * 60)

    thread = threading.Thread(target=cycle_loop, daemon=True)
    thread.start()

    def optimizer_loop():
        from agents.optimizer_agent import OptimizerAgent
        while True:
            try:
                OptimizerAgent().run()
            except Exception as e:
                memory.log_error("optimizer", str(e))
            time.sleep(7200)  # every 2 hours

    opt_thread = threading.Thread(target=optimizer_loop, daemon=True)
    opt_thread.start()

    try:
        if HEADLESS:
            while True:
                process_price_triggers(websocket_prices.get_all_prices() or {})
                sync_position_stores()
                snapshot_equity()
                time.sleep(30)
        with Live(make_layout(portfolio, pos_mgr, memory, live_broker), refresh_per_second=2, screen=True) as live:
            while True:
                prices = websocket_prices.get_all_prices()
                if not prices:
                    analysis = memory.read("analyses", "market_scan")
                    if analysis:
                        prices = {s: {"price": d.get("price", 0)}
                                 for s, d in (analysis.get("all_analyses", {}) or {}).items()}
                process_price_triggers(prices or {})
                sync_position_stores()
                portfolio = load_portfolio()
                live.update(make_layout(portfolio, pos_mgr, memory, live_broker))
                time.sleep(2)
    except KeyboardInterrupt:
        console.print("\n[bold yellow]Shutting down...[/bold yellow]")
        memory.log("system", "Trading firm stopped by user")
        websocket_prices.stop()


if __name__ == "__main__":
    main()

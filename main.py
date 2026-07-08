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

from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich import box

from config import DATA_DIR, INITIAL_BALANCE, TRADING_INTERVAL_MINUTES, BROKER_TYPE, BINANCE_API_KEY, BINANCE_API_SECRET, BINANCE_USE_TESTNET, MT5_LOGIN, MT5_PASSWORD, MT5_SERVER, DXTRADE_API_URL, DXTRADE_USERNAME, DXTRADE_PASSWORD, DXTRADE_DOMAIN, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, WATCHED_SYMBOLS, LOCK_PORT
from core.broker import PaperBroker
from core.binance_broker import BinanceBroker
from core.live_broker import MetaQuotesBroker
from core.dxtrade_broker import DXTradeBroker
from core.portfolio import load_portfolio, save_portfolio, Portfolio
from core.memory import SharedMemory
from core.database import init_db, fetchall
from core.positions import PositionManager
from core import websocket_prices
from core.notifier import Notifier
from core.analytics import get_analytics, get_strategy_stats
from core.webserver import start_webserver, get_market_prices
from core.backtester import run_all_backtests, get_backtest_results, backtest_symbol
from core.equity import snapshot_equity, build_daily_summary, pop_completed_day
from core.reconcile import reconcile_with_exchange
from agents.orchestrator import Orchestrator
from agents.analyst import ResearchAnalyst
from agents.sentiment_agent import SentimentAgent
from agents.regime_agent import RegimeAgent
from agents.pricing_agent import PricingAgent
from agents.risk_manager import RiskManager
from agents.position_sizer import PositionSizer
from agents.portfolio_manager import PortfolioManagerAgent
from agents.compliance_agent import ComplianceAgent
from agents.execution_agent import ExecutionAgent
from agents.trader import Trader
from agents.auditor import Auditor
from agents.optimizer_agent import OptimizerAgent
from agents.health_monitor import HealthMonitor

console = Console()
memory = SharedMemory()


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
    if BROKER_TYPE == "mt5":
        return MetaQuotesBroker(MT5_LOGIN, MT5_PASSWORD, MT5_SERVER)
    if BROKER_TYPE == "binance":
        return BinanceBroker(BINANCE_API_KEY, BINANCE_API_SECRET, BINANCE_USE_TESTNET)
    if BROKER_TYPE == "dxtrade":
        return DXTradeBroker(DXTRADE_API_URL, DXTRADE_USERNAME, DXTRADE_PASSWORD, DXTRADE_DOMAIN)
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
    """Close worst underwater position if better opportunities exist."""
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
    ResearchAnalyst,
    HealthMonitor,
    SentimentAgent,
    RegimeAgent,
    PricingAgent,
    RiskManager,
    PositionSizer,
    PortfolioManagerAgent,
    ComplianceAgent,
    ExecutionAgent,
    Trader,
    Auditor,
    OptimizerAgent,
)


_cycle_count = 0


def run_cycle():
    global _cycle_count
    _cycle_count += 1
    try:
        for agent_cls in CYCLE_AGENTS:
            agent_cls().run()

        prices = websocket_prices.get_all_prices()
        if prices:
            triggered = pos_mgr.update_prices(prices)
            broker = make_broker_with_retry() if triggered else None
            for tr in triggered:
                close_side = "SELL" if tr["side"] == "BUY" else "BUY"
                order = broker.place_order(tr["symbol"], close_side, tr["qty"], tr["exit_price"])
                memory.log("system", f"{tr['reason']}: {tr['symbol']} ${tr['pnl']:+.2f} (exit {order.get('status', '?')})")
                notifier.on_sl_tp(tr)

        _rebalance_positions()
        snapshot_equity()
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


def make_positions_panel() -> Panel:
    summary = pos_mgr.get_positions_summary()
    if summary["count"] == 0:
        return Panel("[dim]No open positions[/dim]", title="[bold cyan]Positions[/bold cyan]")
    table = Table(box=box.SIMPLE)
    table.add_column("Symbol", style="yellow")
    table.add_column("Side", width=5)
    table.add_column("Qty", justify="right", width=10)
    table.add_column("Entry", justify="right", width=10)
    table.add_column("Price", justify="right", width=10)
    table.add_column("SL", justify="right", width=10)
    table.add_column("TP", justify="right", width=10)
    table.add_column("P&L", justify="right", width=10)
    rows = summary["positions"]
    for pos in rows[:20]:
        color = "green" if pos["pnl"] >= 0 else "red"
        table.add_row(
            pos["symbol"],
            f"[{'green' if pos['side']=='BUY' else 'red'}]{pos['side'][:4]}[/]",
            f"{pos['quantity']:.4f}",
            f"${pos['entry_price']:.5f}",
            f"${pos['current_price']:.5f}",
            f"${pos['stop_loss']:.5f}" if pos["stop_loss"] else "-",
            f"${pos['take_profit']:.5f}" if pos["take_profit"] else "-",
            f"[{color}]${pos['pnl']:+.2f}[/{color}]",
        )
    extra = len(rows) - 20
    if extra > 0:
        table.add_row("[dim]...[/dim]", "", "", "", "", "", "", f"[dim]{extra} more[/dim]")
    return Panel(table, title=f"[bold cyan]Positions ({summary['count']})[/bold cyan]", box=box.ROUNDED)


def make_status_panel(portfolio: Portfolio) -> Panel:
    live_balance = None
    if BROKER_TYPE == "mt5" and live_broker and live_broker.connected:
        try:
            live_balance = live_broker.get_account_info()
        except Exception:
            pass
    elif BROKER_TYPE == "binance" and live_broker and live_broker.connected:
        try:
            live_balance = live_broker.get_account_info()
        except Exception:
            pass
    elif BROKER_TYPE == "dxtrade" and live_broker and live_broker.connected:
        try:
            live_balance = live_broker.get_account_info()
        except Exception:
            pass

    text = Text()
    text.append(f"Paper Equity: ${portfolio.equity:,.2f}\n", style="bold white")
    text.append(f"Paper P&L: ${portfolio.total_pnl:+,.2f} ", style="bold green" if portfolio.total_pnl >= 0 else "bold red")
    text.append(f"({portfolio.total_pnl_pct:+.2f}%)\n", style="green" if portfolio.total_pnl_pct >= 0 else "red")
    text.append(f"Cash: ${portfolio.cash:,.2f}  ", style="green")
    text.append(f"Exposure: {portfolio.exposure_pct:.1f}%", style="yellow" if portfolio.exposure_pct > 60 else "white")
    if live_balance:
        bal = live_balance.get("balance", live_balance.get("free", 0))
        text.append(f"\n{BROKER_TYPE.upper()} Live: ${bal:,.2f}", style="cyan")
    return Panel(text, title="[bold cyan]Status[/bold cyan]", box=box.ROUNDED)


def make_trades_panel() -> Panel:
    trades = pos_mgr.get_recent_trades(20)
    if not trades:
        return Panel("[dim]No trades yet[/dim]", title="[bold cyan]Closed Trades[/bold cyan]")
    table = Table(box=box.SIMPLE)
    table.add_column("Time", style="dim", width=10)
    table.add_column("Symbol", style="yellow")
    table.add_column("Side", width=5)
    table.add_column("Qty", justify="right", width=8)
    table.add_column("Entry", justify="right", width=10)
    table.add_column("Exit", justify="right", width=10)
    table.add_column("Reason", width=8)
    table.add_column("P&L", justify="right", width=10)
    for t in trades[:15]:
        color = "green" if t["pnl"] >= 0 else "red"
        ts = t.get("closed_at", "")
        tm = ts[11:19] if len(ts) > 19 else ts[:10]
        table.add_row(
            tm,
            t["symbol"],
            f"[{'green' if t['side']=='BUY' else 'red'}]{t['side'][:4]}[/]",
            f"{t['qty']:.4f}",
            f"${t['entry_price']:.5f}",
            f"${t['exit_price']:.5f}",
            t.get("reason", "close")[:8],
            f"[{color}]${t['pnl']:+.2f}[/{color}]",
        )
    extra = len(trades) - 15
    if extra > 0:
        table.add_row("", "[dim]...[/dim]", "", "", "", "", "", f"[dim]{extra} more[/dim]")
    return Panel(table, title="[bold cyan]Closed Trades[/bold cyan]", box=box.ROUNDED)


def make_analytics_panel() -> Panel:
    a = get_analytics()
    if a["total_trades"] == 0:
        return Panel("[dim]No trade data yet[/dim]", title="[bold cyan]Analytics[/bold cyan]")
    text = Text()
    text.append(f"Trades: {a['total_trades']}  ", style="bold")
    text.append(f"Win: {a['win_rate']:.0f}%\n", style="green" if a['win_rate'] >= 50 else "red")
    text.append(f"P&L: ${a['total_pnl']:+,.2f}  ", style="green" if a['total_pnl'] >= 0 else "red")
    text.append(f"PF: {a['profit_factor'] or '--'}\n", style="yellow")
    text.append(f"Sharpe: {a['sharpe_ratio']}  ", style="cyan")
    text.append(f"MaxDD: {a.get('max_drawdown_pct', a.get('max_drawdown', 0)):.1f}%\n", style="red" if a.get('max_drawdown_pct', 0) > 10 else "white")
    text.append(f"Expect: ${a['expectancy']:+.2f}", style="green" if a['expectancy'] >= 0 else "red")
    strat = a.get("strategy_breakdown", [])
    if strat:
        text.append("\n\n[bold]Best Strategy:[/bold]\n", style="white")
        best = strat[0]
        text.append(f"  {best['strategy']}: {best['trades']}t {best['win_rate']:.0f}% ${best['pnl']:+.2f}", style="green")
    return Panel(text, title="[bold cyan]Analytics[/bold cyan]", box=box.ROUNDED)


def make_backtest_panel() -> Panel:
    results = get_backtest_results()
    if not results:
        return Panel("[dim]No backtest data[/dim]", title="[bold cyan]Backtest[/bold cyan]")
    table = Table(box=box.SIMPLE)
    table.add_column("Symbol", style="yellow")
    table.add_column("Return", justify="right", width=8)
    table.add_column("Trades", justify="right", width=6)
    table.add_column("Win%", justify="right", width=6)
    table.add_column("PF", justify="right", width=6)
    table.add_column("Sharpe", justify="right", width=7)
    table.add_column("MaxDD", justify="right", width=7)
    for r in results[:10]:
        ret_c = "green" if r["total_return"] >= 0 else "red"
        table.add_row(
            r["symbol"][:8],
            f"[{ret_c}]{r['total_return']:+.1f}%[/{ret_c}]",
            str(r["total_trades"]),
            f"{r['win_rate']:.0f}%",
            f"{r['profit_factor'] or '--'}",
            f"{r['sharpe_ratio']}",
            f"{r['max_drawdown']:.1f}%",
        )
    return Panel(table, title="[bold cyan]Backtest (90d)[/bold cyan]", box=box.ROUNDED)


def make_activity_panel() -> Panel:
    logs = memory.get_recent_logs(15)
    table = Table(box=box.SIMPLE, show_header=False)
    table.add_column("Agent", style="cyan", width=14)
    table.add_column("Message", style="white")
    for entry in reversed(logs):
        agent = entry.get("agent", "?")
        msg = entry.get("message", "")
        table.add_row(f"[bold]{agent}[/bold]", msg[:70])
    return Panel(table, title="[bold cyan]Activity Log[/bold cyan]", box=box.ROUNDED)


def make_market_panel() -> Panel:
    prices = websocket_prices.get_all_prices()
    if not prices:
        return Panel("[dim]Waiting for price data...[/dim]", title="[bold cyan]Market Prices[/bold cyan]")
    table = Table(box=box.SIMPLE)
    table.add_column("Symbol", style="yellow")
    table.add_column("Price", justify="right", width=12)
    table.add_column("24h", justify="right", width=8)
    table.add_column("Vol", justify="right", width=10)
    for sym in sorted(prices.keys()):
        p = prices[sym]
        price = p.get("price", 0)
        chg = p.get("change_24h", 0)
        vol = p.get("volume_24h", 0)
        chg_color = "green" if chg >= 0 else "red"
        vol_s = f"${vol/1e6:.1f}M" if vol >= 1e6 else f"${vol:,.0f}"
        table.add_row(
            sym[:10],
            f"${price:,.2f}" if price > 1 else f"${price:.5f}",
            f"[{chg_color}]{chg:+.2f}%[/{chg_color}]",
            vol_s,
        )
    return Panel(table, title=f"[bold cyan]Market Prices ({len(prices)})[/bold cyan]", box=box.ROUNDED)


def make_opportunities_panel() -> Panel:
    analysis = memory.read("analyses", "market_scan")
    if not analysis:
        return Panel("[dim]No data yet[/dim]", title="[bold cyan]Opportunities[/bold cyan]")
    opps = analysis.get("opportunities", [])
    if not opps:
        return Panel("[yellow]No opportunities found[/yellow]", title="[bold cyan]Opportunities[/bold cyan]")

    opps = pos_mgr.filter_new_signals(opps)
    if not opps:
        return Panel("[yellow]All symbols have open positions[/yellow]", title="[bold cyan]Opportunities[/bold cyan]")

    table = Table(box=box.SIMPLE)
    table.add_column("Symbol", style="yellow")
    table.add_column("Signal", style="green")
    table.add_column("Price", justify="right")
    table.add_column("Conf.", justify="right")
    table.add_column("Reasons")
    for opp in opps[:12]:
        table.add_row(
            opp["symbol"],
            opp.get("action", "?"),
            f"${opp['price']:.2f}" if opp['price'] else "-",
            f"{opp['confidence']:.0%}",
            ", ".join(opp.get("reasons", [])[:2]),
        )
    extra = len(opps) - 12
    if extra > 0:
        table.add_row("[dim]...[/dim]", "", "", "", f"[dim]{extra} more[/dim]")
    return Panel(table, title="[bold cyan]Opportunities[/bold cyan]", box=box.ROUNDED)


def make_risk_panel() -> Panel:
    risk = memory.read("decisions", "risk_assessment")
    if not risk:
        return Panel("[dim]No risk data[/dim]", title="[bold cyan]Risk[/bold cyan]")
    verdict = risk.get("verdict", "unknown")
    color = {"low": "green", "moderate_risk": "yellow",
             "high_risk": "red", "critical": "bold red"}.get(verdict, "white")
    risks = risk.get("risks", [])
    text = Text()
    text.append(f"Verdict: ", style="bold")
    text.append(f"{verdict}\n", style=color)
    text.append(f"Exposure: {risk.get('exposure_pct', 0)}%\n")
    text.append(f"Max trade: ${risk.get('max_trade_size', 0):,.2f}\n")
    for r in risks[:3]:
        text.append(f"- {r}\n", style="red")
    return Panel(text, title="[bold cyan]Risk Assessment[/bold cyan]", box=box.ROUNDED)


def make_layout(portfolio) -> Layout:
    layout = Layout()
    layout.split(
        Layout(name="header", size=3),
        Layout(name="body"),
        Layout(name="footer", size=3),
    )

    header_text = Text("Trading Agent Firm", style="bold white on blue", no_wrap=True)
    header_text.append(f"  |  Cycle every {TRADING_INTERVAL_MINUTES}m  |  "
                       f"{datetime.now().strftime('%H:%M:%S')}", style="white")

    layout["header"].update(Panel(header_text, box=box.SIMPLE))
    layout["body"].split_row(
        Layout(name="left"),
        Layout(name="right"),
    )
    layout["left"].split(
        Layout(name="left-top"),
        Layout(name="left-mid", ratio=3),
        Layout(name="left-bot"),
    )
    layout["left-top"].split_row(
        Layout(make_status_panel(portfolio), ratio=2),
        Layout(make_market_panel(), ratio=3),
    )
    layout["left-mid"].update(make_positions_panel())
    layout["left-bot"].update(make_opportunities_panel())
    layout["right"].split(
        Layout(name="right-top"),
        Layout(name="right-mid", ratio=2),
        Layout(name="right-bot"),
    )
    layout["right-top"].split_row(
        Layout(make_analytics_panel()),
        Layout(make_backtest_panel()),
        Layout(make_risk_panel()),
    )
    layout["right-mid"].update(make_trades_panel())
    layout["right-bot"].update(make_activity_panel())
    layout["footer"].update(Panel(
        f"[dim]Press Ctrl+C to stop  |  "
        "Spot-only (no leverage)  |  "
        f"Broker: {BROKER_TYPE.upper()}  |  "
        f"Open: {pos_mgr.get_positions_summary()['count']}  |  "
        "Agents: Orchestrator → Analyst → Sentiment → Regime → Pricing → Risk → PositionSizer → PortfolioMgr → Compliance → Execution → Trader → Auditor → Optimizer[/dim]",
        box=box.SIMPLE,
    ))
    return layout


def main():
    global live_broker

    # --reset MUST run first, before any module-level init touches DATA_DIR
    if RESET:
        import shutil
        console.print("[bold yellow]--reset: wiping all data...[/bold yellow]")
        db_file = DATA_DIR / "trading.db"
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
        console.print("[bold green]Data reset complete. Starting fresh.[/bold green]")

    console.clear()
    console.print("[bold cyan]Trading Agent Firm[/bold cyan]")

    # Startup diagnostics
    console.print(f"[dim]DATA_DIR: {DATA_DIR}[/dim]")
    console.print(f"[dim]BROKER_TYPE: {BROKER_TYPE}[/dim]")
    console.print(f"[dim]PLATFORM: {sys.platform}[/dim]")
    if BROKER_TYPE == "mt5" and sys.platform != "win32":
        console.print("[bold yellow]WARNING: MT5 requires Windows — broker will fall back to paper[/bold yellow]")
    memory.log("system", f"startup: DATA_DIR={DATA_DIR} BROKER_TYPE={BROKER_TYPE} platform={sys.platform}")

    if not acquire_instance_lock():
        console.print(f"[bold red]Another instance already holds lock port {LOCK_PORT} — "
                      "exiting to prevent duplicate trading.[/bold red]")
        memory.log("system", "Startup aborted: another instance is already running")
        sys.exit(1)

    init_db()
    console.print("[dim]Database initialized[/dim]")

    ws_testnet = BROKER_TYPE == "binance" and BINANCE_USE_TESTNET
    websocket_prices.start(testnet=ws_testnet)
    console.print("[dim]WebSocket price feed started[/dim]")

    web_port = start_webserver()
    console.print(f"[dim]Dashboard running on port {web_port}[/dim]")

    if BROKER_TYPE == "mt5":
        live_broker = MetaQuotesBroker(MT5_LOGIN, MT5_PASSWORD, MT5_SERVER)
        if live_broker.connected:
            info = live_broker.get_account_info()
            memory.log("system", f"MT5 connected: {info['name']}, ${info['balance']} {info['currency']}")
            portfolio = load_portfolio()
            if portfolio.initial_balance == 0:
                portfolio.initial_balance = info['balance']
                portfolio.cash = info['balance']
                save_portfolio(portfolio)
        else:
            memory.log("system", "MT5 not connected — using paper fallback")
    elif BROKER_TYPE == "binance":
        live_broker = BinanceBroker(BINANCE_API_KEY, BINANCE_API_SECRET, BINANCE_USE_TESTNET)
        if live_broker.connected:
            memory.log("system", "Binance testnet connected")
        else:
            memory.log("system", "Binance not connected — using paper fallback")
    elif BROKER_TYPE == "dxtrade":
        live_broker = DXTradeBroker(DXTRADE_API_URL, DXTRADE_USERNAME, DXTRADE_PASSWORD, DXTRADE_DOMAIN)
        if live_broker.connected:
            info = live_broker.get_account_info()
            memory.log("system", f"DXtrade connected: account {info.get('account') or info.get('accountId', '?')}, balance ${info.get('balance', 0)}")
            portfolio = load_portfolio()
            if portfolio.initial_balance == 0:
                portfolio.initial_balance = info.get('balance', INITIAL_BALANCE)
                portfolio.cash = info.get('balance', INITIAL_BALANCE)
                save_portfolio(portfolio)
        else:
            memory.log("system", "DXtrade not connected — using paper fallback")
    portfolio = load_portfolio()
    if portfolio.initial_balance == 0:
        portfolio.initial_balance = INITIAL_BALANCE
        portfolio.cash = INITIAL_BALANCE
        save_portfolio(portfolio)
    init_cap = portfolio.initial_balance
    console.print(f"[dim]Initial capital: ${init_cap:,.2f}[/dim]\n")

    if live_broker and live_broker.connected and hasattr(live_broker, "get_balances"):
        recon = reconcile_with_exchange(live_broker)
        if recon:
            console.print(f"[dim]Reconciliation: {recon['drifted_positions']} of "
                          f"{len(recon['positions'])} tracked positions drift from exchange[/dim]")

    snapshot_equity()

    for pos in pos_mgr.get_open_positions():
        memory.log("system", f"Restored position: {pos['side']} {pos['quantity']} {pos['symbol']} @ ${pos['entry_price']:.5f}")
    existing = pos_mgr.get_positions_summary()
    if existing["count"] > 0:
        console.print(f"[yellow]Restored {existing['count']} open position(s)[/yellow]")

    if notifier._enabled:
        notifier.send("[Trading Agent Firm started]")

    console.print("[dim]Running backtests on key symbols...[/dim]")
    try:
        bt_symbols = [s for s in WATCHED_SYMBOLS if "/" in s][:5]
        bt_results = run_all_backtests(bt_symbols)
        console.print(f"[dim]Backtested {len(bt_results)} symbols[/dim]")
        for r in bt_results[:3]:
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

    try:
        if HEADLESS:
            console.print("[dim]Headless mode: dashboard disabled, updates via Telegram and logs[/dim]")
            while True:
                prices = websocket_prices.get_all_prices()
                if prices:
                    pos_mgr.update_prices(prices)
                    sync_position_stores()
                time.sleep(30)
        with Live(make_layout(portfolio), refresh_per_second=2, screen=True) as live:
            while True:
                prices = websocket_prices.get_all_prices()
                if not prices:
                    analysis = memory.read("analyses", "market_scan")
                    if analysis:
                        prices = {s: {"price": d.get("price", 0)}
                                 for s, d in (analysis.get("all_analyses", {}) or {}).items()}
                if prices:
                    pos_mgr.update_prices(prices)
                    sync_position_stores()
                portfolio = load_portfolio()
                live.update(make_layout(portfolio))
                time.sleep(2)
    except KeyboardInterrupt:
        console.print("\n[bold yellow]Shutting down...[/bold yellow]")
        memory.log("system", "Trading firm stopped by user")
        websocket_prices.stop()


if __name__ == "__main__":
    main()

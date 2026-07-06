#!/usr/bin/env python3
import os
import sys
import time
import threading
from datetime import datetime, timezone
from pathlib import Path

# Headless mode: no dashboard, for running on a server (use --headless or HEADLESS=true)
HEADLESS = "--headless" in sys.argv or os.getenv("HEADLESS", "").lower() == "true"

from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich import box

from config import DATA_DIR, INITIAL_BALANCE, TRADING_INTERVAL_MINUTES, BROKER_TYPE, BINANCE_API_KEY, BINANCE_API_SECRET, BINANCE_USE_TESTNET, MT5_LOGIN, MT5_PASSWORD, MT5_SERVER, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, WATCHED_SYMBOLS
from core.broker import PaperBroker
from core.binance_broker import BinanceBroker
from core.mt5_broker import MetaQuotesBroker
from core.portfolio import load_portfolio, save_portfolio, Portfolio
from core.memory import SharedMemory
from core.database import init_db
from core.positions import PositionManager
from core import websocket_prices
from core.notifier import Notifier
from core.analytics import get_analytics, get_strategy_stats
from core.backtester import run_all_backtests, get_backtest_results, backtest_symbol
from agents.orchestrator import Orchestrator
from agents.analyst import ResearchAnalyst
from agents.risk_manager import RiskManager
from agents.trader import Trader
from agents.auditor import Auditor

console = Console()
memory = SharedMemory()
mt5_broker = None
pos_mgr = PositionManager()
notifier = Notifier(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)


def run_cycle():
    try:
        o = Orchestrator()
        a = ResearchAnalyst()
        r = RiskManager()
        t = Trader()
        au = Auditor()
        o.run()
        a.run()
        r.run()
        t.run()
        au.run()

        prices = websocket_prices.get_all_prices()
        if prices:
            triggered = pos_mgr.update_prices(prices)
            for tr in triggered:
                memory.log("system", f"{tr['reason']}: {tr['symbol']} ${tr['pnl']:+.2f}")
                notifier.on_sl_tp(tr)
    except Exception as e:
        memory.log("system", f"Cycle error: {e}")
        notifier.on_error(str(e))


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
    for pos in summary["positions"]:
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
    return Panel(table, title=f"[bold cyan]Positions ({summary['count']})[/bold cyan]", box=box.ROUNDED)


def make_portfolio_table(portfolio: Portfolio) -> Table:
    table = Table(box=box.ROUNDED, title="Positions", title_style="bold cyan")
    table.add_column("Symbol", style="yellow")
    table.add_column("Qty", justify="right")
    table.add_column("Entry", justify="right")
    table.add_column("Price", justify="right")
    table.add_column("P&L", justify="right")
    table.add_column("P&L%", justify="right")

    for sym, pos in sorted(portfolio.positions.items()):
        color = "green" if pos.pnl >= 0 else "red"
        table.add_row(
            sym,
            f"{pos.quantity:.4f}",
            f"${pos.entry_price:.5f}",
            f"${pos.current_price:.5f}",
            f"[{color}]${pos.pnl:.2f}[/{color}]",
            f"[{color}]{pos.pnl_pct:+.2f}%[/{color}]",
        )
    return table


def make_status_panel(portfolio: Portfolio) -> Panel:
    live_balance = None
    if BROKER_TYPE == "mt5" and mt5_broker and mt5_broker.connected:
        try:
            live_balance = mt5_broker.get_account_info()
        except Exception:
            pass
    elif BROKER_TYPE == "binance" and mt5_broker and mt5_broker.connected:
        try:
            live_balance = mt5_broker.get_account_info()
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
    trades = pos_mgr.get_recent_trades(10)
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
    for t in trades:
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
    text.append(f"MaxDD: {a['max_drawdown']:.1f}%\n", style="red" if a['max_drawdown'] > 10 else "white")
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
    for r in results[:6]:
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


def make_opportunities_panel() -> Panel:
    analysis = memory.read_latest("analyses")
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
    for opp in opps[:8]:
        table.add_row(
            opp["symbol"],
            opp.get("action", "?"),
            f"${opp['price']:.2f}" if opp['price'] else "-",
            f"{opp['confidence']:.0%}",
            ", ".join(opp.get("reasons", [])[:2]),
        )
    return Panel(table, title="[bold cyan]Opportunities[/bold cyan]", box=box.ROUNDED)


def make_risk_panel() -> Panel:
    risk = memory.read_latest("decisions")
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
        Layout(make_status_panel(portfolio), size=7),
        Layout(make_positions_panel(), size=8),
        Layout(make_opportunities_panel()),
    )
    layout["right"].split(
        Layout(make_analytics_panel(), size=7),
        Layout(make_backtest_panel(), size=7),
        Layout(make_risk_panel(), size=8),
        Layout(make_trades_panel(), size=8),
        Layout(make_activity_panel()),
    )
    layout["footer"].update(Panel(
        f"[dim]Press Ctrl+C to stop  |  "
        "Spot-only (no leverage)  |  "
        f"Broker: {BROKER_TYPE.upper()}  |  "
        f"Open: {pos_mgr.get_positions_summary()['count']}  |  "
        "Agents: Orchestrator -> Analyst -> Risk Manager -> Trader -> Auditor[/dim]",
        box=box.SIMPLE,
    ))
    return layout


def main():
    global mt5_broker
    console.clear()
    console.print("[bold cyan]Trading Agent Firm[/bold cyan]")

    init_db()
    console.print("[dim]Database initialized[/dim]")

    ws_testnet = BROKER_TYPE == "binance" and BINANCE_USE_TESTNET
    websocket_prices.start(testnet=ws_testnet)
    console.print("[dim]WebSocket price feed started[/dim]")

    if BROKER_TYPE == "mt5":
        mt5_broker = MetaQuotesBroker(MT5_LOGIN, MT5_PASSWORD, MT5_SERVER)
        if mt5_broker.connected:
            info = mt5_broker.get_account_info()
            memory.log("system", f"MT5 connected: {info['name']}, ${info['balance']} {info['currency']}")
            portfolio = load_portfolio()
            portfolio.initial_balance = info['balance']
            portfolio.cash = info['balance']
            init_cap = info['balance']
            save_portfolio(portfolio)
        else:
            memory.log("system", "MT5 not connected — using paper fallback")
            init_cap = INITIAL_BALANCE
    elif BROKER_TYPE == "binance":
        mt5_broker = BinanceBroker(BINANCE_API_KEY, BINANCE_API_SECRET, BINANCE_USE_TESTNET)
        if mt5_broker.connected:
            memory.log("system", "Binance testnet connected")
            portfolio = load_portfolio()
            portfolio.initial_balance = INITIAL_BALANCE
            portfolio.cash = INITIAL_BALANCE
            init_cap = INITIAL_BALANCE
            save_portfolio(portfolio)
        else:
            memory.log("system", "Binance not connected — using paper fallback")
            init_cap = INITIAL_BALANCE
    else:
        init_cap = INITIAL_BALANCE
    portfolio = load_portfolio()
    if portfolio.initial_balance == 0:
        portfolio.initial_balance = INITIAL_BALANCE
        portfolio.cash = INITIAL_BALANCE
        init_cap = INITIAL_BALANCE
        save_portfolio(portfolio)
    console.print(f"[dim]Initial capital: ${init_cap:,.2f}[/dim]\n")

    for pos in pos_mgr.get_open_positions():
        memory.log("system", f"Restored position: {pos['side']} {pos['quantity']} {pos['symbol']} @ ${pos['entry_price']:.5f}")
    existing = pos_mgr.get_positions_summary()
    if existing["count"] > 0:
        console.print(f"[yellow]Restored {existing['count']} open position(s)[/yellow]")

    if notifier._enabled:
        notifier.send("[Trading Agent Firm started]")

    console.print("[dim]Running backtests on key symbols...[/dim]")
    bt_symbols = [s for s in WATCHED_SYMBOLS if "/" in s][:5]
    bt_results = run_all_backtests(bt_symbols)
    console.print(f"[dim]Backtested {len(bt_results)} symbols[/dim]")
    for r in bt_results[:3]:
        c = "green" if r["total_return"] >= 0 else "red"
        console.print(f"  {r['symbol']:8s}  [{c}]{r['total_return']:+.1f}%[/{c}]  "
                      f"{r['total_trades']}t  WR:{r['win_rate']:.0f}%  "
                      f"S:{r['sharpe_ratio']}  DD:{r['max_drawdown']:.1f}%")

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
                portfolio = load_portfolio()
                prices = websocket_prices.get_all_prices()
                if prices:
                    portfolio.update_prices({s: p.get("price", 0) if isinstance(p, dict) else p
                                             for s, p in prices.items()})
                    save_portfolio(portfolio)
                time.sleep(30)
        with Live(make_layout(portfolio), refresh_per_second=2, screen=True) as live:
            while True:
                portfolio = load_portfolio()
                broker = PaperBroker()
                prices = websocket_prices.get_all_prices()
                if not prices:
                    analysis = memory.read_latest("analyses")
                    if analysis:
                        prices = {s: {"price": d.get("price", 0)}
                                 for s, d in (analysis.get("all_analyses", {}) or {}).items()}
                broker.portfolio = portfolio
                broker.portfolio.update_prices({s: p.get("price", 0) if isinstance(p, dict) else p
                                               for s, p in prices.items()})
                save_portfolio(portfolio)
                live.update(make_layout(portfolio))
                time.sleep(2)
    except KeyboardInterrupt:
        console.print("\n[bold yellow]Shutting down...[/bold yellow]")
        memory.log("system", "Trading firm stopped by user")
        websocket_prices.stop()


if __name__ == "__main__":
    main()

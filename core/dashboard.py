from datetime import datetime

from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich import box

from config import BROKER_TYPE, TRADING_INTERVAL_MINUTES
from core.analytics import get_analytics
from core.backtester import get_backtest_results
from core import websocket_prices


def make_positions_panel(pos_mgr):
    summary = pos_mgr.get_positions_summary()
    if summary["count"] == 0:
        return Panel("[dim]No open positions[/dim]", title="[bold cyan]Positions[/bold cyan]")
    table = Table(box=box.SIMPLE)
    table.add_column("Pair", style="yellow")
    table.add_column("Side", width=5)
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
            f"${pos['entry_price']:.5f}",
            f"${pos['current_price']:.5f}",
            f"${pos['stop_loss']:.5f}" if pos["stop_loss"] else "-",
            f"${pos['take_profit']:.5f}" if pos["take_profit"] else "-",
            f"[{color}]${pos['pnl']:+.2f}[/{color}]",
        )
    extra = len(rows) - 20
    if extra > 0:
        table.add_row("[dim]...[/dim]", "", "", "", "", "", f"[dim]{extra} more[/dim]")
    return Panel(table, title=f"[bold cyan]Positions ({summary['count']})[/bold cyan]", box=box.ROUNDED)


def make_status_panel(portfolio, live_broker):
    live_balance = None
    if live_broker and live_broker.connected:
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


def make_trades_panel(pos_mgr):
    trades = pos_mgr.get_recent_trades(20)
    if not trades:
        return Panel("[dim]No trades yet[/dim]", title="[bold cyan]Closed Trades[/bold cyan]")
    table = Table(box=box.SIMPLE)
    table.add_column("Time", style="dim", width=10)
    table.add_column("Pair", style="yellow")
    table.add_column("Side", width=5)
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
            f"${t['entry_price']:.5f}",
            f"${t['exit_price']:.5f}",
            t.get("reason", "close")[:8],
            f"[{color}]${t['pnl']:+.2f}[/{color}]",
        )
    extra = len(trades) - 15
    if extra > 0:
        table.add_row("", "[dim]...[/dim]", "", "", "", "", f"[dim]{extra} more[/dim]")
    return Panel(table, title="[bold cyan]Closed Trades[/bold cyan]", box=box.ROUNDED)


def make_analytics_panel():
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


def make_backtest_panel():
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


def make_activity_panel(memory):
    logs = memory.get_recent_logs(15)
    table = Table(box=box.SIMPLE, show_header=False)
    table.add_column("Agent", style="cyan", width=14)
    table.add_column("Message", style="white")
    for entry in reversed(logs):
        agent = entry.get("agent", "?")
        msg = entry.get("message", "")
        table.add_row(f"[bold]{agent}[/bold]", msg[:70])
    return Panel(table, title="[bold cyan]Activity Log[/bold cyan]", box=box.ROUNDED)


def make_market_panel():
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


def make_opportunities_panel(memory, pos_mgr):
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


def make_risk_panel(memory):
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


def make_layout(portfolio, pos_mgr, memory, live_broker):
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
        Layout(make_status_panel(portfolio, live_broker), ratio=2),
        Layout(make_market_panel(), ratio=3),
    )
    layout["left-mid"].update(make_positions_panel(pos_mgr))
    layout["left-bot"].update(make_opportunities_panel(memory, pos_mgr))
    layout["right"].split(
        Layout(name="right-top"),
        Layout(name="right-mid", ratio=2),
        Layout(name="right-bot"),
    )
    layout["right-top"].split_row(
        Layout(make_analytics_panel()),
        Layout(make_backtest_panel()),
        Layout(make_risk_panel(memory)),
    )
    layout["right-mid"].update(make_trades_panel(pos_mgr))
    layout["right-bot"].update(make_activity_panel(memory))
    layout["footer"].update(Panel(
        f"[dim]Press Ctrl+C to stop  |  "
        "Spot-only (no leverage)  |  "
        f"Broker: {BROKER_TYPE.upper()}  |  "
        f"Open: {pos_mgr.get_positions_summary()['count']}  |  "
        "Agents: Orchestrator -> HealthMonitor -> Sentiment -> Regime -> Analyst -> Risk -> PositionSizer -> PortfolioMgr -> Compliance -> Execution -> Trader -> Auditor -> HeadTrader (+Optimizer bg)[/dim]",
        box=box.SIMPLE,
    ))
    return layout

import requests
from datetime import datetime, timezone


class Notifier:
    def __init__(self, bot_token="", chat_id=""):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self._enabled = bool(bot_token and chat_id)

    def send(self, text):
        if not self._enabled:
            return
        try:
            url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
            requests.post(url, json={"chat_id": self.chat_id, "text": text,
                                     "parse_mode": "HTML"}, timeout=10)
        except Exception:
            pass

    def on_trade(self, order):
        if not self._enabled:
            return
        symbol = order.get("symbol", "?").replace("/", "")
        side = order.get("side", order.get("action", "?"))
        qty = order.get("quantity", order.get("qty", 0))
        price = order.get("price", 0)
        sl = order.get("stop_loss", order.get("sl", 0))
        tp = order.get("take_profit", order.get("tp", 0))
        status = order.get("status", "filled")
        if status == "filled":
            pct = abs((price - sl) / price * 100) if sl and price else 0
            est_profit = abs((tp - price) / price * 100) if tp and price else 0
            now = datetime.now(timezone.utc).strftime("%H:%M:%S")
            self.send(
                f"<b>{now}</b> trader | OPEN {side} {symbol}\n"
                f"Entry ${price:.5f} | SL ${sl:.5f} ({pct:.1f}%)\n"
                f"TP ${tp:.5f} ({est_profit:.1f}%) | Qty {qty:.4f}"
            )

    def on_error(self, message):
        self.send(f"<b>ERROR:</b> {message}")

    def daily_summary(self, s):
        sign = "+" if s["day_pnl_pct"] >= 0 else ""
        self.send(
            f"<b>Daily Summary — {s['date']}</b>\n"
            f"Equity: ${s['equity']:,.2f} ({sign}{s['day_pnl_pct']:.2f}% today, "
            f"{s['total_pnl_pct']:+.2f}% all-time)\n"
            f"Closed trades: {s['trades_closed']}  Win rate: {s['win_rate']:.0f}%\n"
            f"Realized P&L: ${s['pnl_closed']:+,.2f}\n"
            f"Open positions: {s['open_positions']}  Cash: ${s['cash']:,.2f}"
        )

    def on_sl_tp(self, result):
        now = datetime.now(timezone.utc).strftime("%H:%M:%S")
        symbol = result.get("symbol", "?").replace("/", "")
        reason = result.get("reason", "exit").upper()
        pnl = result.get("pnl", 0)
        pnl_pct = result.get("pnl_pct", 0)
        side = result.get("side", "?")
        icon = "+" if pnl >= 0 else "-"
        self.send(
            f"<b>{now}</b> trader | {reason} {side} {symbol} {icon}\n"
            f"P&L ${pnl:+.2f} ({pnl_pct:+.2f}%) "
            f"| Exit ${result.get('exit_price', 0):.5f}"
        )

    def on_agent_action(self, agent_name, action_text):
        """Generic real-time notification for any agent decision."""
        if not self._enabled:
            return
        now = datetime.now(timezone.utc).strftime("%H:%M:%S")
        self.send(f"<b>{now}</b> {agent_name} | {action_text}")

    def portfolio_snapshot(self, portfolio):
        if not self._enabled:
            return
        now = datetime.now(timezone.utc).strftime("%H:%M:%S")
        total_pnl = portfolio.get("total_pnl", 0)
        total_value = portfolio.get("total_value", 0)
        cash = portfolio.get("cash", 0)
        positions = portfolio.get("positions", portfolio.get("open_positions", []))
        pnl_s = f"${total_pnl:+,.2f}"
        positions_str = ""
        for p in positions:
            sym = p.get("symbol", "?")
            side = p.get("side", p.get("direction", "LONG"))
            entry = p.get("entry_price", p.get("avg_price", 0))
            current = p.get("current_price", p.get("mark_price", 0))
            upnl = p.get("unrealized_pnl", (current - entry) if current and entry else 0)
            upnl_s = f"${upnl:+,.2f}" if isinstance(upnl, (int, float)) else "?"
            positions_str += f"\n{sym} {side} @ ${entry:.5f} → ${current:.5f} ({upnl_s})"
        self.send(
            f"<b>{now}</b> Portfolio Snapshot\n"
            f"Value: ${total_value:,.2f} | P&L: {pnl_s}\n"
            f"Cash: ${cash:,.2f} | Positions: {len(positions)}{positions_str}"
        )

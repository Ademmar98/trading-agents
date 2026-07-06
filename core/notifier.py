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
        symbol = order.get("symbol", "?")
        side = order.get("side", "?")
        qty = order.get("quantity", 0)
        price = order.get("price", 0)
        sl = order.get("stop_loss", 0)
        tp = order.get("take_profit", 0)
        status = order.get("status", "?")
        pnl = order.get("realized_pnl", order.get("pnl"))
        icon = "+" if status == "filled" else "-"
        lines = [
            f"<b>[{icon}] {side} {symbol}</b>",
            f"Qty: {qty:.4f}  @  ${price:.5f}",
        ]
        if sl:
            lines.append(f"SL: ${sl:.5f}")
        if tp:
            lines.append(f"TP: ${tp:.5f}")
        if pnl is not None:
            lines.append(f"P&L: ${pnl:+.2f}")
        lines.append(f"Time: {datetime.now(timezone.utc).strftime('%H:%M:%S')}")
        self.send("\n".join(lines))

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
        self.send(
            f"<b>{result['reason'].upper()}</b> {result['side']} {result['symbol']}\n"
            f"P&L: ${result['pnl']:+.2f} ({result['pnl_pct']:+.2f}%)"
        )

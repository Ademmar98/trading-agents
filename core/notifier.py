import json
import threading
import time
from datetime import datetime, timezone
from functools import partial

import requests

from config import TELEGRAM_QUIET


def _fmt_num(n, decimals=2):
    """Format a number with commas and decimals."""
    if n is None:
        return "?"
    return f"{n:,.{decimals}f}"


def _fmt_price(p):
    """Price with sensible precision: $63,850.12 for BTC, $0.0000345 for
    micro-caps — fixed 5 decimals made both ends unreadable."""
    if p is None:
        return "?"
    if p >= 1000:
        return f"{p:,.2f}"
    if p >= 1:
        return f"{p:,.4f}"
    return f"{p:.6g}"


def _fmt_pct(n, decimals=2):
    if n is None:
        return "?"
    sign = "+" if n >= 0 else ""
    return f"{sign}{n:.{decimals}f}%"


class Notifier:
    def __init__(self, bot_token="", chat_id="", allowed_ids=None):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self._enabled = bool(bot_token and chat_id)
        self._allowed_ids = set(allowed_ids or [])
        if chat_id:
            self._allowed_ids.add(chat_id)
        self._poll_thread = None
        self._running = False
        self._offset = 0  # getUpdates offset
        self._handlers = {}
        self._register_commands()

    def _register_commands(self):
        self._handlers = {
            "/start": self._cmd_start,
            "/help": self._cmd_help,
            "/positions": self._cmd_positions,
            "/pnl": self._cmd_pnl,
            "/status": self._cmd_status,
            "/stats": self._cmd_stats,
        }

    def start_polling(self):
        if not self._enabled or self._poll_thread is not None:
            return
        self._running = True
        self._poll_thread = threading.Thread(target=self._poll_loop, daemon=True, name="tg-poll")
        self._poll_thread.start()

    def stop_polling(self):
        self._running = False
        self._poll_thread = None

    def _poll_loop(self):
        while self._running:
            try:
                updates = self._get_updates()
                for u in updates:
                    self._handle_update(u)
            except Exception:
                pass
            time.sleep(1.5)

    def _get_updates(self):
        url = f"https://api.telegram.org/bot{self.bot_token}/getUpdates"
        r = requests.post(url, json={
            "offset": self._offset,
            "timeout": 5,
            "allowed_updates": ["message", "callback_query"],
        }, timeout=10)
        data = r.json()
        if not data.get("ok"):
            return []
        results = data.get("result", [])
        if results:
            self._offset = results[-1]["update_id"] + 1
        return results

    def _authorized(self, chat_id, sender_id):
        """Only the configured chat / allow-listed ids may command the bot.
        The bot's username is discoverable, so without this gate any Telegram
        user could read the portfolio — and the original handler even
        reassigned self.chat_id to the sender, letting a stranger hijack
        every future notification."""
        allowed = self._allowed_ids
        return bool(allowed) and (str(chat_id) in allowed or str(sender_id) in allowed)

    def _handle_update(self, update):
        msg = update.get("message") or {}
        cb = update.get("callback_query") or {}
        if cb:
            self._handle_callback(cb)
            return
        chat_id = str(msg.get("chat", {}).get("id", ""))
        sender_id = str((msg.get("from") or {}).get("id", ""))
        if not chat_id or not self._authorized(chat_id, sender_id):
            return  # silently ignore strangers; never adopt their chat_id
        text = (msg.get("text") or "").strip()
        if not text:
            return
        handler = self._handlers.get(text.split()[0].lower())
        if handler:
            try:
                handler(msg)
            except Exception as e:
                self._send(f"Error: {e}")
        else:
            self._cmd_help(msg)

    def _handle_callback(self, cb):
        data = cb.get("data", "")
        msg = cb.get("message", {})
        chat_id = str(msg.get("chat", {}).get("id", ""))
        sender_id = str((cb.get("from") or {}).get("id", ""))
        if not self._authorized(chat_id, sender_id):
            return
        self._answer_callback(cb["id"])
        handler = self._handlers.get(data)
        if handler:
            try:
                handler(msg)
            except Exception as e:
                self._send(f"Error: {e}")

    def _answer_callback(self, callback_id):
        url = f"https://api.telegram.org/bot{self.bot_token}/answerCallbackQuery"
        try:
            requests.post(url, json={"callback_query_id": callback_id}, timeout=5)
        except Exception:
            pass

    def _send(self, text, keyboard=None, parse_mode="HTML"):
        if not self._enabled or not self.chat_id:
            return
        payload = {"chat_id": self.chat_id, "text": text, "parse_mode": parse_mode}
        if keyboard:
            payload["reply_markup"] = json.dumps(keyboard)
        try:
            url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
            requests.post(url, json=payload, timeout=10)
        except Exception:
            pass

    def _reply_to(self, msg, text, keyboard=None, parse_mode="HTML"):
        chat_id = msg.get("chat", {}).get("id", self.chat_id)
        self.chat_id = str(chat_id)
        self._send(text, keyboard, parse_mode)

    def _inline_keyboard(self, buttons):
        kb = {"inline_keyboard": []}
        for row in buttons:
            kb["inline_keyboard"].append(
                [{"text": b[0], "callback_data": b[1]} for b in row]
            )
        return kb

    # --- Command handlers ---

    def _cmd_start(self, msg=None):
        text = (
            "<b>Trading Firm Bot</b>\n\n"
            "Automated multi-asset trading system.\n\n"
            "<b>Commands:</b>\n"
            "/positions — Open positions\n"
            "/pnl — Profit & Loss\n"
            "/status — System status\n"
            "/stats — Trading stats\n"
            "/help — This menu"
        )
        kb = self._inline_keyboard([
            [("/positions", "/positions"), ("/pnl", "/pnl")],
            [("/status", "/status"), ("/stats", "/stats")],
        ])
        self._reply_to(msg or {}, text, kb)

    def _cmd_help(self, msg=None):
        self._cmd_start(msg)

    def _cmd_positions(self, msg=None):
        try:
            from core.positions import PositionManager
            pm = PositionManager()
            positions = pm.get_open_positions()
        except Exception:
            positions = []
        if not positions:
            text = "<b>Open Positions</b>\n\nNo open positions."
        else:
            lines = [f"<b>Open Positions ({len(positions)})</b>\n"]
            for p in positions:
                sym = p["symbol"]
                side = p["side"]
                entry = p["entry_price"]
                current = p.get("current_price", entry)
                qty = p["quantity"]
                upnl = p.get("pnl", 0)
                upnl_pct = p.get("pnl_pct", 0)
                sl = p.get("stop_loss", 0)
                tp = p.get("take_profit", 0)
                sl_dist = abs(current - sl) / current * 100 if sl and current else 0
                tp_dist = abs(tp - current) / current * 100 if tp and current else 0
                sign = "+" if upnl >= 0 else ""
                lines.append(
                    f"{sym} {side} x{qty:.4f}\n"
                    f"  Entry ${entry:.5f} → ${current:.5f}\n"
                    f"  P&L {sign}${upnl:.2f} ({sign}{upnl_pct:.2f}%)\n"
                    f"  SL ${sl:.5f} ({sl_dist:.1f}%) | TP ${tp:.5f} ({tp_dist:.1f}%)"
                )
            text = "\n".join(lines)
        kb = self._inline_keyboard([
            [("/pnl", "/pnl"), ("/status", "/status")],
        ])
        self._reply_to(msg or {}, text, kb)

    def _cmd_pnl(self, msg=None):
        try:
            from core.portfolio import load_portfolio
            p = load_portfolio()
        except Exception:
            p = None
        try:
            from core.analytics import get_analytics
            a = get_analytics()
        except Exception:
            a = {}
        if p:
            text = (
                f"<b>P&L Summary</b>\n\n"
                f"Equity: ${p.equity:,.2f}\n"
                f"Cash: ${p.cash:,.2f}\n"
                f"Exposure: {p.exposure_pct:.1f}%\n"
                f"Total P&L: {_fmt_pct(p.total_pnl_pct)}\n"
                f"Open Positions: {len(p.positions)}\n"
            )
            if a:
                text += (
                    f"Trades: {a.get('total_trades', 0)}\n"
                    f"Win Rate: {a.get('win_rate', 0):.1f}%\n"
                    f"Profit Factor: {a.get('profit_factor', 0):.2f}\n"
                    f"Expectancy: {_fmt_pct(a.get('expectancy', 0))}\n"
                    f"Max DD: {_fmt_pct(a.get('max_drawdown_pct', 0))}"
                )
        else:
            text = "<b>P&L Summary</b>\n\nUnavailable."
        kb = self._inline_keyboard([
            [("/positions", "/positions"), ("/status", "/status")],
        ])
        self._reply_to(msg or {}, text, kb)

    def _cmd_status(self, msg=None):
        try:
            from core.portfolio import load_portfolio
            p = load_portfolio()
        except Exception:
            p = None
        try:
            from core.memory import SharedMemory
            mem = SharedMemory()
            recent = (mem.read("reports", "audit") or {}).get("summary", {})
        except Exception:
            recent = {}
        equity = p.equity if p else 0
        init = p.initial_balance if p else 10000
        total_pnl = ((equity - init) / init * 100) if init else 0
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        text = (
            f"<b>System Status</b>\n\n"
            f"Time: {now}\n"
            f"Equity: ${equity:,.2f}\n"
            f"Total Return: {_fmt_pct(total_pnl)}\n"
            f"Positions: {len(p.positions) if p else 0}\n"
        )
        if recent:
            text += f"Today: {recent.get('trades_today', 0)} trades\n"
        kb = self._inline_keyboard([
            [("/positions", "/positions"), ("/pnl", "/pnl")],
            [("/stats", "/stats")],
        ])
        self._reply_to(msg or {}, text, kb)

    def _cmd_stats(self, msg=None):
        try:
            from core.analytics import get_analytics
            a = get_analytics()
        except Exception:
            a = {}
        if not a:
            text = "<b>Trading Stats</b>\n\nNo data yet."
        else:
            total = a.get("total_trades", 0)
            wins = a.get("wins", 0)
            losses = a.get("losses", 0)
            wr = a.get("win_rate", 0)
            pf = a.get("profit_factor", 0)
            expectancy = a.get("expectancy", 0)
            avg_win = a.get("avg_win", 0)
            avg_loss = a.get("avg_loss", 0)
            max_dd = a.get("max_drawdown_pct", 0)
            best = a.get("best_trade_pct", 0)
            worst = a.get("worst_trade_pct", 0)
            consecutive_wins = a.get("consecutive_wins", 0)
            consecutive_losses = a.get("consecutive_losses", 0)
            sharpe = a.get("sharpe", 0)
            text = (
                f"<b>Trading Stats</b>\n\n"
                f"Trades: {total} ({wins}W / {losses}L)\n"
                f"Win Rate: {wr:.1f}%\n"
                f"Profit Factor: {pf:.2f}\n"
                f"Expectancy: {_fmt_pct(expectancy)}\n"
                f"Avg Win: {_fmt_pct(avg_win)} | Avg Loss: {_fmt_pct(avg_loss)}\n"
                f"Best: {_fmt_pct(best)} | Worst: {_fmt_pct(worst)}\n"
                f"Max DD: {_fmt_pct(max_dd)}\n"
                f"Sharpe: {sharpe:.2f}\n"
                f"Consecutive: {consecutive_wins}W / {consecutive_losses}L"
            )
        kb = self._inline_keyboard([
            [("/positions", "/positions"), ("/pnl", "/pnl")],
            [("/status", "/status")],
        ])
        self._reply_to(msg or {}, text, kb)

    # --- Existing notification methods (unchanged API) ---

    def send(self, text):
        self._send(text)

    def on_trade(self, order):
        if not self._enabled:
            return
        symbol = order.get("symbol", "?")
        side = order.get("side", order.get("action", "?"))
        qty = order.get("quantity", order.get("qty", 0))
        price = order.get("price", 0)
        sl = order.get("stop_loss", order.get("sl", 0))
        tp = order.get("take_profit", order.get("tp", 0))
        status = order.get("status", "filled")
        if status == "filled":
            sl_pct = abs((price - sl) / price * 100) if sl and price else 0
            tp_pct = abs((tp - price) / price * 100) if tp and price else 0
            rr = (tp_pct / sl_pct) if sl_pct else 0
            now = datetime.now(timezone.utc).strftime("%H:%M UTC")
            self.send(
                f"🟢 <b>OPEN {side} {symbol}</b>  <i>{now}</i>\n"
                f"Entry ${_fmt_price(price)} × {qty:g}\n"
                f"SL ${_fmt_price(sl)} (−{sl_pct:.2f}%) | "
                f"TP ${_fmt_price(tp)} (+{tp_pct:.2f}%) | R:R {rr:.1f}"
            )

    def on_error(self, message):
        self.send(f"🚨 <b>ERROR:</b> {message}")

    def daily_summary(self, s):
        self.send(
            f"📊 <b>Daily Summary — {s['date']}</b>\n"
            f"Equity ${s['equity']:,.2f}  ({s['day_pnl_pct']:+.2f}% today, "
            f"{s['total_pnl_pct']:+.2f}% all-time)\n"
            f"Closed {s['trades_closed']} | Win rate {s['win_rate']:.0f}% | "
            f"Realized ${s['pnl_closed']:+,.2f}\n"
            f"Open {s['open_positions']} | Cash ${s['cash']:,.2f}"
        )

    def on_sl_tp(self, result):
        now = datetime.now(timezone.utc).strftime("%H:%M UTC")
        symbol = result.get("symbol", "?")
        reason = result.get("reason", "exit").upper()
        pnl = result.get("pnl", 0)
        pnl_pct = result.get("pnl_pct", 0)
        side = result.get("side", "?")
        icon = "🟢" if pnl >= 0 else "🔴"
        self.send(
            f"{icon} <b>{reason} {side} {symbol}</b>  <i>{now}</i>\n"
            f"P&L ${pnl:+.2f} ({pnl_pct:+.2f}%) | Exit ${_fmt_price(result.get('exit_price', 0))}"
        )

    def on_rejected_signal(self, symbol, reason):
        """Broken-geometry rejections — always alert, even in quiet mode:
        a corrupt signal reaching execution means a data problem upstream."""
        if not self._enabled:
            return
        self.send(f"⛔ <b>REJECTED {symbol}</b> — broken signal geometry: {reason}")

    def on_agent_action(self, agent_name, action_text):
        if not self._enabled:
            return
        # Quiet mode: per-cycle agent chatter stays in the logs and web
        # dashboard; only halts are urgent enough for the phone.
        if TELEGRAM_QUIET and "HALT" not in action_text.upper():
            return
        now = datetime.now(timezone.utc).strftime("%H:%M:%S")
        self.send(f"<b>{now}</b> {agent_name} | {action_text}")

    def portfolio_snapshot(self, portfolio):
        if not self._enabled or TELEGRAM_QUIET:
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
            positions_str += f"\n{sym} {side} @ ${entry:.5f} -> ${current:.5f} ({upnl_s})"
        self.send(
            f"<b>{now}</b> Portfolio Snapshot\n"
            f"Value: ${total_value:,.2f} | P&L: {pnl_s}\n"
            f"Cash: ${cash:,.2f} | Positions: {len(positions)}{positions_str}"
        )

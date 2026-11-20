# -*- coding: utf-8 -*-
"""
Telegram notifier for the Propr bot.

Posts a message for every action the bot takes. Uses plain requests -> the
Telegram Bot API; token/chat id come from the environment. Messages include a
`why` field with the measured reason for the trade so you can audit the
decision instead of trusting the green/red line.
"""
import logging
import os
import time

import requests

log = logging.getLogger("notify")

API = "https://api.telegram.org/bot{token}/sendMessage"
_MAX_LEN = 3990          # Telegram hard limit (4096) minus our header
_LEAVE_RESERVE = 96      # keep for the error suffix we append on failure


def _post(text: str) -> int | None:
    token = os.getenv("TELEGRAM_TOKEN", "").strip()
    chat = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat:
        log.warning("TELEGRAM_TOKEN/TELEGRAM_CHAT_ID not set — no message sent")
        return None
    try:
        r = requests.post(
            API.format(token=token),
            json={"chat_id": chat, "text": text[:_MAX_LEN], "parse_mode": "HTML",
                  "disable_web_page_preview": True},
            timeout=10,
        )
        if r.status_code == 200:
            return r.status_code
        log.warning(f"telegram api {r.status_code}: {r.text[:200]}")
        return r.status_code
    except Exception as e:
        log.warning(f"telegram send failed: {e}")
        return None


def send(kind: str, title: str, lines: list[str], why: str | None = None) -> None:
    """Compose one HTML message. kind is a short tag like 'ENTRY' or 'CLOSE'."""
    head = f"<b>[{kind}]</b> {title}"
    body = "\n".join(f"  {ln}" for ln in lines)
    msg = head + ("\n" + body if body else "")

    # The whole point: every decision carries its measured reason, not just a
    # "bought $X" line. The user asked for the most important piece to be WHY.
    if why:
        msg += "\n\n<b>why</b> " + why
    msg = msg[:_MAX_LEN]
    _post(msg)


def send_startup(account: str, equity: float, target: float,
                 daily_wall: float, dd_floor: float, cfg_desc: str) -> None:
    send("START", "bot live",
         [f"account <code>{account}</code>",
          f"equity <b>${equity:,.2f}</b> target <b>${target:,.0f}</b>",
          f"daily wall ${daily_wall:,.0f}  dd floor ${dd_floor:,.0f}",
          f"config <code>{cfg_desc}</code>"])


def send_entry(asset: str, qty: float, px: float, notional: float,
               sl: float, tp: float, risk: float, why: str) -> None:
    send("ENTRY", f"LONG <b>{asset}</b>",
         [f"qty {qty:.6f} @ ${px:,.4f} = ${notional:,.0f}",
          f"SL ${sl:,.4f} (−{100 * (px - sl) / px:.2f}%)   "
          f"TP ${tp:,.4f} (+{100 * (tp - px) / px:.2f}%)",
          f"risk ${risk:,.2f} ({100 * risk / notional:.1f}% of notional)"],
         why)


def send_close(asset: str, reason: str, pnl: float | None = None) -> None:
    lines = [f"reason <code>{reason}</code>"]
    if pnl is not None:
        sign = "+" if pnl >= 0 else ""
        lines.append(f"realized <b>{sign}${pnl:,.2f}</b>")
    send("CLOSE", f"closed <b>{asset}</b>", lines)


def send_rejected(asset: str, code: str, msg: str) -> None:
    send("REJECT", f"order rejected <b>{asset}</b>",
         [f"code <code>{code}</code> — {msg}"])


def send_halt(reason: str) -> None:
    send("HALT", "bot halted", [f"<code>{reason}</code>"])


def send_target(equity: float) -> None:
    send("TARGET", f"profit target reached — equity ${equity:,.2f}", [])


def send_status(equity: float, profit_pct: float, daily_pct: float,
                dd_pct: float, open_n: int) -> None:
    send("PULSE", f"equity ${equity:,.2f}",
         [f"profit +{profit_pct:.2%}   daily {daily_pct:.2%}  "
          f"dd {dd_pct:.2%}  open {open_n}"],
         why=None)
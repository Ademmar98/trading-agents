#!/usr/bin/env python3
"""
Propr account performance report — READ-ONLY.

Issues GET requests only. Places, modifies and cancels nothing.

Checks, in order of what actually matters:
  1. Equity vs the challenge start, and headroom to the daily-loss / max-DD walls
  2. Whether every open position has a live stop (see the naked-stop bug fixed
     in client.py — a position without a stop is the one state that ends a
     challenge on its own)
  3. Realized trade history
"""
import json
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

API = "https://api.propr.xyz/v1"
ACCOUNT = os.getenv("PROPR_ACCOUNT_ID", "urn:prp-account:mYa6seVsUtDY")
KEY = os.getenv("PROPR_API_KEY", "")
HEADERS = {"X-API-Key": KEY}


def get(path, **params):
    try:
        r = requests.get(f"{API}{path}", headers=HEADERS, params=params or None, timeout=15)
        if r.status_code >= 400:
            return {"_error": f"{r.status_code} {r.text[:160]}"}
        return r.json()
    except Exception as e:
        return {"_error": f"{type(e).__name__}: {e}"}


def main():
    if not KEY:
        print("PROPR_API_KEY not set in .env")
        return 1

    print("=" * 68)
    print("PROPR ACCOUNT PERFORMANCE  (read-only)")
    print("=" * 68)

    acct = get(f"/accounts/{ACCOUNT}")
    if "_error" in acct:
        print(f"\nAccount fetch failed: {acct['_error']}")
        return 1

    balance = float(acct.get("balance", 0))
    unreal = float(acct.get("totalUnrealizedPnl", 0))
    avail = float(acct.get("availableBalance", 0))
    equity = balance + unreal

    # Challenge start: prefer the live attempt, fall back to the account field.
    start = None
    attempts = get("/challenge-attempts", status="active")
    if isinstance(attempts, dict) and not attempts.get("_error"):
        for a in attempts.get("data", []):
            if a.get("accountId") == ACCOUNT or start is None:
                start = float(a.get("initialBalance") or a.get("startingBalance") or 0) or None
    if not start:
        start = float(acct.get("initialBalance", 0)) or 5000.0

    pnl = equity - start
    pnl_pct = pnl / start * 100 if start else 0

    print(f"\n  Start balance      ${start:,.2f}")
    print(f"  Balance            ${balance:,.2f}")
    print(f"  Unrealized PnL     ${unreal:+,.2f}")
    print(f"  EQUITY             ${equity:,.2f}")
    print(f"  Net P/L            ${pnl:+,.2f}  ({pnl_pct:+.2f}%)")
    print(f"  Available          ${avail:,.2f}")

    # Challenge walls (CLASSIC_1STEP defaults: 3% daily, 6% max DD, 10% target)
    dd_floor = start * 0.94
    daily_wall = start * 0.03
    target = start * 0.10
    print(f"\n  Profit target      ${target:,.2f}  -> {pnl/target*100 if target else 0:.1f}% of the way")
    print(f"  Max-DD floor       ${dd_floor:,.2f}   headroom ${equity - dd_floor:+,.2f}")
    print(f"  Daily loss cap     ${daily_wall:,.2f}")

    # ---- Open positions and, critically, whether each has a stop -----------
    pos_resp = get(f"/accounts/{ACCOUNT}/positions", status="open")
    positions = [p for p in pos_resp.get("data", []) if float(p.get("quantity", 0)) > 0] \
        if not pos_resp.get("_error") else []

    # Conditional orders (stop_market / take_profit_market) rest in "pending"
    # until their trigger fires -- they are NOT in "open". Querying only "open"
    # reports every stop as missing, which is a false alarm.
    open_orders = []
    for st in ("open", "pending"):
        r = get(f"/accounts/{ACCOUNT}/orders", status=st, limit=100)
        if not r.get("_error"):
            open_orders.extend(r.get("data", []))

    print(f"\n{'-' * 68}\n  OPEN POSITIONS: {len(positions)}\n{'-' * 68}")
    naked = []
    for p in positions:
        base = p.get("base", "?")
        qty = float(p.get("quantity", 0))
        entry = float(p.get("entryPrice", 0) or 0)
        upnl = float(p.get("unrealizedPnl", 0) or 0)
        notional = qty * entry

        prot = [o for o in open_orders if o.get("base") == base]
        stops = [o for o in prot if "stop" in str(o.get("type", "")).lower()]
        tps = [o for o in prot if "take_profit" in str(o.get("type", "")).lower()]

        if not stops:
            naked.append(base)
            flag = "*** NO STOP ***"
        else:
            trig = float(stops[0].get("triggerPrice", 0) or 0)
            risk = (entry - trig) * qty if trig else 0
            flag = f"stop {trig:.4f} ({(trig/entry-1)*100:+.2f}%) risk ${risk:,.0f}"
        print(f"  {base:6s} qty={qty:<11.2f} entry={entry:<9.4f} "
              f"notional=${notional:>7,.0f} uPnL=${upnl:+7.2f}  "
              f"SL={len(stops)} TP={len(tps)}  {flag}")

    # Total risk if every stop fires — the number that matters against the walls.
    total_risk = 0.0
    for p in positions:
        base, qty = p.get("base"), float(p.get("quantity", 0))
        entry = float(p.get("entryPrice", 0) or 0)
        s = [o for o in open_orders
             if o.get("base") == base and "stop" in str(o.get("type", "")).lower()]
        if s:
            trig = float(s[0].get("triggerPrice", 0) or 0)
            total_risk += max(0.0, (entry - trig) * qty)
    if total_risk:
        print(f"\n  Risk if ALL stops fire: ${total_risk:,.2f}  "
              f"= {total_risk/daily_wall*100:.0f}% of the ${daily_wall:,.0f} daily cap, "
              f"{total_risk/(equity-dd_floor)*100:.0f}% of remaining DD headroom")

    if positions:
        gross = sum(float(p.get("quantity", 0)) * float(p.get("entryPrice", 0) or 0) for p in positions)
        print(f"\n  Gross notional     ${gross:,.0f}  = {gross/equity*100 if equity else 0:.0f}% of equity")

    # ---- Realized history --------------------------------------------------
    tr = get(f"/accounts/{ACCOUNT}/trades", limit=50)
    trades = tr.get("data", []) if not tr.get("_error") else []
    print(f"\n{'-' * 68}\n  TRADES: {len(trades)}\n{'-' * 68}")
    if tr.get("_error"):
        print(f"  fetch failed: {tr['_error']}")
    for t in trades[:15]:
        print(f"  {str(t.get('createdAt', ''))[:19]}  {t.get('base', '?'):6s} "
              f"{str(t.get('side', '?')):5s} qty={t.get('quantity', '?'):>12} "
              f"px={t.get('price', '?')}")

    if naked:
        print(f"\n{'=' * 68}")
        print(f"  WARNING: {len(naked)} position(s) with NO STOP: {', '.join(naked)}")
        print(f"  Each is unbounded downside against a ${daily_wall:,.0f} daily cap.")
        print(f"{'=' * 68}")

    return 0


if __name__ == "__main__":
    sys.exit(main())

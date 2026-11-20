# -*- coding: utf-8 -*-
"""Read-only: one-shot status for the paper-hand Free Trial account.

Bot is offline (VPS plan expired) -- resting stop/take-profit orders were
placed exchange-side at entry and survive the process. This shows the
current protection and P&L without touching anything.
"""
import sys, io, json
from datetime import datetime, timezone

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, r"C:\Users\DELL\OneDrive\1m")
from core.exchange.propr.propr_sdk import ProprClient

KEY = "pk_beta_6bJMTVfFfiQJfIPBgsuGzsPBhUvIxq0q1AGj613QiPv1RogM"
ACCT = "urn:prp-account:LDA3R3vW74Ym"
c = ProprClient(api_key=KEY, base_url="https://api.beta.propr.xyz/v1")
c.account_id = ACCT

print("=" * 62)
print("PAPER-HAND MONITOR  (bot offline, exchange orders only)")
print("now:", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"))
print("=" * 62)

try:
    a = c.get_account()
    print(f"balance  {float(a['balance']):10.2f}  upnl {float(a['totalUnrealizedPnl']):+9.2f}")
    print(f"marginEq {float(a['marginBalance']):10.2f}  hwm {float(a['highWaterMark']):10.2f}")
    print(f"start    {a['createdAt'][:10]}  challengeId {a['challengeId'][-8:]}")
except Exception as e:
    print("account ERR", e)

try:
    for p in c.get_positions():
        pid = p["positionId"]
        print(f"\n{ p['base']:6s} {p['positionSide']:5s} qty {float(p['quantity']):11.4f}"
              f"  entry {float(p['entryPrice']):9.4f}  breakeven {float(p['breakEvenPrice']):9.4f}")
        print(f"{"":6s} mark {float(p['markPrice']):9.4f}  upnl {float(p['unrealizedPnl']):+9.4f}"
              f"  opened {p['createdAt'][:16].replace('T',' ')}")
        for o in c.get_orders(position_id=pid, limit=50):
            trg = o.get("triggerPrice")
            trg = f"{float(trg):.4f}" if trg else "-"
            print(f"       {o.get('type', ''):22s} {o.get('status',''):8s} trigger {trg}")
except Exception as e:
    print("positions ERR", e)

try:
    trades = c.get_trades()
    real = [t for t in trades if t.get("type") == "close" and t.get("realizedPnl")]
    print(f"\nrealized trades ({len(real)}):")
    tot = 0.0
    for t in real:
        pnl = float(t["realizedPnl"]); tot += pnl
        print(f"   {t['createdAt'][:16].replace('T',' ')}  {t['asset']:6s}"
              f" {pnl:+9.2f}")
    print(f"   TOTAL REALIZED {tot:+10.2f}")
    if not real:
        print("   (none — every open position still open)")
except Exception as e:
    print("trades ERR", e)
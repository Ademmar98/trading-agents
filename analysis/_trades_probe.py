# -*- coding: utf-8 -*-
"""Read-only: dump account, positions, and recent trades for the beta $1,000."""
import sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, r"C:\Users\DELL\OneDrive\1m")
from core.exchange.propr.propr_sdk import ProprClient

KEY = "pk_beta_6bJMTVfFfiQJfIPBgsuGzsPBhUvIxq0q1AGj613QiPv1RogM"
ACCT = "urn:prp-account:LDA3R3vW74Ym"
c = ProprClient(api_key=KEY, base_url="https://api.beta.propr.xyz/v1")
c.account_id = ACCT

try:
    acct = c.get_account()
    print("BALANCE", acct.get("balance"), "HWM", acct.get("highWaterMark"),
          "UPNL", acct.get("totalUnrealizedPnl"))
except Exception as e:
    print("acct ERR", e)

try:
    pos = c.get_positions()
    print("\nPOSITIONS", len(pos))
    for p in pos:
        print(" ", json.dumps(p, default=str))
except Exception as e:
    print("pos ERR", e)

try:
    trades = c.get_trades()
    print("\nTRADES", len(trades))
    for t in trades[:15]:
        d = {k: t.get(k) for k in ("tradeId","asset","side","quantity","price","realizedPnl","createdAt","type","status")}
        print(" ", json.dumps(d, default=str))
except Exception as e:
    print("trades ERR", e)

try:
    orders = c.get_orders()
    print("\nORDERS", len(orders))
    for o in orders[:15]:
        print(" ", json.dumps(o, default=str)[:220])
except Exception as e:
    print("orders ERR", e)
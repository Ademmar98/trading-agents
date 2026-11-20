# -*- coding: utf-8 -*-
"""Read-only: orders on the failed account around the KAITO crash (stop or none?)."""
import os, sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
from dotenv import load_dotenv
load_dotenv("/opt/propr-bot/.env", override=True)
sys.path.insert(0, "/opt/propr-bot")
from propr import ProprClient
from datetime import datetime, timezone

c = ProprClient(api_key=os.getenv("PROPR_API_KEY"))
c.account_id = "urn:prp-account:mYa6seVsUtDY"

for st in ("open", "pending"):
    try:
        oo = c.get_orders(status=st, limit=100)
        print(f"status={st}: {len(oo)} orders")
        for o in oo:
            print("  ", json.dumps({k: o.get(k) for k in
                  ("base","type","side","quantity","triggerPrice","status",
                   "createdAt","updatedAt")})[:200])
    except Exception as e:
        print(st, "ERR", e)

# all orders incl filled/cancelled for the KAITO period
try:
    oo = c.get_orders(limit=100)
    print(f"\nall orders: {len(oo)}")
    for o in oo:
        t = str(o.get("createdAt") or o.get("updatedAt") or "")[:19]
        if "2026-08" in t:
            print("  ", t, json.dumps({k: o.get(k) for k in
                  ("base","type","side","quantity","triggerPrice","status")})[:180])
except Exception as e:
    print("all ERR", e)
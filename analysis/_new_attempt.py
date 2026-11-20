# -*- coding: utf-8 -*-
"""Read-only: list current challenge attempts (look for the new purchase)."""
import os, sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
from dotenv import load_dotenv
load_dotenv("/opt/propr-bot/.env", override=True)
sys.path.insert(0, "/opt/propr-bot")
from propr import ProprClient

c = ProprClient(api_key=os.getenv("PROPR_API_KEY"))

aa = c.get_challenge_attempts()
print(f"attempts: {len(aa)}")
for a in aa:
    print(json.dumps({
        "accountId": a.get("accountId"),
        "status": a.get("status"),
        "phase": (a.get("phase") or {}).get("status"),
        "startedAt": a.get("startedAt"),
        "closedAt": a.get("closedAt"),
        "productName": (a.get("product") or a.get("challenge") or {}).get("name") if isinstance(a.get("product") or a.get("challenge"), dict) else (a.get("product") or a.get("challenge")),
    }, default=str))

ch = c.get_challenge_products()
print("products:")
for p in ch if isinstance(ch, list) else []:
    print("  ", json.dumps({"id": p.get("id"), "name": p.get("name"), "maxPurchases": p.get("maxPurchases"), "slots": p.get("slots")}, default=str)[:200])
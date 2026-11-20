# -*- coding: utf-8 -*-
"""Read-only: full purchases list + paginated attempts."""
import os, sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
from dotenv import load_dotenv
load_dotenv("/opt/propr-bot/.env", override=True)
sys.path.insert(0, "/opt/propr-bot")
from propr.propr_sdk import ProprClient

c = ProprClient(api_key=os.getenv("PROPR_API_KEY"))

r = c._get("/purchases")
d = r.get("data", [])
tot = r.get("total")
print("PURCHASES total:", tot)
for p in d:
    prod = p.get("product", {})
    ch = prod.get("challenge", {})
    print("  ", p.get("purchaseId"), "|", ch.get("slug"), "|", ch.get("name"),
          "| init=", ch.get("initialBalance"), "|", p.get("status"), "|", p.get("createdAt"))

print("\n-- attempts paginated --")
for off in range(0, 60, 10):
    a = c.get_challenge_attempts(limit=10, offset=off)
    if not a:
        break
    for x in a:
        print("  ", x.get("attemptId"), x.get("accountId"), x.get("status"), x.get("startedAt"),
              "init-leftover:", json.dumps(x.get("account", {}).get("balance")))
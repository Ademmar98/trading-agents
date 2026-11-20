# -*- coding: utf-8 -*-
"""Read-only: probe raw endpoints for accounts/purchases/attempts totals."""
import os, sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
from dotenv import load_dotenv
load_dotenv("/opt/propr-bot/.env", override=True)
sys.path.insert(0, "/opt/propr-bot")
from propr.propr_sdk import ProprClient

c = ProprClient(api_key=os.getenv("PROPR_API_KEY"))

for path in ("/accounts", "/me/purchases", "/purchases", "/challenge-attempts?limit=50",
             "/challenge-attempts?limit=50&status=active"):
    try:
        res = c._get(path)
        print(f"\n{path} =>", json.dumps(res, default=str)[:1500])
    except Exception as e:
        print(f"\n{path} ERR {e}")

# full raw attempt record for passed account
a2 = c.get_challenge_attempts(limit=50)
print("\nRAW attempts:", json.dumps(a2, default=str)[:2000])
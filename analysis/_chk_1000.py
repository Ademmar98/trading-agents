# -*- coding: utf-8 -*-
"""Read-only: list challenge attempts & product rules (find the new $1000 one)."""
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
        "startedAt": a.get("startedAt"),
        "closedAt": a.get("closedAt"),
    }, default=str))

# attempt detail to find rules
if aa:
    acc = aa[0].get("accountId")
    det = c.get_attempt(account_id=acc) if hasattr(c, "get_attempt") else None
    print("detail:", json.dumps(det, default=str)[:1500] if det else "n/a")
# -*- coding: utf-8 -*-
"""Read-only: dump all challenge attempts + per-account equity snapshot."""
import os, sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
from dotenv import load_dotenv
load_dotenv(".env")
sys.path.insert(0, ".")
from propr import ProprClient

c = ProprClient(api_key=os.getenv("PROPR_API_KEY"))

print("=== users/me ===")
try:
    print(json.dumps(c.get_user(), indent=1)[:1500])
except Exception as e:
    print("ERR", e)

print("\n=== challenge-attempts (all statuses) ===")
try:
    atts = c.get_challenge_attempts(limit=50)
    for a in atts:
        keys = {k: a[k] for k in a if k in
                ("challengeAttemptId","attemptId","accountId","status","challengeId",
                 "productId","createdAt","updatedAt","startedAt","completedAt",
                 "finalBalance","initialBalance","startingBalance","targetBalance",
                 "failureReason","phase")}
        print(json.dumps(keys)[:400])
    print("TOTAL", len(atts))
except Exception as e:
    print("ERR", type(e).__name__, e)

print("\n=== accounts (all) ===")
try:
    import requests
    r = requests.get("https://api.propr.xyz/v1/accounts",
                     headers={"X-API-Key": os.getenv("PROPR_API_KEY","")}, timeout=15)
    print(r.status_code, r.text[:2500])
except Exception as e:
    print("ERR", e)
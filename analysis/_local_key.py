# -*- coding: utf-8 -*-
"""Read-only: identify this API key's user + check for new $1000 challenge."""
import os, sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, r"C:\Users\DELL\OneDrive\1m")
from dotenv import load_dotenv
load_dotenv(r"C:\Users\DELL\OneDrive\1m\.env", override=True)
from core.exchange.propr.propr_sdk import ProprClient

c = ProprClient(api_key=os.getenv("PROPR_API_KEY"))

try:
    u = c.get_user()
    print("USER:", json.dumps(u, default=str)[:500])
except Exception as e:
    print("user ERR", e)

try:
    raw = c._get("/purchases")
    print("\nPURCHASES:", json.dumps(raw, default=str)[:2500])
except Exception as e:
    print("purchases ERR", e)

try:
    aa = c.get_challenge_attempts(limit=50)
    print("\nATTEMPTS:")
    for a in aa:
        print("  ", a.get("attemptId"), "|", a.get("challengeId"), "|",
              a.get("status"), "|", a.get("startedAt"))
except Exception as e:
    print("attempts ERR", e)
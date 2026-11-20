# -*- coding: utf-8 -*-
"""Read-only: page through all attempts, all statuses."""
import os, sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
from dotenv import load_dotenv
load_dotenv("/opt/propr-bot/.env", override=True)
sys.path.insert(0, "/opt/propr-bot")
from propr.propr_sdk import ProprClient

c = ProprClient(api_key=os.getenv("PROPR_API_KEY"))

for status in (None, "active", "pending", "not_started", "purchased", "passed", "failed"):
    try:
        aa = c.get_challenge_attempts(status=status, limit=100)
        print(f"status={status}: {len(aa)}")
        for a in aa:
            print("   ", json.dumps({
                "accountId": a.get("accountId"),
                "challengeId": a.get("challengeId"),
                "status": a.get("status"),
                "createdAt": a.get("createdAt"),
                "startedAt": a.get("startedAt"),
                "deletedAt": a.get("deletedAt"),
            }, default=str))
    except Exception as e:
        print(f"status={status}: ERR {e}")
# -*- coding: utf-8 -*-
"""Read-only: identify bpWkSg3DyaY1 + look for low-balance challenges and purchase endpoints."""
import os, sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
from dotenv import load_dotenv
load_dotenv("/opt/propr-bot/.env", override=True)
sys.path.insert(0, "/opt/propr-bot")
from propr.propr_sdk import ProprClient

c = ProprClient(api_key=os.getenv("PROPR_API_KEY"))

try:
    ch = c.get_challenge_attempt("urn:prp-challenge:bpWkSg3DyaY1")
    print("attempt-by-challengeId:", json.dumps(ch, default=str)[:800])
except Exception as e:
    print("ch Attempt err", e)

ch2 = c.get_challenges(limit=100, offset=0)
print("\nall challenges inited balance values:")
for x in ch2:
    print("  ", x.get("initialBalance"), x.get("name"), x.get("slug"), x.get("challengeId"))
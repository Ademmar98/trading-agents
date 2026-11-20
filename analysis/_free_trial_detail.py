# -*- coding: utf-8 -*-
"""Read-only: fetch free-trial challenge detail + account detail."""
import os, sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
from dotenv import load_dotenv
load_dotenv("/opt/propr-bot/.env", override=True)
sys.path.insert(0, "/opt/propr-bot")
from propr.propr_sdk import ProprClient

c = ProprClient(api_key=os.getenv("PROPR_API_KEY"))

ch = c.get_challenges(challenge_id="urn:prp-challenge:bpWkSg3DyaY1")
print("FREE-TRIAL CHALLENGE:", json.dumps(ch, default=str)[:2500])

try:
    det = c.get_challenge_attempts(limit=1)
    if det:
        aid = det[0]["attemptId"]
        x = c.get_challenge_attempt(aid)
        print("\nATTEMPT DETAIL:", json.dumps(x, default=str)[:2500])
except Exception as e:
    print("attempt detail ERR", e)
# -*- coding: utf-8 -*-
"""Read-only: full challenge-attempt records (looking for phases / deadlines)."""
import os, sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
from dotenv import load_dotenv
load_dotenv("/opt/propr-bot/.env", override=True)
sys.path.insert(0, "/opt/propr-bot")
from propr import ProprClient

c = ProprClient(api_key=os.getenv("PROPR_API_KEY"))
for aid in ("urn:prp-challenge-attempt:vXXQSywexgtc",  # passed
            "urn:prp-challenge-attempt:pjiVu68CLjsB"): # failed
    try:
        a = c.get_challenge_attempt(aid)
        print("=" * 70)
        print(aid)
        print(json.dumps(a, indent=1)[:4000])
    except Exception as e:
        print(aid, "ERR", e)
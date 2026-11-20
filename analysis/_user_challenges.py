# -*- coding: utf-8 -*-
"""Read-only: user + challenges + products to locate the $1000 challenge."""
import os, sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
from dotenv import load_dotenv
load_dotenv("/opt/propr-bot/.env", override=True)
sys.path.insert(0, "/opt/propr-bot")
from propr.propr_sdk import ProprClient

c = ProprClient(api_key=os.getenv("PROPR_API_KEY"))

try:
    u = c.get_user()
    print("USER:", json.dumps(u, default=str)[:1200])
except Exception as e:
    print("user ERR", e)

try:
    ch = c.get_challenges()
    print("\nCHALLENGES count:", len(ch) if isinstance(ch, list) else type(ch))
    print(json.dumps(ch, default=str)[:3000])
except Exception as e:
    print("challenges ERR", e)
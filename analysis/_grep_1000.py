# -*- coding: utf-8 -*-
"""Read-only: dump everything, grep for 1000 and attempt/account fields."""
import os, sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
from dotenv import load_dotenv
load_dotenv("/opt/propr-bot/.env", override=True)
sys.path.insert(0, "/opt/propr-bot")
from propr.propr_sdk import ProprClient

c = ProprClient(api_key=os.getenv("PROPR_API_KEY"))

raw = json.dumps(c.get_challenges(), default=str)
print("challenges containing '1000':", raw.count("1000"))
for needle in ("1000", "Free Trial", "free-trial"):
    idx = raw.find(needle)
    while idx != -1:
        print(f"  ...{raw[max(0,idx-120):idx+160]}...")
        idx = raw.find(needle, idx+1)
        if idx > 200000:
            break
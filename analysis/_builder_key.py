# -*- coding: utf-8 -*-
"""Read-only: test builder key on beta — user, account access, orders scopes."""
import os, sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, r"C:\Users\DELL\OneDrive\1m")
from dotenv import load_dotenv
load_dotenv(r"C:\Users\DELL\OneDrive\1m\.env", override=True)
from core.exchange.propr.propr_sdk import ProprClient

KEY = "builder_nydKlPUdiXUYb6lan4uPUALAZnrSMObo"
ACCT = "urn:prp-account:LDA3R3vW74Ym"

for label, url in (("prod", "https://api.propr.xyz/v1"), ("beta", "https://api.beta.propr.xyz/v1")):
    print(f"===== {label} =====")
    c = ProprClient(api_key=KEY, base_url=url)
    try:
        u = c.get_user()
        print("user:", json.dumps(u, default=str)[:250])
    except Exception as e:
        print("user ERR", e); continue
    for path in (f"/accounts/{ACCT}", f"/accounts/{ACCT}/orders",
                 f"/accounts/{ACCT}/positions"):
        try:
            res = c._get(path)
            print(f"{path} => OK:", json.dumps(res, default=str)[:250])
        except Exception as e:
            print(f"{path} => ERR {e}")
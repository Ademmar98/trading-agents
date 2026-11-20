# -*- coding: utf-8 -*-
"""Read-only: identify this beta key's user + purchases + attempts."""
import os, sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, r"C:\Users\DELL\OneDrive\1m")
from dotenv import load_dotenv
load_dotenv(r"C:\Users\DELL\OneDrive\1m\.env", override=True)
from core.exchange.propr.propr_sdk import ProprClient

KEY = "pk_beta_sOVJiNCzx6mT1mevSL71e12nnlXazp7XFzXByO8eOsa44VKC"

for label, url in (("prod", "https://api.propr.xyz/v1"), ("beta", "https://api.beta.propr.xyz/v1")):
    print(f"\n===== {label} ({url}) =====")
    c = ProprClient(api_key=KEY, base_url=url)
    try:
        u = c.get_user()
        print("USER:", json.dumps(u, default=str)[:300])
    except Exception as e:
        print("user ERR", e)
        continue
    try:
        r = c._get("/purchases")
        print("PURCHASES total:", r.get("total"))
        for p in r.get("data", []):
            prod = p.get("product", {}); ch = prod.get("challenge", {})
            print("  ", p.get("purchaseId"), "|", ch.get("slug"), "|",
                  ch.get("name"), "| init=", ch.get("initialBalance"), "|",
                  p.get("status"), "|", p.get("createdAt"))
    except Exception as e:
        print("purchases ERR", e)
    try:
        aa = c.get_challenge_attempts(limit=50)
        for a in aa:
            bal = a.get("account", {}).get("balance")
            print("  ATT ", a.get("attemptId"), "|", a.get("challengeId"), "|",
                  a.get("status"), "|", a.get("startedAt"), "| bal=", bal)
    except Exception as e:
        print("attempts ERR", e)
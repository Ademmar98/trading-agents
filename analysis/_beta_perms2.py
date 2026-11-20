# -*- coding: utf-8 -*-
"""Read-only: compare full user payloads + permissions across keys."""
import os, sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, r"C:\Users\DELL\OneDrive\1m")
from dotenv import load_dotenv
load_dotenv(r"C:\Users\DELL\OneDrive\1m\.env", override=True)
from core.exchange.propr.propr_sdk import ProprClient

BETA = "pk_beta_sOVJiNCzx6mT1mevSL71e12nnlXazp7XFzXByO8eOsa44VKC"
c = ProprClient(api_key=BETA, base_url="https://api.beta.propr.xyz/v1")

u = c.get_user()
print("BETA USER KEYS:", list(u.keys()))
print(json.dumps(u, default=str)[:1200])

for path in ("/users/me/permissions", "/permissions/me", "/api-keys",
             "/users/me/api-key", "/auth/permissions"):
    try:
        res = c._get(path)
        print(f"\n{path} =>", json.dumps(res, default=str)[:600])
    except Exception as e:
        print(f"\n{path} => ERR {e}")
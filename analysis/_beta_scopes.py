# -*- coding: utf-8 -*-
"""Read-only: test which beta scopes the key has (GET only, no orders placed)."""
import os, sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, r"C:\Users\DELL\OneDrive\1m")
from dotenv import load_dotenv
load_dotenv(r"C:\Users\DELL\OneDrive\1m\.env", override=True)
from core.exchange.propr.propr_sdk import ProprClient

KEY = "pk_beta_sOVJiNCzx6mT1mevSL71e12nnlXazp7XFzXByO8eOsa44VKC"
c = ProprClient(api_key=KEY, base_url="https://api.beta.propr.xyz/v1")

ACCT = "urn:prp-account:LDA3R3vW74Ym"

for path in (
    f"/accounts/{ACCT}/orders",
    f"/accounts/{ACCT}/positions",
    f"/accounts/{ACCT}/trades",
    f"/accounts/{ACCT}/margin-config",
    "/leverage-limits/effective",
):
    try:
        res = c._get(path)
        print(f"{path} => OK:", json.dumps(res, default=str)[:300])
    except Exception as e:
        print(f"{path} => ERR {e}")
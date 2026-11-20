# -*- coding: utf-8 -*-
"""Read-only: probe beta key permissions & account access routes."""
import os, sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, r"C:\Users\DELL\OneDrive\1m")
from dotenv import load_dotenv
load_dotenv(r"C:\Users\DELL\OneDrive\1m\.env", override=True)
from core.exchange.propr.propr_sdk import ProprClient

KEY = "pk_beta_sOVJiNCzx6mT1mevSL71e12nnlXazp7XFzXByO8eOsa44VKC"
c = ProprClient(api_key=KEY, base_url="https://api.beta.propr.xyz/v1")

for path in (
    "/api-keys/me", "/me", "/me/api-keys", "/keys/me", "/auth/me",
    "/challenge-attempts/sBy3N6f4sTYX?include=account,orders",
    "/billing", "/subscriptions", "/accounts/LDA3R3vW74Ym?fields=balance",
):
    try:
        res = c._get(path)
        print(f"{path} => OK:", json.dumps(res, default=str)[:500])
    except Exception as e:
        print(f"{path} => ERR {e}")
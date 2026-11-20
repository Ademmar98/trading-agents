# -*- coding: utf-8 -*-
"""Read-only: determine correct beta account path format."""
import os, sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, r"C:\Users\DELL\OneDrive\1m")
from dotenv import load_dotenv
load_dotenv(r"C:\Users\DELL\OneDrive\1m\.env", override=True)
from core.exchange.propr.propr_sdk import ProprClient

KEY = "pk_beta_sOVJiNCzx6mT1mevSL71e12nnlXazp7XFzXByO8eOsa44VKC"
c = ProprClient(api_key=KEY, base_url="https://api.beta.propr.xyz/v1")

for path in (
    "/accounts/urn:prp-account:LDA3R3vW74Ym",
    "/accounts/LDA3R3vW74Ym",
    "/challenge-attempts/urn:prp-challenge-attempt:sBy3N6f4sTYX/account",
    "/challenge-attempts/sBy3N6f4sTYX/account",
):
    try:
        res = c._get(path)
        print(f"{path} => OK:", json.dumps(res, default=str)[:400])
    except Exception as e:
        print(f"{path} => ERR {e}")
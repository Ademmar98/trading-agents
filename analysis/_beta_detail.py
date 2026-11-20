# -*- coding: utf-8 -*-
"""Read-only: beta account detail + challenge rules."""
import os, sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, r"C:\Users\DELL\OneDrive\1m")
from dotenv import load_dotenv
load_dotenv(r"C:\Users\DELL\OneDrive\1m\.env", override=True)
from core.exchange.propr.propr_sdk import ProprClient

KEY = "pk_beta_sOVJiNCzx6mT1mevSL71e12nnlXazp7XFzXByO8eOsa44VKC"
c = ProprClient(api_key=KEY, base_url="https://api.beta.propr.xyz/v1")

det = c.get_challenge_attempt("sBy3N6f4sTYX")
print("ATTEMPT:", json.dumps(det, default=str)[:2600])

ch = c.get_challenges(challenge_id="urn:prp-challenge:dLa36LDBowCa")
print("\nCHALLENGE:", json.dumps(ch, default=str)[:2000])
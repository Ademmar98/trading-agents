# -*- coding: utf-8 -*-
"""Read-only: dump challenge rules for beta free-trial."""
import sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, r"C:\Users\DELL\OneDrive\1m")
from core.exchange.propr.propr_sdk import ProprClient

KEY = "pk_beta_6bJMTVfFfiQJfIPBgsuGzsPBhUvIxq0q1AGj613QiPv1RogM"
c = ProprClient(api_key=KEY, base_url="https://api.beta.propr.xyz/v1")

try:
    ch = c.get("/challenges/dLa36LDBowCa" if False else "/challenges")
except Exception as e:
    print("challenges ERR", e); ch = None
print(json.dumps(ch, default=str)[:2000])
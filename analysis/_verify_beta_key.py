# -*- coding: utf-8 -*-
"""Read-only: verify new pk_beta key - user, account, orders, positions."""
import os, sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, r"C:\Users\DELL\OneDrive\1m")
from dotenv import load_dotenv
load_dotenv(r"C:\Users\DELL\OneDrive\1m\.env", override=True)
from core.exchange.propr.propr_sdk import ProprClient

KEY = "pk_beta_6bJMTVfFfiQJfIPBgsuGzsPBhUvIxq0q1AGj613QiPv1RogM"
ACCT = "urn:prp-account:LDA3R3vW74Ym"

c = ProprClient(api_key=KEY, base_url="https://api.beta.propr.xyz/v1")

try:
    u = c.get_user()
    print("user:", json.dumps(u, default=str)[:250])
except Exception as e:
    print("user ERR", e)

try:
    aa = c.get_challenge_attempts(limit=50)
    print("\nattempts:")
    for a in aa:
        print("  ", a.get("attemptId"), "|", a.get("challengeId"), "|",
              a.get("status"), "|", a.get("startedAt"),
              "| acc=", a.get("accountId"), "| bal=", a.get("account", {}).get("balance"))
except Exception as e:
    print("attempts ERR", e)

c.account_id = ACCT
for name, fn in (("account", lambda: c.get_account()),
                 ("orders", lambda: c.get_orders()),
                 ("positions", lambda: c.get_positions()),
                 ("trades", lambda: c.get_trades())):
    try:
        print(f"\n{name}:", json.dumps(fn(), default=str)[:300])
    except Exception as e:
        print(f"\n{name} ERR {e}")
# -*- coding: utf-8 -*-
"""Read-only: enumerate SDK methods + probe user/products/purchases for the new challenge."""
import os, sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
from dotenv import load_dotenv
load_dotenv("/opt/propr-bot/.env", override=True)
sys.path.insert(0, "/opt/propr-bot")
from propr import ProprClient, propr_sdk

c = ProprClient(api_key=os.getenv("PROPR_API_KEY"))

meths = [m for m in dir(propr_sdk.ProprSDK if hasattr(propr_sdk, "ProprSDK") else type(c.sdk)) if not m.startswith("_")]
print("SDK methods:", meths)

for name in meths:
    if any(k in name.lower() for k in ("challenge", "product", "purchase", "account", "attempt", "user", "me")):
        try:
            fn = getattr(c.sdk, name)
            res = fn() if "(" not in name else None
            print(f"\n--- {name} ---")
            print(json.dumps(res, default=str)[:1200])
        except Exception as e:
            print(f"\n--- {name} ERR {e}")
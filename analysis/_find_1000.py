# -*- coding: utf-8 -*-
"""Read-only: find the $1000 challenge and its phase rules."""
import os, sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
from dotenv import load_dotenv
load_dotenv("/opt/propr-bot/.env", override=True)
sys.path.insert(0, "/opt/propr-bot")
from propr.propr_sdk import ProprClient

c = ProprClient(api_key=os.getenv("PROPR_API_KEY"))

ch = c.get_challenges()
for x in ch:
    bal = x.get("initialBalance")
    first = x.get("challengeId")
    phases = x.get("phases") or []
    rules = []
    for p in phases:
        rules.append((p.get("profitTargetPercent"), p.get("maxDailyLossPercent"),
                      p.get("maxDrawdownPercent"), p.get("drawdownType")))
    print(f"{x.get('name')} | {x.get('slug')} | init={bal} | active={x.get('isActive')} | maxPurch={x.get('maxPurchases')} | {x.get('challengeId')}")
    print(f"    phases={rules}")
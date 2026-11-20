# -*- coding: utf-8 -*-
"""Read-only: deep dive into both challenge attempts — trades + account + product."""
import os, sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
from dotenv import load_dotenv
load_dotenv("/opt/propr-bot/.env", override=True)
sys.path.insert(0, "/opt/propr-bot")
from propr import ProprClient

c = ProprClient(api_key=os.getenv("PROPR_API_KEY"))

product = None
try:
    ch = c.get_challenges(limit=50)
    for x in ch:
        if x.get("challengeId") == "urn:prp-challenge:bpWkSg3DyaY1":
            product = x
except Exception as e:
    print("challenges ERR", e)
print("PRODUCT:")
print(json.dumps({k: v for k, v in (product or {}).items()
                  if k not in ("createdAt","updatedAt")}, indent=1)[:2500])

for acc in ("urn:prp-account:mYa6seVsUtDY", "urn:prp-account:iqw6EgAYpQ4Z"):
    print("\n" + "=" * 70)
    print("ACCOUNT", acc)
    print("=" * 70)
    c.account_id = acc
    try:
        a = c.get_account()
        print("account fields:", json.dumps(
            {k: a[k] for k in a if k in ("balance","totalUnrealizedPnl",
               "availableBalance","initialBalance","createdAt","updatedAt",
               "status","currency")}))
    except Exception as e:
        print("acct ERR", e)
    try:
        tr = c.get_trades(limit=1000)
        print(f"trades: {len(tr)}")
        for t in tr[-80:]:
            print("  %s %-6s %-5s qty=%-12s px=%-10s pnl=%s" % (
                str(t.get("createdAt") or "")[:19], t.get("base"),
                t.get("side"), str(t.get("quantity"))[:14],
                str(t.get("price") or t.get("avgPrice"))[:12],
                str(t.get("realizedPnl"))[:12]))
    except Exception as e:
        print("trades ERR", e)
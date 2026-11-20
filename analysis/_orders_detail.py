# -*- coding: utf-8 -*-
"""Read-only: full order detail for the beta account — check SL/TP attachment."""
import sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, r"C:\Users\DELL\OneDrive\1m")
from core.exchange.propr.propr_sdk import ProprClient

KEY = "pk_beta_6bJMTVfFfiQJfIPBgsuGzsPBhUvIxq0q1AGj613QiPv1RogM"
ACCT = "urn:prp-account:LDA3R3vW74Ym"
c = ProprClient(api_key=KEY, base_url="https://api.beta.propr.xyz/v1")
c.account_id = ACCT

orders = c.get_orders()
print("ORDERS", len(orders))
for o in orders:
    keep = {k: o.get(k) for k in ("orderId","intentId","asset","side","type","status",
             "quantity","price","stopPrice","takeProfitPrice","timeInForce",
             "reduceOnly","closePosition","createdAt","updatedAt","filledQuantity",
             "avgFillPrice","leverage","marginMode")}
    print(json.dumps(keep, default=str))
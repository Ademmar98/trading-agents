# -*- coding: utf-8 -*-
"""Read-only: full trade + equity reconstruction for the FAILED challenge account."""
import os, sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
from dotenv import load_dotenv
load_dotenv("/opt/propr-bot/.env", override=True)
import requests

KEY = os.getenv("PROPR_API_KEY")
ACC = "urn:prp-account:mYa6seVsUtDY"
H = {"X-API-Key": KEY}
base = f"https://api.propr.xyz/v1/accounts/{ACC}"

all_tr = []
for off in range(0, 400, 100):
    r = requests.get(f"{base}/trades", params={"limit": 100, "offset": off}, headers=H, timeout=20)
    d = r.json().get("data", [])
    all_tr += d
    if len(d) < 100:
        break
print(f"total trades: {len(all_tr)}")
all_tr.sort(key=lambda t: t.get("createdAt") or "")
for t in all_tr:
    print("  %s %-7s %-5s qty=%-14s px=%-10s pnl=%s" % (
        (t.get("createdAt") or "")[:19], t.get("base"), t.get("side"),
        str(t.get("quantity"))[:14], str(t.get("price") or t.get("avgPrice"))[:10],
        str(t.get("realizedPnl"))[:12]))

# cumulative realized pnl
cum = 0.0
print("\nper-day realized pnl:")
day = None
for t in all_tr:
    d = (t.get("createdAt") or "")[:10]
    p = float(t.get("realizedPnl") or 0)
    if d != day:
        if day:
            print(f"  {day}: {day_pnl:+.2f}")
        day, day_pnl = d, 0.0
    day_pnl += p
if day:
    print(f"  {day}: {day_pnl:+.2f}")
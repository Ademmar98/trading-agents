# -*- coding: utf-8 -*-
"""Read-only: INJ trade rationale + market context using the SDK."""
import sys, io, json, datetime
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, r"C:\Users\DELL\OneDrive\1m")
from core.exchange.propr.propr_sdk import ProprClient
import requests

KEY = "pk_beta_6bJMTVfFfiQJfIPBgsuGzsPBhUvIxq0q1AGj613QiPv1RogM"
ACCT = "urn:prp-account:LDA3R3vW74Ym"
c = ProprClient(api_key=KEY, base_url="https://api.beta.propr.xyz/v1")
c.account_id = ACCT

print("== ACCOUNT ==")
try:
    acct = c.get_account()
    print("  balance", acct.get("balance"), "hwm", acct.get("highWaterMark"),
          "upnl", acct.get("totalUnrealizedPnl"))
except Exception as e:
    print("  acct ERR", e)

print("\n== POSITIONS ==")
try:
    pos = c.get_positions()
    for p in pos:
        print("  ", json.dumps(p, default=str))
except Exception as e:
    print("  pos ERR", e)

print("\n== INJ TRADES ==")
try:
    trades = c.get_trades()
    for t in trades:
        if "INJ" in json.dumps(t, default=str):
            print("  ---")
            print("  ", json.dumps(t, default=str))
except Exception as e:
    print("  trades ERR", e)

print("\n== INJ ORDERS ==")
try:
    orders = c.get_orders()
    for o in orders:
        if "INJ" in json.dumps(o, default=str):
            print("  ---")
            print("  ", json.dumps(o, default=str)[:400])
except Exception as e:
    print("  orders ERR", e)

def candles(coin, interval, start, end):
    r = requests.post("https://api.hyperliquid.xyz/info", json={
        "type": "candleSnapshot", "req": {"coin": coin, "interval": interval,
        "startTime": start, "endTime": end}}, timeout=30)
    return r.json()

sep9 = int(datetime.datetime(2026,9,9,tzinfo=datetime.timezone.utc).timestamp()*1000)
now = int(datetime.datetime.now(datetime.timezone.utc).timestamp()*1000)

print("\n== INJ 1h: setup window (Sep 9 00:00 -> entry 15:40) ==")
for c in candles("INJ", "1h", sep9, now)[-48:]:
    t = datetime.datetime.fromtimestamp(c["t"]/1000, tz=datetime.timezone.utc)
    print(f"  {t:%m-%d %H:%M}  C {float(c['c']):>8.3f}  H {float(c['h']):>8.3f}  L {float(c['l']):>8.3f}  v {c['v']}")

print("\n== INJ 15m: the trade (15:30 UTC 09-10 -> now) ==")
entry = int(datetime.datetime(2026,9,10,15,30,tzinfo=datetime.timezone.utc).timestamp()*1000)
for c in candles("INJ", "15m", entry, now):
    t = datetime.datetime.fromtimestamp(c["t"]/1000, tz=datetime.timezone.utc)
    print(f"  {t:%m-%d %H:%M}  O {float(c['o']):>8.3f}  H {float(c['h']):>8.3f}  L {float(c['l']):>8.3f}  C {float(c['c']):>8.3f}  v {c['v']}")

print("\n== MARKET 15m during trade (BTC/ETH/SOL) ==")
for coin in ("BTC","ETH","SOL"):
    print(f"  -- {coin}")
    for c in candles(coin, "15m", entry, now):
        t = datetime.datetime.fromtimestamp(c["t"]/1000, tz=datetime.timezone.utc)
        print(f"  {t:%m-%d %H:%M}  C {float(c['c']):>10.2f}  H {float(c['h']):>10.2f}  L {float(c['l']):>10.2f}  v {c['v']}")
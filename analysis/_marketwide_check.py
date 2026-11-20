# -*- coding: utf-8 -*-
"""Read-only: check if the 12:45-13:00 UTC Sep 10 dump was market-wide."""
import sys, io, json, datetime, requests
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

def bars(coin, interval="5m", days=3):
    start = int((datetime.datetime(2026,9,8,tzinfo=datetime.timezone.utc)).timestamp()*1000)
    end = int((datetime.datetime(2026,9,11,tzinfo=datetime.timezone.utc)).timestamp()*1000)
    r = requests.post("https://api.hyperliquid.xyz/info", json={
        "type":"candleSnapshot","req":{"coin":coin,"interval":interval,"startTime":start,"endTime":end}}, timeout=30)
    return r.json()

for coin in ("BTC","ETH","SOL","ZEC"):
    rows = bars(coin)
    print(f"\n== {coin} 5m 12:00-13:30 UTC 09-10 ==")
    for c in rows:
        t = datetime.datetime.fromtimestamp(c["t"]/1000, tz=datetime.timezone.utc)
        if t.day==10 and datetime.time(12,0) <= t.time() <= datetime.time(13,35):
            print(f"  {t:%H:%M}  O {float(c['o']):>9.4f} H {float(c['h']):>9.4f} "
                  f"L {float(c['l']):>9.4f} C {float(c['c']):>9.4f} v {c['v']}")
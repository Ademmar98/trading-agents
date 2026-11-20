# -*- coding: utf-8 -*-
"""Read-only: price context around the two losing fills (ZEC, ETHFI)."""
import sys, io, json, datetime, requests
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

sep10 = datetime.datetime(2026, 9, 8, 0, 0, tzinfo=datetime.timezone.utc)
start = int(sep10.timestamp()*1000)
end = int((datetime.datetime(2026, 9, 11, 0, 0, tzinfo=datetime.timezone.utc)).timestamp()*1000)
for coin in ("ZEC", "ETHFI"):
    payload = {"type": "candleSnapshot", "req": {
        "coin": coin, "interval": "15m", "startTime": start, "endTime": end}}
    r = requests.post("https://api.hyperliquid.xyz/info", json=payload, timeout=30)
    try:
        rows = r.json()
    except Exception:
        print(coin, "HTTP", r.status_code, r.text[:300]); continue
    print(f"\n== {coin} 15m ({len(rows)} bars) ==")
    for c in rows:
        t = datetime.datetime.fromtimestamp(c["t"]/1000, tz=datetime.timezone.utc)
        if t.day in (9, 10, 11):
            print(f"  {t:%m-%d %H:%M}  O {float(c['o']):>9.4f}  H {float(c['h']):>9.4f}  "
                  f"L {float(c['l']):>9.4f}  C {float(c['c']):>9.4f}  v {c['v']}")
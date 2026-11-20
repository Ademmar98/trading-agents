# -*- coding: utf-8 -*-
"""Read-only: reconstruct the INJ signal the bot saw at 15:40 UTC Sep 10.
Bot config: mom lb96 k3/long risk1.0% sl2.0 tp2.0 trail0.0 mc2 hold72.
Signal window: 96h returns; top-k=3 crossover long.
"""
import sys, io, datetime, requests
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

def candles(coin, interval, start, end):
    r = requests.post("https://api.hyperliquid.xyz/info", json={
        "type": "candleSnapshot", "req": {"coin": coin, "interval": interval,
        "startTime": start, "endTime": end}}, timeout=30)
    return r.json()

# 96h window ending at the signal bar: entry 15:40 -> last closed 1h bar = 15:00
end_bar = datetime.datetime(2026,9,10,15,0,tzinfo=datetime.timezone.utc)
start = int((end_bar - datetime.timedelta(hours=96)).timestamp()*1000)
end = int(end_bar.timestamp()*1000)

rows = candles("INJ", "1h", start, end + 3600*1000)
print(f"INJ 1h bars: {len(rows)} (expect ~97)")
# lb=96: return = close[-1]/close[-1-96]-1, i.e. 96 bars back from the signal bar
signal_close = float(rows[-1]["c"])
lb96_close = float(rows[-1-96]["c"])
r96 = signal_close / lb96_close - 1
print(f"signal bar (15:00) close: {signal_close}")
print(f"96 bars back close ({datetime.datetime.fromtimestamp(rows[-1-96]['t']/1000, tz=datetime.timezone.utc):%m-%d %H:%M}): {lb96_close}")
print(f"96h return used by bot: {r96:.3%}")

print("\nATR estimate (1h, 14-period Wilder on close)) signature window:")
# rough ATR approximation from last 20 bars
import math
trs = []
for i in range(1, 21):
    h = float(rows[-i]["h"]); l = float(rows[-i]["l"]); pc = float(rows[-i-1]["c"])
    trs.append(max(h-l, abs(h-pc), abs(l-pc)))
atr = sum(trs)/20
print(f"  20-bar mean TR (approx ATR): {atr:.4f}")
print(f"  sl = live - 2*ATR -> ~{signal_close - 2*atr:.4f}")
print(f"  tp = live + 2*(2*ATR) -> ~{signal_close + 4*atr:.4f}")

print(f"\nINJ from signal bar to now (mark hi/lo):")
sep10 = int(datetime.datetime(2026,9,10,12,0,tzinfo=datetime.timezone.utc).timestamp()*1000)
now = int(datetime.datetime.now(datetime.timezone.utc).timestamp()*1000)
for c in candles("INJ", "1h", sep10, now):
    t = datetime.datetime.fromtimestamp(c["t"]/1000, tz=datetime.timezone.utc)
    print(f"  {t:%m-%d %H:%M}  C {float(c['c']):>8.3f}  H {float(c['h']):>8.3f}  L {float(c['l']):>8.3f}  v {c['v']}")
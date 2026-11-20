# -*- coding: utf-8 -*-
"""Append recent bars to candles_1y.pkl so the dataset covers through today
(prev backtest data ended 2026-08-30, missing the Sep ARB run)."""
import sys, io, time, pickle
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
import pandas as pd
import requests
from datetime import datetime, timezone

API = "https://api.hyperliquid.xyz/info"
P = "/opt/propr-bot/candles_1y.pkl"

def hl_post(payload, retries=8):
    for a in range(retries):
        r = requests.post(API, json=payload, timeout=40)
        if r.status_code == 200:
            return r.json()
        if r.status_code == 429:
            time.sleep(2.5 * (a + 1))
        else:
            time.sleep(1.5 * (a + 1))
    return None

raw = pickle.loads(open(P, "rb").read())
now = int(datetime.now(timezone.utc).timestamp() * 1000)
# fetch last 8 days per coin (a single 1h request; well under the 5000 cap)
start = now - 8 * 86400 * 1000
added = 0
for i, (c, df) in enumerate(raw.items(), 1):
    d = hl_post({"type": "candleSnapshot", "req": {
        "coin": c, "interval": "1h", "startTime": start, "endTime": now}})
    if not d:
        continue
    nf = pd.DataFrame([{
        "open": float(x["o"]), "high": float(x["h"]), "low": float(x["l"]),
        "close": float(x["c"]), "volume": float(x["v"]),
        "timestamp": pd.to_datetime(int(x["t"]), unit="ms", utc=True)}
        for x in d]).set_index("timestamp").sort_index()
    before = len(df)
    df2 = pd.concat([df, nf])
    df2 = df2[~df2.index.duplicated(keep="last")].sort_index()
    raw[c] = df2
    added += len(df2) - before
    if i % 10 == 0:
        print(f"  {i}/{len(raw)} last={df2.index.max():%Y-%m-%d %H:%M}", flush=True)
    time.sleep(0.4)

pickle.dump(raw, open(P, "wb"))
spans = [(df.index.min(), df.index.max()) for df in raw.values()]
print(f"\nupdated {len(raw)} assets; +{added} bars")
print(f"earliest {min(s[0] for s in spans):%Y-%m-%d}  latest {max(s[1] for s in spans):%Y-%m-%d}")
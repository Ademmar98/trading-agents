#!/usr/bin/env python3
"""Step 1: Fetch and cache all 177 Hyperliquid 1h candles (30 days)"""
import sys, pickle, time
import requests
import pandas as pd
from datetime import datetime, timezone
from pathlib import Path

sys.stdout.reconfigure(line_buffering=True)
OUT = Path(__file__).parent / "hl_177_30d_1h.pkl"
API = "https://api.hyperliquid.xyz/info"

resp = requests.post(API, json={"type": "meta"}, timeout=15)
all_symbols = [s["name"] for s in resp.json()["universe"]]
print(f"{len(all_symbols)} symbols")

all_data = {}
for i, sym in enumerate(all_symbols):
    now = datetime.now(timezone.utc)
    et = int(now.timestamp() * 1000)
    st = et - (30 * 24 * 3600 * 1000)
    try:
        r = requests.post(API, json={"type": "candleSnapshot",
            "req": {"coin": sym, "interval": "1h", "startTime": st, "endTime": et}}, timeout=15)
        r.raise_for_status()
        data = r.json()
        if data:
            rows = [{"open": float(c["o"]), "high": float(c["h"]),
                     "low": float(c["l"]), "close": float(c["c"]),
                     "volume": float(c["v"]),
                     "ts": pd.to_datetime(int(c["t"]), unit="ms", utc=True)}
                    for c in data]
            df = pd.DataFrame(rows).set_index("ts").sort_index()
            if len(df) >= 220:
                all_data[sym] = df
    except:
        pass
    if (i+1) % 25 == 0:
        print(f"  [{i+1}/{len(all_symbols)}] {len(all_data)} loaded")
        time.sleep(1)
    time.sleep(0.05)

print(f"\n{len(all_data)} symbols cached")
with open(OUT, "wb") as f:
    pickle.dump(all_data, f)
print(f"Saved to {OUT}")

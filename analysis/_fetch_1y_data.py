# -*- coding: utf-8 -*-
"""Fetch ~1 year of hourly candles for the top-50 HL perps, paginating past the
5000-bar per-request cap, with 429/5xx backoff. Caches to candles_1y.pkl."""
import sys, io, time, pickle
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
import numpy as np
import pandas as pd
import requests
from datetime import datetime, timezone

API = "https://api.hyperliquid.xyz/info"
RUN_DAYS = 365
CHUNK_DAYS = 140          # under the 5000-bar cap (140*24=3360)
CACHE = "/opt/propr-bot/cache_1y"
OUT = "/opt/propr-bot/candles_1y.pkl"


def hl_post(payload, retries=8):
    for a in range(retries):
        r = requests.post(API, json=payload, timeout=40)
        if r.status_code == 200:
            return r.json()
        if r.status_code == 429:
            time.sleep(2.5 * (a + 1))       # back off on rate limit
        elif r.status_code >= 500:
            time.sleep(2.0 * (a + 1))
        else:
            time.sleep(1.0)
    return None


def fetch_coin(coin, total_days=RUN_DAYS, chunk=CHUNK_DAYS):
    now = int(datetime.now(timezone.utc).timestamp() * 1000)
    lo = now - total_days * 86400 * 1000
    frames = []
    end = now
    while end > lo:
        start = end - chunk * 86400 * 1000
        d = hl_post({"type": "candleSnapshot", "req": {
            "coin": coin, "interval": "1h", "startTime": start, "endTime": end}})
        if d:
            df = pd.DataFrame([{
                "open": float(c["o"]), "high": float(c["h"]),
                "low": float(c["l"]), "close": float(c["c"]),
                "volume": float(c["v"]),
                "timestamp": pd.to_datetime(int(c["t"]), unit="ms", utc=True)}
                for c in d]).set_index("timestamp").sort_index()
            frames.append(df)
        end = start
        time.sleep(0.5)                     # gentle pacing to avoid 429
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames).sort_index()
    return df[~df.index.duplicated(keep="last")]


def main():
    import os
    os.makedirs(CACHE, exist_ok=True)
    # reuse any partial cache
    raw = {}
    if os.path.exists(OUT):
        raw = pickle.loads(open(OUT, "rb").read())
    # universe from current top-50 by notional volume
    meta = hl_post({"type": "metaAndAssetCtxs"})   # [ {universe:..}, [ctxs] ]
    head, ctxs = meta
    names = [u["name"] for u in head["universe"]]
    vols = [float(c.get("dayNtlVlm", 0) or 0) for c in ctxs]
    ranked = sorted(zip(names, vols), key=lambda x: -x[1])
    coins = [nm for nm, _ in ranked[:50]]
    print(f"universe: {len(coins)} -> will fetch {RUN_DAYS}d each", flush=True)

    for i, c in enumerate(coins, 1):
        if c in raw and len(raw[c]) > 6000:
            print(f"  [{i}/{len(coins)}] {c}: cached {len(raw[c])}", flush=True)
            continue
        try:
            df = fetch_coin(c)
        except Exception as e:
            print(f"  [{i}/{len(coins)}] {c}: ERR {type(e).__name__} {e}", flush=True)
            continue
        if len(df) > 1000:
            raw[c] = df
        print(f"  [{i}/{len(coins)}] {c}: {len(df)} bars", flush=True)
        if len(raw) % 5 == 0:
            open(OUT, "wb").write(pickle.dumps(raw))
    open(OUT, "wb").write(pickle.dumps(raw))
    print(f"\nsaved {len(raw)} assets -> {OUT}", flush=True)


if __name__ == "__main__":
    main()

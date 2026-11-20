#!/usr/bin/env python3
import requests
from datetime import datetime, timezone

base = "https://testnet.binance.vision"
real = "https://api.binance.com"

for name, url in [("testnet", base), ("real", real)]:
    try:
        resp = requests.get(f"{url}/api/v3/klines", params={
            "symbol": "SOLUSDT", "interval": "1m", "limit": 5
        }, timeout=10)
        data = resp.json()
        if isinstance(data, list) and len(data) > 0:
            dt0 = datetime.fromtimestamp(data[0][0]/1000, tz=timezone.utc)
            dt1 = datetime.fromtimestamp(data[-1][0]/1000, tz=timezone.utc)
            print(f"{name}: OK - {len(data)} candles, {dt0} to {dt1}")
        else:
            print(f"{name}: {str(data)[:200]}")
    except Exception as e:
        print(f"{name}: ERROR {e}")

# How far back does each go?
for name, url in [("testnet", base), ("real", real)]:
    try:
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        start_90d = now_ms - (90 * 24 * 3600 * 1000)
        # Page through to find where data starts
        resp = requests.get(f"{url}/api/v3/klines", params={
            "symbol": "SOLUSDT", "interval": "1m", "startTime": start_90d, "limit": 1
        }, timeout=10)
        data = resp.json()
        if isinstance(data, list) and len(data) > 0:
            dt = datetime.fromtimestamp(data[0][0]/1000, tz=timezone.utc)
            print(f"{name}: has data from {dt.strftime('%Y-%m-%d %H:%M')} (requested 90d ago)")
        else:
            print(f"{name}: no data at 90d ago")

        # Try 30d
        start_30d = now_ms - (30 * 24 * 3600 * 1000)
        resp = requests.get(f"{url}/api/v3/klines", params={
            "symbol": "SOLUSDT", "interval": "1m", "startTime": start_30d, "limit": 1
        }, timeout=10)
        data = resp.json()
        if isinstance(data, list) and len(data) > 0:
            dt = datetime.fromtimestamp(data[0][0]/1000, tz=timezone.utc)
            print(f"{name}: has data from {dt.strftime('%Y-%m-%d %H:%M')} (requested 30d ago)")
        else:
            print(f"{name}: no data at 30d ago")

        # Count total in 30d window
        resp = requests.get(f"{url}/api/v3/klines", params={
            "symbol": "SOLUSDT", "interval": "1m", "startTime": start_30d, "endTime": now_ms, "limit": 1000
        }, timeout=10)
        data = resp.json()
        if isinstance(data, list) and len(data) > 0:
            dt0 = datetime.fromtimestamp(data[0][0]/1000, tz=timezone.utc)
            dt1 = datetime.fromtimestamp(data[-1][0]/1000, tz=timezone.utc)
            print(f"{name}: first page = {len(data)} candles ({dt0} to {dt1})")

    except Exception as e:
        print(f"{name}: ERROR {e}")

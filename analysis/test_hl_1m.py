import requests, json
from datetime import datetime, timezone, timedelta

API = "https://api.hyperliquid.xyz/info"
now = datetime.now(timezone.utc)

# Try a small chunk first: last 1 hour
end_ms = int(now.timestamp() * 1000)
start_ms = end_ms - (1 * 3600 * 1000)  # 1 hour

print(f"Fetching BTC 1m from {start_ms} to {end_ms}")
resp = requests.post(API, json={
    "type": "candleSnapshot",
    "req": {"coin": "BTC", "interval": "1m",
            "startTime": start_ms, "endTime": end_ms}
}, timeout=15)
print(f"Status: {resp.status_code}")
data = resp.json()
print(f"Candles: {len(data)}")
if data:
    print(f"First: {data[0]}")
    print(f"Last:  {data[-1]}")

# Try 1 day
start_ms2 = end_ms - (1 * 24 * 3600 * 1000)
print(f"\nFetching BTC 1m for 1 day...")
resp2 = requests.post(API, json={
    "type": "candleSnapshot",
    "req": {"coin": "BTC", "interval": "1m",
            "startTime": start_ms2, "endTime": end_ms}
}, timeout=15)
data2 = resp2.json()
print(f"Candles: {len(data2)}")

# Try 5 days
start_ms3 = end_ms - (5 * 24 * 3600 * 1000)
print(f"\nFetching BTC 1m for 5 days...")
resp3 = requests.post(API, json={
    "type": "candleSnapshot",
    "req": {"coin": "BTC", "interval": "1m",
            "startTime": start_ms3, "endTime": end_ms}
}, timeout=30)
data3 = resp3.json()
print(f"Candles: {len(data3)}")

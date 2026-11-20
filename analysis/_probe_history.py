import requests, time
from datetime import datetime, timezone
API = "https://api.hyperliquid.xyz/info"
now = int(datetime.now(timezone.utc).timestamp()*1000)

def fetch(coin, start_ms, end_ms):
    d = requests.post(API, json={"type":"candleSnapshot","req":{
        "coin":coin,"interval":"1h","startTime":start_ms,"endTime":end_ms}}, timeout=40).json()
    return d

# Try BTC windows at various offsets in the past
for daysback in (0, 100, 200, 300, 400, 500):
    end = now - daysback*86400*1000
    start = end - 30*86400*1000
    d = fetch("BTC", start, end)
    n = len(d) if isinstance(d, list) else d
    info = ""
    if isinstance(d, list) and d:
        info = f"first={datetime.fromtimestamp(d[0]['t']/1000, timezone.utc):%Y-%m-%d} last={datetime.fromtimestamp(d[-1]['t']/1000, timezone.utc):%Y-%m-%d}"
    print(f"window ending {daysback}d ago: returned {n} bars  {info}")
    time.sleep(0.3)

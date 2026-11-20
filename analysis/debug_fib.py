import requests, numpy as np, pandas as pd
from datetime import datetime, timezone

API = "https://api.hyperliquid.xyz/info"

def fetch(symbol, days=90):
    now = datetime.now(timezone.utc)
    end = int(now.timestamp() * 1000)
    start = end - (days * 24 * 3600 * 1000)
    resp = requests.post(API, json={"type":"candleSnapshot","req":{"coin":symbol,"interval":"1h","startTime":start,"endTime":end}}, timeout=30)
    data = resp.json()
    rows = [{"close": float(c["c"]), "high": float(c["h"]), "low": float(c["l"]),
             "volume": float(c["v"]), "ts": int(c["t"])} for c in data]
    df = pd.DataFrame(rows)
    df["timestamp"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    df.set_index("timestamp", inplace=True)
    df.sort_index(inplace=True)
    return df

for sym in ["BTC", "SOL", "SUI", "FET"]:
    df = fetch(sym)
    close = df["close"].values
    high = df["high"].values
    low = df["low"].values
    n = len(close)
    
    print(f"\n=== {sym} ({n} bars) ===")
    
    # Find swing lows
    lookback = 5
    swing_lows = []
    for i in range(lookback, n - lookback):
        window = low[i - lookback:i + lookback + 1]
        if low[i] == window.min():
            swing_lows.append({"idx": i, "price": low[i]})
    
    # Find swing highs
    swing_highs = []
    for i in range(lookback, n - lookback):
        window = high[i - lookback:i + lookback + 1]
        if high[i] == window.max():
            swing_highs.append({"idx": i, "price": high[i]})
    
    print(f"Swing lows: {len(swing_lows)}, Swing highs: {len(swing_highs)}")
    
    # Check uptrend at various points
    uptrend_count = 0
    bos_count = 0
    gold_zone_count = 0
    
    for i in range(60, n):
        # Uptrend check
        recent = [s for s in swing_lows if s["idx"] < i]
        if len(recent) < 3:
            continue
        last3 = recent[-3:]
        if last3[1]["price"] > last3[0]["price"] and last3[2]["price"] > last3[1]["price"]:
            uptrend_count += 1
            
            # BOS check
            recent_highs = [s for s in swing_highs if s["idx"] < i and s["idx"] > i - 150]
            if not recent_highs:
                continue
            last_sh = recent_highs[-1]
            if close[i] > last_sh["price"]:
                bos_count += 1
                
                # Gold zone check
                recent_lows = [s for s in swing_lows if s["idx"] < last_sh["idx"] and s["idx"] > i - 200]
                if not recent_lows:
                    continue
                sl = recent_lows[-1]["price"]
                sh = last_sh["price"]
                fr = sh - sl
                if fr <= 0:
                    continue
                gz_top = sh - fr * 0.5
                gz_bot = sh - fr * 0.618
                if gz_bot <= close[i] <= gz_top:
                    gold_zone_count += 1
    
    print(f"Uptrend bars: {uptrend_count}")
    print(f"BOS bars: {bos_count}")
    print(f"Gold zone entries: {gold_zone_count}")
    
    # Show last few swing lows/highs
    print(f"Last 3 swing lows: {[(s['idx'], round(s['price'],2)) for s in swing_lows[-3:]]}")
    print(f"Last 3 swing highs: {[(s['idx'], round(s['price'],2)) for s in swing_highs[-3:]]}")
    print(f"Price range: {close.min():.2f} - {close.max():.2f}")

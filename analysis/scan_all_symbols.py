from dotenv import load_dotenv; import os
load_dotenv(r'C:\Users\DELL\OneDrive\1m\.env')
import sys; sys.path.insert(0, r'C:\Users\DELL\OneDrive\1m')
from core.exchange.propr.challenge_passer import HyperliquidFeed
from core.exchange.propr.config import ProprConfig
import numpy as np

config = ProprConfig(api_key=os.getenv('PROPR_API_KEY'))
feed = HyperliquidFeed(config)

all_symbols = ["BTC", "ETH", "SOL", "DOGE", "XRP", "AVAX", "LINK", "SUI", "NEAR", "AAVE", "INJ", "FET"]

results = []
for symbol in all_symbols:
    df = feed.get_candles(symbol, '1h', 100)
    if df.empty or len(df) < 30:
        continue

    close = df['close'].values
    volume = df['volume'].values
    high = df['high'].values
    low = df['low'].values

    buy_vol = np.where(close[1:] > close[:-1], volume[1:], 0)
    sell_vol = np.where(close[1:] < close[:-1], volume[1:], 0)
    buy_vol = np.append([0], buy_vol)
    sell_vol = np.append([0], sell_vol)

    cvd_raw = buy_vol - sell_vol
    cvd_cumsum = np.cumsum(cvd_raw)
    cvd_z = (cvd_cumsum[-1] - np.mean(cvd_cumsum[-20:])) / (np.std(cvd_cumsum[-20:]) + 1e-10)

    returns = np.diff(np.log(close + 1e-10))
    rv = np.std(returns[-20:]) * np.sqrt(365)

    current_price = close[-1]
    recent_high = np.max(high[-60:])
    dip_pct = (recent_high - current_price) / recent_high if recent_high > 0 else 0

    results.append({
        'symbol': symbol,
        'cvd': cvd_z,
        'rv': rv,
        'dip': dip_pct,
        'price': current_price,
    })

results.sort(key=lambda x: x['cvd'])

print(f"{'Symbol':6s} {'CVD':>8s} {'RV':>6s} {'Dip%':>6s} {'Price':>10s}")
print("-" * 50)
for r in results:
    print(f"{r['symbol']:6s} {r['cvd']:+8.2f} {r['rv']:6.2f} {r['dip']:6.3f} {r['price']:10.4f}")

from dotenv import load_dotenv; import os
load_dotenv(r'C:\Users\DELL\OneDrive\1m\.env')
import sys; sys.path.insert(0, r'C:\Users\DELL\OneDrive\1m')
from core.exchange.propr.challenge_passer import HyperliquidFeed, MicrostructureAbsorptionM3
from core.exchange.propr.config import ProprConfig

config = ProprConfig(api_key=os.getenv('PROPR_API_KEY'))
feed = HyperliquidFeed(config)
strategy = MicrostructureAbsorptionM3(config)

for symbol in ['BTC', 'ETH', 'SOL', 'DOGE', 'XRP', 'AVAX', 'LINK', 'SUI']:
    df = feed.get_candles(symbol, '1h', 200)
    if df.empty:
        print(f'{symbol}: No data')
        continue
    features = strategy.compute_features(df)
    if features:
        cvd = features['cvd_zscore']
        rv = features['rv_annualized']
        dip = features['dip_pct']
        signal, reason = strategy.generate_signal(features)
        status = 'BUY' if signal else 'NO'
        print(f'{symbol:5s}: CVD={cvd:+.2f}  RV={rv:.2f}  Dip={dip:.3f}  -> {status}')
        print(f'       {reason}')
    else:
        print(f'{symbol}: Not enough data')

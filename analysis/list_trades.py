from dotenv import load_dotenv; import os, requests, json
load_dotenv(r'C:\Users\DELL\OneDrive\1m\.env')
key = os.getenv('PROPR_API_KEY')
headers = {'X-API-Key': key}
acc = 'urn:prp-account:mYa6seVsUtDY'
resp = requests.get(f'https://api.propr.xyz/v1/accounts/{acc}/trades?limit=10', headers=headers, timeout=10)
trades = resp.json().get('data', [])
print(f'Recent trades: {len(trades)}')
for t in trades:
    print(f'  {t["type"]:10s} {t["side"]:4s} {t["asset"]:6s} qty={t["quantity"]} price={t["price"]} pnl={t.get("realizedPnl", "N/A")}')

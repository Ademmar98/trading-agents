from dotenv import load_dotenv; import os, requests, json
load_dotenv(r'C:\Users\DELL\OneDrive\1m\.env')
key = os.getenv('PROPR_API_KEY')
headers = {'X-API-Key': key}
acc = 'urn:prp-account:mYa6seVsUtDY'

resp = requests.get(f'https://api.propr.xyz/v1/accounts/{acc}/positions?status=open', headers=headers, timeout=10)
positions = resp.json().get('data', [])
print('=== OPEN POSITIONS ===')
found = False
for p in positions:
    qty = float(p.get('quantity', 0))
    if qty > 0:
        found = True
        print(json.dumps(p, indent=2))
if not found:
    print('No open positions')

resp2 = requests.get(f'https://api.propr.xyz/v1/accounts/{acc}', headers=headers, timeout=10)
acct = resp2.json()
print()
print('=== ACCOUNT ===')
print('Balance:', acct['balance'])
print('Unrealized PnL:', acct['totalUnrealizedPnl'])
print('Available:', acct['availableBalance'])

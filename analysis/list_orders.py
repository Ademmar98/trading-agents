from dotenv import load_dotenv; import os, requests, json
load_dotenv(r'C:\Users\DELL\OneDrive\1m\.env')
key = os.getenv('PROPR_API_KEY')
headers = {'X-API-Key': key}
acc = 'urn:prp-account:mYa6seVsUtDY'
resp = requests.get(f'https://api.propr.xyz/v1/accounts/{acc}/orders?status=open', headers=headers, timeout=10)
orders = resp.json().get('data', [])
print(f'Open orders: {len(orders)}')
for o in orders:
    otype = o['type']
    asset = o['asset']
    side = o['side']
    trigger = o.get('triggerPrice', 'N/A')
    qty = o['quantity']
    status = o['status']
    print(f'  {otype:25s} {asset:6s} {side:4s} trigger={trigger} qty={qty} status={status}')

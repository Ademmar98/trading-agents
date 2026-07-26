from dotenv import load_dotenv; import os, requests, json
load_dotenv(r'C:\Users\DELL\OneDrive\1m\.env')
key = os.getenv('PROPR_API_KEY')
headers = {'X-API-Key': key}
acc = 'urn:prp-account:mYa6seVsUtDY'

resp = requests.get(f'https://api.propr.xyz/v1/accounts/{acc}/orders?status=pending', headers=headers, timeout=10)
orders = resp.json().get('data', [])
print(f'Pending: {len(orders)}')

# Find duplicate SUI stop_market
seen_sl = False
for o in orders:
    if o['asset'] == 'SUI' and o['type'] == 'stop_market':
        if seen_sl:
            oid = o['orderId']
            r = requests.post(f'https://api.propr.xyz/v1/accounts/{acc}/orders/{oid}/cancel', headers=headers, timeout=10)
            print(f'Cancelled extra SUI SL: {r.status_code}')
        else:
            seen_sl = True

# Verify
resp2 = requests.get(f'https://api.propr.xyz/v1/accounts/{acc}/orders?status=pending', headers=headers, timeout=10)
remaining = resp2.json().get('data', [])
print(f'\nRemaining: {len(remaining)}')
for o in remaining:
    print(f'  {o["type"]:25s} {o["asset"]:6s} trigger={o.get("triggerPrice")}')

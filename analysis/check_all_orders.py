from dotenv import load_dotenv; import os, requests
load_dotenv(r'C:\Users\DELL\OneDrive\1m\.env')
key = os.getenv('PROPR_API_KEY')
headers = {'X-API-Key': key}
acc = 'urn:prp-account:mYa6seVsUtDY'
for status in ['open', 'pending', 'cancelled']:
    resp = requests.get(f'https://api.propr.xyz/v1/accounts/{acc}/orders?status={status}', headers=headers, timeout=10)
    orders = resp.json().get('data', [])
    print(f'{status}: {len(orders)}')
    for o in orders:
        print(f'  {o["type"]:25s} {o["asset"]:6s} {o["side"]:4s} trigger={o.get("triggerPrice","")} posId={o.get("positionId","")} status={o["status"]}')

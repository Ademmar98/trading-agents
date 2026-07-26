from dotenv import load_dotenv; import os, requests, json, time
load_dotenv(r'C:\Users\DELL\OneDrive\1m\.env')
key = os.getenv('PROPR_API_KEY')
headers = {'X-API-Key': key, 'Content-Type': 'application/json'}
acc = 'urn:prp-account:mYa6seVsUtDY'

# Get all pending orders
resp = requests.get(f'https://api.propr.xyz/v1/accounts/{acc}/orders?status=pending', headers=headers, timeout=10)
orders = resp.json().get('data', [])

# Target triggers for SUI
sui_sl = '0.6889'
sui_tp = '0.7694'
avax_sl = '6.44'
avax_tp = '7.1927'

print(f'Found {len(orders)} pending orders. Cancelling extras...')

cancelled = 0
for o in orders:
    oid = o['orderId']
    asset = o['asset']
    otype = o['type']
    trigger = o.get('triggerPrice', '')

    # Keep only the correct ones
    keep = False
    if asset == 'SUI' and otype == 'stop_market' and trigger == sui_sl:
        keep = True
    elif asset == 'SUI' and otype == 'take_profit_market' and trigger == sui_tp:
        keep = True
    elif asset == 'AVAX' and otype == 'stop_market' and trigger == avax_sl:
        keep = True
    elif asset == 'AVAX' and otype == 'take_profit_market' and trigger == avax_tp:
        keep = True

    if not keep:
        r = requests.post(f'https://api.propr.xyz/v1/accounts/{acc}/orders/{oid}/cancel', headers=headers, timeout=10)
        status = r.status_code
        cancelled += 1
        print(f'  CANCELLED {asset} {otype} trigger={trigger} ({status})')
    else:
        print(f'  KEPT      {asset} {otype} trigger={trigger}')

print(f'\nCancelled {cancelled} orders')

time.sleep(1)

# Verify
resp2 = requests.get(f'https://api.propr.xyz/v1/accounts/{acc}/orders?status=pending', headers=headers, timeout=10)
remaining = resp2.json().get('data', [])
print(f'Remaining pending: {len(remaining)}')
for o in remaining:
    print(f'  {o["type"]:25s} {o["asset"]:6s} trigger={o.get("triggerPrice")} posId={o.get("positionId")}')

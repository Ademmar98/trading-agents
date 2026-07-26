from dotenv import load_dotenv; import os, requests, json, time
from ulid import ULID
load_dotenv(r'C:\Users\DELL\OneDrive\1m\.env')
key = os.getenv('PROPR_API_KEY')
headers = {'X-API-Key': key, 'Content-Type': 'application/json'}
acc = 'urn:prp-account:mYa6seVsUtDY'

resp = requests.get(f'https://api.propr.xyz/v1/accounts/{acc}/positions?status=open&asset=SUI', headers=headers, timeout=10)
positions = resp.json().get('data', [])
pos = None
for p in positions:
    if float(p.get('quantity', 0)) > 0:
        pos = p
        break

if not pos:
    print('No SUI position')
    exit()

pid = pos['positionId']
entry = float(pos['entryPrice'])
qty = pos['quantity']
atr = entry * 0.025
sl = round(entry - (atr * 1.5), 4)
tp = round(entry + (atr * 3.0), 4)

print(f'SUI: entry={entry:.4f} SL={sl:.4f} TP={tp:.4f} posId={pid}')

# Try positionSide=long instead of short
sl_order = {
    "accountId": acc,
    "intentId": str(ULID()),
    "exchange": "hyperliquid",
    "type": "stop_market",
    "side": "sell",
    "positionSide": "long",
    "productType": "perp",
    "timeInForce": "GTC",
    "asset": "SUI",
    "base": "SUI",
    "quote": "USDC",
    "quantity": qty,
    "triggerPrice": str(sl),
    "reduceOnly": True,
    "positionId": pid,
}
r = requests.post(f'https://api.propr.xyz/v1/accounts/{acc}/orders', headers=headers, json={"orders": [sl_order]}, timeout=10)
print(f'SL (positionSide=long): {r.status_code} {json.dumps(r.json(), indent=2)[:200]}')

time.sleep(2)

# Check if it's still there
resp2 = requests.get(f'https://api.propr.xyz/v1/accounts/{acc}/orders?status=open', headers=headers, timeout=10)
orders = resp2.json().get('data', [])
print(f'\nOpen orders after SL: {len(orders)}')
for o in orders:
    print(f'  {o["type"]} {o["asset"]} {o["side"]} trigger={o.get("triggerPrice")} status={o["status"]}')

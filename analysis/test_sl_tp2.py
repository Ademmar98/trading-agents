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

pid = pos['positionId']
entry = float(pos['entryPrice'])
qty = pos['quantity']
atr = entry * 0.025
sl = round(entry - (atr * 1.5), 4)
tp = round(entry + (atr * 3.0), 4)

print(f'SUI: entry={entry:.4f} SL={sl:.4f} TP={tp:.4f}')
print(f'Position: {pid}')
print(f'Quantity: {qty}')

# Try with closePosition=true and positionSide=short
sl_order = {
    "accountId": acc,
    "intentId": str(ULID()),
    "exchange": "hyperliquid",
    "type": "stop_market",
    "side": "sell",
    "positionSide": "short",
    "productType": "perp",
    "timeInForce": "GTC",
    "asset": "SUI",
    "base": "SUI",
    "quote": "USDC",
    "quantity": qty,
    "triggerPrice": str(sl),
    "reduceOnly": True,
    "closePosition": True,
    "positionId": pid,
}

print('\nPlacing SL with closePosition=true...')
r = requests.post(f'https://api.propr.xyz/v1/accounts/{acc}/orders', headers=headers, json={"orders": [sl_order]}, timeout=10)
print(f'Response: {r.status_code}')
resp_data = r.json()
print(json.dumps(resp_data, indent=2)[:500])

time.sleep(3)

# Check open orders
resp2 = requests.get(f'https://api.propr.xyz/v1/accounts/{acc}/orders?status=open', headers=headers, timeout=10)
orders = resp2.json().get('data', [])
print(f'\nOpen orders: {len(orders)}')
for o in orders:
    print(f'  {o["type"]} {o["asset"]} {o["side"]} trigger={o.get("triggerPrice")} posId={o.get("positionId")} status={o["status"]}')

# Also check pending
resp3 = requests.get(f'https://api.propr.xyz/v1/accounts/{acc}/orders?status=pending', headers=headers, timeout=10)
pending = resp3.json().get('data', [])
print(f'\nPending orders: {len(pending)}')
for o in pending:
    print(f'  {o["type"]} {o["asset"]} {o["side"]} trigger={o.get("triggerPrice")} posId={o.get("positionId")} status={o["status"]}')

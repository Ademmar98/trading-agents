from dotenv import load_dotenv; import os, requests, json
load_dotenv(r'C:\Users\DELL\OneDrive\1m\.env')
key = os.getenv('PROPR_API_KEY')
headers = {'X-API-Key': key, 'Content-Type': 'application/json'}
acc = 'urn:prp-account:mYa6seVsUtDY'

resp = requests.get(f'https://api.propr.xyz/v1/accounts/{acc}/positions?status=open', headers=headers, timeout=10)
positions = resp.json().get('data', [])
for p in positions:
    qty = float(p.get('quantity', 0))
    if qty > 0:
        asset = p['base']
        pos_side = p['positionSide']
        pid = p['positionId']
        print(f'Closing {asset} ({pos_side}) qty={qty} posId={pid}')

        from ulid import ULID
        close_side = 'sell' if pos_side == 'long' else 'buy'
        close_pos_side = 'short' if pos_side == 'long' else 'long'
        order = {
            "accountId": acc,
            "intentId": str(ULID()),
            "exchange": "hyperliquid",
            "type": "market",
            "side": close_side,
            "positionSide": close_pos_side,
            "productType": "perp",
            "timeInForce": "IOC",
            "asset": asset,
            "base": asset,
            "quote": "USDC",
            "quantity": str(qty),
            "reduceOnly": True,
            "closePosition": True,
            "positionId": pid,
        }
        r = requests.post(f'https://api.propr.xyz/v1/accounts/{acc}/orders', headers=headers, json={"orders": [order]}, timeout=10)
        print(f'Response: {r.status_code}')
        print(json.dumps(r.json(), indent=2))

from dotenv import load_dotenv; import os, requests, json, time
from ulid import ULID
load_dotenv(r'C:\Users\DELL\OneDrive\1m\.env')
key = os.getenv('PROPR_API_KEY')
headers = {'X-API-Key': key, 'Content-Type': 'application/json'}
acc = 'urn:prp-account:mYa6seVsUtDY'

resp = requests.get(f'https://api.propr.xyz/v1/accounts/{acc}/positions?status=open', headers=headers, timeout=10)
positions = resp.json().get('data', [])

for pos in positions:
    qty = float(pos.get('quantity', 0))
    if qty <= 0:
        continue

    asset = pos['base']
    entry = float(pos['entryPrice'])
    pid = pos['positionId']

    # Calculate SL/TP based on 2.5% ATR
    atr = entry * 0.025
    sl = round(entry - (atr * 1.5), 4)
    tp = round(entry + (atr * 3.0), 4)

    print(f'{asset}: entry={entry:.4f} SL={sl:.4f} TP={tp:.4f} qty={qty} posId={pid}')

    # SL
    sl_order = {
        "accountId": acc,
        "intentId": str(ULID()),
        "exchange": "hyperliquid",
        "type": "stop_market",
        "side": "sell",
        "positionSide": "short",
        "productType": "perp",
        "timeInForce": "GTC",
        "asset": asset,
        "base": asset,
        "quote": "USDC",
        "quantity": str(qty),
        "triggerPrice": str(sl),
        "reduceOnly": True,
        "positionId": pid,
    }
    r = requests.post(f'https://api.propr.xyz/v1/accounts/{acc}/orders', headers=headers, json={"orders": [sl_order]}, timeout=10)
    status = r.json().get('data', [{}])[0].get('status', 'error')
    print(f'  SL: {r.status_code} {status}')

    time.sleep(0.5)

    # TP
    tp_order = {
        "accountId": acc,
        "intentId": str(ULID()),
        "exchange": "hyperliquid",
        "type": "take_profit_market",
        "side": "sell",
        "positionSide": "short",
        "productType": "perp",
        "timeInForce": "GTC",
        "asset": asset,
        "base": asset,
        "quote": "USDC",
        "quantity": str(qty),
        "triggerPrice": str(tp),
        "reduceOnly": True,
        "positionId": pid,
    }
    r2 = requests.post(f'https://api.propr.xyz/v1/accounts/{acc}/orders', headers=headers, json={"orders": [tp_order]}, timeout=10)
    status2 = r2.json().get('data', [{}])[0].get('status', 'error')
    print(f'  TP: {r2.status_code} {status2}')

    time.sleep(0.5)

print('\nDone!')

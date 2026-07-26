import requests
resp = requests.post('https://api.hyperliquid.xyz/info', json={'type': 'meta'}, timeout=10)
data = resp.json()
universe = data.get('universe', [])
print(f'Total assets: {len(universe)}')
crypto = [a for a in universe if not a['name'].startswith('xyz:')]
print(f'Crypto perps: {len(crypto)}')
for a in crypto[:30]:
    print(f"  {a['name']}: szDecimals={a.get('szDecimals',2)}, maxLev={a.get('maxLeverage',20)}")
if len(crypto) > 30:
    print(f'  ... and {len(crypto)-30} more')
hip3 = [a for a in universe if a['name'].startswith('xyz:')]
print(f'HIP-3 (stocks/commodities): {len(hip3)}')
for a in hip3:
    print(f"  {a['name']}: szDecimals={a.get('szDecimals',2)}")

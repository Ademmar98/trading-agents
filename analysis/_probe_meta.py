import requests, json
d = requests.post("https://api.hyperliquid.xyz/info",
                  json={"type": "metaAndAssetCtxs"}, timeout=30).json()
print("top:", type(d), len(d))
a = d[0]
print("d[0]:", type(a), "keys:", list(a.keys()) if isinstance(a, dict) else "n/a")
if isinstance(a, dict) and "universe" in a:
    u = a["universe"]
    print("universe len:", len(u), "first:", json.dumps(u[0])[:300])
c = d[1]
print("d[1]:", type(c), "len:", len(c) if hasattr(c, '__len__') else '?')

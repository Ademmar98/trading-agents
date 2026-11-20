# -*- coding: utf-8 -*-
"""Read-only: probe URL variants for builder key."""
import requests

KEY = "builder_nydKlPUdiXUYb6lan4uPUALAZnrSMObo"

candidates = [
    "https://api.builder.propr.xyz/v1/users/me",
    "https://builder.propr.xyz/v1/users/me",
    "https://api.propr.xyz/builder/v1/users/me",
    "https://api.beta.propr.xyz/builder/v1/users/me",
    "https://builder-api.propr.xyz/v1/users/me",
    "https://gateway.propr.xyz/v1/users/me",
    "https://api.hyperliquid.xyz/info",
]
for url in candidates:
    try:
        r = requests.get(url, headers={"X-API-Key": KEY, "Authorization": f"Bearer {KEY}"},
                         timeout=15)
        txt = r.text[:150].replace("\n", " ")
        print(f"{url} => {r.status_code} {txt}")
    except Exception as e:
        print(f"{url} => ERR {type(e).__name__}")
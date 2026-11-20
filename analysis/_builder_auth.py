# -*- coding: utf-8 -*-
"""Read-only: try auth header variants for builder key."""
import requests, json

KEY = "builder_nydKlPUdiXUYb6lan4uPUALAZnrSMObo"

for url in ("https://api.beta.propr.xyz/v1/users/me",
            "https://api.propr.xyz/v1/users/me"):
    print(f"\n== {url} ==")
    for name, headers in (
        ("X-API-Key", {"X-API-Key": KEY}),
        ("Bearer", {"Authorization": f"Bearer {KEY}"}),
        ("X-Api-Token", {"X-Api-Token": KEY}),
        ("both", {"Authorization": f"Bearer {KEY}", "X-API-Key": KEY}),
    ):
        try:
            r = requests.get(url, headers=headers, timeout=20)
            print(f"  {name}: {r.status_code} {r.text[:120]}")
        except Exception as e:
            print(f"  {name}: ERR {e}")
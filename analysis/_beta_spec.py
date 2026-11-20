# -*- coding: utf-8 -*-
"""Read-only: fetch beta OpenAPI spec & look for account/permission routes."""
import os, sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
import requests

for url in ("https://api.beta.propr.xyz/v1/openapi.json",
            "https://api.beta.propr.xyz/openapi.json",
            "https://api.beta.propr.xyz/v1/docs"):
    try:
        r = requests.get(url, timeout=20)
        print(f"{url} -> {r.status_code} len={len(r.text)}")
        if r.status_code == 200:
            try:
                spec = r.json()
                paths = spec.get("paths", {})
                for p in paths:
                    if "account" in p or "permission" in p or "key" in p:
                        print("  ", p, list(paths[p].keys()))
                if "paths" not in spec:
                    print("  sample:", r.text[:300])
                break
            except Exception as e:
                print("  not json:", r.text[:200])
    except Exception as e:
        print(f"{url} ERR {e}")
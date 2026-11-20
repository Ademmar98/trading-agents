# -*- coding: utf-8 -*-
"""Read-only: inspect propr openapi for auth schemes + builder endpoints."""
import requests, json

spec = requests.get("https://propr.xyz/openapi.json", timeout=30).json()
print("securitySchemes:", json.dumps(spec.get("components", {}).get("securitySchemes", {}), default=str))
print("\npages/tag:", spec.get("info"))
paths = spec.get("paths", {})
for p in sorted(paths):
    if any(k in p.lower() for k in ("builder", "account", "order", "position", "trade", "margin")):
        print(p)
print("\ntotal paths:", len(paths))
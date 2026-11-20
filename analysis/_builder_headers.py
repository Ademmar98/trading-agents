# -*- coding: utf-8 -*-
"""Read-only: exhaustive auth-header variants for builder key against beta."""
import requests

KEY = "builder_nydKlPUdiXUYb6lan4uPUALAZnrSMObo"
urls = [
    "https://api.beta.propr.xyz/v1/users/me",
    "https://api.beta.propr.xyz/v1/challenge-attempts",
    "https://api.beta.propr.xyz/v1/health",
]
headers_variants = {
    "X-API-Key": {"X-API-Key": KEY},
    "x-api-key lower": {"x-api-key": KEY},
    "Authorization Bearer": {"Authorization": f"Bearer {KEY}"},
    "Authorization prp": {"Authorization": f"prp {KEY}"},
    "Authorization Basic": {"Authorization": f"Basic {KEY}"},
    "X-Builder-Key": {"X-Builder-Key": KEY},
    "X-Api-Key2": {"X-Api-Key": KEY, "X-Environment": "beta"},
    "jwt-style": {"Authorization": f"bearer {KEY}", "X-API-Key": KEY},
}
for url in urls:
    print(f"\n== {url} ==")
    for name, h in headers_variants.items():
        try:
            r = requests.get(url, headers=h, timeout=15)
            txt = r.text[:100].replace("\n", " ")
            print(f"  {name}: {r.status_code} {txt}")
        except Exception as e:
            print(f"  {name}: ERR {type(e).__name__}")
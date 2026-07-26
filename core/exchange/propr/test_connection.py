"""
Propr SDK Connection Test — Verify API key and list available assets.
"""
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent.parent.parent / ".env")
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

import requests


def test_health():
    print("1. Testing API health...")
    resp = requests.get("https://api.propr.xyz/v1/health", timeout=10)
    print(f"   Status: {resp.json()}")
    return resp.status_code == 200


def test_leverage_limits():
    print("\n2. Fetching leverage limits...")
    resp = requests.get("https://api.propr.xyz/v1/leverage-limits/effective", timeout=10)
    data = resp.json()
    print(f"   Default max: {data.get('defaultMax')}x")
    overrides = data.get("overrides", {})
    for asset, limit in sorted(overrides.items()):
        print(f"   {asset}: {limit}x")
    return data


def test_challenges():
    print("\n3. Listing available challenges...")
    resp = requests.get("https://api.propr.xyz/v1/challenges?exchange=hyperliquid", timeout=10)
    data = resp.json().get("data", [])
    for ch in data[:5]:
        name = ch.get("name", "?")
        fee = ch.get("pricing", {}).get("price", "?")
        size = ch.get("initialBalance", "?")
        target = ch.get("phases", [{}])[0].get("profitTarget", "?") if ch.get("phases") else "?"
        print(f"   {name}: ${size} account, fee=${fee}, target={target}")
    if not data:
        print("   No challenges found")
    return data


def test_user(api_key: str):
    print("\n4. Testing authenticated endpoint...")
    resp = requests.get(
        "https://api.propr.xyz/v1/users/me",
        headers={"X-API-Key": api_key},
        timeout=10,
    )
    if resp.status_code == 200:
        data = resp.json()
        print(f"   User: {data.get('email', data.get('userId', '?'))}")
        return data
    else:
        print(f"   Error: {resp.status_code} {resp.text}")
        return None


def test_attempts(api_key: str):
    print("\n5. Checking challenge attempts...")
    resp = requests.get(
        "https://api.propr.xyz/v1/challenge-attempts?status=active",
        headers={"X-API-Key": api_key},
        timeout=10,
    )
    data = resp.json().get("data", [])
    for attempt in data:
        account_id = attempt.get("accountId", "?")
        status = attempt.get("status", "?")
        print(f"   Account: {account_id}, Status: {status}")
    if not data:
        print("   No active challenges found")
    return data


def main():
    api_key = os.getenv("PROPR_API_KEY", "")
    print(f"API Key: {'SET' if api_key else 'NOT SET'}")
    print("=" * 50)

    test_health()
    test_leverage_limits()
    test_challenges()

    if api_key:
        test_user(api_key)
        test_attempts(api_key)
    else:
        print("\n4-5. Skipped (no API key)")

    print("\n" + "=" * 50)
    print("Test complete.")


if __name__ == "__main__":
    main()

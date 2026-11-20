import json
import os
import time
import requests

API = "https://api.hyperliquid.xyz/info"
STATS = "https://stats-data.hyperliquid.xyz/Mainnet/leaderboard"
OUT = "analysis/profitable_wallets_scan2.json"
PROGRESS = "analysis/wallet_scan2_progress.json"

s = requests.Session()
# reuse connection, retry on transient failures
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
retry = Retry(total=5, backoff_factor=1.5,
              status_forcelist=[429, 500, 502, 503, 504],
              allowed_methods=["POST", "GET"])
s.mount("https://", HTTPAdapter(max_retries=retry, pool_connections=1, pool_maxsize=1))


def get_leaderboard():
    for _ in range(5):
        try:
            r = s.get(STATS, timeout=60)
            return r.json()["leaderboardRows"]
        except Exception:
            time.sleep(3)
    return []


def perf_map(row):
    d = {}
    for win, per in row.get("windowPerformances", []):
        d[win] = per
    return d


def get_fills(addr):
    for _ in range(5):
        try:
            r = s.post(API, json={"type": "userFills", "user": addr}, timeout=30)
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass
        time.sleep(2)
    return None


def analyze(fills):
    opens_long = opens_short = closes = wins = losses = 0
    realized = 0.0
    for f in fills:
        d = f.get("dir", "")
        cp = float(f.get("closedPnl", "0") or 0)
        realized += cp
        if d == "Open Long":
            opens_long += 1
        elif d == "Open Short":
            opens_short += 1
        if "Close" in d:
            closes += 1
            if cp > 0:
                wins += 1
            elif cp < 0:
                losses += 1
    tot = opens_long + opens_short
    return {
        "fills": len(fills),
        "opens_long": opens_long,
        "opens_short": opens_short,
        "long_share": opens_long / tot if tot else 0.0,
        "closes": closes,
        "wins": wins,
        "losses": losses,
        "winrate": wins / closes if closes else 0.0,
        "realized": realized,
    }


def main():
    limit = int(os.getenv("SCAN_LIMIT", "1500"))
    done = {}
    if os.path.exists(PROGRESS):
        done = json.load(open(PROGRESS))

    rows = get_leaderboard()
    print(f"leaderboard rows: {len(rows)}", flush=True)

    candidates = []
    for row in rows:
        pm = perf_map(row)
        at = pm.get("allTime", {})
        wk = pm.get("week", {})
        pnl_at = float(at.get("pnl", "0") or 0)
        pnl_wk = float(wk.get("pnl", "0") or 0)
        acct = float(row.get("accountValue", "0") or 0)
        if pnl_at <= 0 or pnl_wk <= 0 or acct < 5000:
            continue
        candidates.append((pnl_at, pnl_wk, acct, row["ethAddress"]))
    candidates.sort(reverse=True)
    print(f"profitable: {len(candidates)} (scanning up to {limit})", flush=True)

    results = []
    for i, (pnl_at, pnl_wk, acct, addr) in enumerate(candidates[:limit]):
        if addr in done:
            continue
        a = analyze(get_fills(addr))
        if a is None:
            continue
        a["addr"] = addr
        a["acct"] = acct
        a["pnl_at"] = pnl_at
        a["pnl_wk"] = pnl_wk
        results.append(a)
        done[addr] = a
        if (i + 1) % 25 == 0:
            json.dump(done, open(PROGRESS, "w"))
            print(f"  ...{i+1} done", flush=True)
        time.sleep(0.1)

    json.dump(done, open(PROGRESS, "w"))
    with open(OUT, "w") as f:
        json.dump(results, f, indent=1)
    print(f"saved {len(results)} new -> {OUT}; total in progress {len(done)}", flush=True)


if __name__ == "__main__":
    main()

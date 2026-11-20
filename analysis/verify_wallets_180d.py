import json
import os
import time
import requests
from datetime import timezone

API = "https://api.hyperliquid.xyz/info"
STATS = "https://stats-data.hyperliquid.xyz/Mainnet/leaderboard"
OUT = "analysis/wallet_verify_180d.json"
PROGRESS = "analysis/wallet_verify_180d_progress.json"

from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
retry = Retry(total=5, backoff_factor=1.5,
              status_forcelist=[429, 500, 502, 503, 504],
              allowed_methods=["POST", "GET"])
s = requests.Session()
s.mount("https://", HTTPAdapter(max_retries=retry, pool_connections=1, pool_maxsize=1))


def get_fills_since(addr, start_ms, end_ms=None):
    end_ms = end_ms or int(time.time() * 1000)
    all_fills = []
    cursor = None
    for _ in range(30):
        body = {"type": "userFillsByTime", "user": addr,
                "startTime": start_ms, "endTime": end_ms}
        if cursor:
            body["cursor"] = cursor
        try:
            r = s.post(API, json=body, timeout=30)
        except Exception:
            time.sleep(2)
            continue
        if r.status_code != 200:
            break
        d = r.json()
        if isinstance(d, list):
            all_fills.extend(d)
            break
        fills = d.get("fills", [])
        if not fills:
            break
        all_fills.extend(fills)
        cursor = d.get("cursor")
        if not cursor:
            break
        time.sleep(0.2)
    return all_fills


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
        "n_fills": len(fills),
        "opens_long": opens_long,
        "opens_short": opens_short,
        "long_share": opens_long / tot if tot else 0.0,
        "closes": closes,
        "wins": wins,
        "losses": losses,
        "winrate": wins / closes if closes else 0.0,
        "realized": realized,
    }


def leaderboard_map():
    for _ in range(5):
        try:
            r = s.get(STATS, timeout=60)
            m = {}
            for row in r.json()["leaderboardRows"]:
                d = {}
                for win, per in row.get("windowPerformances", []):
                    d[win] = per
                m[row["ethAddress"]] = (row, d)
            return m
        except Exception:
            time.sleep(3)
    return {}


def main():
    addrs = [l.split()[-1].strip()
             for l in open("analysis/verify_wallets_list.txt")
             if l.strip() and not l.startswith("#")]
    done = {}
    if os.path.exists(PROGRESS):
        done = json.load(open(PROGRESS))

    lb = leaderboard_map()
    now = int(time.time() * 1000)
    day180 = now - 180 * 86400 * 1000

    for addr in addrs:
        if addr in done:
            continue
        f = analyze(get_fills_since(addr, day180))
        row, pm = lb.get(addr, (None, {}))
        at = pm.get("allTime", {})
        wk = pm.get("week", {})
        mo = pm.get("month", {})
        f["addr"] = addr
        f["alltime_pnl"] = float(at.get("pnl", 0) or 0)
        f["alltime_roi"] = float(at.get("roi", 0) or 0)
        f["week_pnl"] = float(wk.get("pnl", 0) or 0)
        f["month_pnl"] = float(mo.get("pnl", 0) or 0)
        f["acct"] = float(row["accountValue"]) if row else 0
        done[addr] = f
        print(f"{addr[:12]}.. 180d: wr={f['winrate']:.3f} long={f['long_share']:.3f} "
              f"closes={f['closes']} realized=${f['realized']:,.0f} "
              f"| alltime=${f['alltime_pnl']:,.0f} roi={f['alltime_roi']*100:.0f}%", flush=True)
        json.dump(done, open(PROGRESS, "w"))
        time.sleep(0.2)

    with open(OUT, "w") as fh:
        json.dump(done, fh, indent=1)
    print(f"saved {len(done)} -> {OUT}")


if __name__ == "__main__":
    main()

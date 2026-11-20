import json
import sys
import time
import requests

API = "https://api.hyperliquid.xyz/info"
STATS = "https://stats-data.hyperliquid.xyz/Mainnet/leaderboard"


def get_leaderboard():
    r = requests.get(STATS, timeout=60)
    return r.json()["leaderboardRows"]


def perf_map(row):
    d = {}
    for win, per in row.get("windowPerformances", []):
        d[win] = per
    return d


def get_fills(addr):
    r = requests.post(API, json={"type": "userFills", "user": addr}, timeout=30)
    if r.status_code != 200:
        return None
    return r.json()


def analyze(addr):
    fills = get_fills(addr)
    if not fills:
        return None
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
    total_open = opens_long + opens_short
    long_share = opens_long / total_open if total_open else 0.0
    winrate = wins / closes if closes else 0.0
    return {
        "addr": addr,
        "fills": len(fills),
        "opens_long": opens_long,
        "opens_short": opens_short,
        "long_share": long_share,
        "closes": closes,
        "wins": wins,
        "losses": losses,
        "winrate": winrate,
        "realized": realized,
    }


def main():
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
        if pnl_at <= 0:
            continue
        if pnl_wk <= 0:
            continue
        if acct < 5000:
            continue
        candidates.append((pnl_at, pnl_wk, acct, row["ethAddress"]))

    candidates.sort(reverse=True)
    print(f"profitable (allTime>0, week>0, acct>=5k): {len(candidates)}", flush=True)

    results = []
    for i, (pnl_at, pnl_wk, acct, addr) in enumerate(candidates[:300]):
        try:
            a = analyze(addr)
        except Exception as e:
            print(f"  err {addr}: {e}", flush=True)
            continue
        if a is None:
            continue
        a["acct"] = acct
        a["pnl_at"] = pnl_at
        a["pnl_wk"] = pnl_wk
        results.append(a)
        print(f"[{i+1}] {addr[:10]}.. wr={a['winrate']:.3f} long={a['long_share']:.3f} "
              f"realized=${a['realized']:,.0f} closes={a['closes']}", flush=True)
        time.sleep(0.15)

    out = "analysis/profitable_wallets_scan.json"
    with open(out, "w") as f:
        json.dump(results, f, indent=1)
    print(f"\nsaved {len(results)} to {out}", flush=True)


if __name__ == "__main__":
    main()

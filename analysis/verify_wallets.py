import json
import time
import requests

API = "https://api.hyperliquid.xyz/info"
STATS = "https://stats-data.hyperliquid.xyz/Mainnet/leaderboard"

wallets = [
    "0x519c721de735f7c9e6146d167852e60d60496a47",
    "0x7bfee91193d9df2ac0bfe90191d40f23c773c060",
    "0x183d0567c33e7591c22540e45d2f74730b42a0ca",
    "0x24eeb5249ce20cfb667e26b463c0ef394588d65f",
    "0x8af700ba841f30e0a3fcb0ee4c4a9d223e1efa05",
    "0x7e4e766d0ae5ea9cded0c694669194db92800107",
    "0x5db201ae15f9702f0a0ff9f00b8c1c18355373d0",
    "0x082e843a431aef031264dc232693dd710aedca88",
    "0xa906355beaf1d69a5fe73ce55899c49c6e67916c",
    "0x5a106f5cf9fba6af5dda6bde1b8f3bacbd532c37",
]


def leaderboard_map():
    r = requests.get(STATS, timeout=60)
    m = {}
    for row in r.json()["leaderboardRows"]:
        d = {}
        for win, per in row.get("windowPerformances", []):
            d[win] = per
        m[row["ethAddress"]] = (row, d)
    return m


def get_fills_since(addr, start_ms):
    fills = []
    cursor = None
    for _ in range(12):
        body = {"type": "userFillsByTime", "user": addr,
                "startTime": start_ms, "endTime": int(time.time() * 1000)}
        if cursor:
            body["cursor"] = cursor
        r = requests.post(API, json=body, timeout=30)
        if r.status_code != 200:
            break
        d = r.json()
        if not d:
            break
        if isinstance(d, list):
            fills.extend(d)
            break
        fills.extend(d.get("fills", []))
        cursor = d.get("cursor")
        if not cursor:
            break
        time.sleep(0.15)
    return fills


def analyze_fills(fills):
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


def main():
    lb = leaderboard_map()
    now = int(time.time() * 1000)
    day90 = now - 90 * 86400 * 1000
    day180 = now - 180 * 86400 * 1000
    for addr in wallets:
        row, pm = lb.get(addr, (None, {}))
        at = pm.get("allTime", {})
        wk = pm.get("week", {})
        mo = pm.get("month", {})
        f90 = analyze_fills(get_fills_since(addr, day90))
        f180 = analyze_fills(get_fills_since(addr, day180))
        print("=" * 78)
        print(addr)
        print(f"  leaderboard  week_pnl=${float(wk.get('pnl',0)):,.0f}  "
              f"month_pnl=${float(mo.get('pnl',0)):,.0f}  "
              f"alltime_pnl=${float(at.get('pnl',0)):,.0f}  "
              f"alltime_roi={float(at.get('roi',0))*100:.1f}%  "
              f"acct=${float(row['accountValue']):,.0f}" if row else "  not on leaderboard")
        for tag, a in [("90d", f90), ("180d", f180)]:
            print(f"  {tag}: fills={a['n_fills']} closes={a['closes']} "
                  f"winrate={a['winrate']:.3f} long_share={a['long_share']:.3f} "
                  f"realized=${a['realized']:,.0f}")
        time.sleep(0.3)


if __name__ == "__main__":
    main()

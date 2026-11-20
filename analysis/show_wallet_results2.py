import json

progress = json.load(open("analysis/wallet_scan2_progress.json"))
scan1 = {}
import os
if os.path.exists("analysis/profitable_wallets_scan.json"):
    for r in json.load(open("analysis/profitable_wallets_scan.json")):
        scan1[r["addr"]] = r

# merge both sources
data = list(progress.values())
for r in scan1.values():
    if r["addr"] not in progress:
        data.append(r)

cand = [r for r in data
        if r["long_share"] >= 0.98 and r["realized"] > 0
        and r["closes"] >= 100 and r["winrate"] >= 0.50]
cand.sort(key=lambda r: (r["winrate"], r["realized"]), reverse=True)
print(f"candidates long>=0.98, closes>=100, wr>=0.50, realized>0: {len(cand)}")
for i, r in enumerate(cand[:25], 1):
    print(f"{i}. {r['addr']}  wr={r['winrate']:.3f}  long={r['long_share']:.3f}  "
          f"closes={r['closes']}  realized=${r['realized']:,.0f}  acct=${r['acct']:,.0f}")

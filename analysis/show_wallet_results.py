import json

data = json.load(open("analysis/profitable_wallets_scan.json"))
cand = [r for r in data
        if r["long_share"] >= 0.98 and r["realized"] > 0
        and r["closes"] >= 50 and r["winrate"] >= 0.50]
cand.sort(key=lambda r: (r["winrate"], r["realized"]), reverse=True)
print("top long-only profitable wallets (scan #1):")
for i, r in enumerate(cand[:15], 1):
    print(f"{i}. {r['addr']}")
    print(f"   winrate={r['winrate']:.3f}  long_share={r['long_share']:.3f}  "
          f"closes={r['closes']}  wins={r['wins']}  losses={r['losses']}  "
          f"realized=${r['realized']:,.0f}  acct=${r['acct']:,.0f}")

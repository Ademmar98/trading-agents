import json

verify = json.load(open("analysis/wallet_verify_180d.json"))

passing = [r for r in verify.values()
           if r["long_share"] >= 0.90
           and r["realized"] > 0
           and r["closes"] >= 100
           and r["winrate"] >= 0.55]
passing.sort(key=lambda r: (r["winrate"], r["realized"]), reverse=True)

print(f"{'wallet':<44}{'wr':>6}{'long%':>7}{'closes':>7}{'realized':>12}{'alltime':>13}{'roi':>8}")
print("-" * 100)
for r in passing[:10]:
    print(f"{r['addr']:<44}{r['winrate']:>6.3f}{r['long_share']:>7.3f}"
          f"{r['closes']:>7}{r['realized']:>12,.0f}{r['alltime_pnl']:>13,.0f}"
          f"{r['alltime_roi']*100:>7.0f}%")

print()
print(f"total verified passing: {len(passing)}")

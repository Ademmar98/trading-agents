"""Turn swing_RESULTS.json into the daily-swing verdict tables."""
import json
import sys

PATH = r"C:\Users\DELL\AppData\Local\Temp\claude\C--Users-DELL-OneDrive-1m--claude-worktrees-festive-pare-bbce48\ca03efac-2fb5-4b2b-4a2d-3c6c93082860\scratchpad\quant\swing_RESULTS.json"
results = json.load(open(sys.argv[1] if len(sys.argv) > 1 else PATH))
ok = [r for r in results if "error" not in r]
for r in results:
    if "error" in r:
        print(f"!! {r['symbol']}: {r['error']}")

print("=" * 82)
print("STAGE A — deployed swing_signal, bracket-simulated (costs 0.14% RT, hold<=60d)")
print("=" * 82)
print(f"{'sym':7s} {'style':16s} {'n':>4s} {'W':>4s} {'L':>4s} {'t/o':>4s} "
      f"{'realWR%':>8s} {'netExp%':>9s} {'avgR':>6s} {'hold_d':>6s} {'sl%':>5s} {'tp%':>6s}")
pool = {}
for r in ok:
    A = r["stageA"]
    for style in ("all", "swing_breakout", "swing_pullback", "swing_momentum"):
        a = A.get(style, {})
        if not a.get("n"):
            continue
        tag = r["symbol"].replace("/USD", "") if style == "all" else ""
        label = style.replace("swing_", "") if style != "all" else "ALL"
        print(f"{tag:7s} {label:16s} {a['n']:4d} {a['win']:4d} {a['loss']:4d} "
              f"{a['timeout']:4d} {a['realized_wr']:8.1f} {a['net_exp_pct']:9.4f} "
              f"{a['avg_R']:6.2f} {a['avg_hold_days']:6.1f} {a['avg_sl_pct']:5.1f} {a['avg_tp_pct']:6.1f}")
        if style == "all":
            pool["n"] = pool.get("n", 0) + a["n"]
            pool["exp_w"] = pool.get("exp_w", 0) + a["net_exp_pct"] * a["n"]
            pool["r_w"] = pool.get("r_w", 0) + a["avg_R"] * a["n"]
if pool.get("n"):
    print(f"{'POOLED':7s} {'ALL':16s} {pool['n']:4d} {'':14s} "
          f"{'':8s} {pool['exp_w']/pool['n']:9.4f} {pool['r_w']/pool['n']:6.2f}")

print()
print("STAGE A — per-symbol strategy total vs buy & hold")
print(f"{'sym':7s} {'years':>6s} {'signals':>8s} {'net_sum%':>9s} {'buy&hold%':>10s}")
for r in ok:
    a = r["stageA"]["all"]
    net_sum = a["net_exp_pct"] * a["n"] if a.get("n") else 0
    print(f"{r['symbol'].replace('/USD',''):7s} {r['years']:6.1f} {a.get('n',0):8d} "
          f"{net_sum:9.1f} {r['buy_hold_pct']:10.1f}")

print()
print("=" * 82)
print("STAGE B — daily regime-hold control (long above SMA50, flat below)")
print("=" * 82)
print(f"{'sym':7s} {'flips':>6s} {'roundtrips':>11s} {'total%':>9s} {'maxDD%':>8s} {'buy&hold%':>10s}")
for r in ok:
    b = r["stageB"]
    print(f"{r['symbol'].replace('/USD',''):7s} {b['flips']:6d} {b['n_round_trips']:11d} "
          f"{b['total_ret_pct']:9.1f} {b['maxdd_pct']:8.1f} {b['buy_hold_pct']:10.1f}")

print()
print("=" * 82)
print("STAGE C — exit-geometry grid on same entries, IS vs OOS net expectancy%")
print("=" * 82)
# Aggregate across symbols by (sl_mult, rr, seg)
agg = {}
for r in ok:
    for g in r.get("stageC", []):
        k = (g["sl_mult"], g["rr"], g["seg"])
        agg.setdefault(k, {"n": 0, "sum": 0.0})
        agg[k]["n"] += g["n"]
        agg[k]["sum"] += g["net_exp_pct"] * g["n"]
print(f"{'sl_mult':>7s} {'rr':>4s} | {'IS_n':>6s} {'IS_exp%':>9s} | {'OOS_n':>6s} {'OOS_exp%':>9s}")
combos = sorted({(k[0], k[1]) for k in agg})
for sl_mult, rr in combos:
    isk, ook = (sl_mult, rr, "IS"), (sl_mult, rr, "OOS")
    isd = agg.get(isk, {"n": 0, "sum": 0}); ood = agg.get(ook, {"n": 0, "sum": 0})
    is_e = isd["sum"] / isd["n"] if isd["n"] else 0
    oo_e = ood["sum"] / ood["n"] if ood["n"] else 0
    print(f"{sl_mult:7.1f} {rr:4.1f} | {isd['n']:6d} {is_e:9.4f} | {ood['n']:6d} {oo_e:9.4f}")

print()
print("VERDICT GUIDE: swing works if net expectancy/trade > 0 AND avg_R > 0 with")
print("enough signals; regime-hold works if it beats B&H risk-adjusted (fewer flips");
print("than the 1H control's 500-700). Costs are ~0.14% vs multi-% targets -> negligible.")

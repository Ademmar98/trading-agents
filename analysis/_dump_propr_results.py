import json

for fname in ["analysis/deep_hunt_v4_propr_results.json",
              "analysis/deep_hunt_v4_propr_strict.json",
              "analysis/deep_hunt_v3_90day_results.json"]:
    try:
        d = json.load(open(fname))
    except Exception as e:
        print(f"== {fname}: ERR {e}")
        continue
    print("=" * 90)
    print(f"FILE: {fname}")
    print("=" * 90)
    if isinstance(d, dict):
        print("keys:", list(d.keys())[:12])
        res = d.get("results", [])
        print(f"configs tested={d.get('configs_tested')} passed={d.get('configs_passed')} failed={d.get('configs_failed')}")
        b = d.get("best_config")
        if b:
            print(f"BEST: {b.get('name')} n={b.get('trades')} WR={b.get('win_rate')}% "
                  f"PF={b.get('profit_factor')} pnl=${b.get('total_pnl')} "
                  f"maxDD={b.get('max_dd_pct')}% final=${b.get('final_capital')} "
                  f"failed={b.get('challenge_failed')} reason={b.get('fail_reason')}")
        print("-" * 90)
        for r in res:
            print(f"  {r.get('name','?'):26s} n={r.get('trades',0):3d} "
                  f"WR={r.get('win_rate',0):5.1f}% PF={r.get('profit_factor',0):5.2f} "
                  f"pnl=${r.get('total_pnl',0):>8.2f} exp=${r.get('expectancy',0):>6.2f} "
                  f"maxDD={r.get('max_dd_pct',0):5.2f}% fail={r.get('challenge_failed')}")

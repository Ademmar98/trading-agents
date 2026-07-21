"""Turn scalp_RESULTS.json into the Stage 0/1 verdict tables."""
import json
import statistics
import sys

PATH = r"C:\Users\DELL\AppData\Local\Temp\claude\C--Users-DELL-OneDrive-1m--claude-worktrees-festive-pare-bbce48\ca03efac-2fb5-4b2b-4a2d-3c6c93082860\scratchpad\quant\scalp_RESULTS.json"

results = json.load(open(sys.argv[1] if len(sys.argv) > 1 else PATH))
ok = [r for r in results if "error" not in r]
bad = [r for r in results if "error" in r]
for r in bad:
    print(f"!! {r['symbol']}: {r['error']}")

print("=" * 78)
print("STAGE 0 — cost feasibility (SL = 1.5x ATR, costs 0.14% round trip)")
print("=" * 78)
print(f"{'sym':9s} {'ATR%':>6s} {'SL%':>6s} | {'rr':>4s} {'randWR':>7s} {'needWR':>7s} "
      f"{'gap':>6s} {'randExp%':>9s}")
for r in ok:
    s0 = r["stage0"]
    for rr in ("1.0", "1.5", "2.0"):
        b = s0["baseline"][f"rr_{rr}"]
        gap = b["breakeven_wr"] - b["random_wr"]
        tag = r["symbol"].replace("/USD", "")
        lead = f"{tag:9s} {s0['median_atr_pct']:6.3f} {s0['typical_sl_pct']:6.3f}" \
            if rr == "1.0" else " " * 22
        print(f"{lead} | {rr:>4s} {b['random_wr']:7.1f} {b['breakeven_wr']:7.1f} "
              f"{gap:+6.1f} {b['random_expectancy_pct']:9.4f}")

print()
print("=" * 78)
print("STAGE 1a — scalp_signal (the live scalp_15m stack), bracket-simulated")
print("=" * 78)
print(f"{'sym':9s} {'n':>5s} {'win':>5s} {'loss':>5s} {'t/o':>5s} {'realWR%':>8s} "
      f"{'netExp%':>8s} {'held':>5s}")
pool_n = pool_exp_w = 0.0
for r in ok:
    sc = r["scalp_signal"]
    n = sc["total"]["n"]
    if not n:
        print(f"{r['symbol'].replace('/USD',''):9s} {0:5d}  (no signals)")
        continue
    br = sc["brackets"]
    resolved = br["win"] + br["loss"]
    wr = br["win"] / resolved * 100 if resolved else 0
    print(f"{r['symbol'].replace('/USD',''):9s} {n:5d} {br['win']:5d} {br['loss']:5d} "
          f"{br['timeout']:5d} {wr:8.1f} {sc['net_expectancy_pct']:8.4f} "
          f"{sc.get('avg_bars_held', 0):5.1f}")
    pool_n += n
    pool_exp_w += sc["net_expectancy_pct"] * n
if pool_n:
    print(f"{'POOLED':9s} {int(pool_n):5d} {'':17s} {'':8s} {pool_exp_w/pool_n:8.4f}")

print()
print("STAGE 1a — win_prob calibration (claimed vs realized)")
for r in ok:
    for k, v in r["scalp_signal"].items():
        if k.startswith("wp_"):
            print(f"  {r['symbol'].replace('/USD',''):8s} claimed {v['claimed_wr']:.0f}% "
                  f"-> realized {v['realized_wr']:.1f}%  (n={v['n']}, "
                  f"netExp {v['net_exp_pct']:+.4f}%)")

print()
print("=" * 78)
print("STAGE 1b — evaluate_tf BUY events: forward returns vs baseline (%)")
print("=" * 78)
print(f"{'sym':9s} {'slice':14s} {'n':>6s} {'fwd1h':>8s} {'fwd4h':>8s} {'base1h':>8s} {'base4h':>8s} {'edge4h':>8s}")
for r in ok:
    tf = r["evaluate_tf"]
    base = r["baseline"]
    for key in ("total", "STRONG_BUY", "BUY", "WEAK_BUY", "gc_up",
                "vol_confirmed", "ichi_pos", "regime_trending", "regime_ranging"):
        a = tf.get(key) or {}
        if not a.get("n"):
            continue
        edge = a["fwd4h_mean"] - base["fwd4h_mean"]
        tag = r["symbol"].replace("/USD", "") if key == "total" else ""
        print(f"{tag:9s} {key:14s} {a['n']:6d} {a['fwd1h_mean']:8.4f} "
              f"{a['fwd4h_mean']:8.4f} {base['fwd1h_mean']:8.4f} "
              f"{base['fwd4h_mean']:8.4f} {edge:+8.4f}")

print()
print("KILL GATE: scalp needs conditional edge >= ~2x costs (0.28%) at 4h,")
print("or realized WR clearing the breakeven frontier at its actual R:R.")

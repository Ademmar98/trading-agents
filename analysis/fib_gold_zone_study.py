#!/usr/bin/env python3
"""
Fibonacci Gold Zone — full study. 3 months, 1m bars, long-only spot.

Resumable: each pair's result is appended to the results file as it completes,
and completed pairs are skipped on restart. Safe to kill and rerun.

Applies the methodology bar from prior studies:
  - 0.14% round-trip costs on every trade
  - 70/30 in-sample / out-of-sample split by TIME
  - per-pair concentration reported (result excluding the best 2 pairs)
  - gross vs net separated, so "no edge" and "edge eaten by fees" are distinct
  - both gold-zone entries (0.500 first-touch and 0.618 far-edge) tested
"""
import json
import sys
import time
from pathlib import Path

import numpy as np

# Some Binance tickers contain characters cp1252 cannot encode; the Windows
# console default kills the run mid-study on a print, not on the analysis.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from analysis.fib_gold_zone import (  # noqa: E402
    qualified_pairs, fetch_1m, backtest, FEE_RT,
)

OUT = Path(__file__).parent / "fib_gold_zone_results.json"
DAYS = 90
MIN_VOL = 1_000_000
VARIANTS = [
    {"name": "gz_0500", "entry_fib": 0.500, "min_R_pct": 0.0},
    {"name": "gz_0618", "entry_fib": 0.618, "min_R_pct": 0.0},
    # Cost floor: only setups whose reward can physically exceed the fee.
    {"name": "gz_0500_costfloor", "entry_fib": 0.500, "min_R_pct": 0.30},
]


def load():
    if OUT.exists():
        try:
            return json.loads(OUT.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"days": DAYS, "fee_rt": FEE_RT, "pairs": {}}


def save(state):
    OUT.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")


def prefetch(pairs, workers=5):
    """Fetch symbols concurrently — the REST round trip, not the backtest, is
    the bottleneck. 5 workers x ~2 weight/req stays well inside Binance's
    1200/min budget. Results are cached to parquet, so this is idempotent."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    todo = [p["symbol"] for p in pairs
            if not (Path(__file__).parent / "fib_1m_data" / f"{p['symbol']}_1m.parquet").exists()]
    if not todo:
        return
    print(f"prefetching {len(todo)} symbols with {workers} workers...")
    done = 0
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(fetch_1m, s, DAYS): s for s in todo}
        for f in as_completed(futs):
            done += 1
            try:
                f.result()
            except Exception as e:
                print(f"  {futs[f]}: {e}")
            if done % 10 == 0:
                el = (time.time() - t0) / 60
                rate = done / max(el, 1e-9)
                print(f"  fetched {done}/{len(todo)}  ({el:.0f}m, "
                      f"eta {(len(todo)-done)/max(rate,1e-9):.0f}m)")


def main():
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else None
    pairs = qualified_pairs(min_vol_usd=MIN_VOL, limit=limit)
    prefetch(pairs)
    state = load()
    print(f"universe: {len(pairs)} pairs (>${MIN_VOL/1e6:.0f}M 24h vol), "
          f"{DAYS}d of 1m bars, fee {FEE_RT*100:.2f}% RT")
    print(f"already done: {len(state['pairs'])}")

    t0 = time.time()
    for n, p in enumerate(pairs, 1):
        sym = p["symbol"]
        if sym in state["pairs"]:
            continue
        try:
            df = fetch_1m(sym, days=DAYS)
        except Exception as e:
            print(f"  {sym}: fetch failed {e}")
            continue
        if df is None or len(df) < 5000:
            state["pairs"][sym] = {"skipped": f"only {0 if df is None else len(df)} bars"}
            save(state)
            continue

        split = int(len(df) * 0.70)
        rec = {"volume": p["volume"], "bars": len(df),
               "start": str(df["ts"].iloc[0]), "end": str(df["ts"].iloc[-1])}
        for v in VARIANTS:
            full = backtest(df, sym, v["entry_fib"], v["min_R_pct"])
            is_ = backtest(df.iloc[:split], sym, v["entry_fib"], v["min_R_pct"])
            oos = backtest(df.iloc[split:], sym, v["entry_fib"], v["min_R_pct"])
            for r in (full, is_, oos):
                if r:
                    r.pop("trade_list", None)
            rec[v["name"]] = {"full": full, "is": is_, "oos": oos}

        state["pairs"][sym] = rec
        save(state)

        f = rec["gz_0500"]["full"]
        el = (time.time() - t0) / 60
        msg = (f"{f['trades']:5d} tr  WR {f['win_rate']:5.1f}%  "
               f"grossPF {f['gross_pf']:6.3f}  netPF {f['profit_factor']:6.3f}  "
               f"net {f['net_sum_pct']:+9.2f}%  avgR {f['avg_R_pct']:.3f}%") \
            if f and f.get("trades") else "no trades"
        print(f"[{n}/{len(pairs)}] {sym:12s} {msg}   ({el:.0f}m)")

    report(state)


def report(state):
    print("\n" + "=" * 78)
    print("FIBONACCI GOLD ZONE — 1m, long-only spot, 3 months")
    print("=" * 78)
    print(f"fee: {FEE_RT*100:.2f}% round trip\n")

    for v in VARIANTS:
        name = v["name"]
        rows = [(s, r[name]["full"]) for s, r in state["pairs"].items()
                if isinstance(r, dict) and r.get(name, {}).get("full")
                and r[name]["full"].get("trades")]
        if not rows:
            print(f"{name}: no pair produced a trade")
            continue

        tot = sum(f["trades"] for _, f in rows)
        net = np.array([f["net_sum_pct"] for _, f in rows])
        gross = np.array([f["gross_sum_pct"] for _, f in rows])
        avg_r = np.mean([f["avg_R_pct"] for _, f in rows])
        wr = np.average([f["win_rate"] for _, f in rows],
                        weights=[f["trades"] for _, f in rows])
        pos = int((net > 0).sum())

        print(f"--- {name}  (entry fib {v['entry_fib']}, minR {v['min_R_pct']}%) ---")
        print(f"  pairs traded      {len(rows)}")
        print(f"  total trades      {tot:,}")
        print(f"  weighted win rate {wr:.1f}%")
        print(f"  mean avg R        {avg_r:.3f}%  vs {FEE_RT*100:.2f}% fee  "
              f"-> reward/fee = {avg_r*0.9/(FEE_RT*100):.2f}x")
        print(f"  GROSS sum         {gross.sum():+,.1f}%   pairs positive "
              f"{int((gross>0).sum())}/{len(rows)}")
        print(f"  NET sum           {net.sum():+,.1f}%   pairs positive {pos}/{len(rows)}")
        print(f"  median pair net   {np.median(net):+.2f}%")
        if len(rows) > 2:
            trimmed = np.sort(net)[:-2]
            print(f"  NET excl. best 2  {trimmed.sum():+,.1f}%  "
                  f"(concentration check)")

        # out-of-sample
        oos = [r[name]["oos"] for _, r in
               [(s, state["pairs"][s]) for s, _ in rows]
               if r.get(name, {}).get("oos") and r[name]["oos"].get("trades")]
        if oos:
            o_net = np.array([o["net_sum_pct"] for o in oos])
            print(f"  OOS (last 30%)    {o_net.sum():+,.1f}%   pairs positive "
                  f"{int((o_net>0).sum())}/{len(o_net)}")
        print()

    print("=" * 78)


if __name__ == "__main__":
    main()

"""Walk-forward optimization driver.

70% In-Sample / 30% locked Out-of-Sample. Rolling WFO inside IS
(train 180d -> test 60d, step 60d). Final parameters = modal combo across
all window selections (robust-zone, never the global best). Single-pass OOS.
"""
import itertools
import json
from collections import Counter

import numpy as np
import pandas as pd

from engine import add_indicators, backtest, metrics

OUT = str(__import__("pathlib").Path(__file__).parent / "data")
SYMBOLS = ["BTCUSD", "ETHUSD", "SOLUSD"]
WARM = 24 * 100          # 100d context so EMA200/ATR-rank are formed
TRAIN = 24 * 180
TEST = 24 * 60
GRID = {
    "adx_thr": [20, 25],
    "donch_n": [24, 48],
    "k_sl": [2.0, 3.0],
    "k_tr": [3.0, 4.0],
    "k_tp": [6.0, 10.0],
    "entry_mode": ["breakout", "pullback"],
}
COMBOS = [dict(zip(GRID, v)) for v in itertools.product(*GRID.values())]


def run_slice(prep, lo, hi, p):
    """Backtest [lo-WARM, hi) but score only [lo, hi)."""
    a = max(0, lo - WARM)
    sl_df = prep[p["donch_n"]].iloc[a:hi]
    trades, eq = backtest(sl_df, adx_thr=p["adx_thr"], k_sl=p["k_sl"],
                          k_tr=p["k_tr"], k_tp=p["k_tp"],
                          entry_mode=p["entry_mode"])
    off = lo - a
    scored = [t for t in trades if t["i_entry"] >= off]
    return scored, eq.iloc[off:], metrics(scored, eq.iloc[off:])


def main():
    data, prep = {}, {}
    for s in SYMBOLS:
        df = pd.read_parquet(f"{OUT}/{s}_1h.parquet").set_index("ts")
        data[s] = df
        prep[s] = {n: add_indicators(df, donch_n=n) for n in GRID["donch_n"]}
        print(s, len(df), "bars")

    picks = []          # winning combo per (symbol, window)
    stitched = {s: [] for s in SYMBOLS}   # IS validation segments

    for s in SYMBOLS:
        n = len(data[s])
        is_end = int(n * 0.70)
        lo = WARM
        while lo + TRAIN + TEST <= is_end:
            tr_lo, tr_hi = lo, lo + TRAIN
            te_lo, te_hi = tr_hi, tr_hi + TEST
            best, best_key = None, None
            for p in COMBOS:
                _, _, m = run_slice(prep[s], tr_lo, tr_hi, p)
                if m["trades"] < 5 or m["pf"] <= 1.0:
                    continue
                key = (m["sharpe"], m["pf"])
                if best is None or key > best_key:
                    best, best_key = p, key
            if best is not None:
                picks.append((s, json.dumps(best, sort_keys=True)))
                _, eq_te, m_te = run_slice(prep[s], te_lo, te_hi, best)
                stitched[s].append((eq_te, m_te, best))
            lo += TEST
        print(f"{s}: {len(stitched[s])} WFO windows")

    # Modal (most-picked) combo across every window -> final locked params
    counts = Counter(k for _, k in picks)
    if not counts:
        print("NO COMBO EVER QUALIFIED — edge does not exist in IS.")
        return
    final = json.loads(counts.most_common(1)[0][0])
    print("\nWFO pick distribution (top 5):")
    for k, v in counts.most_common(5):
        print(f"  {v:3d}x {k}")
    print("\nFINAL locked params:", final)

    # Stitched IS-validation (out-of-window test segments, honest IS estimate)
    print("\n== stitched IS test segments (per symbol) ==")
    is_summary = {}
    for s in SYMBOLS:
        if not stitched[s]:
            continue
        rets = pd.concat([eq.pct_change().dropna() for eq, _, _ in stitched[s]])
        sharpe = rets.mean() / rets.std() * np.sqrt(24 * 365) if rets.std() > 0 else 0
        total = float(np.prod([1 + r for r in rets]) - 1)
        is_summary[s] = dict(sharpe=round(float(sharpe), 2),
                             total_ret=round(total * 100, 2))
        print(f"  {s}: stitched Sharpe {sharpe:.2f}, total {total*100:+.1f}%")

    # ── the locked 30% OOS, single pass ──
    print("\n== OOS single pass ==")
    oos = {}
    port_rets = []
    for s in SYMBOLS:
        n = len(data[s])
        is_end = int(n * 0.70)
        trades, eq, m = run_slice(prep[s], is_end, n, final)
        oos[s] = dict(metrics=m,
                      period=[str(data[s].index[is_end]), str(data[s].index[-1])],
                      trades=trades)
        port_rets.append(eq.pct_change().dropna())
        bh = (data[s]["close"].iloc[-1] / data[s]["close"].iloc[is_end] - 1) * 100
        print(f"  {s}: {m} | buy&hold {bh:+.1f}%")

    # Equal-weight portfolio of the three sleeves
    aligned = pd.concat(port_rets, axis=1).fillna(0)
    pr = aligned.mean(axis=1)
    p_sharpe = pr.mean() / pr.std() * np.sqrt(24 * 365) if pr.std() > 0 else 0
    p_eq = (1 + pr).cumprod()
    p_dd = ((p_eq - p_eq.cummax()) / p_eq.cummax()).min() * 100
    yrs = len(pr) / (24 * 365)
    p_cagr = (float(p_eq.iloc[-1]) ** (1 / yrs) - 1) * 100 if yrs > 0 else 0
    print(f"\n  PORTFOLIO OOS: sharpe {p_sharpe:.2f}, cagr {p_cagr:+.1f}%, "
          f"maxdd {p_dd:.1f}%, calmar {p_cagr/abs(p_dd) if p_dd else 0:.2f}")

    json.dump({
        "final_params": final,
        "pick_distribution": counts.most_common(10),
        "is_stitched": is_summary,
        "oos": {s: {"metrics": o["metrics"], "period": o["period"],
                    "reasons": Counter(t["reason"] for t in o["trades"]),
                    "regimes": Counter(t["regime"] for t in o["trades"])}
                for s, o in oos.items()},
        "portfolio_oos": dict(sharpe=round(float(p_sharpe), 2),
                              cagr=round(p_cagr, 2), maxdd=round(float(p_dd), 2)),
    }, open(f"{OUT}/wfo_results.json", "w"), indent=1, default=str)
    print("\nsaved wfo_results.json")


if __name__ == "__main__":
    main()

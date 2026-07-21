"""Cross-sectional selection — quick pass (survivorship-BIASED upper bound).

Rank a ~22-coin daily universe each week by a factor, hold top-K long-only
(spot, equal weight, fully invested), mark-to-market with turnover costs.

Isolates SELECTION from timing: top-K and every benchmark are 100% invested,
so differences are which-coins, not how-much-in-market.

Benchmarks: equal-weight-all, BTC-hold.
Null (decider): random-K Monte Carlo at matched K/turnover — does factor
  ranking beat random selection?
Diagnostic: long-short spread (top-K minus bottom-K), the pure cross-sectional
  signal even though spot-only can't trade the short leg.
70/30 IS/OOS. Costs 0.0007 per side on traded fraction.

NOTE: universe = coins with clean Alpaca history = SURVIVORS. Results are an
upper bound; a positive finding needs a survivorship-free re-run.
"""
import glob
import json
import os

import numpy as np
import pandas as pd

DIR = os.path.join(os.path.dirname(__file__), "daily_bars")
OUT = os.path.dirname(__file__)
COST_SIDE = 0.0007
REBAL = 7
SEED = 20260721


def load_panel():
    closes = {}
    for f in sorted(glob.glob(os.path.join(DIR, "*.csv"))):
        sym = os.path.basename(f)[:-4]
        df = pd.read_csv(f).drop_duplicates("ts").sort_values("ts")
        df["date"] = pd.to_datetime(df["ts"], unit="s", utc=True)
        closes[sym] = df.set_index("date")["close"]
    return pd.DataFrame(closes).sort_index()


def factor_matrix(panel, kind, L):
    ret = panel.pct_change()
    if kind == "xmom":
        return (panel.shift(7) / panel.shift(L + 7) - 1).values
    if kind == "lowvol":
        return (-ret.rolling(L).std()).values
    if kind == "reversal":
        return (-(panel / panel.shift(7) - 1)).values
    if kind == "relstr":
        r = panel / panel.shift(L) - 1
        return r.sub(r.mean(axis=1), axis=0).values
    raise ValueError(kind)


# ── numpy core ──
def run_weights(ret_v, valid, rebal_mask, target_fn):
    """ret_v: (D,C) daily returns (0 where invalid). valid: (D,C) bool.
    target_fn(i)->(C,) target weight array or None. Returns daily net returns."""
    D, C = ret_v.shape
    cur = np.zeros(C)
    out = np.empty(D)
    for i in range(D):
        out[i] = float(cur @ ret_v[i])
        cur = cur * (1 + ret_v[i])
        s = cur.sum()
        if s > 0:
            cur = cur / s
        if rebal_mask[i]:
            tgt = target_fn(i)
            if tgt is not None:
                out[i] -= np.abs(tgt - cur).sum() * COST_SIDE
                cur = tgt
    return out


def topk_target(fac, valid, i, K, bottom=False):
    row = fac[i].copy()
    ok = valid[i] & ~np.isnan(row)
    if ok.sum() < 2 * K:
        return None
    row[~ok] = -np.inf if not bottom else np.inf
    order = np.argsort(row)
    pick = order[:K] if bottom else order[::-1][:K]
    w = np.zeros(len(row))
    w[pick] = 1.0 / K
    return w


def metrics(ret):
    ret = pd.Series(ret).dropna()
    if len(ret) < 60:
        return dict(sharpe=0, cagr=0, maxdd=0, calmar=0, total=0)
    eq = (1 + ret).cumprod()
    sh = ret.mean() / ret.std() * np.sqrt(365) if ret.std() > 0 else 0
    yrs = len(ret) / 365.25
    cg = eq.iloc[-1] ** (1 / yrs) - 1 if eq.iloc[-1] > 0 else -1
    dd = ((eq - eq.cummax()) / eq.cummax()).min()
    return dict(sharpe=round(float(sh), 3), cagr=round(float(cg) * 100, 1),
                maxdd=round(float(dd) * 100, 1),
                calmar=round(float(cg / abs(dd)) if dd else 0, 2),
                total=round(float(eq.iloc[-1] - 1) * 100, 1))


def main():
    panel = load_panel()
    idx = panel.index
    ret_df = panel.pct_change()
    valid = ~panel.isna().values
    ret_v = np.nan_to_num(ret_df.values)
    D, C = ret_v.shape
    rebal_mask = np.zeros(D, bool)
    rebal_mask[::REBAL] = True
    split_i = int(D * 0.70)
    print("universe:", list(panel.columns), "| days:", D, "| split:", str(idx[split_i])[:10])

    def seg(r):
        r = np.asarray(r)
        return {"IS": metrics(r[:split_i]), "OOS": metrics(r[split_i:]),
                "ALL": metrics(r)}

    results = {"universe": list(panel.columns), "days": int(D),
               "split": str(idx[split_i])[:10], "factors": {}}

    # benchmarks
    def ew_target(i):
        ok = valid[i]
        w = np.zeros(C)
        if ok.sum():
            w[ok] = 1.0 / ok.sum()
        return w
    ew = run_weights(ret_v, valid, rebal_mask, ew_target)
    results["benchmarks"] = {"equal_weight_all": seg(ew),
                             "btc_hold": seg(ret_v[:, list(panel.columns).index("BTCUSD")])}

    FACTORS = [("xmom", 30), ("xmom", 60), ("xmom", 90),
               ("lowvol", 30), ("reversal", 7), ("relstr", 60)]
    KS = [3, 5]
    rng = np.random.default_rng(SEED)
    rebal_idx = np.where(rebal_mask)[0]

    for kind, L in FACTORS:
        fac = factor_matrix(panel, kind, L)
        for K in KS:
            key = f"{kind}_{L}_top{K}"
            top = run_weights(ret_v, valid, rebal_mask,
                              lambda i: topk_target(fac, valid, i, K, False))
            bot = run_weights(ret_v, valid, rebal_mask,
                              lambda i: topk_target(fac, valid, i, K, True))
            top_sh = metrics(top)["sharpe"]
            # random-K null: precompute random picks per rebalance per draw
            null_sh = []
            for _ in range(200):
                picks = {}
                for i in rebal_idx:
                    ok = np.where(valid[i])[0]
                    picks[i] = rng.choice(ok, size=min(K, len(ok)), replace=False) \
                        if len(ok) >= K else None

                def rt(i, picks=picks):
                    p = picks.get(i)
                    if p is None:
                        return None
                    w = np.zeros(C); w[p] = 1.0 / K
                    return w
                null_sh.append(metrics(run_weights(ret_v, valid, rebal_mask, rt))["sharpe"])
            null_sh = np.array(null_sh)
            results["factors"][key] = {
                "top": seg(top), "long_short_spread": seg(np.asarray(top) - np.asarray(bot)),
                "null_random_sharpe_mean": round(float(null_sh.mean()), 3),
                "null_random_sharpe_p95": round(float(np.percentile(null_sh, 95)), 3),
                "top_percentile_vs_null": round(float((null_sh < top_sh).mean() * 100), 1),
            }
        print(kind, L, "done", flush=True)

    json.dump(results, open(os.path.join(OUT, "cross_RESULTS.json"), "w"),
              indent=1, default=str)
    print("saved cross_RESULTS.json")


if __name__ == "__main__":
    main()

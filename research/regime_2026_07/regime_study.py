"""Regime-participation follow-up — the deployable form of the daily study.

Stage 1: walk-forward the SMA period {50,100,150,200} (train 2y / test 6mo)
         -> is any single period special, or is the rule period-robust?
Stage 2: hysteresis band sweep {0,1,2,3,5}% -> cut flips without killing return.
Stage 3: equal-weight multi-symbol portfolio, each sleeve on/off by its own
         regime -> honest Sharpe / CAGR / maxDD / Calmar vs buy&hold.
Stage 4: nulls -> vs buy&hold AND vs exposure-matched random-timing Monte Carlo
         (does the TIMING add value, or is it just lower exposure?).

Regime rule (look-ahead-free): state decided at close[i] vs SMA(period) with a
+/- band; position held during day i+1; close-to-close returns; per-flip cost
0.14% round trip. Mark-to-market throughout.
"""
import glob
import json
import os

import numpy as np
import pandas as pd

DIR = os.path.join(os.path.dirname(__file__), "daily_bars")
OUT = os.path.dirname(__file__)
COST_SIDE = 0.0007
SEED = 12345


def load():
    data = {}
    for f in sorted(glob.glob(os.path.join(DIR, "*.csv"))):
        sym = os.path.basename(f)[:-4]
        df = pd.read_csv(f).drop_duplicates("ts").sort_values("ts")
        df["date"] = pd.to_datetime(df["ts"], unit="s", utc=True)
        data[sym] = df.set_index("date")
    return data


def regime_returns(df, period, band_pct=0.0):
    """Daily strategy returns (close-to-close), per-flip costs. Returns
    (ret series aligned to df.index[1:], flips, exposure_fraction)."""
    c = df["close"].values
    sma = pd.Series(c).rolling(period).mean().values
    n = len(c)
    signal = np.zeros(n)          # desired state decided at close[i]
    state = 0
    up = 1 + band_pct / 100.0
    dn = 1 - band_pct / 100.0
    for i in range(n):
        if np.isnan(sma[i]):
            signal[i] = 0
            continue
        if state == 0 and c[i] > sma[i] * up:
            state = 1
        elif state == 1 and c[i] < sma[i] * dn:
            state = 0
        signal[i] = state
    pos = np.zeros(n)
    pos[1:] = signal[:-1]          # act next day
    asset_ret = np.zeros(n)
    asset_ret[1:] = c[1:] / c[:-1] - 1
    ret = pos * asset_ret
    flips = int(np.sum(np.abs(np.diff(pos)) > 0))
    cost = np.zeros(n)
    cost[1:] = (np.abs(np.diff(pos)) > 0) * COST_SIDE
    ret = ret - cost
    exposure = float(np.mean(pos[period:])) if n > period else 0.0
    return pd.Series(ret[1:], index=df.index[1:]), flips, exposure


def metrics(ret):
    ret = ret.dropna()
    if len(ret) < 30:
        return dict(sharpe=0, cagr=0, maxdd=0, calmar=0)
    eq = (1 + ret).cumprod()
    sharpe = ret.mean() / ret.std() * np.sqrt(365) if ret.std() > 0 else 0
    yrs = len(ret) / 365.25
    cagr = (eq.iloc[-1] ** (1 / yrs) - 1) if yrs > 0 and eq.iloc[-1] > 0 else -1
    dd = ((eq - eq.cummax()) / eq.cummax()).min()
    return dict(sharpe=round(float(sharpe), 3), cagr=round(float(cagr) * 100, 1),
                maxdd=round(float(dd) * 100, 1),
                calmar=round(float(cagr / abs(dd)) if dd else 0, 2),
                total=round(float(eq.iloc[-1] - 1) * 100, 1))


def buyhold(df):
    c = df["close"]
    ret = c.pct_change().dropna()
    return metrics(ret)


# ── Stage 1: walk-forward the SMA period ──
def stage1(data):
    from collections import Counter
    PERIODS = [50, 100, 150, 200]
    TRAIN, TEST = 504, 126
    out = {}
    picks = Counter()
    for sym, df in data.items():
        c = df["close"].values
        if len(c) < TRAIN + TEST + 200:
            continue
        oos = []
        lo = 200
        while lo + TRAIN + TEST <= len(c):
            tr = df.iloc[lo:lo + TRAIN]
            best_p, best_s = None, -1e9
            for p in PERIODS:
                r, _, _ = regime_returns(tr, p)
                s = metrics(r)["sharpe"]
                if s > best_s:
                    best_s, best_p = s, p
            picks[best_p] += 1
            te = df.iloc[lo + TRAIN - 200:lo + TRAIN + TEST]  # warmup for SMA
            r, _, _ = regime_returns(te, best_p)
            oos.append(r.iloc[200:])
            lo += TEST
        if oos:
            stitched = pd.concat(oos)
            # fixed-period baselines on the same span
            span = df.iloc[200:]
            fix50, _, _ = regime_returns(span, 50)
            fix100, _, _ = regime_returns(span, 100)
            out[sym] = {"wfo_oos": metrics(stitched),
                        "fixed_50": metrics(fix50),
                        "fixed_100": metrics(fix100),
                        "buyhold": buyhold(df)}
    return {"per_symbol": out, "pick_distribution": dict(picks)}


# ── Stage 2: hysteresis band sweep ──
def stage2(data, period=100):
    BANDS = [0, 1, 2, 3, 5]
    rows = []
    for band in BANDS:
        sh, cg, dd, fl, ex = [], [], [], [], []
        for sym, df in data.items():
            if len(df) < period + 200:
                continue
            r, flips, expo = regime_returns(df, period, band)
            m = metrics(r)
            sh.append(m["sharpe"]); cg.append(m["cagr"]); dd.append(m["maxdd"])
            fl.append(flips); ex.append(expo)
        rows.append({"band_pct": band, "mean_sharpe": round(np.mean(sh), 3),
                     "mean_cagr": round(np.mean(cg), 1), "mean_maxdd": round(np.mean(dd), 1),
                     "mean_flips": round(np.mean(fl), 1),
                     "mean_exposure": round(np.mean(ex) * 100, 1)})
    return rows


def _position_series(df, period, band):
    """The look-ahead-free daily position (held during day i), as a Series."""
    c = df["close"].values
    sma = pd.Series(c).rolling(period).mean().values
    st = 0
    sig = np.zeros(len(c))
    up, dn = 1 + band / 100, 1 - band / 100
    for i in range(len(c)):
        if np.isnan(sma[i]):
            continue
        if st == 0 and c[i] > sma[i] * up:
            st = 1
        elif st == 1 and c[i] < sma[i] * dn:
            st = 0
        sig[i] = st
    pos = np.zeros(len(c))
    pos[1:] = sig[:-1]        # act next day
    return pd.Series(pos, index=df.index)


def _port_from_positions(data, positions, idx):
    """Equal-weight portfolio daily returns from a {sym: position Series}
    dict, charging COST_SIDE per flip. Equal weight among active sleeves."""
    contribs = pd.DataFrame(index=idx)
    active = pd.DataFrame(index=idx)
    for sym, df in data.items():
        c = df["close"].values
        aret = np.zeros(len(c)); aret[1:] = c[1:] / c[:-1] - 1
        pos = positions[sym].reindex(df.index).fillna(0).values
        cost = np.zeros(len(c)); cost[1:] = (np.abs(np.diff(pos)) > 0) * COST_SIDE
        r = pd.Series(pos * aret - cost, index=df.index)
        contribs[sym] = r.reindex(idx)
        active[sym] = df["close"].pct_change().reindex(idx).notna()
    w = active.div(active.sum(axis=1).replace(0, np.nan), axis=0)
    ret = (contribs.fillna(0) * w.fillna(0)).sum(axis=1)
    return ret[active.sum(axis=1) > 0]


# ── Stage 3: portfolio + Stage 4: circular-shift null (cost-fair) ──
def stage3_4(data, period=100, band=2):
    idx = None
    for df in data.values():
        idx = df.index if idx is None else idx.union(df.index)

    positions = {sym: _position_series(df, period, band) for sym, df in data.items()}
    port_ret = _port_from_positions(data, positions, idx)
    port_m = metrics(port_ret)

    # buy&hold portfolio (always in where data exists)
    bh_pos = {sym: pd.Series(1.0, index=df.index) for sym, df in data.items()}
    bh_ret = _port_from_positions(data, bh_pos, idx)
    bh_m = metrics(bh_ret)

    deploy = float(np.mean([positions[s].reindex(df.index)[period:].mean()
                            for s, df in data.items()])) * 100

    # NULL: circularly shift each sleeve's OWN position series by a random
    # offset. Same flip count, same exposure, same block structure, same
    # costs — only the alignment with price is destroyed. If regime beats
    # this, the timing carries genuine predictive value.
    rng = np.random.default_rng(SEED)
    null_sharpes = []
    for _ in range(500):
        shifted = {}
        for sym, df in data.items():
            p = positions[sym].values
            k = int(rng.integers(period + 1, len(p) - 1))
            shifted[sym] = pd.Series(np.roll(p, k), index=df.index)
        null_sharpes.append(metrics(_port_from_positions(data, shifted, idx))["sharpe"])
    null_sharpes = np.array(null_sharpes)
    pct = float((null_sharpes < port_m["sharpe"]).mean() * 100)
    return {"period": period, "band": band,
            "portfolio_regime": port_m, "portfolio_buyhold": bh_m,
            "avg_deployment_pct": round(deploy, 1),
            "null_shift_sharpe_mean": round(float(null_sharpes.mean()), 3),
            "null_shift_sharpe_p50": round(float(np.percentile(null_sharpes, 50)), 3),
            "null_shift_sharpe_p95": round(float(np.percentile(null_sharpes, 95)), 3),
            "regime_percentile_vs_null": round(pct, 1)}


def main():
    data = load()
    print("loaded:", {s: len(d) for s, d in data.items()})
    # De-risking benchmark: constant fractional exposure (hold f of the
    # basket, rest cash, never time). Sharpe is f-invariant; maxDD scales by
    # f. The fair bar for any timing overlay that only reduces exposure.
    idx = None
    for df in data.values():
        idx = df.index if idx is None else idx.union(df.index)
    bh_pos = {s: pd.Series(1.0, index=df.index) for s, df in data.items()}
    bh_ret = _port_from_positions(data, bh_pos, idx)
    const_bench = {f"constant_{int(f*100)}pct_exposure": metrics(bh_ret * f)
                   for f in (1.0, 0.43)}
    result = {
        "symbols": {s: len(d) for s, d in data.items()},
        "stage1_sma_wfo": stage1(data),
        "stage2_band_sweep": stage2(data),
        "stage3_4_sma100_band2": stage3_4(data, period=100, band=2),
        "stage3_4_sma50_band2": stage3_4(data, period=50, band=2),
        "constant_exposure_benchmark": const_bench,
    }
    json.dump(result, open(os.path.join(OUT, "regime_RESULTS.json"), "w"),
              indent=1, default=str)
    print("saved regime_RESULTS.json")


if __name__ == "__main__":
    main()

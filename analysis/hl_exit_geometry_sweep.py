#!/usr/bin/env python3
"""
STUDY 20 — exit-geometry sweep on the hl_system B+C signals.

HYPOTHESIS
----------
Study #19 found the entry signal has positive central tendency (mean +0.174%,
median +0.698% net per trade, WR 60.8%) but loses because the exit geometry is
mis-scaled: a stop at 2x DAILY ATR against a target of an INTRADAY VWAP mean
gave R:R of 1:5.6, needing an 84.9% win rate against an actual 60.8%.

This tests whether matching the exit timescale to the signal timescale fixes
it. The stop moves to ATR computed on 1h bars, targets become R-multiples of
the actual stop distance, and time stops are tested.

PROTOCOL (study #11's lesson, which this study is at risk of repeating)
-----------------------------------------------------------------------
Study #11 ran a 77-config SL/TP grid, found the in-sample winner did PF 0.63
out-of-sample, and concluded that an IS/OOS split alone does NOT validate a
parameter sweep: with N configs the IS winner is mostly selection noise. The
decisive tests are

  (a) run EVERY config out-of-sample and ask where the IS pick RANKS, and
  (b) the IS->OOS rank correlation across the whole config space.

Both are computed here. A pick that does not land above the ~90th percentile
OOS, on a positive IS->OOS correlation, is selection noise and is reported as
such regardless of how good its headline looks.

Windows are NON-OVERLAPPING (study #19 used a 3-day slide on 30-day windows,
which is 90% overlap — six views of one sample, not six samples).

SIZING IS COUPLED TO THE STOP
-----------------------------
size = (equity * risk_factor) / stop_distance, where stop_distance is whatever
the exit config actually uses. This is the correct coupling — dollar risk per
trade is then constant by construction — and it also dissolves study #19's
second defect (6% average exposure) without a separate knob: a tighter stop
buys a larger position for the same risk, bounded by the 3%/15% exposure caps.
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import pickle
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from itertools import product
from pathlib import Path

import numpy as np
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
except Exception:
    pass

from hl_system.config import RiskConfig, SignalConfig  # noqa: E402
from hl_system.data_engine import Candle  # noqa: E402
from hl_system.risk_manager import atr  # noqa: E402
from hl_system.signals import (Direction, FundingZScoreSignal,  # noqa: E402
                               VwapReversionSignal)

API = "https://api.hyperliquid.xyz/info"
HERE = Path(__file__).parent
CACHE = Path(os.environ.get(
    "HL_SWEEP_CACHE",
    r"C:\Users\DELL\AppData\Local\Temp\claude\C--Users-DELL-OneDrive-1m"
    r"\461d6d4d-db6f-4366-adba-df2562ad4a11\scratchpad\hl_sweep_cache"))
CACHE.mkdir(parents=True, exist_ok=True)

HOUR_MS = 3_600_000
DAY_MS = 86_400_000
HISTORY_DAYS = 195           # 180 test + 15 warmup; HL 1h caps at ~208d
WARMUP_DAYS = 15
WINDOW_DAYS = 30
N_WINDOWS = 6                # non-overlapping
MAKER_FEE = 0.00015
START_EQUITY = 10_000.0
SESSION = requests.Session()


# ------------------------------------------------------------- fetch -------


def _post(payload: dict, retries: int = 4):
    for i in range(retries):
        try:
            r = SESSION.post(API, json=payload, timeout=40)
            if r.status_code == 429:
                time.sleep(2 * (i + 1)); continue
            r.raise_for_status()
            return r.json()
        except Exception:
            if i == retries - 1:
                raise
            time.sleep(1.5 * (i + 1))
    return None


def fetch_symbol(coin: str, start_ms: int, end_ms: int) -> dict | None:
    path = CACHE / f"{coin}_{HISTORY_DAYS}d.pkl.gz"
    if path.exists():
        try:
            with gzip.open(path, "rb") as f:
                return pickle.load(f)
        except Exception:
            path.unlink(missing_ok=True)

    out: dict = {"coin": coin, "candles": {}, "funding": []}
    for iv in ("1h", "4h", "1d"):
        raw = _post({"type": "candleSnapshot",
                     "req": {"coin": coin, "interval": iv,
                             "startTime": start_ms, "endTime": end_ms}})
        if not raw:
            return None
        out["candles"][iv] = [
            Candle(int(c["t"]), float(c["o"]), float(c["h"]), float(c["l"]),
                   float(c["c"]), float(c["v"]), int(c.get("n", 0))) for c in raw]

    cursor, rows, seen = start_ms, [], set()
    while cursor < end_ms:
        batch = _post({"type": "fundingHistory", "coin": coin, "startTime": cursor})
        if not batch:
            break
        fresh = [(int(r["time"]), float(r["fundingRate"]))
                 for r in batch if int(r["time"]) not in seen]
        if not fresh:
            break
        seen.update(t for t, _ in fresh)
        rows.extend(fresh)
        nxt = max(t for t, _ in fresh) + 1
        if nxt <= cursor:
            break
        cursor = nxt
        if len(batch) < 500:
            break
    out["funding"] = sorted(rows)

    if len(out["candles"]["1h"]) < 3000:      # need the full history for 6 windows
        return None
    with gzip.open(path, "wb", compresslevel=4) as f:
        pickle.dump(out, f)
    return out


def load_universe(workers: int = 10) -> dict[str, dict]:
    meta = _post({"type": "meta"})
    coins = [a["name"] for a in meta["universe"] if not a.get("isDelisted")]
    sz_dec = {a["name"]: int(a["szDecimals"]) for a in meta["universe"]}
    end = int(time.time() * 1000)
    start = end - HISTORY_DAYS * DAY_MS
    print(f"fetching {len(coins)} assets x {HISTORY_DAYS}d (1h/4h/1d + funding)...")
    data: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(fetch_symbol, c, start, end): c for c in coins}
        for i, fut in enumerate(as_completed(futs), 1):
            try:
                d = fut.result()
                if d:
                    d["sz_decimals"] = sz_dec.get(futs[fut], 2)
                    data[futs[fut]] = d
            except Exception as e:
                print(f"  {futs[fut]}: {type(e).__name__}")
            if i % 40 == 0:
                print(f"  [{i}/{len(coins)}] {len(data)} ok")
    print(f"{len(data)} assets with full {HISTORY_DAYS}d history")
    return data


# --------------------------------------------------------- exit config -----


@dataclass(frozen=True)
class ExitCfg:
    stop_src: str          # 'atr1h' | 'atr1d' | 'none'
    stop_mult: float
    target: str            # 'vwap' | 'R'
    r_mult: float
    time_stop_h: int | None

    @property
    def name(self) -> str:
        s = "nostop" if self.stop_src == "none" else f"{self.stop_src}x{self.stop_mult:g}"
        t = "vwap" if self.target == "vwap" else f"{self.r_mult:g}R"
        return f"{s}/{t}/{self.time_stop_h or 'noT'}"


def build_grid() -> list[ExitCfg]:
    grid = [ExitCfg("atr1d", 2.0, "vwap", 0, None)]          # study #19 baseline
    for m, tgt, ts in product([1.0, 1.5, 2.0, 3.0],
                              [("R", 1.0), ("R", 1.5), ("R", 2.0), ("R", 3.0), ("vwap", 0)],
                              [24, 48, None]):
        grid.append(ExitCfg("atr1h", m, tgt[0], tgt[1], ts))
    for ts in [24, 48, None]:
        grid.append(ExitCfg("none", 0, "vwap", 0, ts))
    return grid


# ------------------------------------------------------ signal precompute --


class _RD:
    def __init__(self):
        self.candles, self.funding = {}, {}
        self.trades, self.books, self.mids, self.assets = {}, {}, {}, {}


def _slice(pair, now, n):
    ts, arr = pair
    j = int(np.searchsorted(ts, now, "left"))
    return arr[max(0, j - n):j]


def precompute_signals(data: dict, idx: dict, start: int, end: int,
                       scfg: SignalConfig, rcfg: RiskConfig) -> dict:
    """
    Evaluate B and C once per (symbol, hour). Config-independent, so the whole
    exit sweep reuses it — this is what makes 64 x 6 runs tractable.
    """
    mod_b, mod_c = FundingZScoreSignal(scfg), VwapReversionSignal(scfg)
    rd = _RD()
    out: dict[int, list] = {}
    grid = list(range(start - (start % HOUR_MS), end, HOUR_MS))
    for gi, now in enumerate(grid):
        hits = []
        for coin in data:
            rd.candles[(coin, scfg.c_interval)] = _slice(idx[coin]["1h"], now, 260)
            rd.candles[(coin, scfg.c_confirm_interval)] = _slice(idx[coin]["4h"], now, 40)
            fts, farr = idx[coin]["f"]
            j = int(np.searchsorted(fts, now, "left"))
            rd.funding[coin] = farr[max(0, j - 120):j]

            longs, exits = [], []
            for m in (mod_b, mod_c):
                s = m.evaluate(coin, rd)
                if not s:
                    continue
                if s.direction is Direction.LONG:
                    longs.append(s)
                elif s.direction is Direction.EXIT:
                    exits.append(s)
            if longs or exits:
                hits.append((coin,
                             min(1.0, sum(s.strength for s in longs) / 3.0) if longs else 0.0,
                             next((s.meta.get("target") for s in longs
                                   if s.meta.get("target")), None),
                             "+".join(sorted({s.module[0] for s in longs})),
                             bool(exits)))
        if hits:
            out[now] = hits
        if gi % 200 == 0:
            print(f"    signals {gi}/{len(grid)}", end="\r")
    return out


# ------------------------------------------------------------ simulate -----


@dataclass
class Res:
    ret: float = 0.0
    mdd: float = 0.0
    trades: int = 0
    wr: float = 0.0
    pf: float = 0.0
    avg_exp: float = 0.0
    skill: float = 0.0
    halted: bool = False
    net_ex_top2: float = 0.0
    avg_net_pct: float = 0.0


def simulate(data, idx, sig: dict, ec: ExitCfg, start: int, end: int,
             rcfg: RiskConfig, bh_pct: float) -> Res:
    cash = START_EQUITY
    peak = START_EQUITY
    mdd = 0.0
    open_pos: dict[str, dict] = {}
    exposure: list[float] = []
    closed: list[dict] = []

    for now in range(start - (start % HOUR_MS), end, HOUR_MS):
        equity = cash
        marks = {}
        for c, p in open_pos.items():
            ts, arr = idx[c]["1h"]
            j = int(np.searchsorted(ts, now, "right")) - 1
            if j < 0:
                continue
            marks[c] = arr[j].close
            equity += p["size"] * marks[c]
        peak = max(peak, equity)
        mdd = max(mdd, (peak - equity) / peak * 100)
        invested = sum(p["size"] * marks.get(c, p["entry"]) for c, p in open_pos.items())
        exposure.append(invested / equity if equity > 0 else 0.0)

        if mdd >= rcfg.dd_hard_lock_pct:                   # hard lock
            for c, p in list(open_pos.items()):
                px = marks.get(c, p["entry"])
                cash += p["size"] * px * (1 - MAKER_FEE)
                closed.append({"sym": c, "net": (px - p["entry"]) / p["entry"] - 2 * MAKER_FEE,
                               "pnl": (px - p["entry"]) * p["size"] - 2 * MAKER_FEE * p["entry"] * p["size"]})
            r = Res(halted=True)
            r.ret = (cash - START_EQUITY) / START_EQUITY * 100
            r.mdd = mdd
            r.trades = len(closed)
            r.avg_exp = float(np.mean(exposure)) if exposure else 0.0
            r.skill = r.ret - r.avg_exp * bh_pct
            return r

        hits = sig.get(now, [])
        exit_syms = {h[0] for h in hits if h[4]}

        # --- manage open positions ---
        for c, p in list(open_pos.items()):
            ts, arr = idx[c]["1h"]
            j = int(np.searchsorted(ts, now, "right")) - 1
            if j < 0:
                continue
            bar = arr[j]
            px = why = None
            if p["stop"] and bar.low <= p["stop"]:
                px, why = p["stop"], "stop"
            elif p["target"] and bar.high >= p["target"]:
                px, why = p["target"], "target"
            elif ec.time_stop_h and now - p["ts"] >= ec.time_stop_h * HOUR_MS:
                px, why = bar.close, "time"
            elif c in exit_syms:
                px, why = bar.close, "signal"
            if px:
                cash += p["size"] * px * (1 - MAKER_FEE)
                net = (px - p["entry"]) / p["entry"] - 2 * MAKER_FEE
                closed.append({"sym": c, "net": net, "pnl": net * p["entry"] * p["size"]})
                del open_pos[c]

        # --- entries ---
        if len(open_pos) >= rcfg.max_concurrent_positions:
            continue
        cands = sorted([h for h in hits if h[1] > 0 and h[0] not in open_pos],
                       key=lambda h: -h[1])
        for coin, conv, vtarget, mod, _ in cands:
            if len(open_pos) >= rcfg.max_concurrent_positions:
                break
            ts, arr = idx[coin]["1h"]
            j = int(np.searchsorted(ts, now, "right")) - 1
            if j < 1:
                continue
            entry = arr[j].close
            if entry <= 0:
                continue

            # --- stop distance defines BOTH the stop and the size ---
            if ec.stop_src == "atr1h":
                a = atr(_slice(idx[coin]["1h"], now, 60), 14)
                dist = a * ec.stop_mult if a else None
            elif ec.stop_src == "atr1d":
                a = atr(_slice(idx[coin]["1d"], now, 40), rcfg.atr_period)
                dist = a * ec.stop_mult if a else None
            else:
                a = atr(_slice(idx[coin]["1h"], now, 60), 14)
                dist = a * 2.0 if a else None              # sizing ref only, no stop
            if not dist or dist <= 0:
                continue

            size = (equity * rcfg.risk_factor) / dist
            cap = (rcfg.max_exposure_major if coin in rcfg.majors
                   else rcfg.max_exposure_alt)
            size = min(size, equity * cap / entry) * conv
            notional = size * entry
            if notional < 12 or notional > cash:
                continue

            stop = None if ec.stop_src == "none" else entry - dist
            if ec.target == "R":
                target = entry + ec.r_mult * dist
            else:
                target = vtarget if (vtarget and vtarget > entry) else None

            cash -= notional * (1 + MAKER_FEE)
            open_pos[coin] = {"entry": entry, "size": size, "ts": now,
                              "stop": stop, "target": target}

    # close survivors
    for c, p in list(open_pos.items()):
        ts, arr = idx[c]["1h"]
        j = int(np.searchsorted(ts, end, "right")) - 1
        px = arr[j].close if j >= 0 else p["entry"]
        cash += p["size"] * px * (1 - MAKER_FEE)
        net = (px - p["entry"]) / p["entry"] - 2 * MAKER_FEE
        closed.append({"sym": c, "net": net, "pnl": net * p["entry"] * p["size"]})

    r = Res()
    r.ret = (cash - START_EQUITY) / START_EQUITY * 100
    r.mdd = mdd
    r.trades = len(closed)
    r.avg_exp = float(np.mean(exposure)) if exposure else 0.0
    r.skill = r.ret - r.avg_exp * bh_pct
    if closed:
        nets = np.array([c["net"] for c in closed])
        pnls = np.array([c["pnl"] for c in closed])
        r.wr = float((nets > 0).mean() * 100)
        gw, gl = pnls[pnls > 0].sum(), -pnls[pnls <= 0].sum()
        r.pf = float(gw / gl) if gl > 0 else float("inf")
        r.avg_net_pct = float(nets.mean() * 100)
        per: dict[str, float] = {}
        for c in closed:
            per[c["sym"]] = per.get(c["sym"], 0.0) + c["pnl"]
        ranked = sorted(per.values(), reverse=True)
        r.net_ex_top2 = float(sum(ranked[2:]))
    return r


def buy_and_hold(data, idx, start, end) -> float:
    rets = []
    for c in data:
        ts, arr = idx[c]["1h"]
        i = int(np.searchsorted(ts, start, "left"))
        j = int(np.searchsorted(ts, end, "right")) - 1
        if 0 <= i < j < len(arr) and arr[i].close > 0:
            rets.append((arr[j].close - arr[i].close) / arr[i].close)
    return float(np.mean(rets)) * 100 if rets else 0.0


# ---------------------------------------------------------------- main -----


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true", help="3 windows, small grid")
    args = ap.parse_args()

    data = load_universe()
    if len(data) < 40:
        print("insufficient universe")
        return 1

    idx = {}
    for c, d in data.items():
        idx[c] = {iv: (np.array([x.ts for x in d["candles"][iv]]), d["candles"][iv])
                  for iv in ("1h", "4h", "1d")}
        idx[c]["f"] = (np.array([t for t, _ in d["funding"]]), d["funding"])

    now = int(time.time() * 1000)
    n_win = 3 if args.quick else N_WINDOWS
    windows = []
    for k in range(n_win):                                   # window 0 == OLDEST
        end = now - (n_win - 1 - k) * WINDOW_DAYS * DAY_MS
        windows.append((end - WINDOW_DAYS * DAY_MS, end))

    scfg, rcfg = SignalConfig(), RiskConfig()
    grid = build_grid()
    if args.quick:
        grid = grid[:8]
    print(f"\n{len(grid)} exit configs x {n_win} non-overlapping {WINDOW_DAYS}d windows "
          f"= {len(grid)*n_win} runs")

    sigs, bhs = [], []
    for wi, (s, e) in enumerate(windows):
        print(f"\nprecomputing signals, window {wi} "
              f"{time.strftime('%Y-%m-%d', time.gmtime(s/1000))} -> "
              f"{time.strftime('%Y-%m-%d', time.gmtime(e/1000))}")
        sigs.append(precompute_signals(data, idx, s, e, scfg, rcfg))
        bhs.append(buy_and_hold(data, idx, s, e))
        print(f"    {len(sigs[-1])} signal-hours | B&H {bhs[-1]:+.2f}%")

    results: dict[str, list[Res]] = {}
    t0 = time.time()
    for ci, ec in enumerate(grid, 1):
        rs = [simulate(data, idx, sigs[w], ec, *windows[w], rcfg, bhs[w])
              for w in range(n_win)]
        results[ec.name] = rs
        el = time.time() - t0
        print(f"  [{ci}/{len(grid)}] {ec.name:<24} "
              f"skill {np.mean([r.skill for r in rs]):+6.2f}%  "
              f"ret {np.mean([r.ret for r in rs]):+6.2f}%  "
              f"exp {np.mean([r.avg_exp for r in rs])*100:4.1f}%  "
              f"| {el:.0f}s eta {el/ci*(len(grid)-ci):.0f}s")

    # ---- study #11 protocol -------------------------------------------
    n_is = n_win - 2
    rows = []
    for name, rs in results.items():
        is_skill = float(np.mean([r.skill for r in rs[:n_is]]))
        oos_skill = float(np.mean([r.skill for r in rs[n_is:]]))
        rows.append({
            "config": name,
            "is_skill": is_skill, "oos_skill": oos_skill,
            "all_skill": float(np.mean([r.skill for r in rs])),
            "all_ret": float(np.mean([r.ret for r in rs])),
            "median_skill": float(np.median([r.skill for r in rs])),
            "pos_windows": int(sum(1 for r in rs if r.skill > 0)),
            "n_windows": n_win,
            "pf": float(np.median([r.pf for r in rs if r.pf != float("inf")] or [0])),
            "mdd": float(np.max([r.mdd for r in rs])),
            "trades": int(np.mean([r.trades for r in rs])),
            "exposure": float(np.mean([r.avg_exp for r in rs])),
            "halts": int(sum(1 for r in rs if r.halted)),
            "net_ex_top2": float(np.mean([r.net_ex_top2 for r in rs])),
        })

    is_sorted = sorted(rows, key=lambda x: -x["is_skill"])
    oos_sorted = sorted(rows, key=lambda x: -x["oos_skill"])
    best_is = is_sorted[0]
    oos_rank = [r["config"] for r in oos_sorted].index(best_is["config"]) + 1
    pct = (1 - (oos_rank - 1) / len(rows)) * 100
    corr = float(np.corrcoef([r["is_skill"] for r in rows],
                             [r["oos_skill"] for r in rows])[0, 1])

    print(f"\n{'='*96}\nSTUDY #11 PROTOCOL\n{'='*96}")
    print(f"  IS = windows 0-{n_is-1} ({n_is}), OOS = windows {n_is}-{n_win-1} (2), non-overlapping")
    print(f"\n  best IS config: {best_is['config']}  IS skill {best_is['is_skill']:+.2f}%")
    print(f"    its OOS skill        : {best_is['oos_skill']:+.2f}%")
    print(f"    its OOS RANK         : {oos_rank}/{len(rows)}  ({pct:.0f}th percentile)")
    print(f"    -> needs >=90th pct to be signal rather than selection noise: "
          f"{'PASS' if pct >= 90 else 'FAIL'}")
    print(f"\n  IS->OOS skill correlation across all {len(rows)} configs: {corr:+.3f}")
    print(f"    -> {'predictive' if corr > 0.3 else 'NOT predictive (selection noise)'}")
    print(f"\n  configs with positive OOS skill: "
          f"{sum(1 for r in rows if r['oos_skill'] > 0)}/{len(rows)}")
    print(f"  configs positive in ALL {n_win} windows: "
          f"{sum(1 for r in rows if r['pos_windows'] == n_win)}/{len(rows)}")

    print(f"\n{'='*96}\nTOP 15 BY OOS SKILL\n{'='*96}")
    print(f"  {'config':<24}{'IS skill':>10}{'OOS skill':>11}{'all':>8}{'pos':>6}"
          f"{'PF':>7}{'mDD':>7}{'exp%':>7}{'trades':>8}{'exTop2':>9}")
    for r in oos_sorted[:15]:
        print(f"  {r['config']:<24}{r['is_skill']:>+10.2f}{r['oos_skill']:>+11.2f}"
              f"{r['all_skill']:>+8.2f}{r['pos_windows']:>4}/{r['n_windows']}"
              f"{r['pf']:>7.2f}{r['mdd']:>7.2f}{r['exposure']*100:>7.1f}"
              f"{r['trades']:>8}{r['net_ex_top2']:>+9.1f}")

    base = next(r for r in rows if r["config"].startswith("atr1dx2"))
    print(f"\n  study #19 baseline: {base['config']}  "
          f"IS {base['is_skill']:+.2f}%  OOS {base['oos_skill']:+.2f}%  "
          f"all {base['all_skill']:+.2f}%  exp {base['exposure']*100:.1f}%")

    out = HERE / "hl_exit_geometry_results.json"
    out.write_text(json.dumps({
        "meta": {"generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                 "universe": len(data), "windows": n_win,
                 "window_days": WINDOW_DAYS, "overlapping": False,
                 "maker_fee_per_side": MAKER_FEE, "configs": len(rows),
                 "is_windows": n_is, "oos_windows": n_win - n_is,
                 "best_is_config": best_is["config"],
                 "best_is_oos_rank": oos_rank,
                 "best_is_oos_percentile": pct,
                 "is_oos_correlation": corr},
        "configs": rows,
        "window_bh_pct": bhs}, indent=2), encoding="utf-8")
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

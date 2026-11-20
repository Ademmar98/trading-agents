#!/usr/bin/env python3
"""
STUDY 19 — 30-day portfolio simulation of the hl_system strategy.

WHAT IS AND IS NOT SIMULATED
----------------------------
Module B (funding Z-score) and Module C (VWAP reversion) run here on real
Hyperliquid data, through the SHIPPED classes — `FundingZScoreSignal`,
`VwapReversionSignal`, `SignalManager`, `RiskManager` are imported from
hl_system, not reimplemented. If this file disagrees with live behaviour, that
is a bug in one of them, not a translation error.

Module A (CVD divergence at liquidity walls) is NOT simulated. Hyperliquid
exposes no historical trade tape and no historical L2 book — probed and
confirmed. Reconstructing CVD from kline volume is exactly the error study #10
made (its backtest used 4h `taker_buy_base`, the live code used the tape), so
the honest move is to leave A out and say so, not to substitute a proxy and
call the result a test of the system. Coverage is therefore 2 of 3 modules.

FILL MODEL — the part that decides whether this is honest
---------------------------------------------------------
Every order is post-only. A resting bid does not fill because price merely
closed lower; it fills because price TRADED DOWN THROUGH IT. So:

  * entry: quote at the signal bar's close, fill only if a later bar's LOW
    <= the limit, within `unfilled_timeout_candles` bars, else cancel.
  * exit at target: fill only if a bar's HIGH >= the target.
  * ATR stop: filled AT the stop price when a bar's low breaches it. This is
    OPTIMISTIC and it is flagged in the output — a stop is a taker action and
    the live system, being maker-only, has no guaranteed stop at all.

Fees: Hyperliquid base perp MAKER fee, both sides. No taker leg anywhere.

Methodology bar carried over from studies #10/#13/#16/#17:
  per-pair concentration, buy-and-hold benchmark, one shared portfolio (not N
  independent accounts), and a window slide so the result is a distribution
  rather than one arbitrary month.
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import pickle
import re
import sys
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
except Exception:
    pass

from hl_system.config import ExecConfig, RiskConfig, SignalConfig  # noqa: E402
from hl_system.data_engine import Candle  # noqa: E402
from hl_system.risk_manager import RiskManager, RiskState, atr  # noqa: E402
from hl_system.signals import (Direction, FundingZScoreSignal,  # noqa: E402
                               VwapReversionSignal)

API = "https://api.hyperliquid.xyz/info"
HERE = Path(__file__).parent
CACHE = Path(os.environ.get(
    "HL_BT_CACHE",
    r"C:\Users\DELL\AppData\Local\Temp\claude\C--Users-DELL-OneDrive-1m"
    r"\461d6d4d-db6f-4366-adba-df2562ad4a11\scratchpad\hl_bt_cache"))
CACHE.mkdir(parents=True, exist_ok=True)

HOUR_MS = 3_600_000
DAY_MS = 86_400_000
WARMUP_DAYS = 15          # 7d anchored VWAP + 72h funding + 14d ATR
TEST_DAYS = 30

MAKER_FEE = 0.00015       # Hyperliquid base perp maker, per side
START_EQUITY = 10_000.0

SESSION = requests.Session()


# ------------------------------------------------------------- fetch -------


def _post(payload: dict, retries: int = 4):
    for i in range(retries):
        try:
            r = SESSION.post(API, json=payload, timeout=30)
            if r.status_code == 429:
                time.sleep(2 * (i + 1))
                continue
            r.raise_for_status()
            return r.json()
        except Exception:
            if i == retries - 1:
                raise
            time.sleep(1.5 * (i + 1))
    return None


def fetch_symbol(coin: str, start_ms: int, end_ms: int) -> dict | None:
    """Candles (1h/4h/1d) + paginated funding for one asset."""
    path = CACHE / f"{coin}.pkl.gz"
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
                   float(c["c"]), float(c["v"]), int(c.get("n", 0)))
            for c in raw]

    # fundingHistory caps at 500 rows/request; paginate forward.
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

    if len(out["candles"]["1h"]) < 200:
        return None
    with gzip.open(path, "wb", compresslevel=4) as f:
        pickle.dump(out, f)
    return out


def load_universe(workers: int = 8) -> dict[str, dict]:
    meta = _post({"type": "meta"})
    coins = [a["name"] for a in meta["universe"] if not a.get("isDelisted")]
    sz_dec = {a["name"]: int(a["szDecimals"]) for a in meta["universe"]}
    end = int(time.time() * 1000)
    start = end - (WARMUP_DAYS + TEST_DAYS + 5) * DAY_MS

    print(f"fetching {len(coins)} perp assets, "
          f"{WARMUP_DAYS + TEST_DAYS + 5}d of 1h/4h/1d + funding ...")
    data: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(fetch_symbol, c, start, end): c for c in coins}
        for i, fut in enumerate(as_completed(futs), 1):
            c = futs[fut]
            try:
                d = fut.result()
                if d:
                    d["sz_decimals"] = sz_dec.get(c, 2)
                    data[c] = d
            except Exception as e:
                print(f"  {c}: {type(e).__name__} {e}")
            if i % 40 == 0:
                print(f"  [{i}/{len(coins)}] {len(data)} ok")
    print(f"{len(data)} assets loaded")
    return data


# ------------------------------------------------------- replay view -------


class ReplayData:
    """
    Minimal stand-in for DataEngine exposing only what B and C read.

    Slices are strictly `< now`, so a module can never see the bar it is being
    asked to decide on, let alone a future one.
    """

    def __init__(self) -> None:
        self.candles: dict[tuple[str, str], list] = {}
        self.funding: dict[str, list] = {}
        self.trades: dict[str, list] = {}
        self.books: dict[str, object] = {}
        self.mids: dict[str, float] = {}
        self.assets: dict[str, object] = {}


@dataclass
class Trade:
    symbol: str
    entry_ts: int
    entry_px: float
    size: float
    reason: str
    module: str
    target: float | None = None
    stop: float | None = None
    exit_ts: int = 0
    exit_px: float = 0.0
    exit_reason: str = ""

    @property
    def gross_pct(self) -> float:
        if not self.exit_px:
            return 0.0
        return (self.exit_px - self.entry_px) / self.entry_px

    @property
    def net_pct(self) -> float:
        return self.gross_pct - 2 * MAKER_FEE

    @property
    def pnl(self) -> float:
        return self.net_pct * self.entry_px * self.size


# -------------------------------------------------------- simulation -------


@dataclass
class SimResult:
    label: str
    trades: list[Trade] = field(default_factory=list)
    equity_curve: list[tuple[int, float]] = field(default_factory=list)
    blocked: dict[str, int] = field(default_factory=dict)
    final_state: str = "NORMAL"
    signals_seen: int = 0
    fills: int = 0
    timeouts: int = 0
    #: (ts, invested_notional / equity) each hour — the exposure-matched null needs this
    exposure: list = field(default_factory=list)


def simulate(data: dict[str, dict], start_ms: int, end_ms: int,
             label: str, use_stop: bool = True,
             max_hold_hours: int = 168) -> SimResult:
    scfg, rcfg, ecfg = SignalConfig(), RiskConfig(), ExecConfig()
    mod_b, mod_c = FundingZScoreSignal(scfg), VwapReversionSignal(scfg)

    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", label)   # Windows: no ":" in filenames
    state_path = CACHE / f"_simstate_{safe}.json"
    state_path.unlink(missing_ok=True)

    class _Cfg:
        risk = rcfg
    rm = RiskManager(_Cfg(), state_path)
    rm.update(START_EQUITY)

    res = SimResult(label)
    cash = START_EQUITY
    open_pos: dict[str, Trade] = {}
    pending: dict[str, dict] = {}      # symbol -> resting order

    # hour grid
    grid = list(range(start_ms - (start_ms % HOUR_MS), end_ms, HOUR_MS))
    rd = ReplayData()

    # pre-index for fast slicing
    idx = {}
    for coin, d in data.items():
        idx[coin] = {
            iv: (np.array([c.ts for c in d["candles"][iv]]), d["candles"][iv])
            for iv in ("1h", "4h", "1d")
        }
        idx[coin]["f"] = (np.array([t for t, _ in d["funding"]]), d["funding"])

    class _Meta:
        def __init__(self, sd): self.sz_decimals = sd

    for now in grid:
        # ---- mark to market -------------------------------------------
        equity = cash
        marks: dict[str, float] = {}
        for coin, tr in open_pos.items():
            ts, arr = idx[coin]["1h"]
            j = int(np.searchsorted(ts, now, "right")) - 1
            if j < 0:
                continue
            px = arr[j].close
            marks[coin] = px
            equity += tr.size * px

        snap = rm.update(equity, len(open_pos))
        res.equity_curve.append((now, equity))
        invested = sum(tr.size * marks.get(c, tr.entry_px) for c, tr in open_pos.items())
        res.exposure.append((now, invested / equity if equity > 0 else 0.0))

        # ---- circuit breakers -----------------------------------------
        if rm.should_flatten:
            for coin, tr in list(open_pos.items()):
                px = marks.get(coin, tr.entry_px)
                tr.exit_ts, tr.exit_px = now, px
                tr.exit_reason = f"circuit breaker {rm.state.value}"
                cash += tr.size * px * (1 - MAKER_FEE)
                res.trades.append(tr)
                del open_pos[coin]
            pending.clear()
            res.blocked[rm.state.value] = res.blocked.get(rm.state.value, 0) + 1
            if rm.state is RiskState.HALTED:
                res.final_state = "HALTED"
                break
            continue

        # ---- resting entry orders: fill / timeout ----------------------
        for coin, o in list(pending.items()):
            ts, arr = idx[coin]["1h"]
            j = int(np.searchsorted(ts, now, "right")) - 1
            if j < 0:
                continue
            bar = arr[j]
            if bar.low <= o["px"]:                      # traded through our bid
                sz = o["size"]
                cost = sz * o["px"] * (1 + MAKER_FEE)
                if cost > cash:
                    sz = (cash / (o["px"] * (1 + MAKER_FEE))) * 0.999
                    cost = sz * o["px"] * (1 + MAKER_FEE)
                if sz > 0 and cost <= cash:
                    cash -= cost
                    a = atr(_slice(idx[coin]["1d"], now, 40), rcfg.atr_period)
                    open_pos[coin] = Trade(
                        coin, now, o["px"], sz, o["reason"], o["module"],
                        target=o.get("target"),
                        stop=(o["px"] - rcfg.atr_multiplier * a) if (use_stop and a) else None)
                    res.fills += 1
                del pending[coin]
            elif now - o["placed"] >= ecfg.unfilled_timeout_candles * HOUR_MS:
                res.timeouts += 1
                del pending[coin]

        # ---- manage open positions -------------------------------------
        for coin, tr in list(open_pos.items()):
            ts, arr = idx[coin]["1h"]
            j = int(np.searchsorted(ts, now, "right")) - 1
            if j < 0:
                continue
            bar = arr[j]
            exit_px = exit_why = None

            if tr.stop and bar.low <= tr.stop:
                exit_px, exit_why = tr.stop, "ATR stop (OPTIMISTIC: taker in reality)"
            elif tr.target and bar.high >= tr.target:
                exit_px, exit_why = tr.target, "VWAP target (maker)"
            elif now - tr.entry_ts >= max_hold_hours * HOUR_MS:
                exit_px, exit_why = bar.close, f"max hold {max_hold_hours}h"
            else:
                _populate(rd, coin, idx, now, scfg, rcfg)
                for m in (mod_b, mod_c):
                    s = m.evaluate(coin, rd)
                    if s and s.direction is Direction.EXIT:
                        exit_px, exit_why = bar.close, f"EXIT: {s.reason}"
                        break

            if exit_px:
                tr.exit_ts, tr.exit_px, tr.exit_reason = now, exit_px, exit_why
                cash += tr.size * exit_px * (1 - MAKER_FEE)
                res.trades.append(tr)
                del open_pos[coin]

        # ---- new entries ------------------------------------------------
        ok, why = rm.can_open(0.0, len(open_pos))
        if not ok:
            key = why.split(",")[0]
            res.blocked[key] = res.blocked.get(key, 0) + 1
            continue

        cands = []
        for coin in data:
            if coin in open_pos or coin in pending:
                continue
            _populate(rd, coin, idx, now, scfg, rcfg)
            sigs = []
            for m in (mod_b, mod_c):
                s = m.evaluate(coin, rd)
                if s and s.direction is Direction.LONG:
                    sigs.append(s)
            if not sigs:
                continue
            res.signals_seen += 1
            conviction = min(1.0, sum(s.strength for s in sigs) / 3.0)  # /3 modules
            target = next((s.meta.get("target") for s in sigs if s.meta.get("target")), None)
            cands.append((conviction, coin, sigs, target))

        cands.sort(reverse=True, key=lambda x: x[0])
        for conviction, coin, sigs, target in cands:
            if len(open_pos) + len(pending) >= rcfg.max_concurrent_positions:
                break
            ts, arr = idx[coin]["1h"]
            j = int(np.searchsorted(ts, now, "right")) - 1
            if j < 0:
                continue
            px = arr[j].close
            daily = _slice(idx[coin]["1d"], now, 40)
            size, _ = rm.position_size(coin, px, daily,
                                       _Meta(data[coin]["sz_decimals"]), conviction)
            if size <= 0 or size * px < ecfg.min_order_notional_usd:
                continue
            if size * px > cash:
                continue
            pending[coin] = {"px": px, "size": size, "placed": now,
                             "reason": "; ".join(s.reason for s in sigs),
                             "module": "+".join(s.module[0] for s in sigs),
                             "target": target}

    # close survivors at the last mark
    for coin, tr in list(open_pos.items()):
        ts, arr = idx[coin]["1h"]
        j = int(np.searchsorted(ts, end_ms, "right")) - 1
        px = arr[j].close if j >= 0 else tr.entry_px
        tr.exit_ts, tr.exit_px, tr.exit_reason = end_ms, px, "end of window"
        cash += tr.size * px * (1 - MAKER_FEE)
        res.trades.append(tr)
    res.equity_curve.append((end_ms, cash))
    state_path.unlink(missing_ok=True)
    return res


def _slice(pair, now: int, n: int) -> list:
    ts, arr = pair
    j = int(np.searchsorted(ts, now, "left"))
    return arr[max(0, j - n):j]


def _populate(rd: ReplayData, coin: str, idx: dict, now: int,
              scfg: SignalConfig, rcfg: RiskConfig) -> None:
    """Fill the replay view with data strictly BEFORE `now`."""
    rd.candles[(coin, scfg.c_interval)] = _slice(idx[coin]["1h"], now, 260)
    rd.candles[(coin, scfg.c_confirm_interval)] = _slice(idx[coin]["4h"], now, 40)
    rd.candles[(coin, rcfg.atr_interval)] = _slice(idx[coin]["1d"], now, 40)
    fts, farr = idx[coin]["f"]
    j = int(np.searchsorted(fts, now, "left"))
    rd.funding[coin] = farr[max(0, j - 120):j]


# ---------------------------------------------------------- reporting ------


def buy_and_hold(data: dict[str, dict], start_ms: int, end_ms: int) -> float:
    """Equal-weight buy & hold across the same universe. The benchmark that matters."""
    rets = []
    for coin, d in data.items():
        arr = d["candles"]["1h"]
        ts = np.array([c.ts for c in arr])
        i = int(np.searchsorted(ts, start_ms, "left"))
        j = int(np.searchsorted(ts, end_ms, "right")) - 1
        if 0 <= i < j < len(arr) and arr[i].close > 0:
            rets.append((arr[j].close - arr[i].close) / arr[i].close)
    return float(np.mean(rets)) * 100 if rets else 0.0


def report(res: SimResult, bh_pct: float, data: dict) -> dict:
    eq = [e for _, e in res.equity_curve]
    ret = (eq[-1] - START_EQUITY) / START_EQUITY * 100
    peak, mdd = eq[0], 0.0
    for e in eq:
        peak = max(peak, e)
        mdd = max(mdd, (peak - e) / peak * 100)

    closed = [t for t in res.trades if t.exit_px]
    wins = [t for t in closed if t.net_pct > 0]
    gross_w = sum(t.pnl for t in wins)
    gross_l = -sum(t.pnl for t in closed if t.net_pct <= 0)
    pf = gross_w / gross_l if gross_l > 0 else float("inf")

    per_pair: dict[str, float] = {}
    for t in closed:
        per_pair[t.symbol] = per_pair.get(t.symbol, 0.0) + t.pnl
    ranked = sorted(per_pair.items(), key=lambda x: -x[1])

    net_ex_top2 = sum(v for _, v in ranked[2:])

    print(f"\n{'='*74}\n{res.label}\n{'='*74}")
    print(f"  return            {ret:+.2f}%   (buy & hold equal-weight {bh_pct:+.2f}%)")
    print(f"  EXCESS over B&H   {ret - bh_pct:+.2f}%")
    print(f"  max drawdown      {mdd:.2f}%   (5.0% hard lock; final state {res.final_state})")
    print(f"  trades            {len(closed)}   fills {res.fills}  "
          f"timed-out orders {res.timeouts}  signals {res.signals_seen}")
    if closed:
        print(f"  win rate          {len(wins)/len(closed)*100:.1f}%")
        print(f"  profit factor     {pf:.2f}")
        print(f"  avg net/trade     {np.mean([t.net_pct for t in closed])*100:+.4f}%")
        print(f"  median net/trade  {np.median([t.net_pct for t in closed])*100:+.4f}%")
        print(f"  pairs traded      {len(per_pair)}  "
              f"profitable {sum(1 for v in per_pair.values() if v>0)}")
        print(f"\n  CONCENTRATION (study #10 check)")
        print(f"    total net       ${sum(per_pair.values()):+,.2f}")
        print(f"    net ex-top-2    ${net_ex_top2:+,.2f}")
        print(f"    best  : " + ", ".join(f"{k} ${v:+,.0f}" for k, v in ranked[:5]))
        print(f"    worst : " + ", ".join(f"{k} ${v:+,.0f}" for k, v in ranked[-5:]))
        by_mod: dict[str, list] = {}
        for t in closed:
            by_mod.setdefault(t.module, []).append(t)
        print(f"\n  BY MODULE")
        for m, ts_ in sorted(by_mod.items()):
            w = sum(1 for t in ts_ if t.net_pct > 0)
            print(f"    {m:>6}: {len(ts_):>4} trades  WR {w/len(ts_)*100:>5.1f}%  "
                  f"net ${sum(t.pnl for t in ts_):+,.2f}")
        by_exit: dict[str, int] = {}
        for t in closed:
            by_exit[t.exit_reason.split(":")[0].split("(")[0].strip()] = \
                by_exit.get(t.exit_reason.split(":")[0].split("(")[0].strip(), 0) + 1
        print(f"\n  EXIT REASONS: " + ", ".join(f"{k}={v}" for k, v in
                                                sorted(by_exit.items(), key=lambda x: -x[1])))
    if res.blocked:
        print(f"\n  RISK GATE blocked hours: " + ", ".join(
            f"{k}={v}" for k, v in sorted(res.blocked.items(), key=lambda x: -x[1])[:5]))

    avg_exp = float(np.mean([e for _, e in res.exposure])) if res.exposure else 0.0
    matched = avg_exp * bh_pct
    print(f"\n  EXPOSURE-MATCHED NULL  (the decisive test)")
    print(f"    average gross exposure  {avg_exp*100:.1f}% of equity")
    print(f"    B&H scaled to that      {matched:+.2f}%   <- what {avg_exp*100:.0f}% "
          f"passive exposure alone would have returned")
    print(f"    strategy                {ret:+.2f}%")
    print(f"    SKILL (strat - matched) {ret - matched:+.2f}%")

    return {"label": res.label, "return_pct": ret, "bh_pct": bh_pct,
            "avg_exposure": avg_exp, "matched_bh_pct": matched,
            "skill_pct": ret - matched,
            "excess_pct": ret - bh_pct, "max_dd_pct": mdd, "trades": len(closed),
            "win_rate": (len(wins) / len(closed) * 100) if closed else 0.0,
            "profit_factor": pf if pf != float("inf") else None,
            "net_ex_top2": net_ex_top2, "final_state": res.final_state,
            "fills": res.fills, "timeouts": res.timeouts,
            "signals": res.signals_seen,
            "per_pair": dict(ranked),
            "trades_detail": [
                {"symbol": t.symbol, "module": t.module, "net_pct": t.net_pct * 100,
                 "gross_pct": t.gross_pct * 100, "pnl": t.pnl,
                 "hold_h": (t.exit_ts - t.entry_ts) / 3_600_000,
                 "exit": t.exit_reason.split(":")[0].split("(")[0].strip(),
                 "notional": t.entry_px * t.size}
                for t in closed]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--slide", type=int, default=5,
                    help="window-slide offsets (study #17); 1 == single window")
    ap.add_argument("--no-stop", action="store_true",
                    help="simulate the system AS SHIPPED — no per-position stop")
    args = ap.parse_args()

    data = load_universe()
    if not data:
        print("no data")
        return 1

    now = int(time.time() * 1000)
    results = []
    for k in range(args.slide):
        end = now - k * 3 * DAY_MS
        start = end - TEST_DAYS * DAY_MS
        label = (f"window {k}: {time.strftime('%Y-%m-%d', time.gmtime(start/1000))}"
                 f" -> {time.strftime('%Y-%m-%d', time.gmtime(end/1000))}"
                 f"{' [no stop]' if args.no_stop else ''}")
        r = simulate(data, start, end, label, use_stop=not args.no_stop)
        results.append(report(r, buy_and_hold(data, start, end), data))

    if len(results) > 1:
        rets = [r["return_pct"] for r in results]
        exc = [r["excess_pct"] for r in results]
        dds = [r["max_dd_pct"] for r in results]
        print(f"\n{'='*74}\nWINDOW-SLIDE DISTRIBUTION (study #17: one window is not a validation)"
              f"\n{'='*74}")
        print(f"  return   mean {np.mean(rets):+.2f}%  median {np.median(rets):+.2f}%  "
              f"range {min(rets):+.2f}% .. {max(rets):+.2f}%  positive {sum(1 for x in rets if x>0)}/{len(rets)}")
        print(f"  excess   mean {np.mean(exc):+.2f}%  median {np.median(exc):+.2f}%  "
              f"positive {sum(1 for x in exc if x>0)}/{len(exc)}")
        sk = [r["skill_pct"] for r in results]
        ex_ = [r["avg_exposure"] * 100 for r in results]
        print(f"  avg exposure  {np.mean(ex_):.1f}%")
        print(f"  SKILL vs exposure-matched B&H: mean {np.mean(sk):+.2f}%  "
              f"median {np.median(sk):+.2f}%  positive {sum(1 for x in sk if x>0)}/{len(sk)}")
        print(f"  max DD   mean {np.mean(dds):.2f}%  worst {max(dds):.2f}%")
        print(f"  halts    {sum(1 for r in results if r['final_state']=='HALTED')}/{len(results)}")

    out = HERE / "hl_system_backtest_results.json"
    out.write_text(json.dumps({
        "meta": {"generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                 "universe": len(data), "test_days": TEST_DAYS,
                 "maker_fee_per_side": MAKER_FEE, "start_equity": START_EQUITY,
                 "modules_simulated": ["B_funding_z", "C_vwap_reversion"],
                 "modules_not_simulated": ["A_microstructure"],
                 "module_a_reason": "Hyperliquid serves no historical trade tape or L2; "
                                    "a kline-derived CVD proxy would repeat the study #10 error",
                 "stop_used": not args.no_stop},
        "windows": results}, indent=2), encoding="utf-8")
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

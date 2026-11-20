#!/usr/bin/env python3
"""
Propr challenge simulator.

The question this answers: is there a configuration that reaches +10% in 30
days without ever touching the 3% daily-loss or 6% max-drawdown wall -- and
does it survive on months it was NOT fitted to?

    python sim.py selftest      # asserts, no network
    python sim.py fetch         # download + cache Hyperliquid 1h candles
    python sim.py search        # grid search on the trailing 30 days
    python sim.py walkforward   # freeze survivors, re-run on every other month

Rules are Propr Classic 1-step, from core/exchange/propr/config.py in the 1m
repo. Every rule is checked against INTRABAR equity (worst extreme of every
open position), not close-to-close, because a prop firm measures equity in
real time and a wick that touches the wall ends the challenge.
"""
import itertools
import json
import pickle
import sys
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests

HERE = Path(__file__).parent
CACHE = HERE / "cache"
HL = "https://api.hyperliquid.xyz/info"
HISTORY_DAYS = 200        # 4800 1h bars; the API caps a snapshot at 5000
UNIVERSE_N = 50


# --------------------------------------------------------------------------
# rules
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Rules:
    start_equity: float = 5000.0
    target_pct: float = 0.10      # +$500
    daily_loss_pct: float = 0.03  # $150 from the day's starting equity
    max_dd_pct: float = 0.06      # $300 from the start, static floor $4,700
    fee: float = 0.00075          # taker, per side
    # Propr's config comments its 0.075% as "taker/maker", i.e. one rate for
    # both, and the dead API key makes that unverifiable. So this defaults to
    # the SAME rate -- maker execution has to earn its keep on fill price
    # alone -- and cmd_maker sweeps the optimistic values separately.
    fee_maker: float = 0.00075
    funding_8h: float = 0.0001    # perp carry drag, charged both directions

    @property
    def dd_floor(self) -> float:
        return self.start_equity * (1 - self.max_dd_pct)


@dataclass(frozen=True)
class Cfg:
    strat: str = "mom"
    # signal params
    lb: int = 48
    k: int = 3
    n: int = 48
    dip: float = 0.05
    rsi: float = 25.0
    allow_short: bool = True
    # risk params
    risk_pct: float = 0.01        # equity risked to the stop, per trade
    sl_atr: float = 2.0
    tp_r: float = 2.0             # take profit at N x the stop distance
    trail_r: float = 0.0          # 0 = off; else trail at N x stop distance
    max_hold: int = 72            # bars (1h)
    max_concurrent: int = 3
    max_gross: float = 3.0        # gross notional cap, x equity
    cooldown: int = 4             # bars before re-entering the same asset
    # LIVE-fill guard. The signal reads the last CLOSED 1h bar; on a fast tape
    # the live price can already be far away. Above this divergence the entry
    # is skipped (stale signal chasing an extended move) instead of filled
    # into a worse price than the backtest ever allowed. 0.015 = 1.5%.
    max_entry_slip: float = 0.015
    # Live-only rule (NOT in the validated backtest): once price travels this
    # fraction of the way to take-profit, move the resting stop up to
    # break-even (incl. fees). 0.5 = halfway.
    be_frac: float = 0.5
    # Passive entry: rest a limit maker_offset ATRs BELOW the signal close
    # (above, for a short) and wait. It fills only if price comes back to you,
    # so the trades that run away immediately are simply missed -- which for a
    # momentum signal are the winners. That adverse selection is the whole
    # question, and it is why lowering the fee alone would be a fake test.
    maker: bool = False
    maker_offset: float = 0.25    # in ATRs
    maker_patience: int = 4       # bars the order rests before cancelling
    # the bot's own halts, tighter than the walls it must not touch
    daily_halt: float = 0.02
    dd_halt: float = 0.045
    # Live-only market-confirmation gate (NOT in the validated backtest):
    # equal-weight 24h market return must clear mkt_min_pct (+ = risk-on) AND
    # the BTC 24h run must stay under mkt_max_run_pct, else new entries stand
    # down. Captures the observed reality that this long-only momentum bot
    # makes its money on pump days and bleeds when it levers into a flat or
    # collapsing tape. 0 disables the corresponding check.
    mkt_min_pct: float = 0.01
    mkt_max_run_pct: float = 0.08

    def key(self) -> tuple:
        return (self.strat, self.lb, self.k, self.n, self.dip, self.rsi,
                self.allow_short)


# --------------------------------------------------------------------------
# data
# --------------------------------------------------------------------------
def hl_post(payload, timeout=30):
    r = requests.post(HL, json=payload, timeout=timeout)
    r.raise_for_status()
    return r.json()


def universe(n=UNIVERSE_N) -> list[str]:
    """Top-n perps by 24h notional volume. Liquidity is not optional: a
    backtest on an asset you cannot fill is fiction."""
    meta, ctxs = hl_post({"type": "metaAndAssetCtxs"})
    names = [u["name"] for u in meta["universe"]]
    vols = [float(c.get("dayNtlVlm", 0) or 0) for c in ctxs]
    ranked = sorted(zip(names, vols), key=lambda x: -x[1])
    return [nm for nm, _ in ranked[:n]]


def fetch_candles(coin: str, days: int) -> pd.DataFrame:
    now = int(datetime.now(timezone.utc).timestamp() * 1000)
    start = now - days * 86400 * 1000
    d = hl_post({"type": "candleSnapshot", "req": {
        "coin": coin, "interval": "1h", "startTime": start, "endTime": now}})
    if not d:
        return pd.DataFrame()
    df = pd.DataFrame([{
        "open": float(c["o"]), "high": float(c["h"]), "low": float(c["l"]),
        "close": float(c["c"]), "volume": float(c["v"]),
        "timestamp": pd.to_datetime(int(c["t"]), unit="ms", utc=True)}
        for c in d])
    return df.set_index("timestamp").sort_index()


def fetch_all(days=HISTORY_DAYS, n=UNIVERSE_N) -> dict:
    CACHE.mkdir(exist_ok=True)
    coins = universe(n)
    out = {}
    for i, c in enumerate(coins, 1):
        try:
            df = fetch_candles(c, days)
        except Exception as e:
            print(f"  [{i}/{len(coins)}] {c}: {type(e).__name__} {e}")
            continue
        if len(df) > 500:
            out[c] = df
        print(f"  [{i}/{len(coins)}] {c}: {len(df)} bars")
        time.sleep(0.15)
    path = CACHE / "candles.pkl"
    path.write_bytes(pickle.dumps(out))
    print(f"\ncached {len(out)} assets -> {path}")
    return out


def load() -> dict:
    path = CACHE / "candles.pkl"
    if not path.exists():
        sys.exit("no cache — run: python sim.py fetch")
    return pickle.loads(path.read_bytes())


def align(raw: dict) -> dict:
    """Assets on one hourly grid. `valid` marks bars with real data; the sim
    never enters where valid is False, so forward-filled gaps cannot be
    traded, only marked."""
    idx = pd.date_range(
        min(df.index.min() for df in raw.values()),
        max(df.index.max() for df in raw.values()), freq="1h", tz="UTC")
    cols = sorted(raw)
    M = {}
    for f in ("open", "high", "low", "close"):
        df = pd.DataFrame({c: raw[c][f].reindex(idx) for c in cols}, index=idx)
        if f == "close":
            M["valid"] = df.notna().to_numpy()
        M[f] = df.ffill().to_numpy(np.float64)

    vol = pd.DataFrame({c: raw[c]["volume"].reindex(idx) for c in cols},
                       index=idx)
    M["volume"] = vol.ffill().to_numpy(np.float64)

    h, l, c = M["high"], M["low"], M["close"]
    pc = np.vstack([c[:1], c[:-1]])
    tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
    M["atr"] = pd.DataFrame(tr).ewm(alpha=1 / 14, adjust=False).mean().to_numpy()
    return {"index": idx, "cols": cols, **M}


# --------------------------------------------------------------------------
# signals -> (n_bars, n_assets) int8 of {-1, 0, +1}, read one bar late
# --------------------------------------------------------------------------
def _rsi(C: np.ndarray, n=14) -> np.ndarray:
    d = pd.DataFrame(C).diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return (100 - 100 / (1 + up / dn.replace(0, np.nan))).to_numpy()


def sig_mom(D, cfg: Cfg) -> np.ndarray:
    """Cross-sectional momentum: long the top-k performers, short the bottom-k."""
    C, lb, k = D["close"], cfg.lb, cfg.k
    ret = np.full_like(C, np.nan)
    ret[lb:] = C[lb:] / C[:-lb] - 1
    ret[~D["valid"]] = np.nan
    sig = np.zeros(C.shape, np.int8)
    rows = np.arange(C.shape[0])[:, None]

    hi = np.argsort(np.nan_to_num(ret, nan=-np.inf), axis=1)[:, -k:]
    m = np.zeros(C.shape, bool)
    m[rows, hi] = True
    sig[m & (ret > 0)] = 1

    if cfg.allow_short:
        lo = np.argsort(np.nan_to_num(ret, nan=np.inf), axis=1)[:, :k]
        m = np.zeros(C.shape, bool)
        m[rows, lo] = True
        sig[m & (ret < 0)] = -1
    return sig


def sig_donch(D, cfg: Cfg) -> np.ndarray:
    """Donchian channel break, measured on the n bars BEFORE this one."""
    n = cfg.n
    hh = pd.DataFrame(D["high"]).rolling(n).max().shift(1).to_numpy()
    ll = pd.DataFrame(D["low"]).rolling(n).min().shift(1).to_numpy()
    C = D["close"]
    sig = np.zeros(C.shape, np.int8)
    sig[C > hh] = 1
    if cfg.allow_short:
        sig[C < ll] = -1
    sig[~D["valid"]] = 0
    return sig


def sig_dip(D, cfg: Cfg) -> np.ndarray:
    """Oversold dip off a local high — the family the 1m repo's dip runner used."""
    C = D["close"]
    hh = pd.DataFrame(C).rolling(cfg.lb).max().to_numpy()
    drop = C / hh - 1
    r = _rsi(C)
    sig = np.zeros(C.shape, np.int8)
    sig[(drop <= -cfg.dip) & (r < cfg.rsi)] = 1
    sig[~D["valid"]] = 0
    return sig


SIGNALS = {"mom": sig_mom, "donch": sig_donch, "dip": sig_dip}


# --------------------------------------------------------------------------
# engine
# --------------------------------------------------------------------------
def simulate(D, sig, cfg: Cfg, rules: Rules, lo=0, hi=None) -> dict:
    O, H, L, C = D["open"], D["high"], D["low"], D["close"]
    ATR, VALID, LEV = D["atr"], D["valid"], D["lev"]
    days = D["dayord"]
    hi = len(C) if hi is None else hi

    cash = equity = day_start = peak = rules.start_equity
    pos: dict[int, list] = {}      # asset -> [side, qty, entry, stop, tp, t0]
    pend: dict[int, list] = {}     # asset -> [side, limit, expire_t, atr]
    cool = np.full(C.shape[1], -10**9)
    cur_day = days[lo]
    halted_day = stopped = False
    outcome, outcome_t = "no_target", None
    trades = wins = 0
    signals_filled = signals_missed = 0
    gross_win = gross_loss = 0.0
    dd_worst = 0.0

    def close_pos(a, px, t, fee=None):
        """fee defaults to taker: a stop and a max-hold flatten are both
        market orders. Only the take-profit rests as a maker limit."""
        nonlocal cash, trades, wins, gross_win, gross_loss
        side, qty, entry, *_ , t0 = pos[a]
        notion = qty * px
        pnl = (px - entry) * qty * side
        pnl -= notion * (rules.fee if fee is None else fee)
        pnl -= qty * entry * rules.funding_8h * (t - t0) / 8.0
        cash += pnl
        trades += 1
        if pnl > 0:
            wins += 1
            gross_win += pnl
        else:
            gross_loss -= pnl
        cool[a] = t
        del pos[a]

    for t in range(max(lo, 1), hi):
        if days[t] != cur_day:
            cur_day, day_start, halted_day = days[t], equity, False

        # --- entries at this bar's open, on the previous bar's signal
        if not (halted_day or stopped) and len(pos) + len(pend) < cfg.max_concurrent:
            gross = sum(p[1] * C[t - 1, a] for a, p in pos.items())
            for a in np.flatnonzero(sig[t - 1]):
                if len(pos) + len(pend) >= cfg.max_concurrent:
                    break
                if a in pos or a in pend or t - cool[a] < cfg.cooldown:
                    continue
                if not (VALID[t, a] and VALID[t - 1, a]):
                    continue
                atr = ATR[t - 1, a]
                side = int(sig[t - 1, a])
                if not atr > 0:
                    continue

                if cfg.maker:
                    # Rest a limit away from the signal close and wait. No
                    # fill, no trade -- and no fee either.
                    limit = C[t - 1, a] - side * cfg.maker_offset * atr
                    if limit > 0:
                        pend[a] = [side, limit, t + cfg.maker_patience, atr]
                    continue

                px = O[t, a]
                if not px > 0:
                    continue
                sd = cfg.sl_atr * atr
                qty = (equity * cfg.risk_pct) / sd
                notion = min(qty * px, equity * min(LEV[a], cfg.max_gross),
                             max(0.0, equity * cfg.max_gross - gross))
                if notion < 10:
                    continue
                qty = notion / px
                cash -= notion * rules.fee
                stop = px - side * sd
                tp = px + side * sd * cfg.tp_r if cfg.tp_r > 0 else np.nan
                pos[a] = [side, qty, px, stop, tp, t]
                gross += notion

        # --- resting maker orders: fill if price came back, else expire
        if pend:
            gross = sum(p[1] * C[t - 1, a] for a, p in pos.items())
            for a in list(pend):
                side, limit, expire, atr = pend[a]
                hit = L[t, a] <= limit if side > 0 else H[t, a] >= limit
                if not (hit and VALID[t, a]):
                    if t >= expire:
                        del pend[a]
                        signals_missed += 1
                    continue
                del pend[a]
                if len(pos) >= cfg.max_concurrent:
                    continue
                sd = cfg.sl_atr * atr
                qty = (equity * cfg.risk_pct) / sd
                notion = min(qty * limit, equity * min(LEV[a], cfg.max_gross),
                             max(0.0, equity * cfg.max_gross - gross))
                if notion < 10:
                    continue
                qty = notion / limit
                cash -= notion * rules.fee_maker
                stop = limit - side * sd
                tp = limit + side * sd * cfg.tp_r if cfg.tp_r > 0 else np.nan
                pos[a] = [side, qty, limit, stop, tp, t]
                gross += notion
                signals_filled += 1

        # --- exits, stop checked before target within the same bar
        for a in list(pos):
            side, qty, entry, stop, tp, t0 = pos[a]
            # a bar that OPENS through the stop fills at the open, not at the
            # trigger. crypto gaps; assuming the trigger price always fills is
            # how a backtest quietly deletes its worst losses.
            # The target rests as a limit and pays the maker rate; the stop is
            # a market order and pays taker. A 100% maker rule and a
            # guaranteed stop cannot both hold.
            if side > 0:
                if L[t, a] <= stop:
                    close_pos(a, min(stop, O[t, a]), t); continue
                if tp == tp and H[t, a] >= tp:
                    close_pos(a, tp, t, rules.fee_maker); continue
            else:
                if H[t, a] >= stop:
                    close_pos(a, max(stop, O[t, a]), t); continue
                if tp == tp and L[t, a] <= tp:
                    close_pos(a, tp, t, rules.fee_maker); continue
            if t - t0 >= cfg.max_hold:
                close_pos(a, C[t, a], t); continue
            if cfg.trail_r > 0:
                sd = abs(entry - stop) if cfg.trail_r else 0
                ref = H[t, a] if side > 0 else L[t, a]
                new = ref - side * cfg.sl_atr * ATR[t, a] * cfg.trail_r
                if (side > 0 and new > stop) or (side < 0 and new < stop):
                    pos[a][3] = new

        # --- mark to market, and to the worst tick inside the bar
        unreal = sum((C[t, a] - p[2]) * p[1] * p[0] for a, p in pos.items())
        adverse = sum(((L[t, a] if p[0] > 0 else H[t, a]) - p[2]) * p[1] * p[0]
                      for a, p in pos.items())
        equity = cash + unreal
        equity_low = cash + min(0.0, adverse)
        peak = max(peak, equity)
        dd_worst = max(dd_worst, rules.start_equity - equity_low)

        # --- the walls. these end the challenge.
        if equity_low <= day_start * (1 - rules.daily_loss_pct):
            outcome, outcome_t = "BREACH_DAILY", t
            break
        if equity_low <= rules.dd_floor:
            outcome, outcome_t = "BREACH_DD", t
            break
        if equity >= rules.start_equity * (1 + rules.target_pct):
            outcome, outcome_t = "TARGET", t
            break

        # --- the bot's own halts, well inside the walls
        if not halted_day and equity <= day_start * (1 - cfg.daily_halt):
            for a in list(pos):
                close_pos(a, C[t, a], t)
            equity = cash
            halted_day = True
        if not stopped and equity <= rules.start_equity * (1 - cfg.dd_halt):
            for a in list(pos):
                close_pos(a, C[t, a], t)
            equity = cash
            stopped = True

    n_days = max(1, (hi - max(lo, 1)) / 24)
    return {
        "outcome": outcome,
        "hit_target": outcome == "TARGET",
        "breached": outcome.startswith("BREACH"),
        "days_to_outcome": None if outcome_t is None else (outcome_t - lo) / 24,
        "return_pct": (equity / rules.start_equity - 1) * 100,
        "max_dd_pct": dd_worst / rules.start_equity * 100,
        "trades": trades,
        "win_rate": 100 * wins / trades if trades else 0.0,
        "profit_factor": gross_win / gross_loss if gross_loss > 0 else
                         (float("inf") if gross_win > 0 else 0.0),
        "trades_per_day": trades / n_days,
        "fill_rate": (signals_filled / (signals_filled + signals_missed)
                      if signals_filled + signals_missed else 0.0),
    }


def prep(raw: dict) -> dict:
    D = align(raw)
    D["dayord"] = np.array([d.toordinal() for d in D["index"].date])
    D["lev"] = np.array([5.0 if c in ("BTC", "ETH") else 2.0
                         for c in D["cols"]])
    return D


# --------------------------------------------------------------------------
# search
# --------------------------------------------------------------------------
def grid() -> list[Cfg]:
    out = []
    for lb, k, sh in itertools.product((24, 48, 96, 168), (2, 3, 5), (True, False)):
        out.append(Cfg(strat="mom", lb=lb, k=k, allow_short=sh))
    for n, sh in itertools.product((24, 48, 96), (True, False)):
        out.append(Cfg(strat="donch", n=n, allow_short=sh))
    for lb, dip, rsi in itertools.product((48, 96), (0.04, 0.07), (25, 35)):
        out.append(Cfg(strat="dip", lb=lb, dip=dip, rsi=rsi, allow_short=False))
    return out


RISK = [dict(risk_pct=r, sl_atr=s, tp_r=tp, trail_r=tr, max_concurrent=mc,
             max_hold=mh)
        for r in (0.005, 0.01, 0.02, 0.03)
        for s in (1.5, 2.5)
        for tp in (1.5, 3.0, 6.0)
        for tr in (0.0, 1.0)
        for mc in (2, 3, 5)
        for mh in (48, 168)]


def windows(D, days=30, step=5):
    """Sliding 30-day windows, newest first. Study #17's lesson: one fixed
    walk-forward is a single sample, not a validation."""
    n, w, s = len(D["index"]), days * 24, step * 24
    return [(n - w - i * s, n - i * s)
            for i in range((n - w) // s + 1) if n - w - i * s >= 200]


def run_all(D, cfgs, lo, hi, rules) -> list[dict]:
    """Signals are precomputed once per signal-param key and reused across
    every risk config that shares it."""
    out, cache = [], {}
    for cfg in cfgs:
        kb = cfg.key()
        if kb not in cache:
            cache[kb] = SIGNALS[cfg.strat](D, cfg)
        r = simulate(D, cache[kb], cfg, rules, lo, hi)
        r["cfg"] = cfg
        out.append(r)
    return out


def describe(cfg: Cfg) -> str:
    sig = {"mom": f"mom lb{cfg.lb} k{cfg.k}",
           "donch": f"donch n{cfg.n}",
           "dip": f"dip lb{cfg.lb} d{cfg.dip} rsi{cfg.rsi:.0f}"}[cfg.strat]
    return (f"{sig}{'/short' if cfg.allow_short else '/long'} "
            f"risk{cfg.risk_pct:.1%} sl{cfg.sl_atr} tp{cfg.tp_r} "
            f"trail{cfg.trail_r} mc{cfg.max_concurrent} hold{cfg.max_hold}")


def cmd_search():
    rules, D = Rules(), prep(load())
    lo, hi = windows(D)[0]
    print(f"universe {len(D['cols'])} assets | search window "
          f"{D['index'][lo].date()} -> {D['index'][hi - 1].date()}")
    cfgs = [replace(c, **r) for c in grid() for r in RISK]
    print(f"{len(cfgs)} configs\n")
    t0 = time.time()
    res = run_all(D, cfgs, lo, hi, rules)
    print(f"done in {time.time() - t0:.0f}s")

    hits = [r for r in res if r["hit_target"]]
    clean = sorted(hits, key=lambda r: r["days_to_outcome"])
    print(f"\nreached +10%: {len(hits)}/{len(res)}   "
          f"breached: {sum(r['breached'] for r in res)}/{len(res)}")
    print(f"\n{'config':<62} {'ret%':>7} {'dd%':>6} {'days':>5} {'trd':>5} {'wr%':>6}")
    print("-" * 96)
    for r in clean[:25]:
        print(f"{describe(r['cfg']):<62} {r['return_pct']:>7.2f} "
              f"{r['max_dd_pct']:>6.2f} {r['days_to_outcome']:>5.1f} "
              f"{r['trades']:>5} {r['win_rate']:>6.1f}")
    (HERE / "search_results.json").write_text(json.dumps(
        [{**{k: v for k, v in r.items() if k != "cfg"},
          "cfg": r["cfg"].__dict__} for r in res], indent=1), encoding="utf-8")
    print(f"\nfull results -> {HERE / 'search_results.json'}")


def cmd_walkforward():
    """Every in-sample survivor, frozen, across every OTHER 30-day window.

    A prop challenge is not scored on mean return. It is scored on
    P(reach target) against P(touch a wall), so that is what this reports.
    """
    rules, D = Rules(), prep(load())
    res = json.loads((HERE / "search_results.json").read_text(encoding="utf-8"))
    hits = [r for r in res if r["hit_target"]]
    if not hits:
        hits = sorted(res, key=lambda r: -r["return_pct"])[:50]
        print("nothing hit target in-sample; carrying the best returns forward\n")
    oos = windows(D)[1:]
    print(f"{len(hits)} frozen configs x {len(oos)} out-of-sample "
          f"30-day windows ({D['index'][oos[-1][0]].date()} -> "
          f"{D['index'][oos[0][1] - 1].date()})\n")

    rows, cache = [], {}
    for h in hits:
        cfg = Cfg(**h["cfg"])
        kb = cfg.key()
        if kb not in cache:
            cache[kb] = SIGNALS[cfg.strat](D, cfg)
        rs = [simulate(D, cache[kb], cfg, rules, lo, hi) for lo, hi in oos]
        n = len(rs)
        rows.append({
            "cfg": cfg.__dict__, "desc": describe(cfg), "n": n,
            "pass": sum(r["hit_target"] for r in rs),
            "breach": sum(r["breached"] for r in rs),
            "clean_pass": sum(r["hit_target"] and not r["breached"] for r in rs),
            "median_ret": float(np.median([r["return_pct"] for r in rs])),
            "worst_ret": float(min(r["return_pct"] for r in rs)),
            "median_dd": float(np.median([r["max_dd_pct"] for r in rs])),
            "median_trades": float(np.median([r["trades"] for r in rs])),
            "insample_ret": h["return_pct"],
        })
    rows.sort(key=lambda r: (-(r["pass"] - r["breach"]), r["breach"]))
    (HERE / "walkforward_results.json").write_text(
        json.dumps(rows, indent=1), encoding="utf-8")

    n = rows[0]["n"]
    print(f"{'config':<62} {'pass':>6} {'breach':>7} {'medret%':>8} "
          f"{'worst%':>8} {'medDD%':>7} {'trd':>5}")
    print("-" * 110)
    for r in rows[:20]:
        print(f"{r['desc']:<62} {r['pass']:>3}/{n:<2} {r['breach']:>4}/{n:<2} "
              f"{r['median_ret']:>8.2f} {r['worst_ret']:>8.2f} "
              f"{r['median_dd']:>7.2f} {r['median_trades']:>5.0f}")

    print(f"\n  survivors carried forward   {len(rows)}")
    print(f"  pass more often than breach {sum(1 for r in rows if r['pass'] > r['breach'])}")
    print(f"  never breached in {n} windows  "
          f"{sum(1 for r in rows if r['breach'] == 0)}")
    print(f"  median pass rate            "
          f"{np.median([r['pass'] for r in rows]) / n:.0%}")
    print(f"  median breach rate          "
          f"{np.median([r['breach'] for r in rows]) / n:.0%}")
    print(f"\n-> {HERE / 'walkforward_results.json'}")


def cmd_stability():
    """Perturb the winner one parameter at a time.

    Picking the best of 421 candidates on the out-of-sample set is still
    selection, so the OOS score of the winner is biased upward. A real edge
    has a smooth neighbourhood -- its neighbours work too. A fluke is an
    isolated spike surrounded by nothing. This is the cheapest test that
    tells the two apart.
    """
    rules, D = Rules(), prep(load())
    rows = json.loads((HERE / "walkforward_results.json").read_text(encoding="utf-8"))
    base = Cfg(**rows[0]["cfg"])
    wins = windows(D)
    print(f"base: {describe(base)}")
    print(f"perturbed one axis at a time over all {len(wins)} windows\n")

    def score(cfg):
        sig = SIGNALS[cfg.strat](D, cfg)
        rs = [simulate(D, sig, cfg, rules, lo, hi) for lo, hi in wins]
        return (sum(r["hit_target"] for r in rs),
                sum(r["breached"] for r in rs),
                float(np.median([r["return_pct"] for r in rs])))

    axes = {
        "n": (12, 24, 36, 48, 60, 72, 96, 144),
        "risk_pct": (0.0025, 0.005, 0.0075, 0.01, 0.015, 0.02, 0.03),
        "sl_atr": (1.0, 1.25, 1.5, 2.0, 2.5, 3.0),
        "tp_r": (1.0, 1.25, 1.5, 2.0, 3.0, 4.0),
        "max_concurrent": (1, 2, 3, 4, 5),
        "max_hold": (24, 48, 96, 168),
        "allow_short": (True, False),
        "cooldown": (1, 4, 12, 24),
        "dd_halt": (0.03, 0.045, 0.055, 9.0),
    }
    n = len(wins)
    out = {}
    for ax, vals in axes.items():
        print(f"  {ax}")
        out[ax] = []
        for v in vals:
            p, b, m = score(replace(base, **{ax: v}))
            mark = "  <-- base" if getattr(base, ax) == v else ""
            print(f"    {str(v):<8} pass {p:>2}/{n}  breach {b:>2}/{n}  "
                  f"med {m:>7.2f}%{mark}")
            out[ax].append({"value": v, "pass": p, "breach": b, "median_ret": m})
    (HERE / "stability_results.json").write_text(
        json.dumps({"base": base.__dict__, "windows": n, "axes": out}, indent=1),
        encoding="utf-8")
    print(f"\n-> {HERE / 'stability_results.json'}")


def subset(D, keep: list[int]) -> dict:
    out = dict(D)
    for f in ("open", "high", "low", "close", "atr", "valid"):
        out[f] = D[f][:, keep]
    out["lev"] = D["lev"][keep]
    out["cols"] = [D["cols"][i] for i in keep]
    return out


def cmd_crosscheck():
    """Is the result an edge, or one lucky coin?

    Study #10 in the 1m repo reported a profit factor of 2.93 that became 0.91
    once the two best pairs were dropped. Same test here: rerun the winner with
    each asset removed in turn, then on random halves of the universe. An edge
    survives losing any one name. A lottery ticket does not.
    """
    rules, D = Rules(), prep(load())
    base = Cfg(**json.loads(
        (HERE / "walkforward_results.json").read_text(encoding="utf-8"))[0]["cfg"])
    wins = windows(D)
    n_w = len(wins)
    print(f"base: {describe(base)}\n")

    def score(Dx):
        sig = SIGNALS[base.strat](Dx, base)
        rs = [simulate(Dx, sig, base, rules, lo, hi) for lo, hi in wins]
        return (sum(r["hit_target"] for r in rs),
                sum(r["breached"] for r in rs),
                float(np.median([r["return_pct"] for r in rs])))

    full = score(D)
    print(f"  full universe ({len(D['cols'])} assets): "
          f"pass {full[0]}/{n_w}  breach {full[1]}/{n_w}  med {full[2]:.2f}%\n")

    print("  leave-one-asset-out — the 8 removals that hurt most:")
    loo = []
    for i, name in enumerate(D["cols"]):
        keep = [j for j in range(len(D["cols"])) if j != i]
        loo.append((name, *score(subset(D, keep))))
    loo.sort(key=lambda x: x[1])
    for name, p, b, m in loo[:8]:
        print(f"    without {name:<8} pass {p:>2}/{n_w}  breach {b:>2}/{n_w}  "
              f"med {m:>7.2f}%   ({p - full[0]:+d} pass)")

    rng = np.random.default_rng(0)
    m_all = len(D["cols"])
    print("\n  random halves — an edge should appear in both:")
    halves = []
    for s in range(10):
        perm = rng.permutation(m_all)
        a, b_ = sorted(perm[:m_all // 2].tolist()), sorted(perm[m_all // 2:].tolist())
        pa, ba, ma = score(subset(D, a))
        pb, bb, mb = score(subset(D, b_))
        halves += [(pa, ba), (pb, bb)]
        print(f"    split {s}:  A pass {pa:>2}/{n_w} breach {ba:>2}/{n_w} "
              f"med {ma:>7.2f}%   |   B pass {pb:>2}/{n_w} breach {bb:>2}/{n_w} "
              f"med {mb:>7.2f}%")
    hp = [h[0] for h in halves]
    print(f"\n  half-universe pass rate: median {np.median(hp) / n_w:.0%}, "
          f"range {min(hp) / n_w:.0%}-{max(hp) / n_w:.0%}   "
          f"(full universe {full[0] / n_w:.0%})")
    json.dump({"full": full, "leave_one_out": loo, "halves": halves},
              open(HERE / "crosscheck_results.json", "w"), indent=1)
    print(f"\n-> {HERE / 'crosscheck_results.json'}")


def cmd_marathon():
    """Drop the 30-day deadline and ask the question the rules actually ask.

    Propr's 1-step rules set a profit target and two walls. They do not set a
    time limit. That changes the objective completely: you do not need +10% in
    a month, you need +10% EVENTUALLY without ever touching a wall. Small size
    and patience beat leverage, if -- and only if -- expectancy is positive.

    Reported on the full history and on each half, because a config that only
    works in one half worked in one regime, not in general.
    """
    rules, D = Rules(), prep(load())
    n = len(D["index"])
    half = n // 2
    spans = [("full 200d", 0, n), ("first half", 0, half), ("second half", half, n)]
    print(f"{n / 24:.0f} days, {len(D['cols'])} assets. No deadline: the run "
          f"ends at +10%, at a wall, or when history does.\n")

    cfgs = [replace(c, risk_pct=r, max_concurrent=mc, dd_halt=0.045)
            for c in grid()
            for r in (0.0025, 0.005, 0.0075)
            for mc in (1, 2)]
    rows, cache = [], {}
    for cfg in cfgs:
        kb = cfg.key()
        if kb not in cache:
            cache[kb] = SIGNALS[cfg.strat](D, cfg)
        rs = [simulate(D, cache[kb], cfg, rules, lo, hi) for _, lo, hi in spans]
        rows.append({"cfg": cfg.__dict__, "desc": describe(cfg),
                     "spans": {s[0]: r for s, r in zip(spans, rs)}})
    (HERE / "marathon_results.json").write_text(
        json.dumps(rows, indent=1), encoding="utf-8")

    def tag(r):
        return ("TARGET" if r["hit_target"] else
                r["outcome"] if r["breached"] else f"{r['return_pct']:+.1f}%")

    full = [r for r in rows if r["spans"]["full 200d"]["hit_target"]]
    clean = [r for r in full
             if not any(r["spans"][s]["breached"] for s, _, _ in spans)]
    print(f"  reached +10% over the full run:        {len(full)}/{len(rows)}")
    print(f"  ...and never breached in either half:  {len(clean)}/{len(rows)}\n")

    show = sorted(clean or full,
                  key=lambda r: r["spans"]["full 200d"]["days_to_outcome"] or 1e9)
    if not show:
        show = sorted(rows, key=lambda r: -r["spans"]["full 200d"]["return_pct"])
        print("  nothing reached the target; showing the best returns instead\n")
    print(f"{'config':<58} {'full 200d':>12} {'days':>6} {'1st half':>12} "
          f"{'2nd half':>12} {'trd':>5}")
    print("-" * 112)
    for r in show[:20]:
        f_, a, b = (r["spans"][s] for s, _, _ in spans)
        print(f"{r['desc']:<58} {tag(f_):>12} "
              f"{(f_['days_to_outcome'] or 0):>6.0f} {tag(a):>12} "
              f"{tag(b):>12} {f_['trades']:>5}")
    print(f"\n-> {HERE / 'marathon_results.json'}")


def cmd_persistence():
    """The only question that decides whether any of this is worth running.

    Selecting the configs that hit +10% last month is only useful if those
    configs then do better than configs picked at random. So: take the 421
    in-sample winners, take an equal number sampled blindly from the losers,
    and score both on the same out-of-sample windows. If the two distributions
    land on top of each other, last month's performance carried no information
    and the whole search was an expensive coin toss.
    """
    rules, D = Rules(), prep(load())
    res = json.loads((HERE / "search_results.json").read_text(encoding="utf-8"))
    oos, n = windows(D)[1:], None
    winners = [r for r in res if r["hit_target"]]
    losers = [r for r in res if not r["hit_target"]]
    rng = np.random.default_rng(0)
    picked = [losers[i] for i in
              rng.choice(len(losers), size=len(winners), replace=False)]

    cache = {}

    def evaluate(group):
        out = []
        for h in group:
            cfg = Cfg(**h["cfg"])
            kb = cfg.key()
            if kb not in cache:
                cache[kb] = SIGNALS[cfg.strat](D, cfg)
            rs = [simulate(D, cache[kb], cfg, rules, lo, hi) for lo, hi in oos]
            out.append((sum(r["hit_target"] for r in rs),
                        sum(r["breached"] for r in rs),
                        float(np.median([r["return_pct"] for r in rs]))))
        return out

    a, b = evaluate(winners), evaluate(picked)
    n = len(oos)
    print(f"out-of-sample: {n} windows, {len(a)} configs per group\n")
    print(f"{'':<34} {'picked on last month':>22} {'picked blind':>16}")
    print("-" * 74)
    for lab, i in (("median pass rate", 0), ("median breach rate", 1)):
        va = np.median([x[i] for x in a]) / n
        vb = np.median([x[i] for x in b]) / n
        print(f"{lab:<34} {va:>21.0%} {vb:>16.0%}")
    for lab, i in (("mean pass rate", 0), ("mean breach rate", 1)):
        va = np.mean([x[i] for x in a]) / n
        vb = np.mean([x[i] for x in b]) / n
        print(f"{lab:<34} {va:>21.0%} {vb:>16.0%}")
    va = np.median([x[2] for x in a])
    vb = np.median([x[2] for x in b])
    print(f"{'median window return':<34} {va:>20.2f}% {vb:>15.2f}%")
    print(f"{'configs that never breached':<34} "
          f"{sum(1 for x in a if x[1] == 0):>21} "
          f"{sum(1 for x in b if x[1] == 0):>16}")

    ia = np.array([r["return_pct"] for r in winners] +
                  [r["return_pct"] for r in picked])
    oa = np.array([x[0] for x in a] + [x[0] for x in b], float)
    rank = np.corrcoef(np.argsort(np.argsort(ia)), np.argsort(np.argsort(oa)))[0, 1]
    print(f"\n  rank correlation, in-sample return vs out-of-sample pass "
          f"count:  {rank:+.3f}")
    print("  (0.0 means last month's ranking told you nothing about next month)")
    json.dump({"winners": a, "blind": b, "rank_corr": float(rank)},
              open(HERE / "persistence_results.json", "w"), indent=1)
    print(f"\n-> {HERE / 'persistence_results.json'}")


def cmd_horizon():
    """Odds as a function of the deadline you actually have.

        python sim.py horizon [account_size]

    Every rule and knob in here is a percentage, so account size changes the
    dollar walls and nothing else -- it is passed only to price the $10
    minimum notional correctly.

    A shorter deadline forces more risk per trade, and more risk per trade
    breaches. This prints that trade-off instead of asserting it, so the
    config can be chosen against the real deadline rather than against the
    30-day one this study started with.
    """
    size = float(sys.argv[2]) if len(sys.argv) > 2 else 5000.0
    rules, D = Rules(start_equity=size), prep(load())
    print(f"account ${size:,.0f}  target ${size * rules.target_pct:,.0f}  "
          f"daily wall ${size * rules.daily_loss_pct:,.0f}  "
          f"dd floor ${rules.dd_floor:,.0f}\n")

    base = dict(strat="mom", lb=96, k=3, allow_short=False,
                sl_atr=2.0, tp_r=2.0, max_hold=72)
    cands = {f"risk {r:.2%}, {mc} slot{'s' if mc > 1 else ''}":
             Cfg(**base, risk_pct=r, max_concurrent=mc)
             for r, mc in ((0.005, 1), (0.005, 2), (0.0075, 2),
                           (0.01, 2), (0.015, 2), (0.02, 3))}
    horizons = (30, 45, 60, 90, 120, 150)
    cache = {}
    out = {}
    for name, cfg in cands.items():
        kb = cfg.key()
        if kb not in cache:
            cache[kb] = SIGNALS[cfg.strat](D, cfg)
        out[name] = {}
        for h in horizons:
            ws = windows(D, days=h)
            rs = [simulate(D, cache[kb], cfg, rules, lo, hi) for lo, hi in ws]
            out[name][h] = {
                "n": len(rs),
                "pass": sum(r["hit_target"] for r in rs),
                "breach": sum(r["breached"] for r in rs),
                "median_ret": float(np.median([r["return_pct"] for r in rs])),
            }

    for label, key in (("PASS  (reached +10% in time)", "pass"),
                       ("BREACH (hit a wall)", "breach")):
        print(f"  {label}")
        print(f"    {'config':<22}" +
              "".join(f"{str(h) + 'd':>10}" for h in horizons))
        print("    " + "-" * (22 + 10 * len(horizons)))
        for name in cands:
            cells = ""
            for h in horizons:
                v = out[name][h]
                cells += f"{v[key] / v['n']:>9.0%} "
            print(f"    {name:<22}{cells}")
        print()
    ns = [out[next(iter(cands))][h]["n"] for h in horizons]
    print("    windows per horizon: " +
          ", ".join(f"{h}d={n}" for h, n in zip(horizons, ns)) +
          "   (overlapping; ~200 days of history total)")
    (HERE / "horizon_results.json").write_text(
        json.dumps({"account_size": size, "grid": out}, indent=1),
        encoding="utf-8")
    print(f"\n-> {HERE / 'horizon_results.json'}")


def grid30() -> list[Cfg]:
    """Same signal families, but the drawdown self-halt is now searchable.

    Over 30 days a permanent halt at -4.5% ends the attempt. Whether that
    protection is worth its cost at this deadline is an empirical question,
    so 0.045 / 0.055 / off are all on the table and the objective decides.
    """
    risk = [dict(risk_pct=r, sl_atr=s, tp_r=tp, max_concurrent=mc,
                 dd_halt=dh, trail_r=tr)
            for r in (0.005, 0.0075, 0.01, 0.015)
            for s in (1.5, 2.5)
            for tp in (1.5, 3.0)
            for mc in (1, 2, 3)
            for dh in (0.045, 0.055, 9.0)
            for tr in (0.0, 1.0)]
    return [replace(c, **k) for c in grid() for k in risk]


def cmd_thirtyday():
    """Pick a config for a hard 30-day deadline, honestly.

    Train on the OLDER half of history, test on the NEWER half. Chronological,
    so nothing from the test period informs the choice. If configs that did
    well on months 1-3 also do well on months 4-6, that is an edge. If the
    train/test correlation is zero -- as it was for single-month selection --
    then no amount of searching produces a 30-day config worth running, and
    the honest output is the sturdiest risk setting rather than the best
    backtest.
    """
    rules, D = Rules(), prep(load())
    wins = windows(D, days=30)          # newest first
    test, train = wins[:len(wins) // 2], wins[len(wins) // 2:]
    print(f"train {len(train)} windows (older, "
          f"{D['index'][train[-1][0]].date()} -> {D['index'][train[0][1] - 1].date()})")
    print(f"test  {len(test)} windows (newer, "
          f"{D['index'][test[-1][0]].date()} -> {D['index'][test[0][1] - 1].date()})")

    cfgs = grid30()
    print(f"{len(cfgs)} configs\n")
    cache, rows = {}, []
    t0 = time.time()
    for i, cfg in enumerate(cfgs):
        kb = cfg.key()
        if kb not in cache:
            cache[kb] = SIGNALS[cfg.strat](D, cfg)
        rs = [simulate(D, cache[kb], cfg, rules, lo, hi) for lo, hi in train]
        rows.append({"cfg": cfg, "desc": describe(cfg),
                     "tr_pass": sum(r["hit_target"] for r in rs),
                     "tr_breach": sum(r["breached"] for r in rs)})
        if i % 2000 == 0 and i:
            print(f"  {i}/{len(cfgs)}  {time.time() - t0:.0f}s")

    for r in rows:
        rs = [simulate(D, cache[r["cfg"].key()], r["cfg"], rules, lo, hi)
              for lo, hi in test]
        r["te_pass"] = sum(x["hit_target"] for x in rs)
        r["te_breach"] = sum(x["breached"] for x in rs)

    ntr, nte = len(train), len(test)
    tr = np.array([r["tr_pass"] - r["tr_breach"] for r in rows], float)
    te = np.array([r["te_pass"] - r["te_breach"] for r in rows], float)
    rank = np.corrcoef(np.argsort(np.argsort(tr)),
                       np.argsort(np.argsort(te)))[0, 1]

    rows.sort(key=lambda r: (-(r["tr_pass"] - r["tr_breach"]), r["tr_breach"]))
    top = rows[:20]
    print(f"\n  top 20 by TRAIN score, and what they then did on TEST:")
    print(f"  {'config':<58} {'train':>12} {'TEST':>12}")
    print("  " + "-" * 84)
    for r in top:
        print(f"  {r['desc']:<58} "
              f"{r['tr_pass']:>3}/{ntr} p {r['tr_breach']:>2}/{ntr} b  "
              f"{r['te_pass']:>3}/{nte} p {r['te_breach']:>2}/{nte} b")

    all_p = np.mean([r["te_pass"] for r in rows]) / nte
    all_b = np.mean([r["te_breach"] for r in rows]) / nte
    top_p = np.mean([r["te_pass"] for r in top]) / nte
    top_b = np.mean([r["te_breach"] for r in top]) / nte
    print(f"\n  mean TEST pass   — top 20 by train: {top_p:.0%}   "
          f"all {len(rows)} configs: {all_p:.0%}")
    print(f"  mean TEST breach — top 20 by train: {top_b:.0%}   "
          f"all {len(rows)} configs: {all_b:.0%}")
    print(f"\n  rank correlation, train score vs test score: {rank:+.3f}")
    print("  (0.0 means choosing on 3 months of history tells you nothing "
          "about the next 3)")
    (HERE / "thirtyday_results.json").write_text(json.dumps(
        [{k: (v.__dict__ if k == "cfg" else v) for k, v in r.items()}
         for r in rows], indent=1), encoding="utf-8")
    print(f"\n-> {HERE / 'thirtyday_results.json'}")


def cmd_bold():
    """Bold play: few big bets instead of many small ones.

    With no edge, probability is conserved -- you cannot raise P(pass) without
    raising P(breach) by at least as much. But the conservative configs leave
    ~50% of the mass in "neither passed nor breached", which a hard deadline
    scores as a loss anyway. Bold play converts that dead mass into attempts.

    The reference point is a driftless walk from 0 that stops at +10% or -6%:
    it reaches the target first 6/16 = 37.5% of the time. Fees and the
    strategy's own negative drift are what a real config loses against that
    ceiling, so the gap below 37.5% measures the cost of churn.
    """
    rules, D = Rules(), prep(load())
    wins = windows(D, days=30)
    n = len(wins)
    print(f"{n} thirty-day windows.  driftless benchmark: 37.5% pass / "
          f"62.5% breach\n")
    print(f"  {'risk/trade':>10} {'tp':>5} {'slots':>6} {'halt':>6} "
          f"{'pass':>7} {'breach':>7} {'neither':>8} {'trades':>7}")
    print("  " + "-" * 64)
    out = []
    for risk, mc in ((0.01, 1), (0.02, 1), (0.03, 1), (0.045, 1),
                     (0.055, 1), (0.03, 2), (0.055, 2)):
        for tp in (1.5, 2.0, 3.0):
            cfg = Cfg(strat="mom", lb=96, k=3, allow_short=False,
                      risk_pct=risk, sl_atr=2.0, tp_r=tp, max_concurrent=mc,
                      max_hold=72, dd_halt=9.0, daily_halt=9.0)
            sig = SIGNALS[cfg.strat](D, cfg)
            rs = [simulate(D, sig, cfg, rules, lo, hi) for lo, hi in wins]
            p = sum(r["hit_target"] for r in rs)
            b = sum(r["breached"] for r in rs)
            out.append({"risk": risk, "tp": tp, "slots": mc,
                        "pass": p, "breach": b, "n": n})
            print(f"  {risk:>9.1%} {tp:>5.1f} {mc:>6} {'off':>6} "
                  f"{p / n:>6.0%} {b / n:>6.0%} {(n - p - b) / n:>7.0%} "
                  f"{np.median([r['trades'] for r in rs]):>7.0f}")
    best = max(out, key=lambda r: r["pass"])
    print(f"\n  best pass rate: {best['pass'] / n:.0%} at risk "
          f"{best['risk']:.1%}, tp {best['tp']}, {best['slots']} slot(s) "
          f"— breaching {best['breach'] / n:.0%}")
    (HERE / "bold_results.json").write_text(json.dumps(out, indent=1),
                                            encoding="utf-8")
    print(f"\n-> {HERE / 'bold_results.json'}")


def cmd_maker():
    """Does passive entry beat crossing the spread?

    Two effects pull opposite ways. Maker execution may pay a lower fee, and
    it always fills at a better price than the open. But it only fills when
    price comes BACK to you, so a momentum signal that runs immediately is
    never taken -- and those are the winners. Lowering the fee without
    modelling the missed fills would manufacture an improvement out of
    nothing, which is the error this whole study exists to avoid.

    Propr's own config calls its 0.075% "taker/maker", so the same-rate row is
    the honest default; the cheaper rows are what a real maker rebate would
    buy, shown separately.
    """
    D = prep(load())
    wins = windows(D, days=30)
    n = len(wins)
    base = dict(strat="mom", lb=96, k=3, allow_short=False, risk_pct=0.01,
                sl_atr=2.0, tp_r=2.0, max_concurrent=2, max_hold=72)
    sig = SIGNALS["mom"](D, Cfg(**base))

    def score(cfg, rules):
        rs = [simulate(D, sig, cfg, rules, lo, hi) for lo, hi in wins]
        return {"pass": sum(r["hit_target"] for r in rs),
                "breach": sum(r["breached"] for r in rs),
                "med": float(np.median([r["return_pct"] for r in rs])),
                "trades": float(np.median([r["trades"] for r in rs])),
                "fill": float(np.mean([r["fill_rate"] for r in rs]))}

    b = score(Cfg(**base), Rules())
    print(f"{n} thirty-day windows\n")
    print(f"  TAKER baseline (crosses the spread, 0.075%/side)")
    print(f"    pass {b['pass']}/{n} ({b['pass'] / n:.0%})   "
          f"breach {b['breach']}/{n} ({b['breach'] / n:.0%})   "
          f"median {b['med']:+.2f}%   trades {b['trades']:.0f}\n")

    rows = []
    for fm, lab in ((0.00075, "same 0.075% (Propr's stated rate)"),
                    (0.00015, "0.015% (Hyperliquid maker)"),
                    (0.00000, "0% (full rebate, best case)")):
        print(f"  MAKER, fee {lab}")
        print(f"    {'offset':>7} {'patience':>9} {'fill':>6} {'trades':>7} "
              f"{'pass':>7} {'breach':>8} {'median':>8}")
        for off in (0.1, 0.25, 0.5):
            for pat in (2, 4, 8):
                cfg = Cfg(**base, maker=True, maker_offset=off,
                          maker_patience=pat)
                s = score(cfg, Rules(fee_maker=fm))
                rows.append({"fee_maker": fm, "offset": off, "patience": pat,
                             **s, "n": n})
                print(f"    {off:>7.2f} {pat:>9} {s['fill']:>5.0%} "
                      f"{s['trades']:>7.0f} {s['pass'] / n:>6.0%} "
                      f"{s['breach'] / n:>7.0%} {s['med']:>7.2f}%")
        print()

    best = max(rows, key=lambda r: r["pass"] - r["breach"])
    print(f"  best maker variant: offset {best['offset']} ATR, patience "
          f"{best['patience']}h, fee {best['fee_maker']:.4%} -> "
          f"pass {best['pass'] / n:.0%}, breach {best['breach'] / n:.0%}")
    print(f"  taker baseline:                                        "
          f"      pass {b['pass'] / n:.0%}, breach {b['breach'] / n:.0%}")
    beat = sum(1 for r in rows if r["pass"] - r["breach"] > b["pass"] - b["breach"])
    print(f"\n  maker variants beating taker on (pass - breach): "
          f"{beat}/{len(rows)}")
    at_propr = [r for r in rows if r["fee_maker"] == 0.00075]
    beat_p = sum(1 for r in at_propr
                 if r["pass"] - r["breach"] > b["pass"] - b["breach"])
    print(f"  ...restricted to Propr's actual stated fee:      "
          f"{beat_p}/{len(at_propr)}")
    (HERE / "maker_results.json").write_text(
        json.dumps({"taker": b, "maker": rows}, indent=1), encoding="utf-8")
    print(f"\n-> {HERE / 'maker_results.json'}")


# --------------------------------------------------------------------------
def selftest():
    """Synthetic paths where the right answer is known by construction."""
    rules = Rules(start_equity=5000.0, fee=0.0, funding_8h=0.0)
    n, m = 300, 2
    idx = pd.date_range("2026-01-01", periods=n, freq="1h", tz="UTC")

    def mk(close):
        C = np.tile(np.asarray(close, float)[:, None], (1, m))
        return {"open": C.copy(), "high": C.copy(), "low": C.copy(),
                "close": C, "atr": np.full((n, m), 1.0),
                "valid": np.ones((n, m), bool), "index": idx,
                "cols": ["A", "B"], "dayord": np.array([d.toordinal()
                                                        for d in idx.date]),
                "lev": np.full(m, 2.0)}

    # a straight ramp with a 2 ATR stop and 2R target must take the target
    D = mk(100 + np.arange(n) * 0.5)
    sig = np.zeros((n, m), np.int8); sig[10, 0] = 1
    r = simulate(D, sig, Cfg(sl_atr=2.0, tp_r=2.0, max_concurrent=1,
                             cooldown=10**6), rules)
    assert r["trades"] == 1 and r["win_rate"] == 100, r

    # a 10% drop one bar after entry gaps straight through the 2-ATR stop, so
    # the loss is the gap, not the planned 1% -- that is the point of the fill
    # rule, and a sim that returns exactly -1% here is lying.
    D = mk(np.r_[np.full(20, 100.0), np.full(n - 20, 90.0)])
    sig = np.zeros((n, m), np.int8); sig[18, 0] = 1
    r = simulate(D, sig, Cfg(risk_pct=0.01, sl_atr=2.0, tp_r=0,
                             max_concurrent=1, cooldown=10**6), rules)
    assert r["trades"] == 1 and r["return_pct"] < -4.0, r

    # same shape, no gap: price walks down through the stop, loss == risk_pct
    D = mk(np.r_[np.full(20, 100.0), np.full(n - 20, 97.5)])
    D["open"][20:, :] = 99.0
    sig = np.zeros((n, m), np.int8); sig[18, 0] = 1
    r = simulate(D, sig, Cfg(risk_pct=0.01, sl_atr=2.0, tp_r=0,
                             max_concurrent=1, cooldown=10**6), rules)
    assert abs(r["return_pct"] + 1.0) < 0.02, r

    # a big loss inside one day with the bot's halts disabled is a DAILY
    # breach -- it must not be misreported as a drawdown breach
    D = mk(np.r_[np.full(20, 100.0), np.full(n - 20, 60.0)])
    sig = np.zeros((n, m), np.int8); sig[18, 0] = 1
    r = simulate(D, sig, Cfg(risk_pct=0.05, sl_atr=2.0, tp_r=0, trail_r=0,
                             max_concurrent=1, daily_halt=9, dd_halt=9), rules)
    assert r["outcome"] == "BREACH_DAILY", r

    # fees alone must show up: 100 round trips at 7.5bp/side is ~-1.5% of
    # the traded notional, so a flat market cannot return zero
    D = mk(np.full(n, 100.0))
    sig = np.zeros((n, m), np.int8); sig[::4, 0] = 1
    r = simulate(D, sig, Cfg(sl_atr=2.0, tp_r=0, max_hold=2, cooldown=1,
                             max_concurrent=1), Rules(fee=0.00075,
                                                      funding_8h=0.0))
    assert r["return_pct"] < 0 and r["trades"] > 30, r

    # shorts must make money when price falls
    D = mk(np.r_[np.full(20, 100.0), np.full(n - 20, 95.0)])
    sig = np.zeros((n, m), np.int8); sig[18, 0] = -1
    r = simulate(D, sig, Cfg(risk_pct=0.01, sl_atr=2.0, tp_r=0, max_hold=10,
                             max_concurrent=1, cooldown=10**6), rules)
    assert r["trades"] == 1 and r["return_pct"] > 2.0, r

    # --- maker execution -------------------------------------------------
    MK = dict(strat="mom", sl_atr=2.0, tp_r=0, max_concurrent=1,
              cooldown=10**6, max_hold=10**6, maker=True,
              maker_offset=0.5, maker_patience=4)
    free = Rules(fee=0.0, fee_maker=0.0, funding_8h=0.0)

    # price only ever rises: a limit resting BELOW never fills, so no trade
    # and no fee. Missing the runaway winners is the cost of being passive.
    D = mk(np.r_[np.full(20, 100.0), np.full(n - 20, 108.0)])
    sig = np.zeros((n, m), np.int8); sig[18, 0] = 1
    r = simulate(D, sig, Cfg(**MK), free)
    assert r["trades"] == 0 and r["fill_rate"] == 0.0 and \
        abs(r["return_pct"]) < 1e-9, r

    # price dips to the limit, then rallies: fills at the limit, which beats
    # the open a taker would have paid on the same signal
    D = mk(np.r_[np.full(20, 100.0), [99.0], np.full(n - 21, 104.0)])
    sig = np.zeros((n, m), np.int8); sig[18, 0] = 1
    r = simulate(D, sig, Cfg(**{**MK, "max_hold": 40}), free)
    taker = simulate(D, sig, Cfg(**{**MK, "max_hold": 40, "maker": False}),
                     free)
    assert r["trades"] == 1 and r["fill_rate"] == 1.0, r
    assert r["return_pct"] > taker["return_pct"], (r, taker)

    # patience expires before price comes back -> cancelled, counted missed
    D = mk(np.r_[np.full(20, 100.0), np.full(10, 101.0), np.full(n - 30, 90.0)])
    sig = np.zeros((n, m), np.int8); sig[18, 0] = 1
    r = simulate(D, sig, Cfg(**{**MK, "maker_patience": 2}), free)
    assert r["trades"] == 0 and r["fill_rate"] == 0.0, r

    print("selftest ok")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "selftest"
    {"selftest": selftest, "fetch": lambda: fetch_all(),
     "search": cmd_search, "walkforward": cmd_walkforward,
     "stability": cmd_stability, "crosscheck": cmd_crosscheck, "marathon": cmd_marathon, "persistence": cmd_persistence, "horizon": cmd_horizon, "thirtyday": cmd_thirtyday, "bold": cmd_bold, "maker": cmd_maker}[cmd]()

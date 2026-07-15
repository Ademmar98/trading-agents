"""Phase 1b — put the firm's OWN signal cores on trial, honestly.

The classic battery is dead (analysis/strategy_expectancy.py: 0/24). The firm
now leans on scalp15 (EMA/MACD/RSI across 1m-4h) and the swing desk (daily
structure + 4h alignment). Do THEY clear the cost floor? Same honesty rules,
but each setup is exited on ITS OWN stop/target geometry (not an imposed 2R):

    entry = next bar open;  stop/target = the signal's own sl_pct/tp_pct;
    intrabar STOP resolves before TARGET;  costs on every trade;  causal regime.

Finding #1 from the classic run: a 0.16% round-trip cost is ~1/3 R on tight
15m stops. So the key question per core is whether its edge (if any) survives
that cost — and swing's wider stops should suffer far less cost drag.

Run:  python analysis/scalp_swing_expectancy.py
"""
import os
import sys
import json
import math
import time
from datetime import datetime, timezone

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.scalp15 import scalp_signal
from core.swing import swing_signal
from core.regime import detect_regime

SYMBOLS = [s for s in os.getenv(
    "EXP_SYMBOLS",
    "BTC/USD,ETH/USD,SOL/USD,XRP/USD,DOT/USD,AVAX/USD,LINK/USD,UNI/USD,LTC/USD,BCH/USD"
).split(",") if s.strip()]

SCALP_TFS = os.getenv("EXP_SCALP_TFS", "5m,15m,30m,1h,4h").split(",")
SCALP_BARS = int(os.getenv("EXP_SCALP_BARS", "5000"))
WARMUP = 120
STRAT_WINDOW = 130
MAX_HOLD_SCALP = 48        # intraday time-exit
REGIME_WINDOW = 150

# Swing
SWING_DAILY_BARS = 900     # ~2.5y of daily
SWING_4H_BARS = 4000
WARMUP_D = 60
MAX_HOLD_SWING = 30        # daily bars (~1 month)

FEE_PCT = float(os.getenv("EXP_FEE_PCT", "0.0"))
SPREAD_PCT = float(os.getenv("EXP_SPREAD_PCT", "0.05"))
SLIP_PCT = float(os.getenv("EXP_SLIP_PCT", "0.03"))
ROUND_TRIP_COST = 2 * (FEE_PCT + SPREAD_PCT + SLIP_PCT) / 100.0

MIN_MEANINGFUL_N = 150

_TF_MS = {"1m": 60, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "4h": 14400}


def _bsym(symbol):
    s = symbol.replace("/", "").upper()
    return s + "T" if s.endswith("USD") and not s.endswith("USDT") else s


def fetch_deep(symbol, interval, total):
    out, end = [], None
    while len(out) < total:
        params = {"symbol": _bsym(symbol), "interval": interval, "limit": 1000}
        if end:
            params["endTime"] = end
        try:
            data = requests.get("https://api.binance.com/api/v3/klines",
                                params=params, timeout=25).json()
        except Exception:
            break
        if not isinstance(data, list) or not data:
            break
        out = [{"open": float(k[1]), "high": float(k[2]), "low": float(k[3]),
                "close": float(k[4]), "volume": float(k[5]), "ts": k[0] // 1000}
               for k in data] + out
        end = data[0][0] - 1
        if len(data) < 1000:
            break
        time.sleep(0.12)
    return out[-total:]


def _resolve(bars, i, entry, sl_pct, tp_pct, max_hold):
    """Walk forward from entry (bar i+1) to first stop/target hit (stop first),
    else time-exit at close. Returns net R and net % after costs."""
    stop = entry * (1 - sl_pct / 100.0)
    target = entry * (1 + tp_pct / 100.0)
    risk_frac = sl_pct / 100.0
    n = len(bars)
    end = min(i + 1 + max_hold, n - 1)
    exit_price, r_mult = None, None
    for j in range(i + 1, end + 1):
        if bars[j]["low"] <= stop:
            exit_price, r_mult = stop, -1.0
            break
        if bars[j]["high"] >= target:
            exit_price, r_mult = target, tp_pct / sl_pct
            break
    if exit_price is None:
        exit_price = bars[end]["close"]
        r_mult = ((exit_price - entry) / entry) / risk_frac if risk_frac else 0
    net_frac = (exit_price - entry) / entry - ROUND_TRIP_COST
    net_r = r_mult - ROUND_TRIP_COST / risk_frac if risk_frac else r_mult
    return net_r, net_frac * 100.0


def _regime_at(bars, i):
    r = detect_regime(bars[max(0, i - REGIME_WINDOW):i + 1])
    return r.get("regime", "unknown") if isinstance(r, dict) else (r or "unknown")


def run_scalp():
    buckets = {f"scalp_{tf}": [] for tf in SCALP_TFS}
    for si, sym in enumerate(SYMBOLS, 1):
        for tf in SCALP_TFS:
            bars = fetch_deep(sym, tf, SCALP_BARS)
            if len(bars) < WARMUP + 100:
                continue
            i = WARMUP
            tag = f"scalp_{tf}"
            while i < len(bars) - 2:
                window = bars[i - STRAT_WINDOW:i + 1] if i >= STRAT_WINDOW else bars[:i + 1]
                try:
                    sig = scalp_signal(sym, regime=None, ohlc=window, timeframe=tf)
                except Exception:
                    sig = None
                if not sig or sig.get("action") != "BUY" or not sig.get("sl_pct"):
                    i += 1
                    continue
                entry = bars[i + 1]["open"]
                if not entry:
                    i += 1
                    continue
                net_r, net_pct = _resolve(bars, i, entry, sig["sl_pct"], sig["tp_pct"], MAX_HOLD_SCALP)
                buckets[tag].append({
                    "net_r": net_r, "net_pct": net_pct, "regime": _regime_at(bars, i),
                    "day": datetime.fromtimestamp(bars[i + 1]["ts"], tz=timezone.utc).strftime("%Y-%m-%d"),
                })
                i += MAX_HOLD_SCALP // 4 + 1  # coarse skip past the hold
        print(f"  scalp [{si}/{len(SYMBOLS)}] {sym} done", flush=True)
    return buckets


def run_swing():
    trades = []
    for si, sym in enumerate(SYMBOLS, 1):
        d1 = fetch_deep(sym, "1d", SWING_DAILY_BARS)
        h4 = fetch_deep(sym, "4h", SWING_4H_BARS)
        if len(d1) < WARMUP_D + 40 or len(h4) < 60:
            print(f"  swing [{si}/{len(SYMBOLS)}] {sym}: thin data, skipped", flush=True)
            continue
        i = WARMUP_D
        while i < len(d1) - 2:
            d_ts = d1[i]["ts"]
            h4_win = [b for b in h4 if b["ts"] <= d_ts][-200:]
            d_win = d1[max(0, i - 200):i + 1]
            try:
                sig = swing_signal(sym, d_win, h4_win, regime=None)
            except Exception:
                sig = None
            if not sig or not sig.get("sl_pct"):
                i += 1
                continue
            entry = d1[i + 1]["open"]
            if not entry:
                i += 1
                continue
            net_r, net_pct = _resolve(d1, i, entry, sig["sl_pct"], sig["tp_pct"], MAX_HOLD_SWING)
            trades.append({
                "net_r": net_r, "net_pct": net_pct, "strategy": sig["strategy"],
                "regime": _regime_at(d1, i),
                "day": datetime.fromtimestamp(d1[i + 1]["ts"], tz=timezone.utc).strftime("%Y-%m-%d"),
            })
            i += 2
        print(f"  swing [{si}/{len(SYMBOLS)}] {sym} done", flush=True)
    return trades


def stats(rows):
    n = len(rows)
    if n == 0:
        return None
    rs = [t["net_r"] for t in rows]
    mean_r = sum(rs) / n
    wins = sum(1 for t in rows if t["net_pct"] > 0)
    if n > 1:
        sd = math.sqrt(sum((x - mean_r) ** 2 for x in rs) / (n - 1))
        se = sd / math.sqrt(n)
    else:
        sd = se = 0.0
    t = mean_r / se if se > 0 else 0.0
    return {"n": n, "win_rate": wins / n * 100, "mean_r": mean_r,
            "mean_pct": sum(t["net_pct"] for t in rows) / n, "t": t,
            "ci_lo": mean_r - 1.96 * se, "ci_hi": mean_r + 1.96 * se,
            "low_sample": n < MIN_MEANINGFUL_N}


def report(title, groups):
    print(f"\n{'='*96}\n{title}")
    print(f"{'signal':16s} {'n':>6s} {'win%':>6s} {'avgR':>7s} {'net%/t':>7s} "
          f"{'t-stat':>7s} {'95% CI (R)':>18s}  verdict")
    print("=" * 96)
    out = []
    for name, rows in groups.items():
        s = stats(rows)
        if not s:
            print(f"{name:16s}   (no trades)")
            continue
        edge = s["mean_r"] > 0 and s["ci_lo"] > 0 and not s["low_sample"]
        verdict = "KEEP" if edge else ("NOISE" if s["low_sample"] else "cut")
        print(f"{name:16s} {s['n']:6d} {s['win_rate']:5.1f}% {s['mean_r']:+7.3f} "
              f"{s['mean_pct']:+7.3f} {s['t']:+7.2f} "
              f"[{s['ci_lo']:+.3f},{s['ci_hi']:+.3f}]  {verdict}")
        # per-regime
        by = {}
        for r in rows:
            by.setdefault(r["regime"], []).append(r)
        parts = [f"{reg}:{stats(v)['mean_r']:+.2f}R(n{len(v)})"
                 for reg, v in sorted(by.items()) if len(v) >= 30]
        if parts:
            print(f"    regimes: {'  '.join(parts)}")
        out.append({"signal": name, **s})
    return out


def main():
    print(f"Phase 1b — scalp15 + swing on trial | {len(SYMBOLS)} symbols")
    print(f"Costs: round-trip {ROUND_TRIP_COST*100:.3f}%\n")
    scalp_groups = run_scalp()
    swing_trades = run_swing()
    swing_groups = {}
    for t in swing_trades:
        swing_groups.setdefault(t["strategy"], []).append(t)
    swing_groups["swing_ALL"] = swing_trades

    r1 = report("SCALP15 STACK (own ATR stop / win-rate-matrix target)", scalp_groups)
    r2 = report("SWING DESK (own daily-ATR stop / 3R target)", swing_groups)

    dst = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scalp_swing_results.json")
    with open(dst, "w") as f:
        json.dump({"generated_at_utc": datetime.now(timezone.utc).isoformat(),
                   "scalp": r1, "swing": r2,
                   "cost_pct": ROUND_TRIP_COST * 100}, f, indent=2)
    print(f"\nSaved -> {dst}")
    print(f"Total scalp trades: {sum(len(v) for v in scalp_groups.values())} | "
          f"swing trades: {len(swing_trades)}")


if __name__ == "__main__":
    main()

"""Phase 1 — prove or kill the edge, per strategy, on deep out-of-sample data.

For every strategy in core/strategies.py, on real Binance 15m history
(paginated well past the live 300/1000-bar cap), simulate its BUY signals
independently with an HONEST fill and cost model, then report per strategy
(and per regime):

    trade count, win rate, average R, net expectancy per trade AFTER
    fees + spread + slippage, a 95% confidence interval, a t-stat, and a
    low-sample flag (< MIN_MEANINGFUL_N trades = noise).

Then: cluster strategies by return correlation (expose "different" strategies
that are the same bet), and apply a multiple-comparison reality check so a
lucky survivor among 28 tests isn't mistaken for edge.

Honesty rules (avoid the traps the audit flagged):
- Entry fills at the NEXT bar's open — never the signal bar's close, never a
  forming candle.
- Intrabar exits resolve STOP before TARGET (pessimistic).
- Costs are subtracted from every trade.
- Regime is labelled causally from data up to the entry bar only.
- Nothing is parameter-fitted here, so every bar is out-of-sample; the real
  risk is multiple testing across 28 strategies, handled explicitly below.

Run:  python analysis/strategy_expectancy.py
Env:  EXP_BARS (default 6000 15m bars ~= 62 days), EXP_TF (default 15m)
"""
import os
import sys
import json
import math
import time
from datetime import datetime, timezone

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.strategies import ALL_STRATEGIES
from core.regime import detect_regime
from core.indicators import atr as atr_last

# ── Universe & knobs ──
SYMBOLS = [s for s in os.getenv(
    "EXP_SYMBOLS",
    "BTC/USD,ETH/USD,SOL/USD,BNB/USD,XRP/USD,ADA/USD,DOGE/USD,DOT/USD,"
    "AVAX/USD,LINK/USD,UNI/USD,ATOM/USD,LTC/USD,BCH/USD,TRX/USD,AAVE/USD"
).split(",") if s.strip()]
TF = os.getenv("EXP_TF", "15m")
BARS = int(os.getenv("EXP_BARS", "6000"))

# Exit model
ATR_SL_MULT = 1.5          # stop = 1.5 x ATR(14) -> defines 1R
TARGET_R = 2.0             # take-profit at +2R
MAX_HOLD = 48              # bars before time-exit (15m x 48 = 12h)
WARMUP = 210               # bars before first trade (indicator warm-up)
STRAT_WINDOW = 200         # trailing bars handed to each detector (causal, bounded)
REGIME_WINDOW = 150        # trailing bars for the causal regime label

# Honest cost model (production runs commission-free; cost lives in spread+slip)
FEE_PCT = float(os.getenv("EXP_FEE_PCT", "0.0"))
SPREAD_PCT = float(os.getenv("EXP_SPREAD_PCT", "0.05"))     # half-spread per side
SLIP_PCT = float(os.getenv("EXP_SLIP_PCT", "0.03"))        # slippage per side
ROUND_TRIP_COST = 2 * (FEE_PCT + SPREAD_PCT + SLIP_PCT) / 100.0  # fraction of notional

MIN_MEANINGFUL_N = 200     # fewer trades than this = statistical noise
CORR_CLUSTER = 0.60        # daily-return correlation above this = "same bet"


def _bsym(symbol):
    s = symbol.replace("/", "").upper()
    if s.endswith("USD") and not s.endswith("USDT"):
        s += "T"
    return s


def fetch_deep(symbol, interval, total):
    """Paginate Binance klines backward via endTime to get `total` bars."""
    out = []
    end = None
    while len(out) < total:
        params = {"symbol": _bsym(symbol), "interval": interval, "limit": 1000}
        if end:
            params["endTime"] = end
        try:
            r = requests.get("https://api.binance.com/api/v3/klines", params=params, timeout=25)
            data = r.json()
        except Exception:
            break
        if not isinstance(data, list) or not data:
            break
        batch = [{
            "open": float(k[1]), "high": float(k[2]), "low": float(k[3]),
            "close": float(k[4]), "volume": float(k[5]), "ts": k[0] // 1000,
        } for k in data]
        out = batch + out
        end = data[0][0] - 1
        if len(data) < 1000:
            break
        time.sleep(0.15)  # be polite to the rate limiter
    return out[-total:]


def atr_series(bars, period=14):
    """ATR(14) at each bar (Wilder), causal. None until warmed up."""
    n = len(bars)
    out = [None] * n
    if n < period + 1:
        return out
    trs = []
    for i in range(1, n):
        h, l, pc = bars[i]["high"], bars[i]["low"], bars[i - 1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    a = sum(trs[:period]) / period
    out[period] = a
    for i in range(period, len(trs)):
        a = (a * (period - 1) + trs[i]) / period
        out[i + 1] = a
    return out


def simulate_strategy(symbol, bars, atrs, fn):
    """Walk bars; every BUY signal opens one trade (flat-only). Returns a list
    of trade dicts with net R and net pnl% after costs, plus entry regime."""
    trades = []
    n = len(bars)
    i = WARMUP
    while i < n - 2:
        window = bars[i - STRAT_WINDOW:i + 1] if i >= STRAT_WINDOW else bars[:i + 1]
        try:
            sig = fn(window)
        except Exception:
            sig = None
        if not sig or sig.get("action") != "BUY":
            i += 1
            continue
        a = atrs[i]
        if not a or a <= 0:
            i += 1
            continue
        entry = bars[i + 1]["open"]           # next-bar-open fill
        if not entry or entry <= 0:
            i += 1
            continue
        risk = ATR_SL_MULT * a
        stop = entry - risk
        target = entry + TARGET_R * risk
        exit_price, r_mult, exit_j = None, None, None
        end = min(i + 1 + MAX_HOLD, n - 1)
        for j in range(i + 1, end + 1):
            lo, hi = bars[j]["low"], bars[j]["high"]
            if lo <= stop:                     # stop checked first (pessimistic)
                exit_price, r_mult, exit_j = stop, -1.0, j
                break
            if hi >= target:
                exit_price, r_mult, exit_j = target, TARGET_R, j
                break
        if exit_price is None:                 # time-exit at close
            exit_j = end
            exit_price = bars[end]["close"]
            r_mult = (exit_price - entry) / risk
        gross_frac = (exit_price - entry) / entry
        net_frac = gross_frac - ROUND_TRIP_COST
        net_r = r_mult - ROUND_TRIP_COST * entry / risk
        # causal regime at entry
        rwin = bars[max(0, i - REGIME_WINDOW):i + 1]
        reg = detect_regime(rwin)
        regime = reg.get("regime", "unknown") if isinstance(reg, dict) else (reg or "unknown")
        trades.append({
            "symbol": symbol, "entry_ts": bars[i + 1]["ts"],
            "net_r": net_r, "net_pct": net_frac * 100.0,
            "regime": regime,
            "day": datetime.fromtimestamp(bars[i + 1]["ts"], tz=timezone.utc).strftime("%Y-%m-%d"),
        })
        i = exit_j + 1                          # resume after the exit
    return trades


def stats(rows):
    """n, win%, mean net R, mean net %, std, t-stat, 95% CI on mean R."""
    n = len(rows)
    if n == 0:
        return None
    rs = [t["net_r"] for t in rows]
    pcts = [t["net_pct"] for t in rows]
    mean_r = sum(rs) / n
    mean_pct = sum(pcts) / n
    wins = sum(1 for x in pcts if x > 0)
    if n > 1:
        var = sum((x - mean_r) ** 2 for x in rs) / (n - 1)
        sd = math.sqrt(var)
        se = sd / math.sqrt(n)
    else:
        sd = se = 0.0
    t = mean_r / se if se > 0 else 0.0
    ci = 1.96 * se
    return {
        "n": n, "win_rate": wins / n * 100, "mean_r": mean_r, "mean_pct": mean_pct,
        "sd": sd, "t": t, "ci_lo": mean_r - ci, "ci_hi": mean_r + ci,
        "low_sample": n < MIN_MEANINGFUL_N,
    }


def pearson(a, b):
    keys = sorted(set(a) & set(b))
    if len(keys) < 10:
        return None
    xs = [a[k] for k in keys]
    ys = [b[k] for k in keys]
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    dy = math.sqrt(sum((y - my) ** 2 for y in ys))
    return num / (dx * dy) if dx > 0 and dy > 0 else None


def main():
    print(f"Phase 1 expectancy study — {len(SYMBOLS)} symbols x {len(ALL_STRATEGIES)} "
          f"strategies, {BARS} {TF} bars each")
    print(f"Costs: round-trip {ROUND_TRIP_COST*100:.3f}% (fee {FEE_PCT}% + spread "
          f"{SPREAD_PCT}% + slip {SLIP_PCT}% per side)\n")

    all_trades = {name: [] for name, _ in ALL_STRATEGIES}
    for si, sym in enumerate(SYMBOLS, 1):
        bars = fetch_deep(sym, TF, BARS)
        if len(bars) < WARMUP + 100:
            print(f"  [{si}/{len(SYMBOLS)}] {sym}: only {len(bars)} bars — skipped")
            continue
        atrs = atr_series(bars)
        span_days = (bars[-1]["ts"] - bars[0]["ts"]) / 86400
        line = f"  [{si}/{len(SYMBOLS)}] {sym}: {len(bars)} bars ({span_days:.0f}d) |"
        for name, fn in ALL_STRATEGIES:
            tr = simulate_strategy(sym, bars, atrs, fn)
            all_trades[name].extend(tr)
        print(line, "done", flush=True)

    # Per-strategy aggregate
    rows = []
    for name, _ in ALL_STRATEGIES:
        s = stats(all_trades[name])
        if s:
            rows.append((name, s))
    rows.sort(key=lambda x: x[1]["mean_r"], reverse=True)

    print("\n" + "=" * 108)
    print(f"{'STRATEGY':28s} {'n':>6s} {'win%':>6s} {'avgR':>7s} {'net%/t':>7s} "
          f"{'t-stat':>7s} {'95% CI (R)':>18s}  verdict")
    print("=" * 108)
    K = sum(1 for _, s in rows if not s["low_sample"])
    bonf_t = 2.32  # ~ one-sided alpha 0.01; tightened below per Bonferroni
    if K > 0:
        # Bonferroni one-sided z for alpha=0.05 across K tests
        from statistics import NormalDist
        bonf_t = NormalDist().inv_cdf(1 - 0.05 / K)
    survivors = []
    for name, s in rows:
        edge = s["mean_r"] > 0 and s["ci_lo"] > 0 and not s["low_sample"]
        strong = edge and s["t"] >= bonf_t
        verdict = ("KEEP*" if strong else "keep?" if edge else
                   "NOISE" if s["low_sample"] else "cut")
        if strong:
            survivors.append(name)
        print(f"{name:28s} {s['n']:6d} {s['win_rate']:5.1f}% {s['mean_r']:+7.3f} "
              f"{s['mean_pct']:+7.3f} {s['t']:+7.2f} "
              f"[{s['ci_lo']:+.3f},{s['ci_hi']:+.3f}]  {verdict}")

    print("\nMultiple-testing guard:")
    print(f"  {K} strategies had a meaningful sample (>= {MIN_MEANINGFUL_N} trades).")
    print(f"  Bonferroni-corrected t threshold (alpha 0.05 / {K}): {bonf_t:.2f}")
    print(f"  Strategies clearing it (real edge after costs): "
          f"{', '.join(survivors) if survivors else 'NONE'}")
    at_005 = sum(1 for _, s in rows if not s["low_sample"] and s["t"] >= 1.65)
    print(f"  At naive p<0.05 (t>=1.65): {at_005} looked profitable; "
          f"~{0.05*K:.1f} expected by chance alone.")

    # Correlation clustering on daily returns (strategies with a real sample)
    daily = {}
    for name, s in rows:
        if s["low_sample"]:
            continue
        d = {}
        for t in all_trades[name]:
            d[t["day"]] = d.get(t["day"], 0.0) + t["net_pct"]
        daily[name] = d
    names = list(daily)
    pairs = []
    for a in range(len(names)):
        for b in range(a + 1, len(names)):
            c = pearson(daily[names[a]], daily[names[b]])
            if c is not None and c >= CORR_CLUSTER:
                pairs.append((names[a], names[b], c))
    pairs.sort(key=lambda x: -x[2])
    print("\nSame-bet clusters (daily-return corr >= %.2f):" % CORR_CLUSTER)
    if pairs:
        for a, b, c in pairs[:20]:
            print(f"  {a:28s} ~ {b:28s} r={c:.2f}")
    else:
        print("  none above threshold")

    # Per-regime for survivors (and the top few) — where does edge live?
    print("\nPer-regime expectancy (strategies with a meaningful pooled sample):")
    for name, s in rows:
        if s["low_sample"]:
            continue
        by_reg = {}
        for t in all_trades[name]:
            by_reg.setdefault(t["regime"], []).append(t)
        parts = []
        for reg, tr in sorted(by_reg.items()):
            rs = stats(tr)
            if rs and rs["n"] >= 30:
                parts.append(f"{reg}:{rs['mean_r']:+.2f}R(n{rs['n']})")
        if parts:
            print(f"  {name:28s} {'  '.join(parts)}")

    # Persist
    out = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "config": {"symbols": SYMBOLS, "tf": TF, "bars": BARS,
                   "round_trip_cost_pct": ROUND_TRIP_COST * 100,
                   "atr_sl_mult": ATR_SL_MULT, "target_r": TARGET_R,
                   "max_hold": MAX_HOLD, "min_meaningful_n": MIN_MEANINGFUL_N},
        "results": [{"strategy": n, **s} for n, s in rows],
        "survivors": survivors,
        "same_bet_pairs": [{"a": a, "b": b, "r": round(c, 3)} for a, b, c in pairs],
    }
    dst = os.path.join(os.path.dirname(os.path.abspath(__file__)), "expectancy_results.json")
    with open(dst, "w") as f:
        json.dump(out, f, indent=2)
    print(f"\nSaved -> {dst}")
    total_trades = sum(len(v) for v in all_trades.values())
    print(f"Total simulated trades: {total_trades}")


if __name__ == "__main__":
    main()

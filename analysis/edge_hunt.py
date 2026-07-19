"""Phase 3 — hunt for a REAL edge, and test the null nobody has tested.

Everything the firm ever traded failed for one structural reason (Phase 1/1b):
the signals were ~break-even gross, and a 0.16% round-trip cost is ~1/3 R on
tight intraday stops. So this hunt deliberately looks where cost CANNOT
dominate: daily bars, long holds, few trades — where 0.16% is noise against a
multi-week move.

Hypotheses on trial (all long-only spot, halal-compatible, net of costs):
  A. BUY & HOLD          — the null hypothesis. Beat this or go home.
  B. TREND-FOLLOW SMA200 — long while close > SMA200, cash otherwise. The
                           classic "own it in bull markets, sit out crashes".
  C. GOLDEN CROSS 50/200 — long while SMA50 > SMA200.
  D. XS MOMENTUM top-K   — monthly, rank the universe by 90d return, hold the
                           strongest K equal-weight. A documented crypto
                           anomaly and structurally unlike anything we tested.

Reported per strategy: total return, CAGR, Sharpe, max drawdown, trade count,
and — the number that matters — whether it beats BUY & HOLD after costs.

Run:  python analysis/edge_hunt.py
"""
import os
import sys
import json
import math
import time
from datetime import datetime, timezone

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SYMBOLS = [s for s in os.getenv(
    "EDGE_SYMBOLS",
    "BTC/USD,ETH/USD,SOL/USD,BNB/USD,XRP/USD,ADA/USD,DOGE/USD,DOT/USD,"
    "AVAX/USD,LINK/USD,UNI/USD,ATOM/USD,LTC/USD,BCH/USD,TRX/USD"
).split(",") if s.strip()]
DAYS = int(os.getenv("EDGE_DAYS", "1100"))          # ~3 years of daily bars

# Cost per side (spread + slippage; production commission is 0)
COST_SIDE = (float(os.getenv("EXP_SPREAD_PCT", "0.05")) +
             float(os.getenv("EXP_SLIP_PCT", "0.03"))) / 100.0
TOP_K = int(os.getenv("EDGE_TOP_K", "3"))
MOM_LOOKBACK = 90
REBAL_DAYS = 30


def _bsym(s):
    s = s.replace("/", "").upper()
    return s + "T" if s.endswith("USD") and not s.endswith("USDT") else s


def fetch_daily(symbol, total):
    out, end = [], None
    while len(out) < total:
        p = {"symbol": _bsym(symbol), "interval": "1d", "limit": 1000}
        if end:
            p["endTime"] = end
        try:
            d = requests.get("https://api.binance.com/api/v3/klines", params=p, timeout=25).json()
        except Exception:
            break
        if not isinstance(d, list) or not d:
            break
        out = [{"close": float(k[4]), "ts": k[0] // 1000} for k in d] + out
        end = d[0][0] - 1
        if len(d) < 1000:
            break
        time.sleep(0.12)
    return out[-total:]


def sma(vals, i, n):
    if i + 1 < n:
        return None
    return sum(vals[i - n + 1:i + 1]) / n


def metrics(equity, n_trades, label):
    """equity = daily equity curve (list of floats, starts at 1.0)."""
    if len(equity) < 2:
        return None
    total = (equity[-1] / equity[0] - 1) * 100
    years = len(equity) / 365.0
    cagr = ((equity[-1] / equity[0]) ** (1 / years) - 1) * 100 if years > 0 else 0
    rets = [equity[i] / equity[i - 1] - 1 for i in range(1, len(equity)) if equity[i - 1] > 0]
    if len(rets) > 1:
        mu = sum(rets) / len(rets)
        sd = math.sqrt(sum((r - mu) ** 2 for r in rets) / (len(rets) - 1))
        sharpe = (mu / sd) * math.sqrt(365) if sd > 0 else 0
    else:
        sharpe = 0
    peak, mdd = equity[0], 0.0
    for v in equity:
        peak = max(peak, v)
        mdd = max(mdd, (peak - v) / peak * 100 if peak > 0 else 0)
    return {"strategy": label, "total_pct": total, "cagr_pct": cagr,
            "sharpe": sharpe, "max_dd_pct": mdd, "trades": n_trades}


def buy_hold(closes):
    eq, base = [], closes[0] * (1 + COST_SIDE)
    for c in closes:
        eq.append(c * (1 - COST_SIDE) / base)
    return eq, 1


def ma_filter(closes, fast, slow):
    """Long while filter true, cash otherwise. Costs charged on each switch."""
    eq, val, pos, trades = [], 1.0, False, 0
    for i in range(len(closes)):
        if i > 0 and pos:
            val *= closes[i] / closes[i - 1]
        if fast is None:
            sig = sma(closes, i, slow) is not None and closes[i] > sma(closes, i, slow)
        else:
            f, s = sma(closes, i, fast), sma(closes, i, slow)
            sig = f is not None and s is not None and f > s
        if sig and not pos:
            val *= (1 - COST_SIDE); pos = True; trades += 1
        elif not sig and pos:
            val *= (1 - COST_SIDE); pos = False
        eq.append(val)
    return eq, trades


def xs_momentum(series, dates, k, lookback, rebal):
    """Monthly: hold the top-k coins by trailing `lookback`-day return."""
    eq, val, held, trades = [], 1.0, [], 0
    for di in range(len(dates)):
        if di > 0 and held:
            r = []
            for s in held:
                c = series[s]
                if c[di] and c[di - 1]:
                    r.append(c[di] / c[di - 1])
            if r:
                val *= sum(r) / len(r)
        if di >= lookback and di % rebal == 0:
            scored = []
            for s, c in series.items():
                if c[di] and c[di - lookback] and c[di - lookback] > 0:
                    scored.append((c[di] / c[di - lookback] - 1, s))
            scored.sort(reverse=True)
            new = [s for _, s in scored[:k] if _ > 0]     # only positive momentum
            turnover = len(set(new) ^ set(held)) / max(len(new) + len(held), 1)
            if turnover:
                val *= (1 - COST_SIDE * turnover * 2)
                trades += len(set(new) - set(held))
            held = new
        eq.append(val)
    return eq, trades


def main():
    print(f"Phase 3 edge hunt — {len(SYMBOLS)} symbols, {DAYS} daily bars, "
          f"cost {COST_SIDE*100:.3f}%/side\n")
    data = {}
    for s in SYMBOLS:
        b = fetch_daily(s, DAYS)
        if len(b) >= 400:
            data[s] = b
            print(f"  {s}: {len(b)} daily bars "
                  f"({(b[-1]['ts']-b[0]['ts'])/86400:.0f}d)", flush=True)
        else:
            print(f"  {s}: {len(b)} bars — skipped (need 400+)", flush=True)
    if not data:
        print("No data.")
        return

    # Align on the common date window (intersection of timestamps)
    common = set(data[list(data)[0]][i]["ts"] for i in range(len(data[list(data)[0]])))
    for s in data:
        common &= set(b["ts"] for b in data[s])
    dates = sorted(common)
    if len(dates) < 400:
        print(f"Only {len(dates)} common days — widening to BTC-length window.")
    series = {s: [next((b["close"] for b in data[s] if b["ts"] == t), None) for t in dates]
              for s in data}
    series = {s: c for s, c in series.items() if all(c)}
    print(f"\nAligned window: {len(dates)} days across {len(series)} symbols "
          f"({datetime.fromtimestamp(dates[0], tz=timezone.utc):%Y-%m-%d} -> "
          f"{datetime.fromtimestamp(dates[-1], tz=timezone.utc):%Y-%m-%d})\n")

    rows = []
    # A. Buy & hold — per symbol and equal-weight basket
    bh_curves = {}
    for s, c in series.items():
        eq, t = buy_hold(c)
        bh_curves[s] = eq
        rows.append(metrics(eq, t, f"A. buy&hold {s}"))
    basket = [sum(bh_curves[s][i] for s in bh_curves) / len(bh_curves) for i in range(len(dates))]
    rows.append(metrics(basket, len(bh_curves), "A. buy&hold BASKET(eq-wt)"))

    # B/C. Trend filters, averaged equal-weight across the universe
    for label, fast, slow in [("B. trend SMA200", None, 200), ("C. golden 50/200", 50, 200)]:
        curves, tot_tr = [], 0
        for s, c in series.items():
            eq, t = ma_filter(c, fast, slow)
            curves.append(eq); tot_tr += t
        avg = [sum(cu[i] for cu in curves) / len(curves) for i in range(len(dates))]
        rows.append(metrics(avg, tot_tr, f"{label} BASKET"))
    # B per-symbol on BTC for reference
    if "BTC/USD" in series:
        eq, t = ma_filter(series["BTC/USD"], None, 200)
        rows.append(metrics(eq, t, "B. trend SMA200 BTC only"))

    # D. Cross-sectional momentum
    eq, t = xs_momentum(series, dates, TOP_K, MOM_LOOKBACK, REBAL_DAYS)
    rows.append(metrics(eq, t, f"D. XS momentum top{TOP_K} (90d, 30d rebal)"))

    rows = [r for r in rows if r]
    bh_btc = next((r for r in rows if r["strategy"] == "A. buy&hold BTC/USD"), None)
    bench = bh_btc["total_pct"] if bh_btc else 0

    rows.sort(key=lambda r: r["total_pct"], reverse=True)
    print("=" * 104)
    print(f"{'STRATEGY':34s} {'total%':>10s} {'CAGR%':>8s} {'Sharpe':>7s} "
          f"{'maxDD%':>8s} {'trades':>7s}  vs B&H BTC")
    print("=" * 104)
    for r in rows:
        delta = r["total_pct"] - bench
        flag = "BEATS" if delta > 0 else "loses"
        print(f"{r['strategy']:34s} {r['total_pct']:+10.1f} {r['cagr_pct']:+8.1f} "
              f"{r['sharpe']:+7.2f} {r['max_dd_pct']:8.1f} {r['trades']:7d}  "
              f"{flag} {delta:+.1f}pp")

    dst = os.path.join(os.path.dirname(os.path.abspath(__file__)), "edge_hunt_results.json")
    with open(dst, "w") as f:
        json.dump({"generated_at_utc": datetime.now(timezone.utc).isoformat(),
                   "days": len(dates), "symbols": list(series),
                   "cost_per_side_pct": COST_SIDE * 100, "results": rows}, f, indent=2)
    print(f"\nSaved -> {dst}")


if __name__ == "__main__":
    main()

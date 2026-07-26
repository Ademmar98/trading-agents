#!/usr/bin/env python3
"""
Fibonacci "Gold Zone" retracement strategy — 1m micro-trend, LONG ONLY, SPOT.

Spec (long variant only; the short/downtrend variant is excluded by the
no-shorts, no-leverage constraint):

  1. Micro-trend   : two consecutive HIGHER LOWS on confirmed 1m swing points
  2. Break of      : price closes above the most recent confirmed swing high
     structure       (impulse leg), then pulls back
  3. Fibonacci     : drawn from the swing low that began the impulse (1.0)
                     up through the break to the impulse high (0.0)
  4. Gold Zone     : limit buy between the 0.5 and 0.618 retracement levels
  5. Risk / Reward : stop at the 1.0 level (the origin swing low),
                     target just inside the impulse high

NO-LOOKAHEAD RULES (the thing that killed the first swing_htf study):
  - A pivot at index i is only *visible* from bar i+K onward. Nothing reads a
    swing before it could have been confirmed in real time.
  - The impulse high uses only bars up to the current one.
  - Entry is a resting limit order: it fills only if the bar's LOW reaches it.
  - Exits check the STOP before the target on the same bar (conservative:
    a bar that spans both is booked as a loss).

Costs: 0.14% round-trip, the bar established in prior studies.
"""
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "fib_1m_data"
DATA.mkdir(exist_ok=True)

SPOT = "https://api.binance.com"
FEE_RT = 0.0014          # 0.14% round trip
PIVOT_K = 2              # bars each side for a fractal pivot
GZ_LO, GZ_HI = 0.500, 0.618
TP_BUFFER = 0.05         # target "just inside" the high: 5% of the leg below it
MAX_WAIT_BARS = 120      # abandon a setup that never retraces within 2h
MAX_HOLD_BARS = 480      # abandon a trade that neither stops nor targets in 8h
STABLES = {"USDC", "USDT", "BUSD", "TUSD", "FDUSD", "DAI", "USDP", "USDN",
           "EUR", "GBP", "AEUR", "USD1", "XUSD"}
LEV_TAGS = ("UP", "DOWN", "BULL", "BEAR", "3L", "3S", "5L", "5S")


# ---------------------------------------------------------------- data ----
def qualified_pairs(min_vol_usd=20_000_000, limit=None):
    """Liquid USDT spot pairs, stablecoin and leveraged-token free."""
    r = requests.get(f"{SPOT}/api/v3/ticker/24hr", timeout=30)
    out = []
    for t in r.json():
        s = t["symbol"]
        if not s.endswith("USDT"):
            continue
        base = s[:-4]
        if base in STABLES or any(x in base for x in LEV_TAGS):
            continue
        vol = float(t.get("quoteVolume", 0))
        if vol < min_vol_usd:
            continue
        out.append({"symbol": s, "base": base, "volume": vol})
    out.sort(key=lambda x: -x["volume"])
    return out[:limit] if limit else out


def fetch_1m(symbol, days=90, cache=True):
    """Page backwards through 1m klines. Cached to parquet per symbol."""
    path = DATA / f"{symbol}_1m.parquet"
    if cache and path.exists():
        return pd.read_parquet(path)

    end = int(time.time() * 1000)
    start = end - days * 24 * 3600 * 1000
    rows, cursor = [], start
    while cursor < end:
        try:
            r = requests.get(f"{SPOT}/api/v3/klines", params={
                "symbol": symbol, "interval": "1m",
                "startTime": cursor, "limit": 1000}, timeout=20)
            if r.status_code != 200:
                break
            batch = r.json()
        except Exception:
            break
        if not batch:
            break
        rows.extend(batch)
        nxt = batch[-1][0] + 60_000
        if nxt <= cursor:
            break
        cursor = nxt
        time.sleep(0.06)

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows, columns=[
        "ts", "open", "high", "low", "close", "volume",
        "close_ts", "qv", "trades", "tbb", "tbq", "ig"])
    df = df[["ts", "open", "high", "low", "close", "volume"]].astype(float)
    df["ts"] = pd.to_datetime(df["ts"].astype("int64"), unit="ms")
    df = df.drop_duplicates("ts").sort_values("ts").reset_index(drop=True)
    if cache:
        df.to_parquet(path, index=False)
    return df


# ------------------------------------------------------------ strategy ----
def pivots(high, low, k=PIVOT_K):
    """Fractal pivot indices. A pivot at i is only knowable from bar i+k."""
    n = len(high)
    ph = np.zeros(n, dtype=bool)
    pl = np.zeros(n, dtype=bool)
    for i in range(k, n - k):
        w_h = high[i - k:i + k + 1]
        w_l = low[i - k:i + k + 1]
        if high[i] == w_h.max() and (w_h.argmax() == k):
            ph[i] = True
        if low[i] == w_l.min() and (w_l.argmin() == k):
            pl[i] = True
    return ph, pl


def backtest(df, symbol="", entry_fib=GZ_LO, min_R_pct=0.0):
    """Walk bar by bar; only ever look at data available at that bar.

    entry_fib : where in the gold zone the limit rests. 0.500 is the first
                touch (fills often, R:R 0.90); 0.618 is the far edge (fills
                less often, R:R 1.49). Both are inside the spec's zone.
    min_R_pct : skip setups whose stop distance is below this % of price.
                Not a fitted parameter -- it is the cost floor. A trade whose
                whole reward is smaller than the round-trip fee cannot win.
    """
    if len(df) < 500:
        return None

    high = df["high"].to_numpy()
    low = df["low"].to_numpy()
    close = df["close"].to_numpy()
    ts = df["ts"].to_numpy()
    n = len(df)

    ph, pl = pivots(high, low)
    swing_highs, swing_lows = [], []   # (idx, price), appended when CONFIRMED

    trades = []
    setup = None      # pending gold-zone limit order
    pos = None        # open trade

    for i in range(PIVOT_K, n):
        # Confirm pivots that became visible at this bar (index i-K).
        c = i - PIVOT_K
        if c >= 0:
            if ph[c]:
                swing_highs.append((c, high[c]))
            if pl[c]:
                swing_lows.append((c, low[c]))

        # ---- manage an open position (stop checked first) ----------------
        if pos is not None:
            if low[i] <= pos["sl"]:
                _close(trades, pos, pos["sl"], i, ts[i], "STOP")
                pos = None
            elif high[i] >= pos["tp"]:
                _close(trades, pos, pos["tp"], i, ts[i], "TARGET")
                pos = None
            elif i - pos["entry_i"] >= MAX_HOLD_BARS:
                _close(trades, pos, close[i], i, ts[i], "TIMEOUT")
                pos = None
            continue

        # ---- a resting limit order in the gold zone ----------------------
        if setup is not None:
            if close[i] < setup["L"]:           # structure broken, void it
                setup = None
            elif i - setup["bos_i"] > MAX_WAIT_BARS:
                setup = None
            elif low[i] <= setup["entry"]:      # limit filled
                pos = {"symbol": symbol, "entry": setup["entry"],
                       "sl": setup["sl"], "tp": setup["tp"],
                       "entry_i": i, "entry_ts": ts[i],
                       "R_pct": setup["R_pct"], "rr": setup["rr"]}
                setup = None
            continue

        # ---- look for a new setup ----------------------------------------
        if len(swing_lows) < 2 or not swing_highs:
            continue

        # 1. micro-trend: two consecutive higher lows
        (l1_i, l1), (l0_i, l0) = swing_lows[-2], swing_lows[-1]
        if not (l0 > l1):
            continue

        # 2. break of structure: close above the most recent confirmed high,
        #    and that high must sit after the higher-low that anchors the leg
        h_i, h_price = swing_highs[-1]
        if h_i <= l0_i or close[i] <= h_price:
            continue

        # 3. fibonacci over the impulse: origin low -> impulse high so far
        L = l0
        H = high[l0_i:i + 1].max()
        leg = H - L
        if leg <= 0:
            continue

        # 4. gold zone 0.5 - 0.618
        entry = H - entry_fib * leg
        sl = L                                   # fib 1.0
        tp = H - TP_BUFFER * leg                 # just inside the high
        risk = entry - sl
        if risk <= 0 or tp <= entry:
            continue
        if risk / entry * 100 < min_R_pct:       # cost floor, not a fit
            continue

        setup = {"L": L, "H": H, "entry": entry, "sl": sl, "tp": tp,
                 "bos_i": i, "R_pct": risk / entry * 100,
                 "rr": (tp - entry) / risk}

    return _summarise(trades, symbol, n, df)


def _close(trades, pos, px, i, t, reason):
    gross = (px - pos["entry"]) / pos["entry"]
    net = gross - FEE_RT
    trades.append({
        "symbol": pos["symbol"], "entry": pos["entry"], "exit": px,
        "entry_ts": str(pos["entry_ts"]), "exit_ts": str(t),
        "bars": i - pos["entry_i"], "reason": reason,
        "gross_pct": gross * 100, "net_pct": net * 100,
        "R_pct": pos["R_pct"], "rr": pos["rr"],
    })


def _summarise(trades, symbol, bars, df):
    if not trades:
        return {"symbol": symbol, "trades": 0, "bars": bars}
    net = np.array([t["net_pct"] for t in trades])
    gross = np.array([t["gross_pct"] for t in trades])
    wins, losses = net[net > 0], net[net <= 0]
    gw, gl = wins.sum(), abs(losses.sum())
    return {
        "symbol": symbol,
        "trades": len(trades),
        "bars": bars,
        "start": str(df["ts"].iloc[0]),
        "end": str(df["ts"].iloc[-1]),
        "win_rate": round(len(wins) / len(trades) * 100, 1),
        "net_sum_pct": round(net.sum(), 2),
        "gross_sum_pct": round(gross.sum(), 2),
        "avg_net_pct": round(net.mean(), 4),
        "expectancy_pct": round(net.mean(), 4),
        "profit_factor": round(gw / gl, 3) if gl > 0 else (999.0 if gw > 0 else 0.0),
        "gross_pf": round(gross[gross > 0].sum() / abs(gross[gross <= 0].sum()), 3)
                    if (gross <= 0).any() and abs(gross[gross <= 0].sum()) > 0 else 0.0,
        "avg_R_pct": round(float(np.mean([t["R_pct"] for t in trades])), 4),
        "avg_rr": round(float(np.mean([t["rr"] for t in trades])), 2),
        "reasons": {r: int(sum(1 for t in trades if t["reason"] == r))
                    for r in {t["reason"] for t in trades}},
        "trade_list": trades,
    }


if __name__ == "__main__":
    syms = sys.argv[1:] or ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    for s in syms:
        d = fetch_1m(s, days=7)
        r = backtest(d, s)
        if r and r.get("trades"):
            print(f"{s:12s} trades={r['trades']:4d} WR={r['win_rate']:5.1f}% "
                  f"PF={r['profit_factor']:6.3f} net={r['net_sum_pct']:+8.2f}% "
                  f"avgR={r['avg_R_pct']:.3f}% rr={r['avg_rr']:.2f} {r['reasons']}")
        else:
            print(f"{s:12s} no trades ({len(d)} bars)")

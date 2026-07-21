"""Daily-bar swing study — runs ON the VPS against /root/firm's deployed code.

Stage A: replay the firm's deployed swing_signal bar-by-bar on daily bars
  (with the matching 4h window for its alignment filter), bracket-simulate
  each BUY's actual SL/TP with SL-priority and costs -> realized WR, net
  expectancy, realized R, hold time, per style. vs buy & hold.
Stage B: daily regime-hold control (long above SMA50, flat below) — the daily
  version of the 1H control that churned to death; measures flip count.
Stage C: exit-geometry grid on the SAME entries, 70/30 IS/OOS (entries are
  independent of RR/SL mult, so vary the bracket cheaply).

Execution realism: signal on daily bar t close, entry at t+1 open, intrabar
SL priority, costs 0.05% taker + 0.02% slippage per side (0.14% round trip).
"""
import bisect
import datetime
import json
import os
import statistics
import sys
import time
import urllib.parse
import urllib.request

os.environ["TRADING_DATA_DIR"] = "/tmp/swing_study_data"   # sandbox BEFORE firm imports
sys.path.insert(0, "/root/firm")

import config                                              # noqa: E402
from core.swing import swing_signal                        # noqa: E402

# Alpaca US crypto (keyless v1beta3), the source the scalp study proved
# reachable from the VPS. BNB/ADA are not on Alpaca US; use its majors.
SYMBOLS = ["BTC/USD", "ETH/USD", "SOL/USD", "LINK/USD", "DOGE/USD", "AVAX/USD"]
START_ISO = "2021-01-01T00:00:00Z"
END_ISO = "2026-07-19T00:00:00Z"
INTERVAL = {"1d": "1Day", "4h": "4Hour"}
COST_SIDE = 0.0007
RT = 2 * COST_SIDE
MAXHOLD = 60            # daily bars (~2 months) bracket walk
OUT = "/tmp/swing_study"


def _epoch_ms(iso):
    return int(datetime.datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() * 1000)


def fetch(symbol, interval):
    """Alpaca crypto bars, keyless, paged. Returns bars with ts/close_time ms."""
    tf = INTERVAL[interval]
    step_ms = 86400_000 if interval == "1d" else 4 * 3600_000
    rows, token = [], None
    base = ("https://data.alpaca.markets/v1beta3/crypto/us/bars"
            f"?symbols={urllib.parse.quote(symbol, safe='')}"
            f"&timeframe={tf}&start={START_ISO}&end={END_ISO}&limit=10000")
    while True:
        url = base + (f"&page_token={token}" if token else "")
        for attempt in range(4):
            try:
                data = json.loads(urllib.request.urlopen(url, timeout=30).read())
                break
            except Exception:
                time.sleep(2 * (attempt + 1))
        else:
            raise RuntimeError(f"fetch failed {symbol} {interval}")
        for b in data.get("bars", {}).get(symbol, []):
            ts = _epoch_ms(b["t"])
            rows.append({"ts": ts, "close_time": ts + step_ms - 1,
                         "open": b["o"], "high": b["h"], "low": b["l"],
                         "close": b["c"], "volume": b["v"]})
        token = data.get("next_page_token")
        if not token:
            break
        time.sleep(0.1)
    return rows


def bracket(bars, i, sl_px, tp_px):
    """Enter at open[i+1], walk <=MAXHOLD daily bars, SL priority intrabar."""
    entry = bars[i + 1]["open"]
    for j in range(i + 1, min(i + 1 + MAXHOLD, len(bars))):
        if bars[j]["low"] <= sl_px:
            return "loss", (sl_px / entry - 1) - RT, j - i, entry
        if bars[j]["high"] >= tp_px:
            return "win", (tp_px / entry - 1) - RT, j - i, entry
    j = min(i + MAXHOLD, len(bars) - 1)
    return "timeout", (bars[j]["close"] / entry - 1) - RT, j - i, entry


def sma(vals, n):
    return sum(vals[-n:]) / n if len(vals) >= n else None


def study_symbol(sym):
    daily = fetch(sym, "1d")
    h4 = fetch(sym, "4h")
    if len(daily) < 200 or len(h4) < 200:
        return {"symbol": sym, "error": f"daily={len(daily)} h4={len(h4)}"}
    h4_ct = [b["close_time"] for b in h4]

    # ── Stage A: deployed swing_signal, bracket-simulated ──
    events = []
    for i in range(60, len(daily) - 2):
        d_ct = daily[i]["close_time"]
        hi = bisect.bisect_right(h4_ct, d_ct)   # 4h bars closed by this daily close
        if hi < 60:
            continue
        sig = swing_signal(sym, daily[:i + 1], h4[:hi], regime=None)
        if not sig:
            continue
        entry_ref = daily[i + 1]["open"]
        sl_px = entry_ref * (1 - sig["sl_pct"] / 100)
        tp_px = entry_ref * (1 + sig["tp_pct"] / 100)
        outcome, net, held, entry = bracket(daily, i, sl_px, tp_px)
        r_mult = (net * 100) / sig["sl_pct"] if sig["sl_pct"] else 0
        events.append({"i": i, "style": sig["strategy"], "outcome": outcome,
                       "net": net, "held": held, "r": r_mult,
                       "sl_pct": sig["sl_pct"], "tp_pct": sig["tp_pct"]})

    def agg(evs):
        if not evs:
            return {"n": 0}
        resolved = [e for e in evs if e["outcome"] != "timeout"]
        w = sum(1 for e in evs if e["outcome"] == "win")
        return {"n": len(evs),
                "win": w, "loss": sum(1 for e in evs if e["outcome"] == "loss"),
                "timeout": sum(1 for e in evs if e["outcome"] == "timeout"),
                "realized_wr": round(w / len(resolved) * 100, 1) if resolved else 0,
                "net_exp_pct": round(statistics.mean(e["net"] for e in evs) * 100, 4),
                "avg_R": round(statistics.mean(e["r"] for e in evs), 3),
                "avg_hold_days": round(statistics.mean(e["held"] for e in evs), 1),
                "avg_sl_pct": round(statistics.mean(e["sl_pct"] for e in evs), 2),
                "avg_tp_pct": round(statistics.mean(e["tp_pct"] for e in evs), 2)}

    stageA = {"all": agg(events)}
    for style in ("swing_breakout", "swing_pullback", "swing_momentum"):
        stageA[style] = agg([e for e in events if e["style"] == style])

    bh = (daily[-1]["close"] / daily[60]["open"] - 1) * 100
    yrs = (daily[-1]["ts"] - daily[60]["ts"]) / (365.25 * 86400_000)

    # ── Stage B: daily regime-hold control (long above SMA50) ──
    closes = [b["close"] for b in daily]
    flips = 0
    in_pos = False
    eq = 1.0
    peak = 1.0
    maxdd = 0.0
    entry_px = 0.0
    rets = []
    for i in range(60, len(daily) - 1):
        s50 = sma(closes[:i + 1], 50)
        if s50 is None:
            continue
        nxt = daily[i + 1]["open"]
        if not in_pos and closes[i] > s50:
            in_pos = True
            entry_px = nxt * (1 + COST_SIDE)
            flips += 1
        elif in_pos and closes[i] < s50:
            in_pos = False
            r = (nxt * (1 - COST_SIDE)) / entry_px - 1
            rets.append(r)
            eq *= (1 + r)
            peak = max(peak, eq)
            maxdd = min(maxdd, eq / peak - 1)
    stageB = {"flips": flips,
              "total_ret_pct": round((eq - 1) * 100, 1),
              "maxdd_pct": round(maxdd * 100, 1),
              "n_round_trips": len(rets),
              "buy_hold_pct": round(bh, 1)}

    # ── Stage C: exit-geometry grid on the SAME entries, 70/30 IS/OOS ──
    # Split by bar index: entries before the 70% mark are IS, after are OOS.
    split_i = 60 + int((len(daily) - 62) * 0.70)
    grid = []
    for sl_mult in (1.5, 2.0, 3.0):
        for rr in (2.0, 3.0, 4.0, 6.0):
            for seg, evs_seg in (("IS", [e for e in events if e["i"] < split_i]),
                                 ("OOS", [e for e in events if e["i"] >= split_i])):
                nets = []
                for e in evs_seg:
                    # recompute bracket with this geometry from the raw ATR-implied
                    # SL. Approx: scale the signal's sl_pct by (sl_mult/2.0) since
                    # deployed uses 2.0; tp = sl*rr. Clamp like the firm.
                    base_sl = e["sl_pct"] * (sl_mult / config.SWING_ATR_SL_MULT)
                    sl_pct = min(max(base_sl, config.SWING_MIN_SL_PCT), config.SWING_MAX_SL_PCT)
                    tp_pct = min(max(sl_pct * rr, config.SWING_MIN_TP_PCT), config.SWING_MAX_TP_PCT)
                    entry = daily[e["i"] + 1]["open"]
                    o, net, _, _ = bracket(daily, e["i"], entry * (1 - sl_pct / 100),
                                           entry * (1 + tp_pct / 100))
                    nets.append(net)
                if nets:
                    grid.append({"sl_mult": sl_mult, "rr": rr, "seg": seg,
                                 "n": len(nets),
                                 "net_exp_pct": round(statistics.mean(nets) * 100, 4),
                                 "total_pct": round(sum(nets) * 100, 1)})

    return {"symbol": sym, "years": round(yrs, 2), "daily_bars": len(daily),
            "buy_hold_pct": round(bh, 1), "stageA": stageA, "stageB": stageB,
            "stageC": grid}


def main():
    os.makedirs(OUT, exist_ok=True)
    os.makedirs("/tmp/swing_study_data", exist_ok=True)
    from core.database import init_db
    init_db()
    results = []
    for sym in SYMBOLS:
        t0 = time.time()
        try:
            r = study_symbol(sym)
        except Exception as e:
            import traceback
            r = {"symbol": sym, "error": str(e), "trace": traceback.format_exc()[-600:]}
        r["elapsed_s"] = round(time.time() - t0, 1)
        results.append(r)
        json.dump(results, open(f"{OUT}/progress.json", "w"), indent=1)
        print(sym, "done", r.get("elapsed_s"), "s", flush=True)
    json.dump(results, open(f"{OUT}/RESULTS.json", "w"), indent=1)
    print("ALL DONE")


if __name__ == "__main__":
    main()

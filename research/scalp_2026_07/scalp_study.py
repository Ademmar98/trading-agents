"""Scalp study — Stage 0 (cost feasibility) + Stage 1 (event study of the
firm's actual scalp signals). Self-contained, stdlib only; runs ON the VPS
against /root/firm's deployed code. Never touches the live data dir.

Execution realism: signals on bar t, entry at bar t+1 OPEN, bracket walked
up to 96 bars (24h) with SL priority inside a bar, costs applied per side.
"""
import json
import math
import os
import statistics
import sys
import time
import urllib.parse
import urllib.request

os.environ["TRADING_DATA_DIR"] = "/tmp/scalp_study_data"   # sandbox BEFORE firm imports
sys.path.insert(0, "/root/firm")

from core.scalp15 import scalp_signal                     # noqa: E402
from core.scalping_signals import evaluate_tf             # noqa: E402

SYMBOLS = ["BTC/USD", "ETH/USD", "SOL/USD", "LINK/USD", "DOGE/USD", "AVAX/USD"]
START = "2024-07-01T00:00:00Z"
END = "2026-07-19T00:00:00Z"
COST_SIDE = 0.0007          # 0.05% taker + 0.02% slippage
RT = 2 * COST_SIDE          # round trip ~0.14%
H_BRACKET = 96              # 24h max hold for the bracket sim
OUT = "/tmp/scalp_study"


def fetch_15m(symbol):
    """Alpaca v1beta3 crypto bars, keyless, paged."""
    bars, token = [], None
    base = ("https://data.alpaca.markets/v1beta3/crypto/us/bars"
            f"?symbols={urllib.parse.quote(symbol, safe='')}"
            f"&timeframe=15Min&start={START}&end={END}&limit=10000")
    while True:
        url = base + (f"&page_token={token}" if token else "")
        with urllib.request.urlopen(url, timeout=30) as r:
            data = json.loads(r.read())
        chunk = data.get("bars", {}).get(symbol, [])
        for b in chunk:
            bars.append({"open": b["o"], "high": b["h"], "low": b["l"],
                         "close": b["c"], "volume": b["v"], "date": b["t"]})
        token = data.get("next_page_token")
        if not token:
            break
        time.sleep(0.1)
    return bars


def atr_series(bars, period=14):
    """Wilder ATR, incremental."""
    out = [0.0] * len(bars)
    prev_c = bars[0]["close"]
    a = None
    for i, b in enumerate(bars):
        tr = max(b["high"] - b["low"], abs(b["high"] - prev_c), abs(b["low"] - prev_c))
        a = tr if a is None else (a * (period - 1) + tr) / period
        out[i] = a
        prev_c = b["close"]
    return out


def pct(vals, p):
    if not vals:
        return 0.0
    s = sorted(vals)
    k = min(len(s) - 1, max(0, int(round(p / 100 * (len(s) - 1)))))
    return s[k]


def bracket(bars, i, sl_px, tp_px):
    """Enter at open[i+1]; walk to H_BRACKET bars. SL priority intrabar.
    Returns (outcome, net_ret, bars_held). Net of full round-trip cost."""
    entry = bars[i + 1]["open"]
    for j in range(i + 1, min(i + 1 + H_BRACKET, len(bars))):
        if bars[j]["low"] <= sl_px:
            return "loss", (sl_px / entry - 1) - RT, j - i
        if bars[j]["high"] >= tp_px:
            return "win", (tp_px / entry - 1) - RT, j - i
    j = min(i + H_BRACKET, len(bars) - 1)
    return "timeout", (bars[j]["close"] / entry - 1) - RT, j - i


def study_symbol(sym):
    bars = fetch_15m(sym)
    n = len(bars)
    if n < 2000:
        return {"symbol": sym, "error": f"only {n} bars"}
    closes = [b["close"] for b in bars]
    atr = atr_series(bars)

    # ── Stage 0: unconditional feasibility ──
    atr_pcts = [atr[i] / closes[i] * 100 for i in range(500, n)]
    mfe4, mae4, mfe16, mae16 = [], [], [], []
    base_brackets = {rr: [0, 0, 0] for rr in (1.0, 1.5, 2.0)}   # win/loss/timeout
    base_net = {rr: [] for rr in (1.0, 1.5, 2.0)}
    for i in range(500, n - H_BRACKET - 2, 4):                  # stride 4 to decorrelate
        e = bars[i + 1]["open"]
        hi4 = max(b["high"] for b in bars[i + 1:i + 5])
        lo4 = min(b["low"] for b in bars[i + 1:i + 5])
        hi16 = max(b["high"] for b in bars[i + 1:i + 17])
        lo16 = min(b["low"] for b in bars[i + 1:i + 17])
        mfe4.append((hi4 / e - 1) * 100); mae4.append((1 - lo4 / e) * 100)
        mfe16.append((hi16 / e - 1) * 100); mae16.append((1 - lo16 / e) * 100)
        sl_d = 1.5 * atr[i]
        for rr in (1.0, 1.5, 2.0):
            o, net, _ = bracket(bars, i, e - sl_d, e + sl_d * rr)
            idx = 0 if o == "win" else 1 if o == "loss" else 2
            base_brackets[rr][idx] += 1
            base_net[rr].append(net)

    med_atr = statistics.median(atr_pcts)
    sl_pct_typ = 1.5 * med_atr
    stage0 = {
        "bars": n,
        "median_atr_pct": round(med_atr, 3),
        "typical_sl_pct": round(sl_pct_typ, 3),
        "mfe_1h_p50_p90": [round(pct(mfe4, 50), 3), round(pct(mfe4, 90), 3)],
        "mae_1h_p50_p90": [round(pct(mae4, 50), 3), round(pct(mae4, 90), 3)],
        "mfe_4h_p50_p90": [round(pct(mfe16, 50), 3), round(pct(mfe16, 90), 3)],
        "mae_4h_p50_p90": [round(pct(mae16, 50), 3), round(pct(mae16, 90), 3)],
        "baseline": {},
    }
    for rr in (1.0, 1.5, 2.0):
        w, l, t = base_brackets[rr]
        tot = max(w + l + t, 1)
        tp_pct = sl_pct_typ * rr
        req_wr = (sl_pct_typ + RT * 100) / (tp_pct + sl_pct_typ)
        stage0["baseline"][f"rr_{rr}"] = {
            "random_wr": round(w / tot * 100, 1),
            "breakeven_wr": round(req_wr * 100, 1),
            "random_expectancy_pct": round(statistics.mean(base_net[rr]) * 100, 4),
            "n": tot, "timeouts": t,
        }

    # ── Stage 1: replay the firm's signals ──
    ema200 = closes[0]
    ema_hist = []
    k = 2 / 201
    scalp_events = []
    tf_events = []
    baseline_fwd4, baseline_fwd16 = [], []
    for i in range(250, n - H_BRACKET - 2):
        ema200 = closes[i] * k + ema200 * (1 - k)
        ema_hist.append(ema200)
        if len(ema_hist) > 30:
            rising = ema200 > ema_hist[-25]
            regime = ("trending_up" if closes[i] > ema200 and rising
                      else "trending_down" if closes[i] < ema200 and not rising
                      else "ranging")
        else:
            regime = "ranging"

        e_next = bars[i + 1]["open"]
        f4 = (closes[i + 4] / e_next - 1) * 100
        f16 = (closes[i + 16] / e_next - 1) * 100
        if i % 8 == 0:
            baseline_fwd4.append(f4)
            baseline_fwd16.append(f16)

        sig = scalp_signal(sym, regime=regime, ohlc=bars[i - 129:i + 1])
        if sig and sig["action"] == "BUY":
            o, net, held = bracket(bars, i, sig["stop_loss"], sig["take_profit"])
            scalp_events.append({"i": i, "outcome": o, "net": net, "held": held,
                                 "wp": sig["win_prob"], "rr": sig["rr"],
                                 "regime": regime, "fwd4": f4, "fwd16": f16})

        tf = evaluate_tf(bars[i - 199:i + 1])
        if tf["action"] == "BUY":
            tf_events.append({"i": i, "tier": tf["signal_7tier"],
                              "conf": tf["confidence"], "regime": tf["regime"],
                              "gc": tf["gc_dir"], "vol": tf["vol_confirm"],
                              "ichi": tf["ichi_score"], "fwd4": f4, "fwd16": f16})

    def agg(evts, key4="fwd4", key16="fwd16"):
        if not evts:
            return {"n": 0}
        a4 = [e[key4] for e in evts]; a16 = [e[key16] for e in evts]
        return {"n": len(evts),
                "fwd1h_mean": round(statistics.mean(a4), 4),
                "fwd4h_mean": round(statistics.mean(a16), 4),
                "fwd4h_median": round(statistics.median(a16), 4)}

    scalp_out = {"total": agg(scalp_events), "brackets": {}}
    for o in ("win", "loss", "timeout"):
        scalp_out["brackets"][o] = sum(1 for e in scalp_events if e["outcome"] == o)
    if scalp_events:
        nets = [e["net"] for e in scalp_events]
        scalp_out["net_expectancy_pct"] = round(statistics.mean(nets) * 100, 4)
        scalp_out["avg_bars_held"] = round(statistics.mean(e["held"] for e in scalp_events), 1)
        for bucket in sorted({e["wp"] for e in scalp_events}):
            evs = [e for e in scalp_events if e["wp"] == bucket]
            w = sum(1 for e in evs if e["outcome"] == "win")
            resolved = [e for e in evs if e["outcome"] != "timeout"]
            wr = w / len(resolved) * 100 if resolved else 0
            scalp_out[f"wp_{bucket}"] = {
                "claimed_wr": bucket * 100, "realized_wr": round(wr, 1),
                "n": len(evs), "net_exp_pct": round(
                    statistics.mean(e["net"] for e in evs) * 100, 4)}

    tf_out = {"total": agg(tf_events)}
    for tier in ("STRONG_BUY", "BUY", "WEAK_BUY"):
        tf_out[tier] = agg([e for e in tf_events if e["tier"] == tier])
    tf_out["gc_up"] = agg([e for e in tf_events if e["gc"] > 0])
    tf_out["vol_confirmed"] = agg([e for e in tf_events if e["vol"] >= 1.0])
    tf_out["ichi_pos"] = agg([e for e in tf_events if e["ichi"] > 0])
    for r in ("trending", "ranging", "volatile"):
        tf_out[f"regime_{r}"] = agg([e for e in tf_events if e["regime"] == r])

    base = {"n": len(baseline_fwd4),
            "fwd1h_mean": round(statistics.mean(baseline_fwd4), 4),
            "fwd4h_mean": round(statistics.mean(baseline_fwd16), 4),
            "fwd4h_median": round(statistics.median(baseline_fwd16), 4)}
    return {"symbol": sym, "stage0": stage0,
            "scalp_signal": scalp_out, "evaluate_tf": tf_out, "baseline": base}


def main():
    os.makedirs(OUT, exist_ok=True)
    os.makedirs("/tmp/scalp_study_data", exist_ok=True)
    from core.database import init_db
    init_db()   # sandbox DB: estimate_win_probability reads strategy_stats
    results = []
    for sym in SYMBOLS:
        t0 = time.time()
        try:
            r = study_symbol(sym)
        except Exception as e:
            import traceback
            r = {"symbol": sym, "error": f"{e}", "trace": traceback.format_exc()[-800:]}
        r["elapsed_s"] = round(time.time() - t0, 1)
        results.append(r)
        with open(f"{OUT}/progress.json", "w") as f:
            json.dump(results, f, indent=1)
        print(sym, "done in", r["elapsed_s"], "s", flush=True)
    with open(f"{OUT}/RESULTS.json", "w") as f:
        json.dump(results, f, indent=1)
    print("ALL DONE")


if __name__ == "__main__":
    main()

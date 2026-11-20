import sys, io, time, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
import numpy as np
import sim
from datetime import datetime, timezone
import pandas as pd

RUN_DAYS = 365

def fetch_year(coin, total_days=365, chunk_days=150):
    now = int(datetime.now(timezone.utc).timestamp() * 1000)
    frames = []
    end = now
    while end > now - total_days * 86400 * 1000:
        start = end - chunk_days * 86400 * 1000
        d = sim.hl_post({"type": "candleSnapshot", "req": {
            "coin": coin, "interval": "1h", "startTime": start, "endTime": end}})
        if d:
            frames.append(pd.DataFrame([{
                "open": float(c["o"]), "high": float(c["h"]), "low": float(c["l"]),
                "close": float(c["c"]), "volume": float(c["v"]),
                "timestamp": pd.to_datetime(int(c["t"]), unit="ms", utc=True)} for c in d])
                .set_index("timestamp").sort_index())
        step = start
        end = step
        if not d:
            end -= chunk_days * 86400 * 1000
        time.sleep(0.12)
        if end <= now - total_days * 86400 * 1000:
            break
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames).sort_index()
    return df[~df.index.duplicated(keep="last")]

coins = sim.universe(50)
print(f"fetching {RUN_DAYS}d x {len(coins)} symbols (paginated)...", flush=True)
raw = {}
for i, c in enumerate(coins, 1):
    try:
        df = fetch_year(c, RUN_DAYS)
    except Exception as e:
        print(f"  [{i}/{len(coins)}] {c}: {type(e).__name__} {e}", flush=True)
        continue
    if len(df) > 500:
        raw[c] = df
    print(f"  [{i}/{len(coins)}] {c}: {len(df)} bars", flush=True)

D = sim.prep(raw)
n = len(D["index"])
print(f"\n{len(D['cols'])} assets, {n} bars = {n/24:.0f} days of aligned history\n")

cfg = sim.Cfg(strat="mom", lb=96, k=3, allow_short=False,
              risk_pct=0.01, sl_atr=2.0, tp_r=2.0,
              max_concurrent=2, max_hold=72, cooldown=4,
              daily_halt=0.02, dd_halt=0.045)
rules = sim.Rules()
sig = sim.SIGNALS["mom"](D, cfg)

print(f"config: {sim.describe(cfg)}\n")

r = sim.simulate(D, sig, cfg, rules)
print("===== FULL {:.0f}-DAY RUN =====".format(n/24))
print(f"  outcome        : {r['outcome']}")
print(f"  return         : {r['return_pct']:+.2f}%  (target +10%)")
print(f"  max drawdown   : {r['max_dd_pct']:.2f}%  (floor 6%)")
print(f"  trades         : {r['trades']}")
print(f"  win rate       : {r['win_rate']:.1f}%")
print(f"  profit factor  : {r['profit_factor']}")
print(f"  days to outcome: {r['days_to_outcome']}" if r["days_to_outcome"] is not None else "  days to outcome: n/a")
print(f"  trades/day     : {r['trades_per_day']:.2f}")

print("\n===== WINDOW ODDS (pass = +10% in time, breach = hit a wall) =====")
print(f"  {'horizon':<8} {'n':>4} {'pass':>6} {'breach':>7} {'median ret':>11}")
win_out = {}
for h in (30, 45, 60, 90, 120, 150):
    ws = sim.windows(D, days=h)
    rs = [sim.simulate(D, sig, cfg, rules, lo, hi) for lo, hi in ws]
    nh = len(rs)
    pass_ = sum(x["hit_target"] for x in rs)
    br   = sum(x["breached"] for x in rs)
    med  = float(np.median([x["return_pct"] for x in rs]))
    win_out[str(h)] = {"n": nh, "pass": pass_, "breach": br, "median_ret": med}
    print(f"  {str(h)+'d':<8} {nh:>4} {pass_:>5}/{nh} {br:>6}/{nh} {med:>+10.2f}%")

out = {"assets": len(D["cols"]), "bars": n, "config": cfg.__dict__,
       "full": r, "windows": win_out}
open("/opt/propr-bot/year_backtest_deadline30.json", "w").write(json.dumps(out, indent=1))
print("\n-> /opt/propr-bot/year_backtest_deadline30.json")

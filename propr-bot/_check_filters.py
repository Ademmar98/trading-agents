# -*- coding: utf-8 -*-
"""Verify the two NEW filters (market regime + entry confirmation) against
live data using the deployed bot/sim, WITHOUT placing orders."""
import io, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, r"C:\Users\DELL\OneDrive\1m\propr-bot")

import sim
from bot import live_mid

def main():
    universe = sorted(sim.load())
    print(f"universe: {len(universe)} assets")
    # live candle snapshot (same as bot.snapshot)
    raw = {}
    for s in universe:
        try:
            df = sim.fetch_candles(s, days=(400 // 24) + 3)
        except Exception as e:
            print(f"  candles {s}: {e}")
            continue
        if len(df) > 220:
            raw[s] = df.iloc[:-1]
    D = sim.prep(raw)
    print(f"bars: {D['index'][-1]}  universe: {len(D['cols'])}")
    cfg = sim.Cfg(strat="mom", lb=96, k=3, allow_short=False,
                  risk_pct=0.005, sl_atr=2.0, tp_r=2.0,
                  max_concurrent=1, max_hold=72, cooldown=4,
                  daily_halt=0.02, dd_halt=0.045)
    sig = sim.SIGNALS["mom"](D, cfg)[-1]
    fired = [int(i) for i in __import__("numpy").flatnonzero(sig)]
    print(f"momentum signals on last bar: {[D['cols'][i] for i in fired]}")
    # per-asset entry confirmation check
    for i in fired:
        asset = D["cols"][i]
        px, atr = float(D["close"][-1, i]), float(D["atr"][-1, i])
        live = live_mid(asset)
        sleeve = max(cfg.max_entry_slip * 10, 0.5 * atr / px) if atr > 0 else 0.015
        slip = (live / px - 1) if live else None
        slip_txt = f"{slip:+.2%}" if slip is not None else "n/a"
        print(f"  {asset}: close {px:.4f} atr {atr:.4f} live {live} "
              f"slip {slip_txt} sleeve {sleeve:.1%} "
              f"-> {'SKIP' if (slip is not None and abs(slip) > sleeve) else 'OK'}")
    # market regime check
    ok, why = _market_ok(D, cfg)
    print(f"market regime: {'OPEN for entries' if ok else 'CLOSED'}" + (f" -- {why}" if why else ""))

def _market_ok(D, cfg):
    btc_i = next((i for i, c in enumerate(D["cols"]) if c == "BTC"), None)
    C, V = D["close"], D["valid"]
    rows = C.shape[0]
    if rows < 50:
        return True, None
    btc_ret = None
    if btc_i is not None and V[-1, btc_i] and V[-25, btc_i]:
        btc_ret = C[-1, btc_i] / C[-25, btc_i] - 1
    base, now = C[-25, :], C[-1, :]
    ok = (base > 0) & V[-25, :] & V[-1, :]
    frac_up = float((ok & (now > base)).sum()) / max(1, int(ok.sum()))
    btc_mid = None
    if btc_i is not None and V[-1, btc_i] and V[-97, btc_i]:
        btc_mid = (D["high"][-96:, btc_i].max() + D["low"][-96:, btc_i].min()) / 2
    msgs = []
    if btc_i is not None and V[-1, btc_i] and V[-97, btc_i] and C[-1, btc_i] < btc_mid:
        msgs.append(f"BTC {C[-1, btc_i]:,.0f} below 96h midpoint {btc_mid:,.0f}")
    if btc_ret is not None and btc_ret <= -0.02:
        msgs.append(f"BTC 24h {btc_ret:+.1%}")
    if frac_up < 0.30:
        msgs.append(f"breadth {int((ok & (now > base)).sum())}/{int(ok.sum())} up 24h")
    return (not msgs), ("; ".join(msgs) if msgs else None)

if __name__ == "__main__":
    main()
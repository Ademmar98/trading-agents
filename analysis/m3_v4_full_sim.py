#!/usr/bin/env python3
"""
M3 (Microstructure Absorption) v4 — Full 30-Day Simulation on ALL 177 Hyperliquid Pairs
Uses cached data.
"""
import sys, pickle, json
import numpy as np
import pandas as pd
from pathlib import Path

sys.stdout.reconfigure(line_buffering=True)

INITIAL_CAPITAL = 5000.0
DAILY_LOSS_LIMIT = 150.0
MAX_DD_LIMIT = 300.0
FEE = 0.00075
SLIP = 0.0005
COST = FEE + SLIP
DATA = Path(__file__).parent / "hl_177_30d_1h.pkl"

# M3 config from challenge_passer.py
CFG = {
    "cvd_thresh": -0.05,
    "rv_thresh": 0.03,
    "dip_pct": 0.008,
    "sl_mult": 1.5,
    "tp_mult": 3.0,
    "pos_size_pct": 0.40,
    "max_concurrent": 3,
    "max_hold_bars": 24,
}


def compute_features(df):
    if len(df) < 60:
        return None
    close = df["close"].values
    volume = df["volume"].values
    high = df["high"].values
    low = df["low"].values

    buy_vol = np.where(close[1:] > close[:-1], volume[1:], 0)
    sell_vol = np.where(close[1:] < close[:-1], volume[1:], 0)
    buy_vol = np.append([0], buy_vol)
    sell_vol = np.append([0], sell_vol)

    cvd_raw = buy_vol - sell_vol
    cvd_cumsum = np.cumsum(cvd_raw)
    lb = 20
    cvd_z = (cvd_cumsum[-1] - np.mean(cvd_cumsum[-lb:])) / (
        np.std(cvd_cumsum[-lb:]) + 1e-10
    )

    returns = np.diff(np.log(close + 1e-10))
    rv = np.std(returns[-20:]) * np.sqrt(365)

    current_price = close[-1]
    lookback = 60
    recent_high = np.max(high[-lookback:])
    dip = (recent_high - current_price) / recent_high if recent_high > 0 else 0

    tr = np.maximum(
        high[1:] - low[1:],
        np.maximum(
            np.abs(high[1:] - close[:-1]),
            np.abs(low[1:] - close[:-1])
        )
    )
    atr = float(np.mean(tr[-14:]))

    return {
        "cvd_z": cvd_z,
        "rv": rv,
        "dip": dip,
        "current_price": current_price,
        "atr": atr,
        "volume_ratio": volume[-1] / np.mean(volume[-20:]) if np.mean(volume[-20:]) > 0 else 1,
    }


def run(all_data):
    cfg = CFG
    proc = {}
    for sym, df in all_data.items():
        feat_list = []
        for i in range(60, len(df)):
            sub = df.iloc[:i+1]
            f = compute_features(sub)
            if f:
                feat_list.append(f)
            else:
                feat_list.append(None)
        proc[sym] = {
            "df": df,
            "features": feat_list,
        }

    cap = INITIAL_CAPITAL
    peak = INITIAL_CAPITAL
    positions = []
    trades = []
    d_pnl = 0.0
    cur_day = None
    d_halt = False
    failed = False
    equity_curve = []
    daily_log = []
    last_entry = {s: -4 for s in proc}

    ref = list(proc.keys())[0]
    timestamps = proc[ref]["df"].index

    for bi in range(60, len(timestamps)):
        ts = timestamps[bi]
        px = {}
        for s in proc:
            df = proc[s]["df"]
            px[s] = df.iloc[bi]["close"] if bi < len(df) else df.iloc[-1]["close"]

        day = ts.date()
        if cur_day is None or day != cur_day:
            if cur_day is not None:
                daily_log.append({"day": str(cur_day), "pnl": round(d_pnl, 2),
                                  "equity": round(cap + sum(p["ep"]*p["qty"] + (px[p["sym"]]-p["ep"])*p["qty"] for p in positions), 2),
                                  "positions": len(positions)})
            if cur_day is not None:
                d_pnl = 0.0
            cur_day = day
            d_halt = False

        if failed:
            break

        # EXIT
        closed = set()
        for p in positions:
            s = p["sym"]
            if s not in proc or bi >= len(proc[s]["df"]):
                continue
            row = proc[s]["df"].iloc[bi]
            bh = bi - p["eb"]

            reason = None
            exit_px = None
            if row["low"] <= p["sl"]:
                reason = "stop_loss"
                exit_px = p["sl"]
            elif row["high"] >= p["tp"]:
                reason = "take_profit"
                exit_px = p["tp"]
            elif bh >= cfg["max_hold_bars"]:
                reason = "time_exit"
                exit_px = row["close"]

            if reason:
                ep = exit_px * (1 - SLIP)
                pnl = (ep - p["ep"]) * p["qty"] - ep * p["qty"] * COST
                cap += p["ep"] * p["qty"] + pnl
                d_pnl += pnl
                trades.append({
                    "sym": s, "reason": reason,
                    "entry": round(p["ep"], 6), "exit": round(ep, 6),
                    "qty": round(p["qty"], 4), "pnl": round(pnl, 2),
                    "bars_held": bh, "ts": str(ts),
                })
                closed.add(s)

        positions = [p for p in positions if p["sym"] not in closed]

        eq = cap + sum(p["ep"]*p["qty"] + (px[p["sym"]]-p["ep"])*p["qty"] for p in positions)
        if eq > peak:
            peak = eq
        dd = peak - eq
        equity_curve.append({"ts": str(ts), "equity": round(eq, 2)})

        if dd > MAX_DD_LIMIT:
            failed = True
            for p in positions:
                ep = px[p["sym"]] * (1 - SLIP)
                pnl = (ep - p["ep"]) * p["qty"] - ep * p["qty"] * COST
                trades.append({"sym": p["sym"], "reason": "dd_exit",
                               "entry": round(p["ep"], 6), "exit": round(ep, 6),
                               "qty": round(p["qty"], 4), "pnl": round(pnl, 2),
                               "bars_held": bi-p["eb"], "ts": str(ts)})
            positions.clear()
            break

        if d_pnl < -DAILY_LOSS_LIMIT:
            d_halt = True
            for p in positions:
                ep = px[p["sym"]] * (1 - SLIP)
                pnl = (ep - p["ep"]) * p["qty"] - ep * p["qty"] * COST
                trades.append({"sym": p["sym"], "reason": "daily_exit",
                               "entry": round(p["ep"], 6), "exit": round(ep, 6),
                               "qty": round(p["qty"], 4), "pnl": round(pnl, 2),
                               "bars_held": bi-p["eb"], "ts": str(ts)})
            positions.clear()
        if d_halt:
            continue

        # ENTRY
        held = {p["sym"] for p in positions}
        candidates = []
        for s in proc:
            if s in held or len(positions) >= cfg["max_concurrent"]:
                continue
            if (bi - last_entry[s]) < 4:
                continue
            if bi >= len(proc[s]["features"]) or proc[s]["features"][bi] is None:
                continue
            f = proc[s]["features"][bi]
            if f["cvd_z"] < cfg["cvd_thresh"] and f["rv"] > cfg["rv_thresh"] and f["dip"] > cfg["dip_pct"]:
                candidates.append((s, f))

        candidates.sort(key=lambda x: x[1]["cvd_z"])

        for s, f in candidates:
            if s in held or len(positions) >= cfg["max_concurrent"]:
                break

            entry_price = f["current_price"] * (1 + SLIP)
            atr = f["atr"]
            sl = entry_price - (atr * cfg["sl_mult"])
            tp = entry_price + (atr * cfg["tp_mult"])

            sl_dist = entry_price - sl
            if sl_dist / entry_price > 0.10:
                continue

            notional = cap * cfg["pos_size_pct"]
            if notional > cap:
                notional = cap
            fee_cost = notional * COST
            if notional + fee_cost > cap:
                continue
            qty = notional / entry_price

            positions.append({
                "sym": s, "ep": entry_price, "qty": qty,
                "sl": sl, "tp": tp, "eb": bi,
            })
            cap -= (entry_price * qty + fee_cost)
            last_entry[s] = bi
            trades.append({
                "sym": s, "reason": "entry",
                "entry": round(entry_price, 6), "exit": None,
                "qty": round(qty, 4), "pnl": 0,
                "bars_held": 0, "ts": str(ts),
                "cvd_z": round(f["cvd_z"], 3),
                "rv": round(f["rv"], 4),
                "dip": round(f["dip"], 4),
                "atr": round(atr, 2),
            })

    for p in positions:
        if p["sym"] in px:
            ep = px[p["sym"]] * (1 - SLIP)
            pnl = (ep - p["ep"]) * p["qty"] - ep * p["qty"] * COST
            cap += p["ep"] * p["qty"] + pnl
            trades.append({"sym": p["sym"], "reason": "force_exit",
                           "entry": round(p["ep"], 6), "exit": round(ep, 6),
                           "qty": round(p["qty"], 4), "pnl": round(pnl, 2),
                           "bars_held": 0, "ts": str(timestamps[-1])})

    daily_log.append({"day": str(cur_day), "pnl": round(d_pnl, 2),
                       "equity": round(cap, 2), "positions": 0})

    return {
        "final_capital": round(cap, 2),
        "total_pnl": round(cap - INITIAL_CAPITAL, 2),
        "total_return_pct": round((cap - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100, 2),
        "total_trades": len([t for t in trades if t["reason"] != "entry"]),
        "challenged_failed": failed,
        "trades": trades,
        "daily_log": daily_log,
        "equity_curve": equity_curve,
    }


def main():
    print("=" * 90)
    print("M3 (MICROSTRUCTURE ABSORPTION) v4 — FULL 30-DAY SIMULATION")
    print("=" * 90)
    print(f"Config: CVD<{CFG['cvd_thresh']} | RV>{CFG['rv_thresh']} | Dip>{CFG['dip_pct']:.1%}")
    print(f"SL={CFG['sl_mult']}×ATR | TP={CFG['tp_mult']}×ATR | Size={CFG['pos_size_pct']:.0%} | MaxConc={CFG['max_concurrent']}")
    print(f"Capital: ${INITIAL_CAPITAL:,.0f} | DD limit: ${MAX_DD_LIMIT} | Daily limit: ${DAILY_LOSS_LIMIT}")
    print()

    with open(DATA, "rb") as f:
        all_data = pickle.load(f)
    print(f"Loaded {len(all_data)} symbols from cache")
    print("Running simulation (this may take a few minutes)...\n")

    result = run(all_data)

    print()
    print("=" * 90)
    print("RESULTS")
    print("=" * 90)
    print(f"  Final Capital:  ${result['final_capital']:>10,.2f}")
    print(f"  Total PnL:      ${result['total_pnl']:>10,.2f} ({result['total_return_pct']:+.2f}%)")
    print(f"  Total Trades:   {result['total_trades']}")
    print(f"  Challenge:      {'FAILED' if result['challenged_failed'] else 'PASSED'}")

    exits = [t for t in result["trades"] if t["reason"] != "entry"]
    if exits:
        wins = [t for t in exits if t["pnl"] > 0]
        losses = [t for t in exits if t["pnl"] <= 0]
        wr = len(wins) / len(exits) * 100
        avg_win = np.mean([t["pnl"] for t in wins]) if wins else 0
        avg_loss = np.mean([t["pnl"] for t in losses]) if losses else 0
        gp = sum(t["pnl"] for t in wins)
        gl = abs(sum(t["pnl"] for t in losses))
        pf = gp / max(gl, 0.01)

        print(f"\n  Win Rate:       {wr:.1f}% ({len(wins)}W / {len(losses)}L)")
        print(f"  Avg Win:        ${avg_win:>+8.2f}")
        print(f"  Avg Loss:       ${avg_loss:>+8.2f}")
        print(f"  Profit Factor:  {pf:.2f}")
        print(f"  Best Trade:     ${max(t['pnl'] for t in exits):>+8.2f}")
        print(f"  Worst Trade:    ${min(t['pnl'] for t in exits):>+8.2f}")

        reasons = {}
        for t in exits:
            reasons[t["reason"]] = reasons.get(t["reason"], 0) + 1
        print(f"\n  Exit Reasons:")
        for r, n in sorted(reasons.items(), key=lambda x: -x[1]):
            rpnl = sum(t["pnl"] for t in exits if t["reason"] == r)
            print(f"    {r:15s}: {n:3d} trades  PnL=${rpnl:>+8.2f}")

    sym_stats = {}
    for t in exits:
        s = t["sym"]
        if s not in sym_stats:
            sym_stats[s] = {"trades": 0, "wins": 0, "pnl": 0}
        sym_stats[s]["trades"] += 1
        sym_stats[s]["pnl"] += t["pnl"]
        if t["pnl"] > 0:
            sym_stats[s]["wins"] += 1

    print(f"\n  Per-Symbol (sorted by PnL):")
    print(f"  {'Sym':10s} {'Tr':>3} {'W':>3} {'L':>3} {'WR%':>5} {'PnL':>10}")
    print(f"  {'-'*45}")
    for s, st in sorted(sym_stats.items(), key=lambda x: -x[1]["pnl"]):
        sw = st["wins"]
        sl = st["trades"] - sw
        swr = sw / max(st["trades"], 1) * 100
        print(f"  {s:10s} {st['trades']:3d} {sw:3d} {sl:3d} {swr:5.1f}% ${st['pnl']:>+8.2f}")

    print(f"\n  Daily PnL:")
    for dl in result["daily_log"]:
        if dl["pnl"] != 0:
            print(f"    {dl['day']}: ${dl['pnl']:>+8.2f}  (equity: ${dl['equity']:,.2f})")

    out_path = Path(__file__).parent / "m3_v4_simulation.json"
    with open(out_path, "w") as f:
        json.dump(result, f, indent=2)
    print(f"\n  Full results saved to m3_v4_simulation.json")


if __name__ == "__main__":
    main()

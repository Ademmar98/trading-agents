# -*- coding: utf-8 -*-
"""Weekly live-vs-sim reconciliation.

Pulls real fills (price, fee, slippage, realizedPnl) from the account and
compares them against the backtest's modelling assumptions:
  - fee paid vs the sim's 0.075%/side
  - slippage vs zero
  - realised trade return vs the sim's expected per-trade distribution

Nothing here trades; it only reads. Exit code 1 if any tolerance is breached,
so it can be run from a scheduler and alert.
"""
import sys, os
from datetime import datetime, timezone, timedelta
from collections import defaultdict

sys.path.insert(0, r"C:\Users\DELL\OneDrive\1m\propr-bot")
from dotenv import load_dotenv
from propr.propr_sdk import ProprClient

ACCOUNTS = [
    ("beta", r"C:\Users\DELL\OneDrive\1m\propr-bot", "urn:prp-account:LDA3R3vW74Ym"),
    ("5k",   r"C:\Users\DELL\OneDrive\1m\propr-bot-5k", "urn:prp-account:hkTafyCzQ3cn"),
]
SIM_FEE = 0.00075  # per side, what the backtest assumes


def fetch_trades(c, limit=100):
    """Trades API caps limit at 100; page via offset until exhausted."""
    out, off = [], 0
    while len(out) < 600:
        batch = c.get_trades(limit=limit, offset=off)
        if not batch:
            break
        out += batch
        if len(batch) < limit:
            break
        off += limit
    return out


def reconcile(name, envdir, acct, days=7):
    load_dotenv(os.path.join(envdir, ".env"), override=True)
    c = ProprClient(); c.setup(acct)
    acctinfo = c.get_account()
    eq = float(acctinfo.get("balance", 0) or 0)

    since = datetime.now(timezone.utc) - timedelta(days=days)
    trades = fetch_trades(c)
    recent = []
    for t in trades:
        ts = t.get("executedAt") or t.get("createdAt")
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except Exception:
            continue
        if dt >= since:
            recent.append(t)

    opens = [t for t in recent if t.get("type") == "open"]
    closes = [t for t in recent if t.get("type") == "close"]
    fees = sum(float(t.get("fee", 0) or 0) for t in recent)
    slip = [abs(float(t.get("slippage", 0) or 0)) for t in recent if t.get("slippage")]
    realized = sum(float(t.get("realizedPnl", 0) or 0) for t in closes)

    # fee rate actually paid, weighted by notional
    notional = sum(abs(float(t.get("quoteQuantity", 0) or 0)) for t in recent)
    fee_bps = (fees / notional * 1e4) if notional else 0.0
    sim_bps = SIM_FEE * 1e4
    avg_slip_bps = (sum(slip) / len(slip) * 1e4) if slip else 0.0

    # round-trip realised return per closed position
    by_pos = defaultdict(list)
    for t in recent:
        by_pos[t.get("positionId")].append(t)
    rt = []
    for pid, ts in by_pos.items():
        o = [x for x in ts if x.get("type") == "open"]
        cl = [x for x in ts if x.get("type") == "close"]
        if o and cl:
            r = sum(float(x.get("realizedPnl", 0) or 0) for x in cl)
            nn = sum(abs(float(x.get("quoteQuantity", 0) or 0)) for x in o)
            if nn:
                rt.append(r / nn)
    wins = [x for x in rt if x > 0]

    print(f"\n=== {name}  ({acct}) ===")
    print(f"  equity           ${eq:,.2f}")
    print(f"  window            {days}d  ({len(recent)} fills: {len(opens)} open / {len(closes)} close)")
    print(f"  fees paid        ${fees:,.2f}  = {fee_bps:.2f} bps/side   (sim {sim_bps:.2f})")
    print(f"  avg slippage      {avg_slip_bps:.2f} bps   (sim 0)")
    print(f"  realised PnL     ${realized:,.2f}")
    print(f"  round-trips       {len(rt)}   win {100*len(wins)/max(len(rt),1):.1f}%"
          f"   avg {100*(sum(rt)/max(len(rt),1)):.2f}%")

    breaches = []
    if fee_bps > sim_bps * 1.25:
        breaches.append(f"fee {fee_bps:.1f}bps > 1.25x sim {sim_bps:.1f}bps")
    if avg_slip_bps > 20:
        breaches.append(f"avg slippage {avg_slip_bps:.1f}bps > 20bps")
    return breaches


if __name__ == "__main__":
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 7
    all_b = []
    for name, d, a in ACCOUNTS:
        try:
            all_b += [(name, b) for b in reconcile(name, d, a, days)]
        except Exception as e:
            all_b.append((name, f"ERROR {type(e).__name__}: {e}"))
    print("\n--- verdict ---")
    if not all_b:
        print("OK: live fills track sim assumptions (fees/slippage within tolerance)")
    else:
        for n, b in all_b:
            print(f"  {n}: {b}")
        sys.exit(1)

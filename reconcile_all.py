# -*- coding: utf-8 -*-
"""Weekly live-vs-sim reconciliation across every deployment.

Pulls real fills from each account and compares them against the backtest's
assumptions (fee per side, slippage, realised round-trip return). Accounts that
are not configured yet are skipped, so this is safe to run before the third 5K
demo is funded.

    python reconcile_all.py [days]

Nothing here trades; it only reads. Exit code 1 if a tolerance is breached, so
a scheduler can alert on it.
"""
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

sys.path.insert(0, r"C:\Users\DELL\OneDrive\1m\propr-bot")
from dotenv import load_dotenv
from propr.propr_sdk import ProprClient

ROOT = r"C:\Users\DELL\OneDrive\1m"
ACCOUNTS = [
    ("beta", os.path.join(ROOT, "propr-bot"), "urn:prp-account:LDA3R3vW74Ym"),
    ("5k", os.path.join(ROOT, "propr-bot-5k"), "urn:prp-account:hkTafyCzQ3cn"),
    ("5k2", os.path.join(ROOT, "propr-bot-5k2"), None),   # id comes from .env
]
SIM_FEE = 0.00045   # per side, what simlib charges (measured ~4.5bps live)


def configured(envdir, acct):
    env = os.path.join(envdir, ".env")
    if not os.path.exists(env):
        return False, acct, "no .env"
    load_dotenv(env, override=True)
    key = os.getenv("PROPR_API_KEY", "")
    if not key.startswith(("pk_beta_", "pk_live_")) or "PASTE" in key.upper():
        return False, acct, "no credentials"
    aid = acct or os.getenv("PROPR_ACCOUNT_ID", "")
    if not aid.startswith("urn:prp-account:") or "PASTE" in aid.upper():
        return False, aid, "no account id"
    return True, aid, ""


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
    c = ProprClient()
    c.setup(acct)
    acctinfo = c.get_account()
    eq = float(acctinfo.get("balance", 0) or 0)

    since = datetime.now(timezone.utc) - timedelta(days=days)
    recent = []
    for t in fetch_trades(c):
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
    slip = [abs(float(t.get("slippage", 0) or 0)) for t in recent
            if t.get("slippage")]
    realized = sum(float(t.get("realizedPnl", 0) or 0) for t in closes)

    notional = sum(abs(float(t.get("quoteQuantity", 0) or 0)) for t in recent)
    fee_bps = (fees / notional * 1e4) if notional else 0.0
    sim_bps = SIM_FEE * 1e4
    avg_slip_bps = (sum(slip) / len(slip) * 1e4) if slip else 0.0

    by_pos = defaultdict(list)
    for t in recent:
        by_pos[t.get("positionId")].append(t)
    rt = []
    for _, ts in by_pos.items():
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
    print(f"  window            {days}d  ({len(recent)} fills: "
          f"{len(opens)} open / {len(closes)} close)")
    print(f"  fees paid        ${fees:,.2f}  = {fee_bps:.2f} bps/side   "
          f"(sim {sim_bps:.2f})")
    print(f"  avg slippage      {avg_slip_bps:.2f} bps   (sim 0)")
    print(f"  realised PnL     ${realized:,.2f}")
    print(f"  round-trips       {len(rt)}   "
          f"win {100 * len(wins) / max(len(rt), 1):.1f}%   "
          f"avg {100 * (sum(rt) / max(len(rt), 1)):.2f}%")

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
        ok, aid, why = configured(d, a)
        if not ok:
            print(f"\n=== {name} ===  skipped ({why})")
            continue
        try:
            all_b += [(name, b) for b in reconcile(name, d, aid, days)]
        except Exception as e:
            all_b.append((name, f"ERROR {type(e).__name__}: {e}"))
    print("\n--- verdict ---")
    if not all_b:
        print("OK: live fills track sim assumptions (fees/slippage within "
              "tolerance)")
    else:
        for n, b in all_b:
            print(f"  {n}: {b}")
        sys.exit(1)

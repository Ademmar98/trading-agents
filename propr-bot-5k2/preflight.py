# -*- coding: utf-8 -*-
"""Pre-flight for propr-bot-5k2. Read-only: places NO orders.

Run this after filling .env and before starting the watchdog:

    python preflight.py

It verifies credentials, that the account matches the one in .env, that the
account is clean, that the universe/funding feed works, and then prints the
exact first-cycle plan (pairs, size per slot, entry limit, stop, take-profit)
so there are no surprises when the bot goes live. Exit code 0 = safe to start.
"""
import argparse
import sys
import time

import fund_bot as fb

FAIL, WARN = [], []


def check(ok, msg, hard=True):
    tag = "PASS" if ok else ("FAIL" if hard else "WARN")
    print(f"  [{tag}] {msg}")
    if not ok:
        (FAIL if hard else WARN).append(msg)
    return ok


def main():
    print("=" * 74)
    print("PROPR 5K DEMO #2 - PREFLIGHT (read-only, no orders)")
    print("=" * 74)

    key = (fb.os.getenv("PROPR_API_KEY") or "").strip()
    acct_env = (fb.os.getenv("PROPR_ACCOUNT_ID") or "").strip()
    venue = (fb.os.getenv("PROPR_API_URL") or "").strip()
    start = fb.CHALLENGE_START

    print(f"\n1. credentials")
    if not check(key.startswith(("pk_live_", "pk_beta_")), "API key present"):
        return finish()
    check("PASTE" not in key.upper(), "API key is not a placeholder")
    if not check(acct_env.startswith("urn:prp-account:"), "account id present"):
        return finish()
    check("PASTE" not in acct_env.upper(), "account id is not a placeholder")
    live_key = key.startswith("pk_live_")
    check(live_key == ("beta" not in venue),
          f"key type matches venue ({venue})")
    check(abs(start - 5000) < 1, f"CHALLENGE_START is $5,000 (got ${start:,.0f})")
    if FAIL:
        # No point probing the API with a placeholder key.
        return finish()

    print(f"\n2. connectivity")
    ns = argparse.Namespace(q=5, K=336, gross=0.20, rebal_h=168.0, daily_halt=0.02,
                            dd_halt=0.05, target=0.10, stop=0.20, tp_mult=1.5,
                            daily_wall=0.03, dd_wall=0.06, live=False)
    try:
        bot = fb.FundBot(ns)
        got = bot.client.setup(fb.ACCOUNT_ID, starting_balance=start)
    except Exception as e:
        check(False, f"client setup: {type(e).__name__}: {e}")
        return finish()
    check(True, f"authenticated, account {got}")
    check(got == acct_env, "API resolved the account named in .env "
                           f"({acct_env})")

    eq = bot.client.equity
    bal = bot.client.balance
    print(f"       balance ${bal:,.2f}  unrealized "
          f"${bot.client.unrealized_pnl:+,.2f}  equity ${eq:,.2f}")
    check(0.95 * start <= eq <= 1.10 * start,
          f"equity ${eq:,.2f} is in range for a fresh ${start:,.0f} account",
          hard=False)

    print(f"\n3. clean slate")
    try:
        pos = [p for p in (bot.client.sdk.get_open_positions() or [])
               if abs(float(p.get("quantity") or 0)) > 0]
        orders = bot.client.sdk.get_orders(status="pending", limit=100) or []
    except Exception as e:
        check(False, f"could not read positions/orders: {e}")
        return finish()
    check(not pos, f"no open positions ({len(pos)} found)")
    check(not orders, f"no resting orders ({len(orders)} found)", hard=False)
    for p in pos:
        print(f"       leftover: {p.get('base')} qty {p.get('quantity')} "
              f"@ {p.get('entryPrice')}")

    print(f"\n4. data feed")
    t0 = time.time()
    try:
        uni = bot.universe
        ok = len(uni) >= 20
        check(ok, f"universe resolved: {len(uni)} coins "
                  f"({time.time() - t0:.1f}s)")
    except Exception as e:
        check(False, f"universe: {e}")
        return finish()

    t0 = time.time()
    try:
        Fdata = fb.refresh_funding(uni)
        tf = fb.trailing_funding(Fdata, uni, bot.K)
        check(len(tf) >= bot.q + 2,
              f"funding ranked {len(tf)} coins over {bot.K}h "
              f"({time.time() - t0:.0f}s)")
    except Exception as e:
        check(False, f"funding feed: {e}")
        return finish()

    print(f"\n5. first-cycle plan (what the bot will do at go-live)")
    targets = [c for c, _ in sorted(tf.items(), key=lambda kv: -kv[1])[:bot.q]]
    per = eq * bot.gross / bot.q
    budget = start * bot.dd_wall
    stop_pct = min(bot.stop_target, 0.95 * budget / max(eq * bot.gross, 1e-9))
    print(f"       gross {bot.gross:.0%}  slots {bot.q}  "
          f"${per:,.2f}/slot (${eq * bot.gross:,.0f} deployed)")
    print(f"       stop -{stop_pct:.1%}  tp +{stop_pct * bot.tp_mult:.1%}  "
          f"stop risk ${bot.q * per * stop_pct:,.0f} vs ${budget:,.0f} gate")
    check(bot.q * per * stop_pct <= budget,
          "aggregate stop risk fits inside the 6% drawdown gate", hard=False)
    print(f"\n       {'pair':<8}{'fund 336h':>12}{'mid':>13}"
          f"{'limit':>13}{'stop':>13}{'tp':>13}{'spread':>9}")
    usable = 0
    for a in targets:
        bk = bot._book(a)
        if not bk:
            print(f"       {a:<8}{tf[a] * 1e4:>+11.2f}b   no book - bot will "
                  f"retry next cycle")
            continue
        lim = min(bk["bid"] * (1 + fb.LIMIT_IMPROVE_BPS / 1e4),
                  bk["mid"] * (1 + fb.LIMIT_MAX_ABOVE_MID_BPS / 1e4),
                  bk["ask"] * (1 - 0.05 / 1e4))
        sl = bk["mid"] * (1 - stop_pct)
        tp = bk["mid"] * (1 + stop_pct * bot.tp_mult)
        chase_ok, why = bot._chase_ok(a)
        flag = "" if chase_ok else f"  <- chase filter: {why}"
        if chase_ok and bk["spread_bps"] <= fb.MAX_SPREAD_BPS:
            usable += 1
        print(f"       {a:<8}{tf[a] * 1e4:>+11.2f}b{bk['mid']:>13.6g}"
              f"{lim:>13.6g}{sl:>13.6g}{tp:>13.6g}"
              f"{bk['spread_bps']:>8.2f}b{flag}")
    check(usable >= 3, f"{usable}/{bot.q} names are currently tradable "
                       f"(spread < {fb.MAX_SPREAD_BPS:.0f}bps, not chased)",
          hard=False)

    print(f"\n6. guard rails")
    print(f"       wall -6% -> equity floor ${start * 0.94:,.2f}")
    print(f"       daily halt -2% of day-start, dd halt -5% of start")
    print(f"       single-instance lock port {fb.LOCK_PORT}")

    return finish()


def finish():
    print("\n" + "-" * 74)
    if FAIL:
        print(f"PREFLIGHT FAILED - {len(FAIL)} blocking issue(s):")
        for m in FAIL:
            print(f"  - {m}")
        print("Do NOT start the watchdog until these are resolved.")
        return 1
    if WARN:
        print(f"PREFLIGHT PASSED with {len(WARN)} warning(s):")
        for m in WARN:
            print(f"  - {m}")
    else:
        print("PREFLIGHT PASSED - credentials, account, data feed and plan all "
              "check out.")
    print("Next:  python watchdog.py     (or start_5k2.ps1)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

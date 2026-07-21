"""pause_and_switch_broker.py — graceful pause of the active session + MT5 bring-up.

⚠ TWO HARD BLOCKERS (read execution/MT5_TRANSITION.md before running):
  1. MetaTrader5's Python API is WINDOWS-ONLY and needs the MT5 terminal running
     on the same host. The firm runs on a LINUX VPS — it cannot reach MT5 from
     there. Phase B must run on a Windows host with the terminal + `MetaTrader5`.
  2. The firm has NO MT5 broker adapter yet (no config vars, no trader branch,
     no MetaQuotesBroker). MT5 verification passing here does NOT mean the firm
     can trade via MT5 — that adapter still has to be built and wired.

So this script does the SAFE work now: snapshot the paused session (Phase A,
runs anywhere with API access) and VERIFY the MT5 account/symbols/limit-order
support (Phase B, runs on the Windows MT5 host). It does not itself reconfigure
the live firm — that is a deliberate, staged decision after both blockers clear.

Halal: spot/FX long-only; no shorting, leverage, or funding legs are placed.
"""
import asyncio
import json
import os
from datetime import datetime, timezone

FIRM_API = os.getenv("FIRM_API", "http://69.48.202.100")
SNAPSHOT_PATH = "research/strategy_sweep_2026_07/test_week_pause_snapshot.json"
MT5_SYMBOLS = ["BTCUSD", "ETHUSD", "SOLUSD"]      # FTMO-Demo symbol names (verify actual)


# ── Phase A: graceful pause of the active (paper/VPS) session ──
async def snapshot_session():
    """Freeze the active session metrics to SNAPSHOT_PATH. Read-only over the API."""
    import urllib.request

    def get(p):
        return json.loads(urllib.request.urlopen(f"{FIRM_API}{p}", timeout=30).read())

    s = await asyncio.to_thread(get, "/api/summary")
    f = await asyncio.to_thread(get, "/api/fill-diagnostics?days=2")
    t = f["totals"]
    snap = {
        "snapshot_at": datetime.now(timezone.utc).isoformat(),
        "portfolio_equity": s["equity"], "portfolio_pnl_pct": s["total_pnl_pct"],
        "open_positions": s["open_positions"], "closed_trades": s["total_trades"],
        "fill_rates": {"total_quotes": t["total_quotes"], "fill_rate_pct": t["fill_rate_pct"]},
        "adverse_selection_1m_pct": t.get("adverse_1m_pct"),
        "net_spread_saved_usd": t["net_spread_saved_usd"],
    }
    os.makedirs(os.path.dirname(SNAPSHOT_PATH), exist_ok=True)
    json.dump(snap, open(SNAPSHOT_PATH, "w"), indent=2)
    print(f"[pause] snapshot -> {SNAPSHOT_PATH}: equity ${snap['portfolio_equity']:,.2f}, "
          f"spread saved ${snap['net_spread_saved_usd']}")
    return snap


def cancel_resting_limits():
    """Cancel the firm's resting maker limits to prevent orphaned entries.
    RUN ON THE FIRM HOST (imports the firm's DB). On the paper broker these are
    the simulated pending_orders; on a live exchange this would send real
    cancel requests. Guarded so it no-ops off-host."""
    try:
        from core.pending_orders import open_pending
        from core.database import execute
    except ImportError:
        print("[pause] cancel_resting_limits skipped — not on the firm host.")
        return 0
    pend = open_pending()
    for po in pend:
        execute("UPDATE pending_orders SET status='cancelled' WHERE id=?", [po["id"]])
    print(f"[pause] cancelled {len(pend)} resting limit(s).")
    return len(pend)


async def graceful_pause():
    """Snapshot, cancel resting quotes, flush. Does NOT stop the systemd service
    — that is an explicit operator action (systemctl stop trading-firm)."""
    await snapshot_session()
    cancel_resting_limits()
    print("[pause] flush complete. Stop the service manually when ready: "
          "`systemctl stop trading-firm`.")


# ── Phase B: MT5 bring-up & verification (run on the Windows MT5 host) ──
def init_and_verify_mt5():
    try:
        import MetaTrader5 as mt5
    except ImportError:
        raise SystemExit(
            "MetaTrader5 not installed / not on Windows. Phase B must run on a "
            "Windows host with the MT5 terminal. See MT5_TRANSITION.md.")

    login = int(os.environ["MT5_LOGIN"])
    password = os.environ["MT5_PASSWORD"]              # secure env var — never hardcode
    server = os.getenv("MT5_SERVER", "FTMO-Demo")

    if not mt5.initialize():
        raise SystemExit(f"mt5.initialize() failed: {mt5.last_error()}")
    if not mt5.login(login, password=password, server=server):
        mt5.shutdown()
        raise SystemExit(f"mt5.login() failed: {mt5.last_error()}")

    acct = mt5.account_info()
    print(f"[mt5] connected — login {acct.login} @ {server} | balance {acct.balance} "
          f"{acct.currency} | trade_allowed={acct.trade_allowed}")

    # 2. symbol mapping + spread/depth verification
    for sym in MT5_SYMBOLS:
        if not mt5.symbol_select(sym, True):
            print(f"[mt5] ! {sym} not available on {server} — check the broker's symbol name")
            continue
        info = mt5.symbol_info(sym)
        tick = mt5.symbol_info_tick(sym)
        spread_pts = info.spread
        spread_pct = (tick.ask - tick.bid) / ((tick.ask + tick.bid) / 2) * 100 if tick.ask else 0
        mt5.market_book_add(sym)
        dom = mt5.market_book_get(sym) or []
        print(f"[mt5] {sym}: bid {tick.bid} ask {tick.ask} spread {spread_pts}pt "
              f"({spread_pct:.3f}%) | DOM levels {len(dom)} | digits {info.digits}")
        mt5.market_book_release(sym)

        # 3. maker/limit compatibility — order_check a BUY_LIMIT WITHOUT sending
        req = {
            "action": mt5.TRADE_ACTION_PENDING,
            "symbol": sym, "volume": info.volume_min,
            "type": mt5.ORDER_TYPE_BUY_LIMIT,
            "price": round(tick.bid * 0.999, info.digits),   # passive: below bid
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_RETURN,        # RETURN = leave remainder resting (maker)
        }
        chk = mt5.order_check(req)
        ok = chk is not None and chk.retcode == 0
        print(f"[mt5] {sym} BUY_LIMIT compatibility: "
              f"{'OK' if ok else 'retcode=' + str(getattr(chk, 'retcode', '?'))} "
              f"({getattr(chk, 'comment', '')})")

    mt5.shutdown()
    print("[mt5] verification complete — connection is live, but the firm still "
          "needs an MT5 broker adapter before it can route orders here.")


def main():
    import argparse
    ap = argparse.ArgumentParser(description="Pause the session and/or verify MT5.")
    ap.add_argument("--pause", action="store_true", help="Phase A: snapshot + cancel resting limits")
    ap.add_argument("--verify-mt5", action="store_true", help="Phase B: MT5 connect + symbol/limit checks (Windows)")
    args = ap.parse_args()
    if args.pause:
        asyncio.run(graceful_pause())
    if args.verify_mt5:
        init_and_verify_mt5()
    if not (args.pause or args.verify_mt5):
        ap.print_help()


if __name__ == "__main__":
    main()

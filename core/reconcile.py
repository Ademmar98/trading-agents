import time

from core.memory import SharedMemory
from core.portfolio import load_portfolio


def reconcile_with_exchange(broker):
    """Compare the local ledger against actual exchange balances and report drift.

    Report-only by design: this account mixes live testnet fills with paper
    fills (and testnet accounts come pre-funded with unrelated assets), so
    auto-adopting exchange state would corrupt the simulation. The report makes
    drift visible so it can be dealt with deliberately.
    """
    memory = SharedMemory()
    try:
        balances = broker.get_balances()
    except Exception as e:
        memory.log("system", f"Reconciliation skipped: {e}")
        return None
    if not balances:
        memory.log("system", "Reconciliation skipped: no exchange balances available")
        return None

    p = load_portfolio()
    positions = []
    drifted = 0
    for sym, pos in p.positions.items():
        asset = sym.split("/")[0]
        on_exchange = balances.get(asset, 0.0)
        drift = pos.quantity - on_exchange
        # Paper fills never reach the exchange, so ledger-only quantity is
        # normal for them; the report can't tell paper from a missed live fill,
        # it can only surface the difference.
        if pos.quantity > 0 and on_exchange < pos.quantity * 0.99:
            note = "ledger exceeds exchange (paper fill or missed live fill)"
            drifted += 1
        elif pos.quantity < 0:
            note = "short exists only in the paper ledger (spot has no shorts)"
            drifted += 1
        else:
            note = "backed by exchange holdings"
        positions.append({
            "symbol": sym,
            "ledger_qty": round(pos.quantity, 8),
            "exchange_qty": round(on_exchange, 8),
            "drift": round(drift, 8),
            "note": note,
        })

    report = {
        "ledger_cash": round(p.cash, 2),
        "exchange_usdt": round(balances.get("USDT", 0.0), 2),
        "positions": positions,
        "drifted_positions": drifted,
        "exchange_assets": {k: round(v, 8) for k, v in sorted(balances.items())},
        "timestamp": time.time(),
    }
    memory.write("reports", "reconciliation", report)
    memory.log(
        "system",
        f"Reconciliation: ledger cash ${report['ledger_cash']:,.2f} vs exchange USDT "
        f"${report['exchange_usdt']:,.2f}, {drifted}/{len(positions)} positions drifted",
    )
    return report

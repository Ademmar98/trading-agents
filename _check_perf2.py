from core.database import init_db, fetchall
init_db()

positions = fetchall('SELECT * FROM positions WHERE status="open"')
print(f'Open positions ({len(positions)}):')
for p in positions:
    d = dict(p)
    print(f'  {d["symbol"]} {d["side"]} qty={d["quantity"]} entry={d["entry_price"]} '
          f'cur={d["current_price"]} sl={d["stop_loss"]} tp={d["take_profit"]} '
          f'pnl={d["pnl"]} partial={d.get("partial_taken", 0)} risk={d.get("initial_risk", 0)}')

trades = fetchall('SELECT closed_at, symbol, side, qty, pnl, pnl_pct, reason, strategy FROM trades ORDER BY closed_at DESC LIMIT 15')
print(f'\nRecent trades ({len(trades)}):')
for t in trades:
    d = dict(t)
    print(f'  {d["closed_at"]} {d["symbol"]} {d["side"]} qty={d["qty"]} pnl={d["pnl"]} ({d["pnl_pct"]}%) {d["reason"]} [{d["strategy"]}]')

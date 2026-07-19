import sys, json, urllib.request

BASE = "http://69.48.202.100"

def fetch(endpoint):
    try:
        r = urllib.request.urlopen(f"{BASE}{endpoint}", timeout=10)
        return json.loads(r.read())
    except Exception as e:
        return {"error": str(e)}

print("=== POSITIONS ===")
positions = fetch("/api/positions")
for p in positions:
    sl = p.get("stop_loss", 0)
    tp = p.get("take_profit", 0)
    entry = p.get("entry_price", 0)
    sl_pct = abs((entry - sl) / entry * 100) if sl and entry else 0
    tp_pct = abs((tp - entry) / entry * 100) if tp and entry else 0
    print(f'{p["symbol"]:10s} {p["side"]:5s} qty={p["quantity"]:>10.4f} '
          f'entry={entry:>10.5f} SL={sl:>10.5f} ({sl_pct:.1f}%) '
          f'TP={tp:>10.5f} ({tp_pct:.1f}%) '
          f'PnL={p["pnl"]:>+8.2f} ({p["pnl_pct"]:>+.2f}%)')

print("\n=== TRADES (last 10) ===")
trades = fetch("/api/trades")
for t in trades[:10]:
    print(f'{t["symbol"]:10s} {t["side"]:5s} entry={t["entry_price"]:>10.5f} '
          f'exit={t.get("exit_price",0):>10.5f} PnL={t["pnl"]:>+8.2f} ({t["pnl_pct"]:>+.2f}%) '
          f'reason={t.get("reason","?")}')

print("\n=== SUMMARY ===")
s = fetch("/api/summary")
print(f'Equity: ${s["equity"]:,.2f}  Cash: ${s["cash"]:,.2f}  '
      f'PnL: {s["total_pnl_pct"]:+.2f}%  '
      f'Open: {s["open_positions"]}  '
      f'Trades: {s["total_trades"]}  '
      f'Win Rate: {s["win_rate"]:.0f}%')

print("\n=== OPPORTUNITIES ===")
opps = fetch("/api/opportunities")
for o in opps[:10]:
    print(f'{o["symbol"]:10s} {o["action"]:5s} price={o.get("price",0):>10.5f} '
          f'conf={o.get("confidence",0):.2f} '
          f'SL={o.get("sl_pct","?"):s}% TP={o.get("tp_pct","?"):s}% '
          f'strat={o.get("strategies",[])}')

print("\n=== ERROR LOG ===")
errors = fetch("/api/errors")
if errors:
    for e in errors[-3:]:
        print(f'{e.get("source","?")}: {e.get("message","?")}')
else:
    print("No errors")

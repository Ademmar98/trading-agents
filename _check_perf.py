from core.database import init_db, fetchall
from core.equity import build_daily_summary
from core.portfolio import load_portfolio

init_db()
p = load_portfolio()
print(f'Cash: ${p.cash:.2f}')
print(f'Initial: ${p.initial_balance:.2f}')
print(f'Equity attr: {getattr(p, "equity", "N/A")}')
print(f'Positions: {len(p.positions)}')
if hasattr(p, 'initial_balance') and p.initial_balance:
    print(f'Return: {((p.cash/p.initial_balance)-1)*100:.2f}%')
print()

for day in ('2026-07-08', '2026-07-09'):
    try:
        summ = build_daily_summary(day)
        print(f'{day} summary: {summ}')
    except Exception as e:
        print(f'{day} summary error: {e}')

print()
equity = fetchall('SELECT recorded_at, balance FROM equity_history ORDER BY recorded_at')
if equity:
    print(f'Equity history: {len(equity)} snapshots')
    print(f'  Start: ${equity[0]["balance"]:.2f} at {equity[0]["recorded_at"]}')
    print(f'  End:   ${equity[-1]["balance"]:.2f} at {equity[-1]["recorded_at"]}')
    low = min(e['balance'] for e in equity)
    high = max(e['balance'] for e in equity)
    print(f'  Low:   ${low:.2f}')
    print(f'  High:  ${high:.2f}')
    dd = (high - low) / high * 100
    print(f'  Max DD: {dd:.2f}%')
else:
    print('No equity history')

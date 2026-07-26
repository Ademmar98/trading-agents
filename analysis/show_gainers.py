"""Show top gainers from backtest results"""
import sys, json
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

with open('analysis/halal_1year_extended.json', 'r') as f:
    data = json.load(f)

core20 = data['tests'][0]['results']
core20.sort(key=lambda x: x['total_return_pct'], reverse=True)

print("=" * 70)
print("TOP GAINERS FROM BACKTEST (1 Year, Core 20 Pairs)")
print("=" * 70)
print()
print(f"{'Rank':<6} {'Symbol':<12} {'Return%':<12} {'Trades':<10} {'WinRate':<12} {'PF':<10}")
print("-" * 70)

for i, r in enumerate(core20, 1):
    pf = f"{r['profit_factor']:.2f}" if r['profit_factor'] < 100 else 'inf'
    print(f"{i:<6} {r['symbol']:<12} {r['total_return_pct']:>8.2f}%  {r['total_trades']:>8}  {r['win_rate']:>10.1f}%  {pf:>8}")

positive = [r for r in core20 if r['total_return_pct'] > 0]
negative = [r for r in core20 if r['total_return_pct'] < 0]

print()
print("=" * 70)
print("SUMMARY")
print("=" * 70)
print(f"Total: {len(core20)} | Positive: {len(positive)} ({len(positive)/len(core20)*100:.0f}%) | Negative: {len(negative)} ({len(negative)/len(core20)*100:.0f}%)")

print()
print("TOP 5 GAINERS (your money-makers):")
for r in core20[:5]:
    print(f"  {r['symbol']}: +{r['total_return_pct']:.2f}% ({r['total_trades']} trades, {r['win_rate']:.0f}% WR)")

print()
print("TOP 5 LOSERS (avoid these):")
for r in core20[-5:]:
    print(f"  {r['symbol']}: {r['total_return_pct']:.2f}% ({r['total_trades']} trades, {r['win_rate']:.0f}% WR)")

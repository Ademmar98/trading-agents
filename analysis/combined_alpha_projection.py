#!/usr/bin/env python3
"""Multi-Alpha combined equity projection."""
import json, math

# ── Module 1: Z-Score Funding (validated, 2-month backtest) ──
with open("analysis/zscore_funding_2month.json") as f:
    m1 = json.load(f)

# Filter to pairs with actual trades
m1_traded = [r for r in m1["results"] if r["total_trades"] > 0]
m1_profitable = [r for r in m1_traded if r["net_return"] > 0]
m1_avg = sum(r["net_return"] for r in m1_traded) / len(m1_traded) if m1_traded else 0
m1_total_trades = sum(r["total_trades"] for r in m1_traded)
m1_total_wins = sum(r["wins"] for r in m1_traded)
m1_total_losses = sum(r["losses"] for r in m1_traded)
m1_wr = m1_total_wins / max(m1_total_wins + m1_total_losses, 1)

# PF = gross_profit / gross_loss
gross_win = sum(r["avg_win"] * r["wins"] for r in m1_traded if r["wins"] > 0)
gross_loss = abs(sum(r["avg_loss"] * r["losses"] for r in m1_traded if r["losses"] > 0))
m1_pf = gross_win / gross_loss if gross_loss > 0 else 999

# ── Module 2: Basket Rebalancing (theoretical 3-7% monthly) ──
m2_monthly_low = 3.0
m2_monthly_high = 7.0
m2_monthly_mid = 5.0

# ── Module 3: Microstructure Absorption (theoretical 3-5% per trade) ──
m3_trades_per_month = 6  # estimated
m3_avg_return = 3.5  # midpoint
m3_win_rate = 0.65
m3_monthly = m3_trades_per_month * m3_avg_return * m3_win_rate

# ── Combined Multi-Alpha Projection ──
# Allocation: M1=40%, M2=35%, M3=25%
alloc_m1 = 0.40
alloc_m2 = 0.35
alloc_m3 = 0.25

# Monthly returns per module (annualized / 12)
m1_monthly = m1_avg / 2  # backtest was 2 months, annualize to monthly
m2_monthly = m2_monthly_mid
m3_monthly = m3_monthly

combined_monthly = alloc_m1 * m1_monthly + alloc_m2 * m2_monthly + alloc_m3 * m3_monthly

# Compounded monthly (with fractional Kelly at 0.25x)
kelly_adj = 0.25
combined_kelly = combined_monthly * kelly_adj

# Projected equity curve
capital = 10000
equity_1m = capital * (1 + combined_kelly / 100)
equity_3m = capital * ((1 + combined_kelly / 100) ** 3)
equity_6m = capital * ((1 + combined_kelly / 100) ** 6)
equity_12m = capital * ((1 + combined_kelly / 100) ** 12)

# Max drawdown estimate (from M1 backtest worst case)
m1_worst_dd = min(r["max_drawdown"] for r in m1_traded)
combined_dd = m1_worst_dd * alloc_m1

print("=" * 60)
print("MULTI-ALPHA COMBINED RESULTS")
print("=" * 60)

print("\n-- Module 1: Z-Score Funding Squeeze --")
print(f"  Validated PF:        {m1_pf:.2f}")
print(f"  Win Rate:            {m1_wr*100:.1f}%")
print(f"  Avg Return (2mo):    {m1_avg:+.2f}%")
print(f"  Total Trades:        {m1_total_trades}")
print(f"  Profitable Pairs:    {len(m1_profitable)}/{len(m1_traded)}")

print("\n-- Module 2: Basket Rebalancing --")
print(f"  Monthly Target:      {m2_monthly_low}-{m2_monthly_high}%")
print(f"  Est. Monthly Yield:  {m2_monthly_mid:.1f}%")
print(f"  Execution:           Maker-only (2bps fee)")

print("\n-- Module 3: Microstructure Absorption --")
print(f"  Trades/Month:        ~{m3_trades_per_month}")
print(f"  Avg Win Rate:        {m3_win_rate*100:.0f}%")
print(f"  Avg Return/Trade:    {m3_avg_return:.1f}%")
print(f"  Est. Monthly Yield:  {m3_monthly:.1f}%")

print("\n-- Combined Multi-Alpha --")
print(f"  Allocation:          M1={alloc_m1*100:.0f}% M2={alloc_m2*100:.0f}% M3={alloc_m3*100:.0f}%")
print(f"  Raw Monthly Return:  {combined_monthly:.2f}%")
print(f"  With 0.25x Kelly:    {combined_kelly:.2f}%")
print(f"  Max DD (est):        {combined_dd:.1f}%")

print("\n-- $10,000 Capital Projection --")
print(f"  Month 1:             ${equity_1m:,.0f}")
print(f"  Month 3:             ${equity_3m:,.0f}")
print(f"  Month 6:             ${equity_6m:,.0f}")
print(f"  Month 12:            ${equity_12m:,.0f}")

print("\n-- Risk Controls --")
print(f"  Monthly Max DD Cap:  -15%")
print(f"  Max Portfolio Exp:   80%")
print(f"  Max Position/Trade:  2% risk")
print(f"  Cash Reserve:        20%")
print(f"  Session Risk:        Asian 0.5x / EU 0.8x / US 1.0x")
print("=" * 60)

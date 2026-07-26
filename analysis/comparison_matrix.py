#!/usr/bin/env python3
"""
Side-by-side comparison matrix: Theoretical vs Empirical results
for all three modules.
"""
import json
import statistics

# Load results
with open("analysis/zscore_funding_2month.json") as f:
    m1_data = json.load(f)

with open("analysis/module2_basket_backtest.json") as f:
    m2_data = json.load(f)

with open("analysis/module3_absorption_backtest.json") as f:
    m3_data = json.load(f)

# Module 1 aggregates
m1_traded = [r for r in m1_data["results"] if r["total_trades"] > 0]
m1_total = sum(r["total_trades"] for r in m1_traded)
m1_wins = sum(r["wins"] for r in m1_traded)
m1_losses = sum(r["losses"] for r in m1_traded)
m1_wr = m1_wins / max(m1_total, 1) * 100
m1_avg_ret = sum(r["net_return"] for r in m1_traded) / len(m1_traded) if m1_traded else 0
gross_win = sum(r["avg_win"] * r["wins"] for r in m1_traded if r["wins"] > 0)
gross_loss = abs(sum(r["avg_loss"] * r["losses"] for r in m1_traded if r["losses"] > 0))
m1_pf = gross_win / gross_loss if gross_loss > 0 else 999
m1_max_dd = min(r["max_drawdown"] for r in m1_traded) if m1_traded else 0

# Module 2 best config
m2_best_name = m2_data.get("best_config", "")
m2_best = None
for r in m2_data["results"]:
    if r["config"] == m2_best_name:
        m2_best = r
        break

# Module 3 best config
m3_best_name = m3_data.get("best_config", "")
m3_best = m3_data["results"].get(m3_best_name, {})

print("=" * 80)
print("MULTI-ALPHA STRATEGY STACK: THEORETICAL vs EMPIRICAL COMPARISON MATRIX")
print("=" * 80)

print(f"\n{'Metric':<35} {'Module 1':>15} {'Module 2':>15} {'Module 3':>15}")
print(f"{'':35} {'Z-Score Fund':>15} {'Basket Rebal':>15} {'Absorption':>15}")
print("-" * 80)

print(f"{'STATUS':35} {'UNVALIDATED':>15} {'FAILED':>15} {'UNVALIDATED':>15}")
print()

print("  EMPIRICAL RESULTS (Backtested)")
print(f"  {'Win Rate':35} {m1_wr:>14.1f}% {m2_best.get('monthly_return_pct', 0):>14.2f}x {m3_best.get('aggregate_win_rate', 0):>14.1f}%")
print(f"  {'Profit Factor':35} {m1_pf:>14.2f} {'N/A':>15} {m3_best.get('aggregate_pf', 0):>14.2f}")
print(f"  {'Total Trades':35} {m1_total:>15} {'N/A':>15} {m3_best.get('total_trades', 0):>15}")
# m1 figure is the MEAN per-pair monthly return, not per-trade. The mean is
# outlier-driven -- median is printed below and is the honest number.
print(f"  {'Avg Monthly Return (mean pair)':35} {m1_avg_ret/2:>13.2f}% {m2_best.get('monthly_return_pct', 0):>13.2f}% {m3_best.get('avg_return_per_trade', 0):>13.2f}%")
m1_median = statistics.median([r["net_return"] for r in m1_traded]) if m1_traded else 0
print(f"  {'Median Pair Return (2mo)':35} {m1_median:>13.2f}% {'N/A':>15} {'N/A':>15}")
print(f"  {'Max Drawdown':35} {m1_max_dd:>13.1f}% {m2_best.get('max_drawdown_pct', 0):>13.2f}% {'N/A':>15}")
print(f"  {'Sharpe Ratio':35} {'N/A':>15} {m2_best.get('sharpe_ratio', 0):>14.2f} {'N/A':>15}")

print()
print("  THEORETICAL TARGETS")
print(f"  {'Win Rate':35} {'40-50%':>15} {'N/A':>15} {'>=60%':>15}")
print(f"  {'Profit Factor':35} {'>=1.5':>15} {'N/A':>15} {'>=1.5':>15}")
print(f"  {'Monthly Return':35} {'3-7%':>15} {'3-7%':>15} {'10-15%':>15}")
print(f"  {'Max Drawdown':35} {'<15%':>15} {'<15%':>15} {'<10%':>15}")

# Audit Study #10: aggregate thresholds alone passed Module 1 while its edge
# was really two pairs. Any verdict must survive dropping the top contributors.
def pf_excluding_top(results, n_drop=2):
    """Recompute aggregate PF after removing the n best-performing pairs."""
    kept = sorted(results, key=lambda r: r["net_return"])[:-n_drop] if len(results) > n_drop else []
    gw = sum(r["avg_win"] * r["wins"] for r in kept if r["wins"] > 0)
    gl = abs(sum(r["avg_loss"] * r["losses"] for r in kept if r["losses"] > 0))
    return gw / gl if gl > 0 else 0.0


m1_pf_ex = pf_excluding_top(m1_traded)
m1_top2 = [r["symbol"] for r in sorted(m1_traded, key=lambda r: r["net_return"])[-2:]]

print()
print("  VALIDATION VERDICT")
wr_ok = m1_wr >= 40
pf_ok = m1_pf >= 1.5
concentration_ok = m1_pf_ex >= 1.0  # must still break even without the outliers
m2_ok = m2_best.get("monthly_return_pct", 0) >= 3.0 if m2_best else False
m3_wr_ok = m3_best.get("aggregate_win_rate", 0) >= 60
m3_pf_ok = m3_best.get("aggregate_pf", 0) >= 1.5
# Module 3's backtest measures a different signal than the live engine, so its
# thresholds cannot validate anything regardless of the numbers.
m3_signal_matches_live = False

print(f"  {'Win Rate Met':35} {'YES' if wr_ok else 'NO':>15} {'N/A':>15} {'YES' if m3_wr_ok else 'NO':>15}")
print(f"  {'PF Met':35} {'YES' if pf_ok else 'NO':>15} {'N/A':>15} {'YES' if m3_pf_ok else 'NO':>15}")
print(f"  {'Monthly Target Met':35} {'N/A':>15} {'YES' if m2_ok else 'NO':>15} {'N/A':>15}")
print(f"  {'Survives outlier removal':35} {'YES' if concentration_ok else 'NO':>15} {'N/A':>15} {'N/A':>15}")
print(f"  {'Backtest tests live signal':35} {'YES':>15} {'N/A':>15} {'YES' if m3_signal_matches_live else 'NO':>15}")
print()
print(f"  PF excluding top 2 pairs ({', '.join(m1_top2)}): {m1_pf_ex:.2f}")
print(f"  {'OVERALL':35} {'FAIL' if not concentration_ok else 'PASS':>15} "
      f"{'FAIL' if not m2_ok else 'PASS':>15} "
      f"{'FAIL' if not m3_signal_matches_live else 'PASS':>15}")

print()
print("=" * 80)
print("ANALYSIS & RECOMMENDATIONS")
print("=" * 80)

print("""
MODULE 1: Z-Score Funding Squeeze -- UNVALIDATED (Audit Study #10)
  Aggregate PF 1.54 over 54 trades / 18 pairs / 2 months.
  Median pair return -1.04%; only 9 of 18 pairs profitable.
  CONCENTRATION KILL: removing NFP (+112%) and SYN (+49%) drops PF to 0.91
  and mean return to -2.74%. The headline is two lottery tickets, not an edge.
  54 trades cannot distinguish PF 1.5 from PF 1.0 anyway.
  NOT FOR DEPLOYMENT.

MODULE 2: Basket Rebalancing -- FAILED
  Monthly return -6.15% (target was +3-7%)
  Buy-and-hold benchmark was -17.20% total (-5.73%/mo)
  Rebalancing slightly UNDERPERFORMED buy-and-hold in this bear period
  ROOT CAUSE: Rebalancing buys losers and sells winners -- in a
  sustained downtrend this amplifies losses (buying the dip into a crash)
  FIX: Add trend filter -- only rebalance when BTC > 200-SMA
  VERDICT: DISABLE until trend filter validated

MODULE 3: Microstructure Absorption -- UNVALIDATED (Audit Study #10)
  The backtest does not test the live engine. It builds CVD from a cumsum of
  4h kline taker-buy volume over months; core/strategies/
  microstructure_absorption.py builds CVD from the last ~50 tape trades.
  Different signal, different timescale -- the PF says nothing about live.
  Also: zero fees applied (MAKER_FEE/TAKER_FEE declared, never used), and
  exits fill at the exact stop/TP price on bars that gap through levels.
  The "Conservative" config was picked as best of three on one dataset and
  has the fewest trades (50 vs 133/107) -- selection on noise. All three sit
  near PF 1.7 before costs.
  VERDICT: DO NOT DEPLOY. Rebuild the backtest against the live signal first.
""")

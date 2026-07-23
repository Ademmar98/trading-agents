# Live-trade meta-analysis — 2026-07-23 (verdict: risk problem, not signal problem)

**Mandate:** honest post-mortem of the firm's real live trades — segment, find
what actually drove P&L, and test any apparent edge for survival rather than
mining winners into a curve-fit. Ran on 77 logical trades reconstructed from the
VPS backups (scaled exits grouped by position, deduped across snapshots).

## Method
- Per-trade **R-multiple** = pnl / (|entry − stop| × qty).
- Daily **regime** (ADX>25 trending / SMA200 trend) and **volatility state**
  (ATR vs its 30-day avg) labelled per trade using only the last CLOSED daily
  bar before entry (anti-lookahead). Session from entry hour (UTC).
- Segmented by asset / session / regime / trend / vol-state / exit-reason.
- **IS/OOS honesty test:** 70/30 chronological split, pick the best in-sample
  single-axis filter, test it out-of-sample + a bootstrap null.

## The headline finding — dollars and R disagree
- Overall: **WR 51.9% · avg +0.63R · E[X] = −$8.59/trade · net −$662 · PF 0.41.**
- **Risk-normalised expectancy is POSITIVE (+0.63R) but dollars are NEGATIVE.**
  That can only happen when per-trade dollar risk isn't constant — the losers
  were sized larger than the winners.
- **Concentration:** UNI = **−$791 across 13 trades = 100%+ of the entire net
  loss.** Remove UNI and the book is **≈ +$129.** One over-traded name *is* the
  drawdown. → It's a **sizing/concentration** failure, not a signal failure.

## Why "extract what worked" is a trap (demonstrated on real data)
- **IS/OOS optimiser's #1 "edge" = `outcome = take_profit`** (IS +$5.67, OOS
  +$59.59, bootstrap 100th pctile, p=0.000). It aces every statistical test —
  and is **useless**: you can't filter on the *result* before the trade. A
  mechanical edge-miner confidently returned a lookahead tautology.
- **`London session`: 88% win rate but E[X] = −$14.38** — the win-rate mirage
  (tiny wins, few big losses).
- Most segment cells hold **n = 2–17** — indistinguishable from luck.

## Verdict → action
No tradeable *entry* edge (as nine prior studies found). The one encouraging
number (+0.63R risk-normalised) is on 77 trades and contradicts the backtests —
worth an honest forward test under constant sizing, nothing more. The real,
actionable finding is that **sequential single-name concentration** turned a
coin-flip book into a −$662 loss. Fixed in commit `60619b2`:
per-symbol re-entry cooldown + per-symbol daily-loss cap (on top of the existing
10% notional cap and $300 daily-loss stop). See [[firm-strategy-research-findings]].

## Reproduce
```
python meta_analysis.py   # on the VPS: reads data_backups + public Alpaca -> RESULTS.json
```

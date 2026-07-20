# Walk-forward strategy study — 2026-07 (verdict: no deployable edge)

**Question:** does a regime-filtered, long-only trend/breakout family on 1H
crypto majors have positive expectancy after realistic costs?

**Answer: no.** And the negative is *clean* — not overfit decay: even the
stitched in-sample validation segments were negative, and no parameter combo
won more than 3 of 35 walk-forward windows (a scattered pick distribution is
the signature of "no stable edge in this family"). The regime filter does
have real **defensive** value: OOS max drawdown −4.2% while buy-and-hold fell
25–54%.

## Design

- **Data:** BTC/ETH/SOL vs USD, 1H bars, 2022-01-01 → 2026-07-19, Alpaca
  (the firm's own market-data feed). ~40k bars/symbol (SOL ~30k, has gaps).
- **Constraints:** long-only spot, no leverage (halal invariants), one
  position per symbol, 0.5% equity risk per trade, 15% equity notional cap
  (mirrors the firm), costs 0.05% taker + 0.02% slippage **per side**.
- **Anti-look-ahead:** signals on bar `t` close, fills at `t+1` open,
  intrabar SL/TP with SL priority.
- **Split:** 70% in-sample / 30% locked out-of-sample (2025-03 → 2026-07),
  opened exactly once.
- **WFO:** 180d train → 60d test, stepped 60d (35 windows across 3 symbols),
  64-combo grid, per-window winner by Sharpe (min 5 trades, PF > 1);
  final parameters = **modal combo across windows**, never the global best.

## Results

| OOS portfolio | Sharpe | CAGR | MaxDD | WR | PF |
|---|---|---|---|---|---|
| 3-sleeve equal weight | −0.88 | −1.9% | −4.2% | ~33% | 0.65–1.01 |

Per-year with the locked params: defends bears (2022 BTC: −2.0% vs −64.6%
B&H), cannot monetize bulls (2023 BTC: +3.1% vs +156% B&H — churned away by
the fee grinder), bleeds in chop (2026 BTC PF 0.45). OOS exits: stopped out
3–4× more often than TP hit.

**Control** (`regime_hold.py`): naive "long above EMA200, flat below" with
zero optimization — rejected: 500–700 flips, 50–70% drawdowns.

## Implications for the firm

1. Public-indicator trend entries on majors at intraday horizons cannot beat
   costs. Strategy weight should flow to breadth/selection (the 400-symbol
   scan) where the live wins actually came from.
2. Regime gating (SMA200 deploy targets) is the reason the live book is flat
   while majors are down 25–50% — keep it. In a bear tape, cash is a position.
3. The +0.5–3%/day goal is refuted by 4.5 years of data; success at this
   horizon is positive expectancy per trade, then single-digit %/month.
4. Next study: daily-bar swing with wide trails on regime-confirmed bulls
   (costs become negligible at that horizon).

## Reproduce

```
python fetch_alpaca.py   # writes data/*.parquet (~40k 1H bars per symbol)
python wfo.py            # full walk-forward run (~5 min), writes wfo_results.json
python regime_hold.py    # the a-priori control
```

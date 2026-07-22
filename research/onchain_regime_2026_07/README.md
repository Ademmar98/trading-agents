# On-Chain / Regime-Gated Halal Swing Strategy — backtest framework (2026-07)

A clean, modular, **anti-lookahead-rigorous** pandas framework for the one lever
nine prior price-only studies never tested: **non-price information** (on-chain
exchange netflow, perp funding as a *sentiment gate*, aggressor volume delta),
combined with a 4H Donchian breakout on 1H spot bars. Long-only spot (Shariah).

## Files
- `onchain_regime_strategy.py` — the framework (config, data adapters, indicators,
  signals, event-driven backtest, metrics, equity plot).
- `binance_vision_data.py` — **real free data adapters** (data.binance.vision):
  price 1H, aggressor delta (from kline taker-buy vol), and perp funding — all
  US-accessible, no API key. `real_bundle()` wires 3 of the 4 inputs on real data.
- `test_engine.py` — validation: **no-lookahead assertion** + win-path test. Run it.

Real run (price/funding/delta real; supply on-chain CSV for a valid edge test):
```python
import binance_vision_data as D, onchain_regime_strategy as S
bundle = D.real_bundle("BTC/USDT", "2023-01", "2024-12", onchain_csv="my_cryptoquant.csv")
print(S.run(bundle, S.Config())["metrics"])
```

## The honest constraint (read first)
The strategy's edge, if any, lives in the **on-chain** input — and exchange
netflow / reserve balance are **paid** metrics (CryptoQuant, Glassnode). Funding
and aggressor delta are free (Binance/Bybit dumps; note Binance API is geo-blocked
from the US VPS). The repo ships a **synthetic generator** so the plumbing runs
end-to-end, but its on-chain series is noise — **synthetic results are meaningless.**
Connect real feeds via the documented adapters before trusting any number.

| Input | Source | Cost |
|---|---|---|
| 1H OHLCV | Binance dumps / Bybit / OKX / ccxt | free |
| Perp funding | Binance/Bybit `fundingRate` | free |
| Aggressor delta | Binance aggTrades → hourly buy−sell vol | free |
| **Exchange netflow / reserve** | **CryptoQuant / Glassnode** | **paid** (CSV adapter provided) |

## Spec implemented (exactly)
- **Cash gate:** force 100% cash if funding > 0.04%/8h OR reserve growth > +2σ.
- **Long entry (all true):** 24h netflow < 0 (accumulation) AND funding ≤ 0 AND
  4H Donchian breakout AND positive aggressor delta.
- **Exits:** partial +6% / +12%, dynamic 2.5×ATR stop, 96h time stop.
- **Execution:** maker-only POST_ONLY, 0.07%/side fee + 0.05%/side slip = **0.24% RT**.
- **Sizing:** 20% of equity per trade (never 100%).
- **Metrics:** Net P&L %, Profit Factor, MaxDD % + duration, Sharpe, Sortino
  (0% rf), trades, win rate, avg duration, equity vs buy-&-hold.

## Two design decisions that matter
1. **Anti-lookahead is enforced, not assumed.** Every signal input is `.shift(1)`;
   the 4H Donchian is mapped to 1H using only the **last CLOSED** 4H bar (the exact
   bug that faked a 3.5-PF result in `../swing_htf_2026_07`). `test_no_lookahead`
   proves it by recomputing decisions on truncated history.
2. **Maker POST_ONLY honestly misses runaway breakouts.** A resting buy limit at
   the prior close fills only when a seller trades down to it; a gap-up bar whose
   low never reaches the limit is a genuine MISS. This is the real cost of maker
   execution on momentum entries — do not "fix" it by allowing taker fills.

## Run
```
python test_engine.py                 # validation (must pass before trusting anything)
python onchain_regime_strategy.py     # synthetic demo (plumbing only)
# real run: replace synthetic_bundle() in __main__ with your real loaders
```
See [[firm-strategy-research-findings]].

# Limit (maker) vs market (taker) execution — 2026-07

**Question (the one testable, novel part of the "liquidity/limit-zone" brief):**
the firm enters with market (taker, 0.05%) orders. Would resting **limit (maker,
0.02%)** orders — which fill at better prices but miss when price runs away —
improve execution net of the missed fills?

**Answer: yes, materially.** A ~1×ATR buy-limit turns per-decision execution
from **−16.5 bps (market) to ~breakeven (−0.7 bps)** across BTC/ETH/SOL — it
does not create edge, it removes most of the execution *drag* that the fee-bleed
studies flagged. The catch: you fill only ~49% of signals, and in genuine
crashes the limit fills anyway and gives ~no protection.

## Scope & honesty

- OHLCV 1H, BTC/ETH/SOL, 2022–2026. **No L2 order-book data**, so this study
  does NOT compute liquidity walls, depth, partial-fill/adverse-selection risk,
  or "liquidity scores" — those need depth-of-market/tick data the firm doesn't
  have. Claims are limited to what bars can support.
- Limit fill model: order rests `offset` below close[t], active 6h; fills at the
  limit if any bar's low touches it (touch = full fill — optimistic; real
  resting fills can be partial). Bracket 2×ATR SL / 3×ATR TP, exit taker.
- The firm **already runs** ICT/SMC (FVG, order-block, liquidity-sweep)
  strategies; live post-mortems put them in the no-edge/loss bucket, so this
  study tests execution *style*, not the ICT zone thesis (already answered).

## Results — per-decision expectancy (missed fills = 0, the honest metric)

| entry method | fill % | exp/decision | exp/fill |
|---|---:|---:|---:|
| market (taker) | 100% | **−16.5 bps** | −16.5 bps |
| limit −0.1% | 92% | −13.6 | −14.7 |
| limit −0.25% | 82% | −13.2 | −16.0 |
| limit −0.5% | 66% | −8.6 | −12.8 |
| **limit −1×ATR** | 49% | **−0.7 bps** | −1.5 |
| limit @ bullish FVG | 56% | −7.0 | −11.8 |

Improvement is monotonic in offset and consistent across all 3 symbols. It comes
from two sources: (1) the **certain 3 bps maker-fee saving** vs taker; (2)
**better fill prices** — a limit only fills on a dip, so entries land at local
lows. Source (2) is real but not free: 51% of 1×ATR signals never fill, and many
were winners (`miss_win` in RESULTS). The per-decision metric already charges
those as 0, and the wider offset still wins — because the underlying signal has
no edge (market ≈ −16.5 bps ≈ the round-trip cost of a coin-flip entry), so
trading half as often at better prices nets close to zero drag.

The **FVG rule** is inconsistent (ETH +8.3 bps, BTC −10, SOL −19; small n) —
consistent with the firm's ICT strategies being in the loss bucket. Not a
robust standalone edge.

## Crash stress test (−10%+ within 6h)

| | events | market exp | limit −0.5% (caught) |
|---|---:|---:|---:|
| BTC | 10 | −187 bps | −105 bps |
| ETH | 2 | −239 bps | −237 bps |
| SOL | 18 | −385 bps | −384 bps |

The prompt's fear is confirmed: in a real cascade the buy-limit **fills every
time** (price blows through it) and the better entry is a rounding error against
a −38% dump. Limit *entries* provide ~zero crash protection — that must come
from the **stop**, not the entry style.

## Recommendation

1. **Switch entries from market (taker) to limit (maker) at ~1×ATR offset.** A
   real, robust execution improvement (~16 bps/trade less drag), halal-neutral
   (spot limit orders), directly attacking the fee-bleed. It won't make the firm
   profitable — the signals have no edge — but it stops execution from bleeding
   on every trade, and skipping ~half the (edge-less) signals loses no alpha.
2. **Keep a market-order fallback / cancel-after-N-bars**: if a limit hasn't
   filled within the window and the setup is still valid, either cancel (skip)
   or cross the spread — don't chase.
3. **Do not rely on limit entries for crash protection** — the stop does that.
4. Don't add FVG/order-block zone triggers: inconsistent here, already losing live.

## Reproduce

```
python limit_study.py   # 1H BTC/ETH/SOL parquet -> limit_RESULTS.json
```

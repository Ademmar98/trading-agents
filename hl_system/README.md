# hl_system — Hyperliquid three-module trading system

Zero leverage, 100% passive maker execution, 5% hard drawdown lock.

```bash
pip install hyperliquid-python-sdk
export HL_SECRET_KEY=0x...        # API wallet (agent) key — NOT your master key
export HL_ACCOUNT_ADDRESS=0x...   # the master account it trades for

python -m hl_system.test_hl_system     # self-check, no network
python -m hl_system.main               # dry-run (default): decides, sends nothing
python -m hl_system.main --live        # sends orders; prompts for the address
```

| file | role |
|---|---|
| `config.py` | `ApiConfig` / `RiskConfig` / `SignalConfig` / `ExecConfig` / `SystemConfig` |
| `data_engine.py` | asset discovery, REST backfill, WS candles/trades/L2, rate limit, reconnect |
| `signals.py` | Modules A (CVD), B (funding Z), C (VWAP reversion) + `SignalManager` |
| `risk_manager.py` | drawdown tiers, daily freeze, volatility-parity sizing |
| `execution.py` | ALO post-only orders, quantisation, re-quote loop, passive flatten |
| `main.py` | async loop: sync → risk → flatten? → orders → signals → entries |
| `test_hl_system.py` | 12 assert-based checks, no credentials needed |

## Corrections to the original brief

- `hyperliquid.node` does not exist — it is `hyperliquid.info.Info` and
  `hyperliquid.exchange.Exchange`.
- The post-only flag is not `ALONE`/`ALO` as an argument; it is the order type
  `{"limit": {"tif": "Alo"}}`.
- `signing.float_to_wire` **raises** on any price/size it cannot represent
  exactly, so quantisation to Hyperliquid's 5-significant-figure + decimal-cap
  rule is mandatory, not cosmetic. See `round_price` / `round_size`.

## Defaults that differ from the brief, and why

**Spot, long-only.** `spot_mode=True`, `long_only=True`. The repo's standing
halal constraint forbids leverage, shorts, and funding (riba). Module B's
"short when Z > +2.5" leg becomes "exit to flat", which is the brief's own
stated spot behaviour. `--perp` and `--allow-short` re-enable the other paths
and log a warning; `--perp` still pins leverage to 1x and refuses anything else.

**Passive liquidation is not guaranteed.** At the 4.8% tier the system cancels
everything and posts reduce-only ALO exits. An all-maker mandate means those
exits can go unfilled in a fast market. `allow_taker_liquidation` (default
`False`) is the explicit escape hatch. This is a real trade-off, not an
oversight — a 100% maker rule and a guaranteed stop are not simultaneously
satisfiable.

## Evidence status — read before running this live

Modules A and B already exist in this repo and were already killed:

- **Module B (funding Z-score)** is `core/strategies/zscore_funding_squeeze.py`,
  study #10. Headline PF 2.93 was actually **1.54** across 54 trades / 18
  pairs; removing the two best pairs (NFP +112%, SYN +49%) gave **PF 0.91,
  mean −2.74%**. The result was two lottery tickets.
- **Module A (CVD/absorption)** is `core/strategies/microstructure_absorption.py`.
  Never validated: its backtest built CVD from 4h kline `taker_buy_base` while
  the live code used the last ~50 tape trades — different signals. This
  implementation reads the raw tape *only* (`DataEngine.trades`), so a backtest
  that replays the same tape now tests what actually runs. That fixes the
  measurement, not the edge.
- **Module C (VWAP mean reversion)** is intraday reversion, which studies #11
  and #12 measured at ~0.004% gross per trade against 0.14% round-trip fees.

Maker-only execution removes the taker leg of that cost, which is the single
most defensible thing about this design — but studies #11/#12 found the gross
edge was ~0 *before* fees, and zero minus a smaller number is still not
positive. Before committing capital, run each module **per-pair** over a
window grid (study #17: slide the boundaries, report the distribution — a fixed
walk-forward is one sample, not a validation).

The risk layer is the part of this system with independent value.

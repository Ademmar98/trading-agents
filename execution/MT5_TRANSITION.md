# MT5 (FTMO-Demo) broker transition — status & plan

Requested: pause the test cycle and switch the firm to MetaTrader 5 (FTMO-Demo,
login 1514052291). Delivered here: the state snapshot, the pause + MT5-verify
script (`pause_and_switch_broker.py`), and the `.env` template. The actual
switch is **blocked by two facts** that must be resolved first — neither is a
config change.

## Blocker 1 — MT5 is Windows-only; the firm runs on Linux

`MetaTrader5` (the Python API) exists only on Windows and requires the MT5
**terminal** running on the same machine. The firm runs on a **Linux VPS**
(`/root/firm`, systemd `trading-firm`). It cannot `import MetaTrader5` or reach
a terminal there. Setting `BROKER_TYPE=mt5` on the VPS would crash the firm at
startup.

**Resolution options:**
- Run the MT5 leg on a **Windows host** (your local Windows box, or a Windows
  VPS): install the MT5 terminal + `pip install MetaTrader5`, and either move
  the firm there or run a small MT5 executor process that the VPS firm talks to.
- FTMO provides the MT5 terminal for the demo account; it must be installed and
  logged in on that Windows host.

## Blocker 2 — the firm has no MT5 broker adapter

Ground truth from the current code: `config.py` defines no MT5 vars,
`agents/trader.py` branches only `binance`/`paper`, and there is **no**
`MetaQuotesBroker` / `core/live_broker.py`. MT5 appears only in two auxiliary
scripts. So MT5 support has to be **built**, not toggled:

1. `core/mt5_broker.py` — a broker class matching the `PaperBroker` interface
   (`place_order`, cancel, position/balance queries) implemented against
   `MetaTrader5` (`order_send` with `TRADE_ACTION_PENDING` + `ORDER_TYPE_BUY_LIMIT`
   for passive maker entries, `order_check` pre-validation, fill polling via
   `history_deals_get`).
2. `config.py` — add `MT5_LOGIN`, `MT5_PASSWORD`, `MT5_READONLY_PASSWORD`,
   `MT5_SERVER` (env-sourced).
3. `agents/trader.py` — add the `BROKER_TYPE == "mt5"` branch and route the
   resting-limit path (`pending_orders`) through MT5 pending orders.
4. Symbol mapping: the firm uses `BTC/USD`; FTMO-Demo uses `BTCUSD` (and offers
   FX/CFD symbols, not spot crypto on every plan) — a name/asset-class map is
   required, and note FTMO is **CFD**, which raises a halal question (see below).

## Halal note (important)

FTMO-Demo is a **prop/CFD** environment. CFDs are leveraged derivatives with
overnight financing (swap) — that conflicts with the firm's hard invariants
(spot, no leverage, no funding/riba). Before routing any real intent through
MT5, confirm the instruments used are swap-free / genuinely spot-equivalent, or
this violates the halal constraint that has governed every change so far.

## How to use what's delivered now

```bash
# Phase A — pause the current session (run anywhere with API access; the
# cancel step no-ops unless run ON the firm host):
python execution/pause_and_switch_broker.py --pause

# Phase B — verify MT5 on the WINDOWS host (terminal installed + logged in):
set MT5_LOGIN=1514052291
set MT5_PASSWORD=...            # secure env var, never committed
set MT5_SERVER=FTMO-Demo
python execution/pause_and_switch_broker.py --verify-mt5
```

Phase B confirms the account connects and that BTCUSD/ETHUSD/… exist with a
tradable spread, DOM depth, and `BUY_LIMIT` acceptance — the prerequisite proof
before anyone spends effort building the adapter in Blocker 2.

## Recommended sequence

1. Decide the MT5 host (Windows box/VPS) and install + log in the FTMO terminal.
2. Run Phase B to prove connectivity + symbol/limit support.
3. Resolve the halal/CFD question on the actual instruments.
4. Build the MT5 broker adapter (Blocker 2) with tests, behind `BROKER_TYPE=mt5`.
5. Stage on the Windows host at demo size; audit fills (reuse `fill_dashboard`).
6. Only then pause the VPS paper firm for real and cut over.

Nothing here has stopped or reconfigured the live firm — the current test cycle
is still running untouched. The snapshot at
`research/strategy_sweep_2026_07/test_week_pause_snapshot.json` is the frozen
record of it as of the pause request.
```

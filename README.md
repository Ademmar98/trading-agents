# Trading Agents

Multi-agent crypto trading bot with paper/live broker support, 25+ strategies, regime detection, risk management, and a real-time dashboard.

## Architecture

Agents run **concurrently** as asyncio actors on a message bus (`core/bus.py`),
each with its own inbox, its own cadence, and persistent state that survives
restarts (`agent_state` table). Every trade must survive a **deliberation**
before it executes:

```
analyst ──proposal──▶ chair (orchestrator)
                        │  fan-out, concurrent
                        ▼
   risk_manager · position_sizer · portfolio_manager · compliance
   sentiment · regime · execution     — each answers with a stance:
        approve / counter (my adjustments) / reject / veto
                        │
        objections ──▶ analyst: concede, defend (+evidence), or withdraw
                        │  bounded rounds, then a weighted vote
                        ▼
              verdict ──▶ execution (order prep) ──▶ trader (broker)
```

- **Vetoes are non-negotiable** and reserved for `compliance`, `risk_manager`,
  and `health` (circuit breakers, spot-only rule, drawdown limits).
- **Counters negotiate**: size cuts compound, the tightest stop wins, and the
  revised trade goes back for another round.
- **Votes are earned**: the auditor archives every deliberation, matches it to
  the closed trade's real PnL, and moves each reviewer's voting weight
  (0.5–1.5×) up when they called it right and down when they didn't.
- **Every argument is on the record**: all messages persist to the
  `agent_messages` table, queryable by deliberation
  (`core.database.get_message_thread`).
- The **HealthMonitor** watches real runtime heartbeats and can halt the desk;
  the **OptimizerAgent** tunes parameters on a slow tick.

Agent roles (analyst, sentiment, regime, risk, sizing, portfolio, compliance,
execution, trader, auditor, health, optimizer) are described in
`agents/desk.py`. Set `MULTI_AGENT_MODE=false` to fall back to the legacy
sequential pipeline (`Orchestrator → … → Auditor` sharing JSON files), which
is kept for comparison and still backs the dashboard's report files.

## Features

| Feature | Details |
|---|---|
| **25+ Strategies** | ICT (FVG, OB, Liquidity Sweep, BOS/CHoCH, OTE, Market Structure), Classic (SMA, EMA, MACD, Bollinger, RSI Div, Stochastic, Ichimoku, Keltner, VWAP, ATR, Donchian, MFI), PA (Engulfing, Pin Bar, Inside Bar, Double Top/Bot, Volume Breakout, S/R, Heikin-Ashi) |
| **Regime-Adaptive** | Strategies are filtered by detected regime (trending → momentum, ranging → mean-reversion, volatile → breakout) |
| **Real Sentiment** | Fetches Fear & Greed Index from alternative.me and blends it with price breadth |
| **Risk Metrics** | VaR (95%), rolling max drawdown, trade duration stats, Sharpe, profit factor, Kelly sizing |
| **Trade Plans** | Each potential trade is saved to SQLite with SL, TP, R:R ratio, strategy, regime, rationale |
| **Multi-Broker** | Paper (default), Binance (testnet/live), MetaQuotes 5, DXtrade |
| **Web Dashboard** | Live dashboard on port 8000 with positions, trades, equity curve, risk, plans |
| **Telegram** | Notifications for trades, SL/TP hits, errors, daily summaries |
| **Backtester** | 90-day backtest per symbol using daily klines; stores results in DB |
| **Optimizer** | Grid-search SL/TP multipliers, position size, and confidence thresholds |
| **Trailing Stops** | Activates after configurable profit threshold; locks in gains |

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Copy `.env.example` to `.env` and configure:

| Variable | Default | Description |
|---|---|---|
| `BROKER_TYPE` | `paper` | `paper`, `binance`, `mt5`, `dxtrade` |
| `TRADING_CAPITAL` | `10000` | Initial paper balance |
| `TRADING_INTERVAL_MINUTES` | `60` | Minutes between trading cycles |
| `WATCHED_SYMBOLS` | 20 cryptos | Comma-separated symbol list |
| `BINANCE_ALL_SYMBOLS` | — | `true` fetches all USDT pairs from Binance at startup (uses testnet if `BINANCE_USE_TESTNET=true`) |
| `TELEGRAM_BOT_TOKEN` | — | Bot token for notifications |
| `TELEGRAM_CHAT_ID` | — | Chat ID for notifications |
| `RESET_ON_START` | — | `true` to wipe all data on next restart (⚠ removes trading history) |

## Usage

```powershell
# Start trading (with live dashboard)
python main.py

# Headless mode (server — web dashboard still available)
python main.py --headless

# Reset all data and start fresh
python main.py --reset

# Wipe on every restart (Railway/Docker — use once then remove)
# WARNING: leaving this true wipes trading history on every crash-restart
# RESET_ON_START=true

# Override broker
$env:BROKER_TYPE = "paper"; python main.py
```

Open `http://localhost:8000` for the web dashboard.

## Docker

```powershell
docker-compose up --build
```

The web dashboard is available on the configured `PORT` (default 8000). The `.env` file is passed to the container automatically. The web server always starts — to disable it, set `PORT=0`.

## Tests

```powershell
python -m pytest
```

353 tests covering all agents, broker, portfolio, position manager, database, memory, strategies, analytics, backtester, and the full pipeline smoke test. All tests run offline against sandboxed data directories.

### Test coverage

- `test_agents.py` — core agent tests
- `test_agents_extended.py` — SentimentAgent, RegimeAgent, ExecutionAgent, ComplianceAgent, Auditor, strategy selector
- `test_analytics.py` — compute_analytics, strategy breakdown, VaR, drawdown, duration stats
- `test_backtester.py` — _calc_sl_tp, _compute_metrics, _to_binance_symbol
- `test_base_agent.py` — base agent lifecycle and error handling
- `test_broker.py` — PaperBroker orders, fills, SL/TP, short selling, funds checking
- `test_database.py` — SQLite init, migrations, CRUD operations
- `test_fuzz.py` — fuzz testing across agents and parsers
- `test_indicators.py` — technical indicator computations
- `test_memory.py` — SharedMemory read/write, file-backed persistence
- `test_optimizer_agent.py` — grid-search parameter optimization
- `test_pipeline_smoke.py` — end-to-end pipeline against canned data (no network)
- `test_portfolio.py` — portfolio load/save, equity, P&L tracking
- `test_position_manager.py` — position open/close, SL/TP, price updates
- `test_strategies.py` — all 25+ strategy signal generation
- `test_webserver.py` — dashboard API endpoints and static file serving

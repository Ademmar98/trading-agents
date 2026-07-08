# Trading Agents

Multi-agent crypto trading bot with paper/live broker support, 25+ strategies, regime detection, risk management, and a real-time dashboard.

## Architecture

Agents run sequentially each cycle in a defined pipeline, sharing state through JSON files in `data/reports/`:

```
Orchestrator → ResearchAnalyst → HealthMonitor → SentimentAgent → RegimeAgent
→ RiskManager → PositionSizer → PortfolioManagerAgent → ComplianceAgent
→ ExecutionAgent → Trader → Auditor
```

- **Orchestrator** — coordinates the cycle; writes start/end markers to shared memory
- **ResearchAnalyst** — fetches OHLC data, runs 25+ strategies per symbol, produces opportunities
- **HealthMonitor** — checks broker connectivity, data freshness, error rates
- **SentimentAgent** — scores market mood from price breadth and Fear & Greed Index
- **RegimeAgent** — detects regime (trending/ranging/volatile) per symbol via ADX, BB width, ATR
- **RiskManager** — sets portfolio-level risk limits (per-symbol, volatility, correlation)
- **PositionSizer** — computes Kelly-optimal position sizes based on historical trade stats
- **PortfolioManagerAgent** — allocates capital across opportunities with strategy-weighted scoring
- **ComplianceAgent** — enforces spot-only, exposure, concentration, and daily-loss gates
- **ExecutionAgent** — builds formal trade plans with SL/TP/RR, checks spread before approval
- **Trader** — executes orders through the selected broker; checks stop-losses on every cycle
- **Auditor** — reviews performance, generates suggestions, tracks agent health

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
| `TELEGRAM_BOT_TOKEN` | — | Bot token for notifications |
| `TELEGRAM_CHAT_ID` | — | Chat ID for notifications |

## Usage

```powershell
# Start trading (with live dashboard)
python main.py

# Headless mode (server — web dashboard still available)
python main.py --headless

# Reset all data and start fresh
python main.py --reset

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

162 tests covering all agents, broker, portfolio, position manager, database, memory, strategies, analytics, backtester, and the full pipeline smoke test. All tests run offline against sandboxed data directories.

### Test coverage

- `test_broker.py` — PaperBroker orders, fills, SL/TP, short selling, funds checking
- `test_analytics.py` — compute_analytics, strategy breakdown, VaR, drawdown, duration stats
- `test_backtester.py` — _calc_sl_tp, _compute_metrics, _to_binance_symbol
- `test_agents_extended.py` — SentimentAgent, RegimeAgent, ExecutionAgent, ComplianceAgent, Auditor, strategy selector
- `test_pipeline_smoke.py` — end-to-end pipeline against canned data (no network)

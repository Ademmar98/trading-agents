import os
import sys
import zlib
from pathlib import Path

BASE_DIR = Path(__file__).parent
# Overridable so tests can run against a throwaway data dir
DATA_DIR = Path(os.getenv("TRADING_DATA_DIR", BASE_DIR / "data"))

# ── Environment variable loading ──
# Railway / production: inject secrets via the cloud dashboard (env vars).
# Local dev: create a .env file beside this script (git-ignored) as a fallback.
# System env vars ALWAYS take priority over .env (setdefault).
_env_file = BASE_DIR / ".env"
if _env_file.exists():
    for _line in _env_file.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _key, _, _value = _line.partition("=")
            os.environ.setdefault(_key.strip(), _value.strip())

INITIAL_BALANCE = float(os.getenv("TRADING_CAPITAL", "10000"))
MARKET_TYPE = os.getenv("MARKET_TYPE", "crypto")  # crypto, stocks, metals, both

ALPACA_API_KEY = os.getenv("ALPACA_API_KEY", "")
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY", "")
ALPACA_PAPER = os.getenv("ALPACA_PAPER", "true").lower() == "true"

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-20250514")

# Market-data fallbacks: Massive (Polygon) for stocks, TwelveData for
# metals/forex spot and stocks. Free tiers are rate-limited (Massive 5
# req/min, TwelveData 800 credits/day) so they back up Yahoo, not replace it.
MASSIVE_API_KEY = os.getenv("MASSIVE_API_KEY", "")
TWELVEDATA_API_KEY = os.getenv("TWELVEDATA_API_KEY", "")

# Nous Research Hermes — powers the HeadTrader review agent (OpenAI-compatible)
HERMES_API_KEY = os.getenv("HERMES_API_KEY", "")
HERMES_API_URL = os.getenv("HERMES_API_URL", "https://inference-api.nousresearch.com/v1/chat/completions")
HERMES_MODEL = os.getenv("HERMES_MODEL", "nousresearch/hermes-4-70b")
# Used when the primary model is unavailable (e.g. no credits on the account)
HERMES_FALLBACK_MODEL = os.getenv("HERMES_FALLBACK_MODEL", "stepfun/step-3.7-flash:free")
HEAD_TRADER_INTERVAL_MIN = int(os.getenv("HEAD_TRADER_INTERVAL_MIN", "60"))

# ── 15-minute scalping stack ──
# Entry: price above/below EMA (trend filter) + MACD crossover in the trend
# direction + RSI guard against buying tops / selling bottoms.
# Exits: SL = SCALP_ATR_SL_MULT x ATR(14); TP from the win-rate/R:R matrix.
SCALP_15M_ENABLED = os.getenv("SCALP_15M_ENABLED", "true").lower() == "true"
SCALP_EMA_PERIOD = int(os.getenv("SCALP_EMA_PERIOD", "50"))
SCALP_ATR_SL_MULT = float(os.getenv("SCALP_ATR_SL_MULT", "1.5"))
SCALP_RSI_OVERBOUGHT = float(os.getenv("SCALP_RSI_OVERBOUGHT", "70"))
SCALP_RSI_OVERSOLD = float(os.getenv("SCALP_RSI_OVERSOLD", "30"))
# Predictive gate before order routing: scalp setups whose estimated win
# probability is below this are aborted. The estimate is a Laplace-smoothed
# empirical win rate + indicator-synergy bonus — an honest heuristic, NOT a
# calibrated probability. A fresh strategy starts near 0.5-0.6 and can only
# climb by closing winners, so a 0.92 bar is a permanent off-switch (no
# trades -> no history -> estimate never rises). Bootstrap at 0.60 and raise
# the bar (e.g. toward 0.92) as the live record earns it.
SCALP_MIN_WIN_PROB = float(os.getenv("SCALP_MIN_WIN_PROB", "0.60"))

_mt5_login_raw = os.getenv("MT5_LOGIN", "0")
MT5_LOGIN = int(_mt5_login_raw) if _mt5_login_raw and _mt5_login_raw.strip() else 0
MT5_PASSWORD = os.getenv("MT5_PASSWORD", "")
MT5_SERVER = os.getenv("MT5_SERVER", "MetaQuotes-Demo")

DXTRADE_API_URL = os.getenv("DXTRADE_API_URL", "https://dx.velotrade.com/dxsca-web")
DXTRADE_USERNAME = os.getenv("DXTRADE_USERNAME", "")
DXTRADE_PASSWORD = os.getenv("DXTRADE_PASSWORD", "")
DXTRADE_DOMAIN = os.getenv("DXTRADE_DOMAIN", "default")

BROKER_TYPE = os.getenv("BROKER_TYPE", "paper")  # paper, binance, mt5, alpaca, dxtrade

BINANCE_API_KEY = os.getenv("BINANCE_API_KEY", "")
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET", "")
BINANCE_USE_TESTNET = os.getenv("BINANCE_USE_TESTNET", "true").lower() == "true"

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

LEVERAGE_ENABLED = False  # spot-only, no margin
MAX_POSITION_SIZE_PCT = float(os.getenv("MAX_POSITION_SIZE_PCT", "15"))
MAX_PORTFOLIO_RISK_PCT = float(os.getenv("MAX_PORTFOLIO_RISK_PCT", "15"))
TRADE_FEE_PCT = float(os.getenv("TRADE_FEE_PCT", "0.1"))
TRADING_TIMEFRAME = os.getenv("TRADING_TIMEFRAME", "5m")
# Half-spread cost per side applied in backtests, mirroring live fills that
# pay the ask / hit the bid instead of the mid (0.05 = 0.05% per leg).
BACKTEST_SPREAD_PCT = float(os.getenv("BACKTEST_SPREAD_PCT", "0.05"))
BACKTEST_BARS = int(os.getenv("BACKTEST_BARS", "2500"))
MAX_CONSECUTIVE_LOSSES = int(os.getenv("MAX_CONSECUTIVE_LOSSES", "3"))
STOP_LOSS_PCT = float(os.getenv("STOP_LOSS_PCT", "2.0"))
DAILY_LOSS_LIMIT_PCT = float(os.getenv("DAILY_LOSS_LIMIT_PCT", "3"))
TRAILING_STOP_PCT = float(os.getenv("TRAILING_STOP_PCT", "0.5"))
TRAILING_ACTIVATION_PCT = float(os.getenv("TRAILING_ACTIVATION_PCT", "0.8"))
# Scaled exits: bank part of a winner at a multiple of initial risk (R), move
# the stop to breakeven, and let the remainder trail. R-based trailing replaces
# the percent-of-price trail, which clipped winners at a fraction of what a
# full stop-loss costs.
PARTIAL_TP_ENABLED = os.getenv("PARTIAL_TP_ENABLED", "true").lower() == "true"
PARTIAL_TP_R = float(os.getenv("PARTIAL_TP_R", "1.5"))
PARTIAL_TP_FRACTION = float(os.getenv("PARTIAL_TP_FRACTION", "0.5"))
TRAILING_ACTIVATION_R = float(os.getenv("TRAILING_ACTIVATION_R", "1.0"))
TRAILING_STOP_R = float(os.getenv("TRAILING_STOP_R", "0.5"))
BREAKEVEN_ENABLED = os.getenv("BREAKEVEN_ENABLED", "true").lower() == "true"
# Breakeven activates when profit reaches BREAKEVEN_ACTIVATION_PCT% of the
# entry-to-SL distance (1R). Default 100 = exactly at 1:1 R:R.
# After breakeven the stop stays parked at entry + buffer — no further
# trailing — so the trade has room to reach the final TP.
BREAKEVEN_ACTIVATION_PCT = float(os.getenv("BREAKEVEN_ACTIVATION_PCT", "100"))
# Tiny buffer above entry for the breakeven stop to avoid noise/commission
# exits. Applied after breakeven activates (default 0.15%).
# Must exceed the round-trip fee (2 × TRADE_FEE_PCT = 0.2%) or a
# "breakeven" exit still loses money after commissions.  0.3 % gives
# a 0.1 % safety margin above the default 0.2 % round-trip cost.
BREAKEVEN_BUFFER_PCT = float(os.getenv("BREAKEVEN_BUFFER_PCT", "0.3"))
# Fallback SL/TP multipliers — used by ExecutionAgent when pricing data is absent
SL_VOL_MULT = float(os.getenv("SL_VOL_MULT", "1.5"))
TP_VOL_MULT = float(os.getenv("TP_VOL_MULT", "2.0"))
# Hard caps on any computed stop/target distance. A scalping firm has no
# business holding a 29% stop — that was daily-range volatility leaking into
# 15m trade pricing. Caps are the last line of defense; the primary fix is
# pricing from the trading timeframe's own ATR.
MAX_SL_PCT = float(os.getenv("MAX_SL_PCT", "3.0"))
MAX_TP_PCT = float(os.getenv("MAX_TP_PCT", "6.0"))
# Must clear the round-trip fee (2 x TRADE_FEE_PCT = 0.2%) with real margin,
# or trades that hit minimum TP still lose money after costs.
MIN_TP_PCT = float(os.getenv("MIN_TP_PCT", "0.5"))
# Trade-frequency caps. 0 = unlimited (default). Set a positive number to
# cap entries per UTC day / per rolling hour — risk data says overtrading
# loses, but the caps also idle the bot once hit, so they are opt-in.
MAX_TRADES_PER_DAY = int(os.getenv("MAX_TRADES_PER_DAY", "150"))
MAX_TRADES_PER_HOUR = int(os.getenv("MAX_TRADES_PER_HOUR", "0"))
# Portfolio heat: total open risk (distance to stop x qty, summed over open
# positions) as % of equity. Blocks new entries only while above the cap —
# exits always run. 0 = off. Breakeven'd runners contribute zero risk.
MAX_OPEN_RISK_PCT = float(os.getenv("MAX_OPEN_RISK_PCT", "10"))
# Concurrent positions per asset cluster (crypto / stock / forex+metals):
# 15 crypto longs are one BTC-beta bet, not 15 independent trades. 0 = off.
MAX_POSITIONS_PER_CLUSTER = int(os.getenv("MAX_POSITIONS_PER_CLUSTER", "8"))
# When a candidate's 30d returns correlate >= this with an already-open
# position, its size is halved (soft de-risk, never a block). 0 = off.
MAX_PAIR_CORRELATION = float(os.getenv("MAX_PAIR_CORRELATION", "0.9"))
# Base risk percentage fed to PricingAgent's per-opportunity calculated_risk_pct
RISK_PER_TRADE_PCT = float(os.getenv("RISK_PER_TRADE_PCT", "0.5"))
_LOCK_PORT_OVERRIDE = int(os.getenv("TRADING_LOCK_PORT", "0"))
if _LOCK_PORT_OVERRIDE:
    LOCK_PORT = _LOCK_PORT_OVERRIDE
else:
    LOCK_PORT = 48620 + (zlib.crc32(str(DATA_DIR).encode()) % 1000)

TRADING_INTERVAL_MINUTES = int(os.getenv("TRADING_INTERVAL_MINUTES", "1"))

# Tunable parameters — each entry defines a range and step for the optimizer
TUNABLE_PARAMS = {
    "SL_VOL_MULT":       {"default": 1.5, "min": 0.3,  "max": 3.0, "increment": 0.3},
    "TP_VOL_MULT":       {"default": 2.0, "min": 0.5,  "max": 5.0, "increment": 0.5},
    "RISK_PER_TRADE_PCT":{"default": 0.5, "min": 0.1,  "max": 2.0, "increment": 0.1},
    "STOP_LOSS_PCT":     {"default": 2.0, "min": 0.3,  "max": 5.0, "increment": 0.3},
}

# Params whose values must never increase via auto-tuning (risk limits)
RISK_TUNABLE_PARAMS = {"MAX_POSITION_SIZE_PCT", "RISK_PER_TRADE_PCT", "STOP_LOSS_PCT", "SL_VOL_MULT"}

# Breakeven SL: when price reaches this % of the TP distance, move SL to entry
# Set BREAKEVEN_ENABLED=false to disable, BREAKEVEN_ACTIVATION_PCT=0 to use 1x SL distance only

# Symbol universes per market. Crypto uses BASE/QUOTE, stocks are plain
# tickers (<=5 letters), metals/forex are 6-letter pairs quoted via Yahoo =X
# (spot) or MT5 when available.
CRYPTO_SYMBOLS = [s for s in os.getenv(
    "CRYPTO_SYMBOLS",
    "BTC/USD,ETH/USD,SOL/USD,BNB/USD,XRP/USD,ADA/USD,DOGE/USD,DOT/USD,AVAX/USD,LINK/USD,UNI/USD,ATOM/USD,LTC/USD,BCH/USD,TRX/USD,AAVE/USD,MATIC/USD,APT/USD,ARB/USD,OP/USD"
).split(",") if s.strip()]
STOCK_SYMBOLS = [s for s in os.getenv(
    "STOCK_SYMBOLS", "AAPL,MSFT,NVDA,TSLA,AMZN,GOOGL,META,SPY"
).split(",") if s.strip()]
METAL_SYMBOLS = [s for s in os.getenv("METAL_SYMBOLS", "XAUUSD,XAGUSD").split(",") if s.strip()]

# An explicit WATCHED_SYMBOLS env var wins as-is; otherwise the list is
# assembled from the universes selected by MARKET_TYPE.
_env_watched = os.getenv("WATCHED_SYMBOLS", "")
if _env_watched:
    WATCHED_SYMBOLS = [s for s in _env_watched.split(",") if s.strip()]
else:
    WATCHED_SYMBOLS = []
    if MARKET_TYPE in ("crypto", "both"):
        WATCHED_SYMBOLS += CRYPTO_SYMBOLS
    if MARKET_TYPE in ("stocks", "both"):
        WATCHED_SYMBOLS += STOCK_SYMBOLS
    if MARKET_TYPE in ("metals", "both"):
        WATCHED_SYMBOLS += METAL_SYMBOLS
    if not WATCHED_SYMBOLS:
        WATCHED_SYMBOLS = list(CRYPTO_SYMBOLS)

# Replace the crypto part with ALL Binance testnet USDT pairs when
# BINANCE_ALL_SYMBOLS=true; stocks/metals from MARKET_TYPE are kept.
if os.getenv("BINANCE_ALL_SYMBOLS", "").lower() in ("true", "1", "yes"):
    try:
        import requests as _req
        _base = "https://testnet.binance.vision" if BINANCE_USE_TESTNET else "https://api.binance.com"
        _resp = _req.get(f"{_base}/api/v3/exchangeInfo", timeout=15)
        _data = _resp.json()
        _all = []
        for _s in _data.get("symbols", []):
            if _s.get("quoteAsset") == "USDT" and _s.get("status") == "TRADING":
                _sym = _s["symbol"].replace("USDT", "/USD").replace("BUSD", "/USD")
                if _sym.endswith("/USD"):
                    _all.append(_sym)
        if _all:
            WATCHED_SYMBOLS = sorted(_all) + [s for s in WATCHED_SYMBOLS if "/" not in s]
    except Exception:
        pass

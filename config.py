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
MARKET_TYPE = os.getenv("MARKET_TYPE", "crypto")  # crypto, stocks, both

ALPACA_API_KEY = os.getenv("ALPACA_API_KEY", "")
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY", "")
ALPACA_PAPER = os.getenv("ALPACA_PAPER", "true").lower() == "true"

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-20250514")

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
BACKTEST_BARS = int(os.getenv("BACKTEST_BARS", "2500"))
MAX_CONSECUTIVE_LOSSES = int(os.getenv("MAX_CONSECUTIVE_LOSSES", "3"))
STOP_LOSS_PCT = float(os.getenv("STOP_LOSS_PCT", "5"))
DAILY_LOSS_LIMIT_PCT = float(os.getenv("DAILY_LOSS_LIMIT_PCT", "3"))
TRAILING_STOP_PCT = float(os.getenv("TRAILING_STOP_PCT", "0.5"))
TRAILING_ACTIVATION_PCT = float(os.getenv("TRAILING_ACTIVATION_PCT", "0.8"))
BREAKEVEN_ENABLED = os.getenv("BREAKEVEN_ENABLED", "true").lower() == "true"
BREAKEVEN_ACTIVATION_PCT = float(os.getenv("BREAKEVEN_ACTIVATION_PCT", "50"))
# Fallback SL/TP multipliers — used by ExecutionAgent when PricingAgent data is absent
SL_VOL_MULT = float(os.getenv("SL_VOL_MULT", "2.0"))
TP_VOL_MULT = float(os.getenv("TP_VOL_MULT", "6.0"))
MIN_TP_PCT = float(os.getenv("MIN_TP_PCT", "0.15"))
# Base risk percentage fed to PricingAgent's per-opportunity calculated_risk_pct
RISK_PER_TRADE_PCT = float(os.getenv("RISK_PER_TRADE_PCT", "1.0"))
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

WATCHED_SYMBOLS = os.getenv(
    "WATCHED_SYMBOLS",
    "BTC/USD,ETH/USD,SOL/USD,BNB/USD,XRP/USD,ADA/USD,DOGE/USD,DOT/USD,AVAX/USD,LINK/USD,UNI/USD,ATOM/USD,LTC/USD,BCH/USD,TRX/USD,AAVE/USD,MATIC/USD,APT/USD,ARB/USD,OP/USD"
).split(",")

# Override with ALL Binance testnet USDT pairs when BINANCE_ALL_SYMBOLS=true
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
            WATCHED_SYMBOLS = sorted(_all)
    except Exception:
        pass

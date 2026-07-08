import os
from pathlib import Path

BASE_DIR = Path(__file__).parent
# Overridable so tests can run against a throwaway data dir
DATA_DIR = Path(os.getenv("TRADING_DATA_DIR", BASE_DIR / "data"))

# Load variables from a local .env file if present (real env vars take priority)
# WARNING: .env stores secrets in plaintext — never commit it, restrict file perms
_env_file = BASE_DIR / ".env"
if _env_file.exists():
    import sys
    print("[WARNING] Loading secrets from .env — plaintext on disk. "
          "Set env vars directly in production.", file=sys.stderr)
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
MAX_POSITION_SIZE_PCT = float(os.getenv("MAX_POSITION_SIZE_PCT", "25"))
MAX_PORTFOLIO_RISK_PCT = float(os.getenv("MAX_PORTFOLIO_RISK_PCT", "10"))
TRADE_FEE_PCT = float(os.getenv("TRADE_FEE_PCT", "0.1"))
MAX_CONSECUTIVE_LOSSES = int(os.getenv("MAX_CONSECUTIVE_LOSSES", "3"))
STOP_LOSS_PCT = float(os.getenv("STOP_LOSS_PCT", "5"))
DAILY_LOSS_LIMIT_PCT = float(os.getenv("DAILY_LOSS_LIMIT_PCT", "3"))
TRAILING_STOP_PCT = float(os.getenv("TRAILING_STOP_PCT", "3"))
TRAILING_ACTIVATION_PCT = float(os.getenv("TRAILING_ACTIVATION_PCT", "4"))
# DEPRECATED — replaced by PricingAgent's dynamic per-symbol sl_mult
SL_VOL_MULT = float(os.getenv("SL_VOL_MULT", "2.0"))
# DEPRECATED — replaced by PricingAgent's dynamic per-symbol tp_mult
TP_VOL_MULT = float(os.getenv("TP_VOL_MULT", "6.0"))
# DEPRECATED — replaced by PricingAgent's dynamic take_profit check
MIN_TP_PCT = float(os.getenv("MIN_TP_PCT", "5.0"))
# DEPRECATED — replaced by PricingAgent's per-opportunity calculated_risk_pct
RISK_PER_TRADE_PCT = float(os.getenv("RISK_PER_TRADE_PCT", "1.0"))
import zlib
_LOCK_PORT_OVERRIDE = int(os.getenv("TRADING_LOCK_PORT", "0"))
if _LOCK_PORT_OVERRIDE:
    LOCK_PORT = _LOCK_PORT_OVERRIDE
else:
    LOCK_PORT = 48620 + (zlib.crc32(str(DATA_DIR).encode()) % 1000)

TRADING_INTERVAL_MINUTES = int(os.getenv("TRADING_INTERVAL_MINUTES", "60"))

# Tunable parameters — each entry defines a range and step for the optimizer
TUNABLE_PARAMS = {
    "SL_VOL_MULT":       {"default": 2.0, "min": 0.5,  "max": 5.0, "increment": 0.5},
    "TP_VOL_MULT":       {"default": 6.0, "min": 2.0,  "max": 12.0,"increment": 1.0},
    "RISK_PER_TRADE_PCT":{"default": 1.0, "min": 0.25, "max": 3.0, "increment": 0.25},
    "STOP_LOSS_PCT":     {"default": 5.0, "min": 1.0,  "max": 10.0,"increment": 1.0},
    "POSITION_SIZE_PCT": {"default": 25,  "min": 5,    "max": 50,  "increment": 5},
    "MAX_POSITION_SIZE_PCT": {"default": 25, "min": 5,  "max": 50,  "increment": 5},
}

WATCHED_SYMBOLS = os.getenv(
    "WATCHED_SYMBOLS",
    "BTC/USD,ETH/USD,SOL/USD,BNB/USD,XRP/USD,ADA/USD,DOGE/USD,DOT/USD,AVAX/USD,LINK/USD,UNI/USD,ATOM/USD,LTC/USD,BCH/USD,TRX/USD,AAVE/USD,MATIC/USD,APT/USD,ARB/USD,OP/USD"
).split(",")

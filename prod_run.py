#!/usr/bin/env python3
"""Production entrypoint for Railway / headless Docker deployment.

Verifies the environment, logs diagnostics, then delegates to main.py's
main() routine.  Exits early with clear messages on fatal misconfiguration.
"""
import os
import sys
import platform
from pathlib import Path

# ── Bootstrap: load env overrides before anything else touches config ──
# Railway injects env vars via its dashboard, but a local .env file beside
# this script can be used for testing the same Docker image locally.
_env_file = Path(__file__).parent / ".env"
if _env_file.exists():
    for _line in _env_file.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _key, _, _value = _line.partition("=")
            os.environ.setdefault(_key.strip(), _value.strip())

# ── Diagnostics banner ──
print("=" * 60, flush=True)
print(f"  prod_run.py  |  host={platform.node()}  os={platform.system()}  py={platform.python_version()}", flush=True)
print(f"  DATA_DIR:     {os.getenv('TRADING_DATA_DIR', '<default>')}", flush=True)
print(f"  BROKER_TYPE:  {os.getenv('BROKER_TYPE', '<not set>')}", flush=True)
print(f"  HEADLESS:     {os.getenv('HEADLESS', '<not set>')}", flush=True)
print("=" * 60, flush=True)

# ── Platform / broker compatibility check ──
BROKER_TYPE = os.getenv("BROKER_TYPE", "paper")
is_linux = platform.system() == "Linux"

if BROKER_TYPE == "mt5" and is_linux:
    print(
        "FATAL: BROKER_TYPE=mt5 requires a Windows host with MetaTrader5 "
        "installed.  This container runs on Linux.\n"
        "       Set BROKER_TYPE to one of:\n"
        "         dxtrade   — REST API, works cross-platform\n"
        "         binance   — REST API, works cross-platform\n"
        "         paper     — in-process simulation (default)\n"
        "       Or run this image on a Windows Docker host.",
        file=sys.stderr, flush=True,
    )
    sys.exit(78)  # EX_CONFIG

if BROKER_TYPE == "mt5":
    try:
        import MetaTrader5  # noqa: F401
    except ImportError:
        print(
            "FATAL: MetaTrader5 package is not installed. "
            "Run: pip install MetaTrader5\n"
            "      Note: MetaTrader5 is Windows-only.",
            file=sys.stderr, flush=True,
        )
        sys.exit(1)

# ── Ensure DATA_DIR exists with all subdirectories ──
from config import DATA_DIR  # noqa: E402
DATA_DIR.mkdir(parents=True, exist_ok=True)
for sub in ("analyses", "decisions", "orders", "reports", "logs"):
    (DATA_DIR / sub).mkdir(parents=True, exist_ok=True)
print(f"  Data directory: {DATA_DIR.resolve()}  (exists={DATA_DIR.exists()})", flush=True)

# ── Delegate ──
print("  Starting production agent firm…", flush=True)
print("=" * 60, flush=True)

from main import main  # noqa: E402
main()

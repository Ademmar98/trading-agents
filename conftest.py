import os
import sys
import tempfile

# Must happen before config.py is imported anywhere: sandbox all runtime data
# into a throwaway dir and force the paper broker so tests never touch the
# live ledger, database, or exchange.
os.environ["TRADING_DATA_DIR"] = tempfile.mkdtemp(prefix="trading-agents-test-")
os.environ["BROKER_TYPE"] = "paper"
os.environ.setdefault("TRADING_CAPITAL", "10000")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

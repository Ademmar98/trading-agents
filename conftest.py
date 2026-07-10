import os
import sys
import tempfile

# Must happen before config.py is imported anywhere: sandbox all runtime data
# into a throwaway dir and force the paper broker so tests never touch the
# live ledger, database, or exchange.
os.environ["TRADING_DATA_DIR"] = tempfile.mkdtemp(prefix="trading-agents-test-")
os.environ["BROKER_TYPE"] = "paper"
os.environ.setdefault("TRADING_CAPITAL", "10000")
# Blank these before config.py falls back to .env, or the Notifier goes live
# and every agent run in the suite sends real Telegram messages.
os.environ["TELEGRAM_BOT_TOKEN"] = ""
os.environ["TELEGRAM_CHAT_ID"] = ""
# Same for the Hermes key — tests must never hit the paid inference API.
os.environ["HERMES_API_KEY"] = ""

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

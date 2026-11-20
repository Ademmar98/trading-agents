import sys, os
sys.path.insert(0, "C:\\Users\\DELL\\OneDrive\\1m")
from dotenv import load_dotenv
load_dotenv()
from core.exchange.propr.config import ProprConfig
from core.exchange.propr.client import ProprRiskClient
cfg = ProprConfig(api_key=os.getenv("PROPR_API_KEY",""))
client = ProprRiskClient(cfg)
start = os.getenv("CHALLENGE_START")
client.setup(os.getenv("PROPR_ACCOUNT_ID") or None,
             float(start) if start else None)
summary = client.get_account_summary()
print(f"Equity: ${summary['equity']:,.2f}")
print(f"Balance: ${summary['balance']:,.2f}")
print(f"PnL: ${summary['profit']:,.2f} ({summary['profit_pct']:.1%})")
positions = client.get_open_positions()
print(f"Open positions: {len(positions)}")
for p in positions:
    print(f"  {p['base']}: {p['quantity']} @ {p['entryPrice']} | mark={p['markPrice']} pnl={p['unrealizedPnl']}")

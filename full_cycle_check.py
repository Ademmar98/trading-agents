import sys; sys.path.insert(0, '.')
import os; os.environ['BROKER_TYPE'] = 'mt5'
from core.memory import SharedMemory
from core.portfolio import load_portfolio, save_portfolio, Portfolio
from core.live_broker import MetaQuotesBroker
from config import MT5_LOGIN, MT5_PASSWORD, MT5_SERVER

memory = SharedMemory()

# Init portfolio from MT5
b = MetaQuotesBroker(MT5_LOGIN, MT5_PASSWORD, MT5_SERVER)
info = b.get_account_info()
p = Portfolio(cash=info['balance'], initial_balance=info['balance'])
save_portfolio(p)
print(f"Portfolio: ${p.cash} cash, ${p.initial_balance} init")

# Run all agents
from agents.orchestrator import Orchestrator
from agents.analyst import ResearchAnalyst
from agents.sentiment_agent import SentimentAgent
from agents.regime_agent import RegimeAgent
from agents.risk_manager import RiskManager
from agents.portfolio_manager import PortfolioManagerAgent
from agents.compliance_agent import ComplianceAgent
from agents.execution_agent import ExecutionAgent
from agents.trader import Trader
from agents.auditor import Auditor

for name, agent_cls in [("Orch",Orchestrator),("Analyst",ResearchAnalyst),("Sentiment",SentimentAgent),("Regime",RegimeAgent),("Risk",RiskManager),("PortfolioMgr",PortfolioManagerAgent),("Compliance",ComplianceAgent),("Execution",ExecutionAgent),("Trade",Trader),("Audit",Auditor)]:
    try:
        agent = agent_cls()
        agent.run()
        print(f"OK: {name}")
    except Exception as e:
        import traceback; traceback.print_exc()
        print(f"FAIL: {name}: {e}")

# Show what happened
print("\n=== Opportunities ===")
analysis = memory.read_latest("analyses")
opps = analysis.get("opportunities", []) if analysis else []
for o in opps:
    print(f"  {o['symbol']}: {o['action']} conf={o['confidence']:.0%} [{', '.join(o.get('strategies',['?'])[:3])}]")

print("\n=== Risk Decisions ===")
risk = memory.read_latest("decisions")
if risk:
    print(f"  Verdict: {risk.get('verdict')}")
    print(f"  Max trade: ${risk.get('max_trade_size', 0):.0f}")

print("\n=== Trades ===")
audit = memory.read_latest("reports")
if audit:
    print(f"  {audit.get('summary', 'none')}")

b.shutdown()
print("DONE")

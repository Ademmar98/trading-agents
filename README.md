# Trading Agents

Local Python trading-agent workspace.

## Structure

- `agents/` contains agent roles such as analyst, trader, risk manager, auditor, and orchestrator.
- `core/` contains broker, market, portfolio, strategy, memory, database, and analytics code.
- `data/` contains generated local runtime data and is ignored by Git.
- `main.py` is the main entry point.
- `test_full_cycle.py` exercises the full trading-agent cycle.

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Notes

Generated databases, logs, orders, reports, decisions, analyses, caches, and virtual environments should stay out of version control.

"""Regression tests for the July 2026 bug-fix batch.

Covers:
- process_price_triggers: SL/TP exits detected by the monitor loops must place
  a closing broker order so the sale proceeds are credited back to cash
  (previously the position's full value vanished from equity).
- ExecutionAgent: a pricing_map entry computed for the opposite direction must
  not be applied — its SL/TP sit on the wrong side of entry.
- PortfolioManagerAgent._load_strategy_weights: losing strategies are penalized
  even when every strategy has negative PnL.
- Trader: orders fill at the current market price, not the hypothetical
  pullback entry price; trades are skipped when the market drifted too far.
"""
import tempfile
import time
from pathlib import Path

import pytest

import config as app_config
from core.database import init_db, execute
from core.memory import SharedMemory
from core.portfolio import Portfolio, save_portfolio, load_portfolio


@pytest.fixture(autouse=True)
def sandbox_data_dir(monkeypatch):
    tmp = Path(tempfile.mkdtemp(prefix="trading-test-"))
    monkeypatch.setattr(app_config, "DATA_DIR", tmp)
    monkeypatch.setenv("TRADING_DATA_DIR", str(tmp))
    yield
    import shutil
    shutil.rmtree(str(tmp), ignore_errors=True)


def test_price_trigger_credits_cash_through_broker(monkeypatch):
    """A stop-loss caught by the monitor loop must sell through the broker."""
    import main
    from core.broker import PaperBroker
    from core.notifier import Notifier

    init_db()
    main.memory = SharedMemory()
    monkeypatch.setattr(main, "notifier", Notifier("", ""))
    save_portfolio(Portfolio(cash=10000.0, initial_balance=10000.0))

    # Open the position the way Trader does: broker ledger + SQLite.
    broker = PaperBroker()
    order = broker.place_order("BTC/USD", "BUY", 0.1, 50000.0, sl=48000.0, tp=55000.0)
    assert order["status"] == "filled"
    main.pos_mgr.open_position("BTC/USD", "BUY", 0.1, 50000.0, sl=48000.0, tp=55000.0)

    triggered = main.process_price_triggers({"BTC/USD": {"price": 47500.0}})

    assert len(triggered) == 1
    assert triggered[0]["reason"] == "stop_loss"
    assert main.pos_mgr.get_open_positions() == []
    p = load_portfolio()
    # 10000 - 5000 entry - fee + 4750 exit - fee; before the fix the exit credit was lost
    fee = app_config.TRADE_FEE_PCT / 100.0
    expected = 10000.0 - 5000.0 * (1 + fee) + 4750.0 * (1 - fee)
    assert p.cash == pytest.approx(expected)
    assert "BTC/USD" not in p.positions


def test_price_trigger_noop_without_hits(monkeypatch):
    import main
    from core.notifier import Notifier

    init_db()
    main.memory = SharedMemory()
    monkeypatch.setattr(main, "notifier", Notifier("", ""))
    save_portfolio(Portfolio(cash=10000.0, initial_balance=10000.0))
    main.pos_mgr.open_position("BTC/USD", "BUY", 0.1, 50000.0, sl=48000.0, tp=55000.0)

    assert main.process_price_triggers({"BTC/USD": {"price": 50100.0}}) == []
    assert len(main.pos_mgr.get_open_positions()) == 1


def _seed_execution_inputs(memory, pricing_action, opp_action):
    memory.write("analyses", "market_scan", {
        "all_analyses": {"BTC/USD": {"price": 50000.0, "bid": 49999.0,
                                     "ask": 50001.0, "volatility": 2.0}},
        "timestamp": time.time(),
    })
    memory.write("decisions", "pricing", {"pricing_map": {"BTC/USD": {
        "symbol": "BTC/USD", "action": pricing_action,
        "entry_price": 49900.0,
        # SELL geometry: SL above entry, TP below (inverted for a BUY)
        "stop_loss": 51400.0 if pricing_action == "SELL" else 48400.0,
        "take_profit": 48400.0 if pricing_action == "SELL" else 51400.0,
        "sl_pct": 3.0, "tp_pct": 3.0, "calculated_risk_pct": 1.0,
    }}, "timestamp": time.time()})
    memory.write("decisions", "compliance_gate", {
        "halted": False,
        "approved_opportunities": [{
            "symbol": "BTC/USD", "action": opp_action, "confidence": 0.9,
            "price": 50000.0, "max_qty": 0.01, "reasons": [], "strategies": ["test"],
            "indicators": {"volatility": 2.0},
        }],
        "timestamp": time.time(),
    })


def test_execution_rejects_opposite_direction_pricing():
    """BUY order must not inherit SELL pricing (SL above entry)."""
    from agents.execution_agent import ExecutionAgent

    init_db()
    memory = SharedMemory()
    save_portfolio(Portfolio(cash=10000.0, initial_balance=10000.0))
    _seed_execution_inputs(memory, pricing_action="SELL", opp_action="BUY")

    plan = ExecutionAgent().run()
    assert len(plan["orders"]) == 1
    o = plan["orders"][0]
    assert o["stop_loss"] < o["entry_price"] < o["take_profit"]


def test_execution_uses_matching_direction_pricing():
    from agents.execution_agent import ExecutionAgent

    init_db()
    memory = SharedMemory()
    save_portfolio(Portfolio(cash=10000.0, initial_balance=10000.0))
    _seed_execution_inputs(memory, pricing_action="BUY", opp_action="BUY")

    plan = ExecutionAgent().run()
    assert len(plan["orders"]) == 1
    o = plan["orders"][0]
    assert o["entry_price"] == 49900.0
    assert o["stop_loss"] == 48400.0
    assert o["take_profit"] == 51400.0


def test_strategy_weights_all_negative_pnl():
    """Losers must be penalized even when every strategy is under water."""
    from agents.portfolio_manager import PortfolioManagerAgent

    init_db()
    execute("INSERT INTO strategy_stats (strategy, trades, win_rate, pnl) VALUES ('LeastBad', 20, 45.0, -50.0)")
    execute("INSERT INTO strategy_stats (strategy, trades, win_rate, pnl) VALUES ('Worst', 15, 30.0, -500.0)")
    weights = PortfolioManagerAgent._load_strategy_weights()
    assert weights["LeastBad"] == 0.50
    assert weights["Worst"] == 0.50


def _seed_trader_inputs(memory, market_price, plan_price):
    memory.write("analyses", "market_scan", {
        "all_analyses": {"BTC/USD": {"price": market_price}},
        "timestamp": time.time(),
    })
    memory.write("orders", "execution_plan", {
        "status": "ready",
        "orders": [{
            "symbol": "BTC/USD", "action": "BUY", "qty": 0.05,
            "price": plan_price, "stop_loss": plan_price * 0.96,
            "take_profit": plan_price * 1.12, "execution_ok": True,
            "strategies": ["test"], "plan_id": None,
        }],
        "timestamp": time.time(),
    })


def test_trader_fills_at_market_price():
    """Paper fills must use the real market price, not the pullback target."""
    from agents.trader import Trader

    init_db()
    memory = SharedMemory()
    save_portfolio(Portfolio(cash=10000.0, initial_balance=10000.0))
    _seed_trader_inputs(memory, market_price=50000.0, plan_price=49750.0)

    trader = Trader()
    executed = trader.run()
    filled = [o for o in executed if o.get("status") == "filled"]
    assert filled and filled[0]["price"] == 50000.0
    positions = trader.pos_mgr.get_open_positions()
    assert positions[0]["entry_price"] == 50000.0
    p = load_portfolio()
    fee = app_config.TRADE_FEE_PCT / 100.0
    assert p.cash == pytest.approx(10000.0 - 0.05 * 50000.0 * (1 + fee))


def test_trader_skips_on_price_drift():
    """Market 4.5% past the planned entry: SL/TP geometry is stale, skip."""
    from agents.trader import Trader

    init_db()
    memory = SharedMemory()
    save_portfolio(Portfolio(cash=10000.0, initial_balance=10000.0))
    _seed_trader_inputs(memory, market_price=52000.0, plan_price=49750.0)

    trader = Trader()
    executed = trader.run()
    assert executed == []
    assert trader.pos_mgr.get_open_positions() == []
    assert load_portfolio().cash == 10000.0

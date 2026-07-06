from agents.position_sizer import PositionSizer
from core.database import init_db, execute


def setup_db():
    init_db()
    execute("DELETE FROM analytics")
    execute("DELETE FROM strategy_stats")


def test_kelly_fraction_no_trades():
    setup_db()
    kelly = PositionSizer._kelly_fraction()
    assert kelly > 0
    assert kelly <= 25.0


def test_kelly_fraction_with_data():
    setup_db()
    for i in range(30):
        execute("INSERT INTO trades (position_id, symbol, side, qty, entry_price, exit_price, pnl, pnl_pct, reason) VALUES (?, 'TEST', 'BUY', 1.0, 100.0, 100.0, ?, 0.0, 'test')",
                [i, 30.0])
    for i in range(20):
        execute("INSERT INTO trades (position_id, symbol, side, qty, entry_price, exit_price, pnl, pnl_pct, reason) VALUES (?, 'TEST', 'BUY', 1.0, 100.0, 100.0, ?, 0.0, 'test')",
                [i + 30, -10.0])
    kelly = PositionSizer._kelly_fraction()
    assert 0 < kelly <= 25.0


def test_strategy_weights_no_stats():
    from agents.portfolio_manager import PortfolioManagerAgent
    setup_db()
    weights = PortfolioManagerAgent._load_strategy_weights()
    assert weights == {}


def test_strategy_weights_with_data():
    from agents.portfolio_manager import PortfolioManagerAgent
    setup_db()
    execute("INSERT INTO strategy_stats (strategy, trades, win_rate, pnl) VALUES ('FVG', 20, 60.0, 400.0)")
    execute("INSERT INTO strategy_stats (strategy, trades, win_rate, pnl) VALUES ('MACD', 15, 40.0, -100.0)")
    weights = PortfolioManagerAgent._load_strategy_weights()
    assert "FVG" in weights
    assert "MACD" in weights
    assert weights["MACD"] < weights["FVG"]

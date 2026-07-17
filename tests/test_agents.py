from agents.position_sizer import PositionSizer
from core.database import init_db, execute


def setup_db():
    init_db()
    execute("DELETE FROM analytics")
    execute("DELETE FROM strategy_stats")
    # Kelly tests assert exact values — isolate them from trade/position rows
    # seeded by earlier tests in the shared session DB.
    execute("DELETE FROM trades")
    execute("DELETE FROM positions")


def test_kelly_fraction_no_trades():
    setup_db()
    kelly = PositionSizer._kelly_fraction()
    assert kelly > 0
    assert kelly <= 25.0


def _seed_position(position_id, pnl, qty=1.0, initial_risk=5.0):
    """One position + one closing trade row; initial_risk freezes 1R so the
    Kelly sample is computed in R-multiples (pnl / (initial_risk * qty))."""
    execute("INSERT INTO positions (id, symbol, side, quantity, entry_price, current_price, initial_risk) "
            "VALUES (?, 'TEST', 'BUY', ?, 100.0, 100.0, ?)",
            [position_id, qty, initial_risk])
    execute("INSERT INTO trades (position_id, symbol, side, qty, entry_price, exit_price, pnl, pnl_pct, reason) "
            "VALUES (?, 'TEST', 'BUY', ?, 100.0, 100.0, ?, 0.0, 'test')",
            [position_id, qty, pnl])


def test_kelly_fraction_with_data():
    setup_db()
    for i in range(30):
        _seed_position(i + 1, 30.0)    # +6R each
    for i in range(20):
        _seed_position(i + 31, -10.0)  # -2R each
    kelly = PositionSizer._kelly_fraction()
    assert 0 < kelly <= 25.0


def test_kelly_fraction_requires_30_positions():
    """Negative-edge book, but only 29 positions -> guard returns the 25%
    default instead of trusting a tiny sample."""
    setup_db()
    for i in range(10):
        _seed_position(i + 1, 5.0)
    for i in range(19):
        _seed_position(i + 11, -5.0)
    assert PositionSizer._kelly_fraction() == 25.0


def test_kelly_fraction_zero_on_negative_edge():
    """25% win rate at b=1 -> Kelly <= 0 -> clamped to exactly 0, not the
    falsy-branch full size the old sizing line produced."""
    setup_db()
    for i in range(10):
        _seed_position(i + 1, 5.0)     # +1R
    for i in range(30):
        _seed_position(i + 11, -5.0)   # -1R
    assert PositionSizer._kelly_fraction() == 0.0


def test_kelly_merges_partial_exit_legs():
    """A partial_tp row + runner row share position_id and must count as ONE
    trade: 31 rows / 30 positions crosses the n>=30 guard; 30 rows / 29
    positions must not."""
    setup_db()
    # 29 positions, negative edge; position 1 has two legs (30 rows total)
    # that net to a +5 win — ungrouped it would count as 1 win + 1 loss.
    execute("INSERT INTO positions (id, symbol, side, quantity, entry_price, current_price, initial_risk) "
            "VALUES (1, 'TEST', 'BUY', 1.0, 100.0, 100.0, 5.0)")
    execute("INSERT INTO trades (position_id, symbol, side, qty, entry_price, exit_price, pnl, pnl_pct, reason) "
            "VALUES (1, 'TEST', 'BUY', 0.5, 100.0, 100.0, 7.5, 0.0, 'partial_tp')")
    execute("INSERT INTO trades (position_id, symbol, side, qty, entry_price, exit_price, pnl, pnl_pct, reason) "
            "VALUES (1, 'TEST', 'BUY', 0.5, 100.0, 100.0, -2.5, 0.0, 'runner')")
    for i in range(28):
        _seed_position(i + 2, -5.0)
    assert PositionSizer._kelly_fraction() == 25.0   # 29 grouped trades -> guard
    _seed_position(30, -5.0)
    assert PositionSizer._kelly_fraction() == 0.0    # 30 grouped trades -> computed


def test_auditor_win_rate_reads_sql_trade_rows():
    """Bug: Auditor read `realized_pnl` from portfolio.trades, but production
    syncs SQL trade rows keyed `pnl` (main.sync_position_stores, main.py:303)
    — every trade scored 0 and a ~87% book reported 0%. The headline stats
    now come from the same position-grouped SQL rows as analytics."""
    from agents.auditor import Auditor
    from core.portfolio import Portfolio, save_portfolio
    setup_db()
    save_portfolio(Portfolio(cash=10000.0, initial_balance=10000.0))
    for i in range(13):
        _seed_position(i + 1, 30.0)
    for i in range(2):
        _seed_position(i + 14, -10.0)
    report = Auditor().run()
    assert report["summary"]["total_trades"] == 15
    assert report["summary"]["win_rate"] == 86.7


def test_zero_kelly_gives_zero_size(monkeypatch):
    """Bug: `size_mult = kelly_pct / 25.0 if kelly_pct else 1.0` sized FULL
    when Kelly was legitimately 0. Now 0 Kelly -> 0 size."""
    from core.memory import SharedMemory
    setup_db()
    monkeypatch.setattr(PositionSizer, "_kelly_fraction", staticmethod(lambda: 0.0))
    memory = SharedMemory()
    memory.write("decisions", "risk_assessment", {
        "approved_opportunities": [
            {"symbol": "BTC/USD", "action": "BUY", "max_qty": 1.0, "price": 50000.0},
        ],
    })
    report = PositionSizer().run()
    sized = report["sized_opportunities"]
    assert len(sized) == 1
    assert sized[0]["size_mult"] == 0.0
    assert sized[0]["max_qty"] == 0.0


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

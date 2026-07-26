"""
Guard: open_long() must never leave a filled entry without a confirmed stop.

At the configured position size a single unprotected trade can breach the
challenge's daily-loss limit on its own, so if the stop cannot be placed the
entry must be flattened and reported as failed.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.exchange.propr.client import ProprRiskClient
from core.exchange.propr.config import ProprConfig


class FakeSDK:
    """Records calls; lets each test choose what the exchange 'returns'."""

    def __init__(self, position_after_entry=True, sl_ok=True, tp_ok=True,
                 close_raises=False, positions=None, orders=None,
                 orders_raise=False):
        self._position_after_entry = position_after_entry
        self.sl_ok = sl_ok
        self.tp_ok = tp_ok
        self._close_raises = close_raises
        self._positions = positions
        self._orders = orders or []
        self._orders_raise = orders_raise
        self.closed = []
        self.buys = []

    def market_buy(self, asset, qty):
        self.buys.append((asset, qty))
        return [{"orderId": "entry-1", "status": "filled"}]

    def get_open_positions(self, base=None):
        if self._positions is not None:
            return self._positions
        if not self._position_after_entry:
            return []
        return [{"positionId": "pos-1", "base": base or "BTC"}]

    def get_orders(self, status=None, limit=20, **kw):
        if self._orders_raise:
            raise RuntimeError("orders endpoint down")
        return [o for o in self._orders if o.get("status") == status]

    def get_account(self):
        return {"availableBalance": "5000", "balance": "5000",
                "totalUnrealizedPnl": "0"}

    def close_position(self, base, quote="USDC"):
        if self._close_raises:
            raise RuntimeError("exchange unreachable")
        self.closed.append(base)
        return [{"orderId": "close-1"}]


def build_client(sdk, monkeypatch):
    cfg = ProprConfig(api_key="test")
    client = ProprRiskClient.__new__(ProprRiskClient)  # skip network in __init__
    client.config = cfg
    client.sdk = sdk
    client.account_id = "acct-1"
    client._last_trade_time = 0.0
    client._trades_today = 0

    # can_trade now takes new_risk_usd; accept anything, these tests exercise
    # the stop guard rather than the risk gate.
    monkeypatch.setattr(client, "can_trade", lambda *a, **k: (True, "OK"))

    def fake_conditional(order_type, asset, quantity, trigger_price, position_id):
        ok = sdk.sl_ok if order_type == "stop_market" else sdk.tp_ok
        return [{"orderId": f"{order_type}-1"}] if ok else []

    monkeypatch.setattr(client, "_place_conditional", fake_conditional)
    return client


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr("core.exchange.propr.client.time.sleep", lambda s: None)


def test_stop_placed_position_kept(monkeypatch):
    sdk = FakeSDK(sl_ok=True, tp_ok=True)
    client = build_client(sdk, monkeypatch)

    orders = client.open_long("BTC", 0.01, stop_price=90_000, take_profit_price=110_000)

    assert orders, "a protected entry must be reported as a real trade"
    assert sdk.closed == [], "must not flatten a properly protected position"


def test_stop_fails_position_is_flattened(monkeypatch):
    sdk = FakeSDK(sl_ok=False)
    client = build_client(sdk, monkeypatch)

    orders = client.open_long("BTC", 0.01, stop_price=90_000, take_profit_price=110_000)

    assert orders == [], "a stopless entry must be reported as FAILED, not as a trade"
    assert sdk.closed == ["BTC"], "a stopless entry must be flattened immediately"


def test_no_position_found_is_flattened(monkeypatch):
    sdk = FakeSDK(position_after_entry=False)
    client = build_client(sdk, monkeypatch)

    orders = client.open_long("BTC", 0.01, stop_price=90_000)

    assert orders == []
    assert sdk.closed == ["BTC"], "unknown position state must still be flattened"


def test_take_profit_failure_keeps_stopped_position(monkeypatch):
    """TP is not a safety control — losing it must not flatten a stopped position."""
    sdk = FakeSDK(sl_ok=True, tp_ok=False)
    client = build_client(sdk, monkeypatch)

    orders = client.open_long("BTC", 0.01, stop_price=90_000, take_profit_price=110_000)

    assert orders, "position is stop-protected; a missing TP is not a reason to exit"
    assert sdk.closed == []


def test_flatten_failure_does_not_raise(monkeypatch):
    """If the emergency close itself fails we log critical, but must not crash
    the scan loop — the next cycle still needs to run."""
    sdk = FakeSDK(sl_ok=False, close_raises=True)
    client = build_client(sdk, monkeypatch)

    orders = client.open_long("BTC", 0.01, stop_price=90_000)

    assert orders == [], "must still report failure when the flatten errors"


# ---------------------------------------------------------------------------
# Aggregate stop-risk gate
#
# The live book reached exactly 100% of the $150 daily cap: two $1,996
# positions, each stopping 3.75% away, $75 risk apiece. Position COUNT was
# within limits the whole time -- counting tickets does not bound loss.
# ---------------------------------------------------------------------------

def risk_client(sdk, start=5000.0, fraction=None):
    """fraction pins max_dd_risk_fraction so these tests exercise the gate's
    logic rather than whatever the shipped default happens to be."""
    from core.exchange.propr.config import AccountSize, ChallengeType
    cfg = ProprConfig(api_key="test", account_size=AccountSize.K5,
                      challenge_type=ChallengeType.CLASSIC_1STEP)
    if fraction is not None:
        cfg.max_dd_risk_fraction = fraction
    c = ProprRiskClient.__new__(ProprRiskClient)
    c.config = cfg
    c.sdk = sdk
    c.account_id = "acct-1"
    c._last_trade_time = 0.0
    c._trades_today = 0
    c._circuit_breaker_until = 0.0
    c._starting_balance = start
    c._high_water_mark = start
    c._day_start_balance = start
    c._day_start_date = None
    c._daily_realized_pnl = 0.0
    c._monthly_realized_pnl = 0.0
    c._closed_positions = []
    return c


def _pos(base, qty, entry):
    return {"positionId": f"p-{base}", "base": base, "quantity": qty,
            "entryPrice": entry}


def _stop(base, trigger, status="pending"):
    return {"base": base, "type": "stop_market", "status": status,
            "triggerPrice": trigger}


def test_no_positions_means_no_risk():
    assert risk_client(FakeSDK(positions=[])).open_risk_usd() == 0.0


def test_stop_in_pending_status_is_counted(monkeypatch):
    """Regression: conditional orders rest in 'pending', not 'open'. Reading
    only 'open' made every stop look missing and risk look like full notional."""
    sdk = FakeSDK(positions=[_pos("SUI", 2788.6, 0.7157)],
                  orders=[_stop("SUI", 0.6889, status="pending")])
    risk = risk_client(sdk).open_risk_usd()
    assert 70 < risk < 80, f"expected ~$75 of stop risk, got ${risk:.2f}"


def test_position_without_stop_counts_full_notional():
    sdk = FakeSDK(positions=[_pos("SUI", 2788.6, 0.7157)], orders=[])
    risk = risk_client(sdk).open_risk_usd()
    assert risk > 1900, f"a stopless position is unbounded, got ${risk:.2f}"


def test_orders_api_failure_fails_safe():
    """If stops cannot be read we must assume the worst, not assume zero."""
    sdk = FakeSDK(positions=[_pos("SUI", 2788.6, 0.7157)], orders_raise=True)
    c = risk_client(sdk)
    assert c.open_risk_usd() > 1900
    ok, reason = c.can_trade()
    assert not ok and "Aggregate stop risk" in reason


def test_default_budget_admits_validated_config_and_no_more():
    """The budget must fit the walk-forward-validated dip config (3 concurrent
    x 2% of equity = 6%) and refuse anything larger."""
    cfg = ProprConfig()
    from core.exchange.propr.config import ChallengeRules, ChallengeType, AccountSize
    rules = ChallengeRules(ChallengeType.CLASSIC_1STEP, AccountSize.K5)
    budget = rules.max_drawdown_usd * cfg.max_dd_risk_fraction
    validated = 5000 * 0.20 * 0.10 * 3          # 3 positions, 20% size, 10% stop
    assert budget >= validated, "budget must admit the validated config"
    assert budget <= rules.max_drawdown_usd, "budget must never exceed the DD wall"


def test_book_at_the_drawdown_wall_refuses_more():
    """The budget is the $300 DD wall. A book already risking ~$300 -- the
    validated config fully loaded -- must refuse a fourth entry."""
    sdk = FakeSDK(
        positions=[_pos("A", 1000, 1.0), _pos("B", 1000, 1.0), _pos("C", 1000, 1.0)],
        orders=[_stop("A", 0.90), _stop("B", 0.90), _stop("C", 0.90)],
    )
    c = risk_client(sdk)                       # default fraction 1.0 -> $300
    risk = c.open_risk_usd()
    assert 295 < risk < 305, f"expected ~$300 of stop risk, got ${risk:.2f}"

    ok, reason = c.can_trade(new_risk_usd=100.0)
    assert not ok, "a fully loaded book must refuse a further entry"
    assert "Aggregate stop risk" in reason


def test_two_position_book_still_admits_a_third():
    """Two of the validated 2%-risk positions ($200) leave room for one more,
    because the wall is $300 -- this is the loosening that was chosen
    deliberately over the old $150 daily-cap reference."""
    sdk = FakeSDK(positions=[_pos("A", 1000, 1.0), _pos("B", 1000, 1.0)],
                  orders=[_stop("A", 0.90), _stop("B", 0.90)])
    c = risk_client(sdk)
    assert 195 < c.open_risk_usd() < 205
    ok, _ = c.can_trade(new_risk_usd=100.0)
    assert ok, "$200 + $100 = the $300 wall exactly; must be admitted"


def test_new_trade_risk_is_priced_in():
    """The gate must see the book AFTER the proposed fill, not before."""
    sdk = FakeSDK(positions=[_pos("A", 1000, 1.0)], orders=[_stop("A", 0.90)])
    c = risk_client(sdk, fraction=0.50)        # 0.50 * $300 = $150 budget

    ok, _ = c.can_trade()
    assert ok, "$100 of open risk is inside the $150 budget on its own"

    ok, reason = c.can_trade(new_risk_usd=100.0)
    assert not ok, "$100 + $100 exceeds $150 -- must be refused"
    assert "Aggregate stop risk" in reason


def test_open_long_refuses_when_aggregate_would_breach(monkeypatch):
    """End to end: every entry routes through open_long, so the cap cannot be
    bypassed by calling it directly."""
    monkeypatch.setattr("core.exchange.propr.client.time.sleep", lambda s: None)
    sdk = FakeSDK(positions=[_pos("A", 1000, 1.0), _pos("B", 1000, 1.0),
                             _pos("C", 1000, 1.0)],
                  orders=[_stop("A", 0.90), _stop("B", 0.90), _stop("C", 0.90)])
    c = risk_client(sdk)

    orders = c.open_long("D", 1000, stop_price=0.90, entry_price=1.0)

    assert orders == [], "entry breaching the aggregate cap must be refused"
    assert sdk.buys == [], "and must never reach the exchange"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

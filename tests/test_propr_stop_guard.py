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
                 close_raises=False):
        self._position_after_entry = position_after_entry
        self.sl_ok = sl_ok
        self.tp_ok = tp_ok
        self._close_raises = close_raises
        self.closed = []
        self.buys = []

    def market_buy(self, asset, qty):
        self.buys.append((asset, qty))
        return [{"orderId": "entry-1", "status": "filled"}]

    def get_open_positions(self, base=None):
        if not self._position_after_entry:
            return []
        return [{"positionId": "pos-1", "base": base or "BTC"}]

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

    monkeypatch.setattr(client, "can_trade", lambda: (True, "OK"))

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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

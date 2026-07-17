import json
import time
from pathlib import Path
from typing import Optional
from datetime import datetime, timezone

import config
from config import DATA_DIR, INITIAL_BALANCE, TRADE_FEE_PCT
from core.portfolio import Portfolio, Position, save_portfolio, load_portfolio

# Adverse fill slippage per side, percent. Audit 2026-07: every paper order
# filled at exactly the caller's price — zero spread, zero slippage — which
# booked entries and exits a live market never allows. A market BUY pays the
# ask and a SELL hits the bid (the Trader already passes that quote in as
# `price`); this models the extra slippage beyond the quote, always adverse
# to the side. getattr fallback: config.py predates this knob.
SLIPPAGE_PCT = float(getattr(config, "SLIPPAGE_PCT", 0.05))


def _slipped_price(side, price):
    """A market order never fills at a better price than quoted."""
    slip = SLIPPAGE_PCT / 100.0
    return price * (1 + slip) if side.upper() == "BUY" else price * (1 - slip)


class PaperBroker:
    def __init__(self, initial_balance: float = INITIAL_BALANCE):
        self.portfolio = load_portfolio()
        if self.portfolio.initial_balance == 0:
            self.portfolio.initial_balance = initial_balance
            self.portfolio.cash = initial_balance
        self.orders_dir = DATA_DIR / "orders"
        self.orders_dir.mkdir(parents=True, exist_ok=True)
        self.pending_orders = []

    def place_order(self, symbol: str, side: str, quantity: float,
                    price: float, order_type: str = "market",
                    sl: float = 0, tp: float = 0) -> dict:
        # Fill at the quote (`price`, already the ask for BUY / bid for SELL)
        # plus adverse slippage — never at the bare quote. requested_price is
        # kept so fills stay auditable against the signal price.
        fill_price = round(_slipped_price(side, price), 8)
        order = {
            "symbol": symbol,
            "side": side.upper(),
            "quantity": quantity,
            "price": fill_price,
            "requested_price": price,
            "slippage_pct": SLIPPAGE_PCT,
            "type": order_type,
            "status": "filled",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "id": str(int(time.time() * 1000)),
        }
        if sl:
            order["stop_loss"] = sl
        if tp:
            order["take_profit"] = tp
        fee_ratio = TRADE_FEE_PCT / 100.0
        cost = quantity * fill_price
        if side.upper() == "BUY":
            if symbol in self.portfolio.positions and self.portfolio.positions[symbol].quantity < 0:
                pos = self.portfolio.positions[symbol]
                cover_qty = min(quantity, abs(pos.quantity))
                cost = cover_qty * fill_price
                fee = cost * fee_ratio
                self.portfolio.cash -= cost + fee
                realized_pnl = (pos.entry_price - fill_price) * cover_qty - fee
                pos.quantity += cover_qty
                if pos.quantity >= 0:
                    del self.portfolio.positions[symbol]
                order["action"] = "COVER"
                order["fee"] = round(fee, 4)
                order["realized_pnl"] = round(realized_pnl, 2)
                self.portfolio.trades.append(order)
            elif cost * (1 + fee_ratio) > self.portfolio.cash:
                order["status"] = "rejected"
                order["reason"] = "insufficient funds"
            else:
                fee = cost * fee_ratio
                self.portfolio.cash -= cost + fee
                order["fee"] = round(fee, 4)
                if symbol in self.portfolio.positions and self.portfolio.positions[symbol].quantity > 0:
                    pos = self.portfolio.positions[symbol]
                    avg_cost = ((pos.entry_price * pos.quantity) + cost) / (pos.quantity + quantity)
                    pos.quantity += quantity
                    pos.entry_price = avg_cost
                else:
                    self.portfolio.positions[symbol] = Position(
                        symbol=symbol, entry_price=fill_price, quantity=quantity,
                        current_price=fill_price
                    )
                self.portfolio.trades.append(order)
        elif side.upper() == "SELL":
            if symbol in self.portfolio.positions and self.portfolio.positions[symbol].quantity > 0:
                pos = self.portfolio.positions[symbol]
                sell_qty = min(quantity, pos.quantity)
                cost = sell_qty * fill_price
                fee = cost * fee_ratio
                self.portfolio.cash += cost - fee
                realized_pnl = (fill_price - pos.entry_price) * sell_qty - fee
                pos.quantity -= sell_qty
                if pos.quantity <= 0:
                    del self.portfolio.positions[symbol]
                order["action"] = "SELL"
                order["fee"] = round(fee, 4)
                order["realized_pnl"] = round(realized_pnl, 2)
                self.portfolio.trades.append(order)
            else:
                cost = quantity * fill_price
                fee = cost * fee_ratio
                self.portfolio.cash += cost - fee
                self.portfolio.positions[symbol] = Position(
                    symbol=symbol, entry_price=fill_price, quantity=-quantity,
                    current_price=fill_price
                )
                order["action"] = "SHORT"
                order["fee"] = round(fee, 4)
                self.portfolio.trades.append(order)
        order["portfolio_cash"] = round(self.portfolio.cash, 2)
        self._save_order(order)
        save_portfolio(self.portfolio)
        return order

    def _save_order(self, order):
        f = self.orders_dir / f"{order['id']}.json"
        f.write_text(json.dumps(order, indent=2))

    def get_status(self) -> dict:
        return {
            "cash": round(self.portfolio.cash, 2),
            "positions_value": round(self.portfolio.positions_value, 2),
            "equity": round(self.portfolio.equity, 2),
            "total_pnl": round(self.portfolio.total_pnl, 2),
            "total_pnl_pct": round(self.portfolio.total_pnl_pct, 2),
            "exposure_pct": round(self.portfolio.exposure_pct, 2),
            "positions_count": len(self.portfolio.positions),
        }

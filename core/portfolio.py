from dataclasses import dataclass, field
from typing import Dict
from pathlib import Path
import json

from config import DATA_DIR


@dataclass
class Position:
    symbol: str
    entry_price: float
    quantity: float
    current_price: float = 0.0
    pnl: float = 0.0
    pnl_pct: float = 0.0


@dataclass
class Portfolio:
    cash: float = 0.0
    positions: Dict[str, Position] = field(default_factory=dict)
    total_value: float = 0.0
    trades: list = field(default_factory=list)
    initial_balance: float = 0.0

    def update_price(self, symbol: str, price: float):
        if symbol in self.positions:
            pos = self.positions[symbol]
            pos.current_price = price
            if pos.quantity >= 0:
                pos.pnl = (price - pos.entry_price) * pos.quantity
                pos.pnl_pct = (price - pos.entry_price) / pos.entry_price * 100
            else:
                pos.pnl = (pos.entry_price - price) * abs(pos.quantity)
                pos.pnl_pct = (pos.entry_price - price) / pos.entry_price * 100

    def update_prices(self, prices: Dict[str, float]):
        for sym, price in prices.items():
            self.update_price(sym, price)

    @property
    def positions_value(self):
        return sum(p.current_price * p.quantity for p in self.positions.values())

    @property
    def equity(self):
        return self.cash + self.positions_value

    @property
    def total_pnl(self):
        return self.equity - self.initial_balance

    @property
    def total_pnl_pct(self):
        if self.initial_balance == 0:
            return 0.0
        return (self.total_pnl / self.initial_balance) * 100

    @property
    def exposure_pct(self):
        if self.equity == 0:
            return 0.0
        return (self.positions_value / self.equity) * 100


def apply_fill(p: Portfolio, symbol: str, side: str, quantity: float, price: float):
    """Apply an externally executed (live broker) fill to the local ledger.

    Spot semantics only: BUY debits cash and adds to the position, SELL
    credits cash for at most the locally held quantity. Without this mirror
    the ledger's cash never moves when the live broker fills an order.
    """
    side = side.upper()
    if side == "BUY":
        pos = p.positions.get(symbol)
        if pos and pos.quantity < 0:
            # Covering a legacy short: never flip it into a long
            cover_qty = min(quantity, -pos.quantity)
            p.cash -= cover_qty * price
            pos.quantity += cover_qty
            if pos.quantity >= -1e-12:
                del p.positions[symbol]
            return
        cost = quantity * price
        p.cash -= cost
        if pos and pos.quantity > 0:
            total_cost = pos.entry_price * pos.quantity + cost
            pos.quantity += quantity
            pos.entry_price = total_cost / pos.quantity
            pos.current_price = price
        else:
            p.positions[symbol] = Position(
                symbol=symbol, entry_price=price, quantity=quantity,
                current_price=price
            )
    elif side == "SELL":
        pos = p.positions.get(symbol)
        if not pos or pos.quantity <= 0:
            return
        sell_qty = min(quantity, pos.quantity)
        p.cash += sell_qty * price
        pos.quantity -= sell_qty
        if pos.quantity <= 1e-12:
            del p.positions[symbol]


def save_portfolio(p: Portfolio):
    import tempfile
    data = {
        "cash": p.cash,
        "initial_balance": p.initial_balance,
        "total_value": p.equity,
        "total_pnl": p.total_pnl,
        "total_pnl_pct": p.total_pnl_pct,
        "exposure_pct": p.exposure_pct,
        "positions": {
            sym: {
                "entry_price": pos.entry_price,
                "quantity": pos.quantity,
                "current_price": pos.current_price,
                "pnl": pos.pnl,
                "pnl_pct": pos.pnl_pct,
            }
            for sym, pos in p.positions.items()
        },
        "trades": p.trades[-50:],
    }
    (DATA_DIR / "reports").mkdir(parents=True, exist_ok=True)
    dst = DATA_DIR / "reports" / "portfolio.json"
    tmp = dst.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(dst)


def load_portfolio() -> Portfolio:
    f = DATA_DIR / "reports" / "portfolio.json"
    if not f.exists():
        return Portfolio()
    try:
        data = json.loads(f.read_text())
        p = Portfolio(
            cash=data.get("cash", 0),
            initial_balance=data.get("initial_balance", 0),
            trades=data.get("trades", []),
        )
        for sym, pos_data in data.get("positions", {}).items():
            p.positions[sym] = Position(
                symbol=sym,
                entry_price=pos_data.get("entry_price", 0),
                quantity=pos_data.get("quantity", 0),
                current_price=pos_data.get("current_price", 0),
                pnl=pos_data.get("pnl", 0),
                pnl_pct=pos_data.get("pnl_pct", 0),
            )
        p.total_value = data.get("total_value", 0)
        return p
    except Exception:
        return Portfolio()

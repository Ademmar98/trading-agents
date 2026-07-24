"""
Funding Rate Capture Strategy
==============================
Delta-neutral: Long spot + Short perpetual = Collect funding payments.
Market-neutral yield from structural funding rate positive bias in crypto.

Entry: When annualized funding > 10%, deploy 90% of capital
Exit: When annualized funding < 2% or position held > 7 days
Risk: Basis risk, exchange risk, liquidation on perp leg
"""

import logging
import time
import numpy as np
from datetime import datetime, timezone
from typing import Optional, Dict, List

import requests

_log = logging.getLogger("funding_rate")

BINANCE_FUTURES_BASE = "https://fapi.binance.com"
BINANCE_SPOT_BASE = "https://api.binance.com"


def _to_binance_symbol(symbol: str) -> str:
    s = symbol.replace("/", "").upper()
    if s.endswith("USD") and not s.endswith("USDT"):
        return s + "T"
    return s


class FundingRateCapture:
    """
    Delta-neutral funding rate arbitrage.

    Mechanics:
      1. Buy BTC spot (long exposure)
      2. Short BTC-USDT perpetual (offsetting exposure)
      3. Collect funding every 8 hours (0:00, 8:00, 16:00 UTC)

    Net directional exposure ≈ 0. Revenue = funding payments.
    """

    def __init__(self, capital: float = 10000, deploy_pct: float = 0.90):
        self.capital = capital
        self.deploy_pct = deploy_pct
        self.position = {
            "spot_qty": 0.0,
            "perp_qty": 0.0,
            "entry_price": 0.0,
            "deployed": 0.0,
            "entry_time": None,
            "total_funding": 0.0,
            "total_costs": 0.0,
        }

    # ── Data ──────────────────────────────────────────────────────────

    def get_funding_rate(self, symbol: str = "BTCUSDT") -> float:
        """Current funding rate (8h interval)."""
        try:
            r = requests.get(
                f"{BINANCE_FUTURES_BASE}/fapi/v1/premiumIndex",
                params={"symbol": symbol},
                timeout=10,
            )
            data = r.json()
            return float(data.get("lastFundingRate", 0))
        except Exception as e:
            _log.error("funding rate fetch failed: %s", e)
            return 0.0

    def get_funding_history(self, symbol: str = "BTCUSDT", limit: int = 30) -> List[Dict]:
        try:
            r = requests.get(
                f"{BINANCE_FUTURES_BASE}/fapi/v1/fundingRate",
                params={"symbol": symbol, "limit": limit},
                timeout=10,
            )
            return r.json()
        except Exception:
            return []

    def get_spot_price(self, symbol: str = "BTCUSDT") -> float:
        try:
            r = requests.get(
                f"{BINANCE_SPOT_BASE}/api/v3/ticker/price",
                params={"symbol": symbol},
                timeout=10,
            )
            return float(r.json()["price"])
        except Exception:
            return 0.0

    def annualized_yield(self, rate: float) -> float:
        """Annualize an 8h funding rate (3 payments/day * 365 days)."""
        return rate * 3 * 365 * 100  # percent

    # ── Execution ─────────────────────────────────────────────────────

    def calculate_qty(self, price: float) -> float:
        deployable = self.capital * self.deploy_pct
        qty = deployable / price
        return round(qty, 3)  # Binance BTC lot size

    def open_position(self, symbol: str = "BTCUSDT") -> Dict:
        """
        Open delta-neutral position. Returns execution summary.

        NOTE: Perp leg requires Binance Futures API keys.
        This method handles the spot leg and logs the perp intent.
        """
        price = self.get_spot_price(symbol)
        if price <= 0:
            return {"success": False, "error": "price_unavailable"}

        qty = self.calculate_qty(price)
        if qty < 0.001:
            return {"success": False, "error": "capital_too_small"}

        cost = (qty * price) * 0.001 * 2  # 0.1% x 2 legs

        self.position.update({
            "spot_qty": qty,
            "perp_qty": -qty,
            "entry_price": price,
            "deployed": qty * price,
            "entry_time": datetime.now(timezone.utc).isoformat(),
            "total_costs": cost,
        })

        _log.info(
            "Opened hedge: spot +%.3f BTC, perp -%.3f BTC @ $%.2f  (cost $%.2f)",
            qty, qty, price, cost,
        )

        return {
            "success": True,
            "qty": qty,
            "price": price,
            "deployed": qty * price,
            "cost": cost,
        }

    def collect_funding(self) -> float:
        """Collect current funding payment."""
        if self.position["spot_qty"] == 0:
            return 0.0

        rate = self.get_funding_rate()
        position_value = self.position["spot_qty"] * self.position["entry_price"]
        income = position_value * rate

        self.position["total_funding"] += income
        _log.info("Funding collected: $%.4f (rate %.4f%%)", income, rate * 100)

        return income

    def close_position(self) -> Dict:
        """Close the delta-neutral position."""
        if self.position["spot_qty"] == 0:
            return {"success": False, "error": "no_position"}

        price = self.get_spot_price()
        qty = self.position["spot_qty"]

        exit_cost = (qty * price) * 0.001 * 2
        self.position["total_costs"] += exit_cost

        total_pnl = self.position["total_funding"] - self.position["total_costs"]

        result = {
            "success": True,
            "qty": qty,
            "entry_price": self.position["entry_price"],
            "exit_price": price,
            "holding_pnl": (price - self.position["entry_price"]) * qty,
            "funding_earned": self.position["total_funding"],
            "total_costs": self.position["total_costs"],
            "net_pnl": total_pnl,
        }

        _log.info(
            "Closed hedge: PnL $%.2f (funding $%.2f - costs $%.2f)",
            total_pnl, self.position["total_funding"], self.position["total_costs"],
        )

        # Reset
        self.position = {
            "spot_qty": 0.0, "perp_qty": 0.0, "entry_price": 0.0,
            "deployed": 0.0, "entry_time": None,
            "total_funding": 0.0, "total_costs": 0.0,
        }

        return result

    # ── Status ────────────────────────────────────────────────────────

    def status(self) -> Dict:
        rate = self.get_funding_rate()
        ann = self.annualized_yield(rate)

        return {
            "strategy": "Funding Rate Capture",
            "active": self.position["spot_qty"] > 0,
            "position_qty": self.position["spot_qty"],
            "entry_price": self.position["entry_price"],
            "deployed": self.position["deployed"],
            "current_funding_rate": round(rate, 6),
            "annualized_yield_pct": round(ann, 2),
            "total_funding_collected": round(self.position["total_funding"], 4),
            "total_costs": round(self.position["total_costs"], 4),
            "net_pnl": round(self.position["total_funding"] - self.position["total_costs"], 4),
        }


class FundingRateScanner:
    """Scan top crypto assets for the best funding rate opportunities."""

    TOP_ASSETS = [
        "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
        "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "LINKUSDT", "DOTUSDT",
    ]

    def scan(self) -> List[Dict]:
        results = []
        for asset in self.TOP_ASSETS:
            try:
                r = requests.get(
                    f"{BINANCE_FUTURES_BASE}/fapi/v1/premiumIndex",
                    params={"symbol": asset},
                    timeout=10,
                )
                data = r.json()
                rate = float(data.get("lastFundingRate", 0))
                ann = rate * 3 * 365 * 100

                results.append({
                    "asset": asset,
                    "rate_8h": round(rate, 6),
                    "annualized_pct": round(ann, 2),
                    "signal": "STRONG" if ann > 20 else "MODERATE" if ann > 10 else "WEAK",
                })
            except Exception:
                pass

        results.sort(key=lambda x: x["annualized_pct"], reverse=True)
        return results

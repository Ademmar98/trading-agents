"""
Module 2: Structural Spot Basket Rebalancing Engine
====================================================
Harvests micro-volatility and variance drag during sideways/ranging markets
via continuous threshold rebalancing of a diversified halal spot basket.

Target: 3% to 7% monthly base yield from rebalancing alpha.

Universe: Top 10 low-correlation, high-volume halal spot assets.

Execution: Maker-only limit orders at Bid 1 / Ask 1 depth.
"""

import logging
import time
import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Dict, List, Tuple
from pathlib import Path

import requests

_log = logging.getLogger("basket_rebalance")

BINANCE_SPOT_BASE = "https://api.binance.com"


@dataclass
class BasketConfig:
    """Configuration for the basket rebalancing engine."""
    # Rebalancing threshold: trigger when weight deviates by > this many std devs
    rebalance_threshold_sigma: float = 1.5
    # Minimum holding period between rebalances (hours)
    min_hold_hours: int = 4
    # Maximum single rebalance trade size (% of total basket value)
    max_trade_size_pct: float = 5.0
    # Cash reserve to maintain (% of total equity)
    cash_reserve_pct: float = 20.0
    # Maximum number of assets in the basket
    basket_size: int = 10
    # Minimum 24h volume to be eligible ($M)
    min_volume_m: float = 5.0
    # Correlation lookback period (days)
    correlation_lookback: int = 30
    # Maximum pairwise correlation allowed between basket members
    max_pairwise_corr: float = 0.65
    # Rebalance cooldown per asset (hours)
    asset_cooldown_hours: int = 6
    # Taker fee for cost modeling
    taker_fee: float = 0.0007
    # Maker fee (what we actually pay with limit orders)
    maker_fee: float = 0.0002
    # Capital allocation
    capital: float = 10_000


@dataclass
class BasketAsset:
    """Single asset in the rebalancing basket."""
    symbol: str
    target_weight: float
    current_weight: float
    last_rebalance_time: float
    volume_24h: float
    market_cap: float
    return_30d: float
    volatility_30d: float


class SpotBasketRebalancer:
    """
    Structural Spot Basket Rebalancing Engine.

    Strategy:
      1. Select top N assets by volume that are low-correlation
      2. Assign equal-weight targets (inverse-vol weighted optional)
      3. When any asset deviates > threshold from target, rebalance
      4. Use maker-only limit orders to minimize execution costs
      5. Maintain cash reserve for drawdown protection

    Expected Edge:
      - Harvests mean-reversion in relative asset weights
      - Exploits variance drag (geometric < arithmetic returns)
      - Low correlation reduces portfolio volatility
      - Maker-only execution saves ~5bps per trade vs taker
    """

    def __init__(self, config: Optional[BasketConfig] = None):
        self.config = config or BasketConfig()
        self.basket: Dict[str, BasketAsset] = {}
        self.price_history: Dict[str, List[float]] = {}
        self.last_scan_time: float = 0
        self._rebalance_log: List[Dict] = []

    def scan_universe(self) -> List[Dict]:
        """Scan Binance for eligible high-volume spot pairs."""
        try:
            r = requests.get(f"{BINANCE_SPOT_BASE}/api/v3/ticker/24hr", timeout=30)
            tickers = r.json()
        except Exception as e:
            _log.error("Failed to fetch tickers: %s", e)
            return []

        skip_bases = {"USDC", "USDT", "BUSD", "TUSD", "FDUSD", "DAI", "USDP", "USDN"}
        skip_tag = ["UP", "DOWN", "BULL", "BEAR", "3L", "3S", "5L", "5S"]

        pairs = []
        for t in tickers:
            s = t["symbol"]
            if not s.endswith("USDT"):
                continue
            base = s[:-4]
            if base in skip_bases or any(x in base for x in skip_tag):
                continue
            vol = float(t.get("quoteVolume", 0))
            if vol < self.config.min_volume_m * 1_000_000:
                continue
            pairs.append({
                "symbol": base,
                "volume_24h": vol,
                "price": float(t.get("lastPrice", 0)),
                "change_pct": float(t.get("priceChangePercent", 0)),
            })

        pairs.sort(key=lambda x: x["volume_24h"], reverse=True)
        return pairs

    def compute_correlations(self, symbols: List[str], prices: Dict[str, List[float]]) -> Dict[str, float]:
        """
        Compute pairwise correlations and return average correlation per symbol.
        Lower average correlation = better candidate for the basket.
        """
        if len(symbols) < 2:
            return {s: 0.0 for s in symbols}

        # Build price matrix
        min_len = min(len(prices.get(s, [])) for s in symbols)
        if min_len < 10:
            return {s: 0.0 for s in symbols}

        returns = {}
        for s in symbols:
            p = prices[s][-min_len:]
            ret = np.diff(np.log(np.maximum(p, 1e-10)))
            returns[s] = ret

        # Compute correlation matrix
        corr_matrix = {}
        for s in symbols:
            corrs = []
            for s2 in symbols:
                if s == s2:
                    continue
                if len(returns[s]) == len(returns[s2]) and len(returns[s]) > 5:
                    c = np.corrcoef(returns[s], returns[s2])[0, 1]
                    if not np.isnan(c):
                        corrs.append(abs(c))
            corr_matrix[s] = np.mean(corrs) if corrs else 0.0

        return corr_matrix

    def select_basket(self, universe: List[Dict], prices: Dict[str, List[float]]) -> List[str]:
        """
        Select basket members based on volume ranking and low correlation.
        Returns list of symbols sorted by composite score.
        """
        if not universe:
            return []

        symbols = [p["symbol"] for p in universe[:self.config.basket_size * 3]]

        # Compute correlations
        avg_corr = self.compute_correlations(symbols, prices)

        # Score: high volume (normalized) + low correlation
        max_vol = max((p["volume_24h"] for p in universe[:len(symbols)]), default=1)

        scored = []
        for p in universe[:len(symbols)]:
            sym = p["symbol"]
            vol_score = p["volume_24h"] / max_vol  # 0-1
            corr_score = 1.0 - avg_corr.get(sym, 0.5)  # lower corr = higher score
            # Composite: 60% volume, 40% low correlation
            composite = 0.6 * vol_score + 0.4 * corr_score
            scored.append((sym, composite, p["volume_24h"]))

        scored.sort(key=lambda x: x[1], reverse=True)
        return [s[0] for s in scored[:self.config.basket_size]]

    def compute_target_weights(self, symbols: List[str], prices: Dict[str, List[float]]) -> Dict[str, float]:
        """
        Compute inverse-volatility weighted targets.
        Assets with lower volatility get higher weights (risk-parity lite).
        """
        if not symbols:
            return {}

        # Compute 30-day volatility for each
        vols = {}
        for s in symbols:
            p = prices.get(s, [])
            if len(p) < 20:
                vols[s] = 0.05  # default 5% daily vol
            else:
                returns = np.diff(np.log(np.maximum(p[-30:], 1e-10)))
                vols[s] = max(np.std(returns), 0.001)

        # Inverse vol weights
        inv_vols = {s: 1.0 / v for s, v in vols.items()}
        total_inv = sum(inv_vols.values())

        weights = {s: iv / total_inv for s, iv in inv_vols.items()}

        # Normalize to sum to (100 - cash_reserve)%
        allocatable = (100 - self.config.cash_reserve_pct) / 100
        weights = {s: w * allocatable for s, w in weights.items()}

        return weights

    def check_rebalance_signals(
        self,
        current_weights: Dict[str, float],
        target_weights: Dict[str, float],
        last_rebalance: Dict[str, float],
    ) -> List[Dict]:
        """
        Check which assets need rebalancing.
        Returns list of rebalance orders (BUY/SELL with sizes).
        """
        now = time.time()
        signals = []

        for sym in set(list(current_weights.keys()) + list(target_weights.keys())):
            curr = current_weights.get(sym, 0)
            target = target_weights.get(sym, 0)
            deviation = abs(curr - target)

            # Check cooldown
            last_rb = last_rebalance.get(sym, 0)
            if (now - last_rb) / 3600 < self.config.asset_cooldown_hours:
                continue

            # Check if deviation exceeds threshold (in sigma terms)
            if deviation < self.config.rebalance_threshold_sigma * 0.01:
                continue

            # Determine direction
            if curr < target:
                # Need to buy more
                trade_size_pct = min(
                    (target - curr) * 100,
                    self.config.max_trade_size_pct
                )
                signals.append({
                    "symbol": sym,
                    "action": "BUY",
                    "current_weight": round(curr * 100, 2),
                    "target_weight": round(target * 100, 2),
                    "deviation_pct": round(deviation * 100, 2),
                    "trade_size_pct": round(trade_size_pct, 2),
                })
            elif curr > target:
                # Need to sell
                trade_size_pct = min(
                    (curr - target) * 100,
                    self.config.max_trade_size_pct
                )
                signals.append({
                    "symbol": sym,
                    "action": "SELL",
                    "current_weight": round(curr * 100, 2),
                    "target_weight": round(target * 100, 2),
                    "deviation_pct": round(deviation * 100, 2),
                    "trade_size_pct": round(trade_size_pct, 2),
                })

        # Sort by deviation (rebalance largest deviations first)
        signals.sort(key=lambda x: x["deviation_pct"], reverse=True)
        return signals

    def estimate_rebalance_yield(
        self,
        prices: Dict[str, List[float]],
        n_days: int = 30,
    ) -> Dict:
        """
        Estimate expected monthly rebalancing yield from historical data.
        Uses variance drag decomposition.
        """
        results = {}
        for sym, p in prices.items():
            if len(p) < n_days + 10:
                continue

            returns = np.diff(np.log(np.maximum(p[-n_days-1:], 1e-10)))
            arithmetic_mean = np.mean(returns)
            variance = np.var(returns)

            # Variance drag = arithmetic - geometric return
            geometric_mean = arithmetic_mean - 0.5 * variance

            # Rebalancing alpha = variance captured by rebalancing
            # Theoretical max = 0.5 * variance (if perfectly rebalanced)
            rebalance_alpha = 0.5 * variance * 0.3  # assume 30% capture rate

            results[sym] = {
                "arithmetic_return": round(arithmetic_mean * n_days * 100, 2),
                "geometric_return": round(geometric_mean * n_days * 100, 2),
                "variance_drag": round(variance * n_days * 100, 2),
                "estimated_rebalance_yield": round(rebalance_alpha * n_days * 100, 2),
                "daily_vol": round(np.std(returns) * 100, 2),
            }

        return results

    def generate_rebalance_plan(
        self,
        portfolio_weights: Dict[str, float],
        target_weights: Dict[str, float],
        total_equity: float,
    ) -> Dict:
        """
        Generate a complete rebalance execution plan.
        Returns orders sized for maker-only execution.
        """
        now = time.time()
        orders = []

        for sym in set(list(portfolio_weights.keys()) + list(target_weights.keys())):
            curr = portfolio_weights.get(sym, 0)
            target = target_weights.get(sym, 0)
            deviation = target - curr

            if abs(deviation) < 0.005:  # Less than 0.5% deviation
                continue

            trade_value = abs(deviation) * total_equity
            trade_value = min(trade_value, total_equity * self.config.max_trade_size_pct / 100)

            side = "BUY" if deviation > 0 else "SELL"
            est_fee = trade_value * self.config.maker_fee

            orders.append({
                "symbol": sym,
                "side": side,
                "trade_value_usd": round(trade_value, 2),
                "estimated_fee": round(est_fee, 4),
                "current_weight_pct": round(curr * 100, 2),
                "target_weight_pct": round(target * 100, 2),
                "deviation_pct": round(abs(deviation) * 100, 2),
            })

        # Sort by trade size (largest first for efficient execution)
        orders.sort(key=lambda x: x["trade_value_usd"], reverse=True)

        total_traded = sum(o["trade_value_usd"] for o in orders)
        total_fees = sum(o["estimated_fee"] for o in orders)

        return {
            "orders": orders,
            "total_traded_usd": round(total_traded, 2),
            "total_estimated_fees": round(total_fees, 4),
            "fee_drag_pct": round(total_fees / total_equity * 100, 4) if total_equity > 0 else 0,
            "num_orders": len(orders),
            "timestamp": now,
        }

    def backtest_historical(
        self,
        prices: Dict[str, List[float]],
        n_days: int = 60,
    ) -> Dict:
        """
        Backtest the rebalancing strategy on historical data.
        Returns performance metrics.
        """
        symbols = list(prices.keys())
        if len(symbols) < 3:
            return {"error": "Need at least 3 symbols"}

        # Use first 30 days to establish targets, rebalance for remaining days
        warmup = min(30, n_days // 3)

        # Compute equal weights (simplified)
        n_assets = len(symbols)
        target_weight = (100 - self.config.cash_reserve_pct) / 100 / n_assets

        portfolio_value = self.config.capital
        holdings = {s: 0.0 for s in symbols}
        cash = self.config.capital
        rebalance_count = 0
        total_fees = 0

        equity_curve = [portfolio_value]

        for t in range(1, min(n_days * 6, len(prices[symbols[0]]))):  # Assume 4h bars
            # Update portfolio value
            portfolio_value = cash
            for s in symbols:
                if t < len(prices[s]):
                    price = prices[s][t]
                    portfolio_value += holdings[s] * price

            # Rebalance every 24 bars (4 days) after warmup
            if t > warmup * 6 and t % 24 == 0:
                for s in symbols:
                    if t >= len(prices[s]):
                        continue
                    price = prices[s][t]
                    current_value = holdings[s] * price
                    current_weight = current_value / portfolio_value if portfolio_value > 0 else 0
                    target_value = portfolio_value * target_weight

                    trade_value = target_value - current_value
                    if abs(trade_value) < 10:  # Skip tiny trades
                        continue

                    fee = abs(trade_value) * self.config.maker_fee
                    total_fees += fee

                    if trade_value > 0 and cash >= trade_value + fee:
                        # Buy
                        qty = (trade_value - fee) / price
                        holdings[s] += qty
                        cash -= trade_value
                    elif trade_value < 0:
                        # Sell
                        qty = abs(trade_value) / price
                        holdings[s] = max(0, holdings[s] - qty)
                        cash += abs(trade_value) - fee

                    rebalance_count += 1

            equity_curve.append(portfolio_value)

        # Compute metrics
        equity = np.array(equity_curve)
        returns = np.diff(equity) / equity[:-1]
        returns = returns[~np.isnan(returns)]

        total_return = (portfolio_value - self.config.capital) / self.config.capital * 100
        max_dd = np.min((equity - np.maximum.accumulate(equity)) / np.maximum.accumulate(equity)) * 100
        sharpe = np.mean(returns) / np.std(returns) * np.sqrt(6 * 365) if np.std(returns) > 0 else 0

        return {
            "total_return_pct": round(total_return, 2),
            "max_drawdown_pct": round(max_dd, 2),
            "sharpe_ratio": round(sharpe, 2),
            "total_rebalances": rebalance_count,
            "total_fees": round(total_fees, 2),
            "final_portfolio": round(portfolio_value, 2),
            "fee_drag_pct": round(total_fees / self.config.capital * 100, 2),
            "bars": len(equity_curve),
        }

    def status(self) -> Dict:
        """Return current engine status."""
        return {
            "engine": "SpotBasketRebalancer",
            "basket_size": len(self.basket),
            "config": {
                "threshold_sigma": self.config.rebalance_threshold_sigma,
                "cash_reserve_pct": self.config.cash_reserve_pct,
                "max_trade_size_pct": self.config.max_trade_size_pct,
                "maker_fee": self.config.maker_fee,
            },
            "recent_rebalances": len(self._rebalance_log),
        }

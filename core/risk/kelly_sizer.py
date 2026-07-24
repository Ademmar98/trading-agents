"""
Fractional Kelly Criterion Position Sizer
==========================================
Dynamic position sizing using fractional Kelly (0.25x to 0.33x Kelly)
to optimize growth rate while limiting drawdown risk.

Formula: f* = (p * b - q) / b
  Where p = win rate, q = 1-p, b = win/loss ratio

Fractional Kelly reduces volatility by sizing at 25-33% of full Kelly.

Position Sizing Cap: Maximum 2-3% equity risk per trade.
"""

import logging
import math
from dataclasses import dataclass
from typing import Optional, Dict, List, Tuple

_log = logging.getLogger("kelly_sizer")


@dataclass
class KellyConfig:
    """Configuration for Kelly position sizer."""
    # Fractional Kelly multiplier (0.25 = quarter Kelly, 0.33 = third Kelly)
    kelly_fraction: float = 0.25

    # Maximum risk per trade as % of equity
    max_risk_per_trade_pct: float = 2.0

    # Maximum portfolio exposure as % of equity
    max_portfolio_exposure_pct: float = 80.0

    # Minimum win rate required to take a trade
    min_win_rate: float = 0.40

    # Minimum samples required for reliable Kelly estimate
    min_samples: int = 30

    # Default Kelly when insufficient data (conservative)
    default_kelly_pct: float = 10.0

    # Maximum single position as % of equity
    max_position_pct: float = 15.0

    # Current capital
    capital: float = 10_000


@dataclass
class TradeStats:
    """Historical trade statistics for Kelly calculation."""
    total_trades: int
    winning_trades: int
    losing_trades: int
    avg_win: float  # average win as % of risk (R-multiples)
    avg_loss: float  # average loss as % of risk (R-multiples, positive value)
    win_rate: float
    win_loss_ratio: float  # avg_win / avg_loss
    expectancy: float  # (win_rate * avg_win) - ((1-win_rate) * avg_loss)


class KellyPositionSizer:
    """
    Fractional Kelly Criterion Position Sizer.

    Computes optimal position size based on strategy edge,
    scaled down by a fractional multiplier for safety.
    """

    def __init__(self, config: Optional[KellyConfig] = None):
        self.config = config or KellyConfig()

    def compute_kelly(self, stats: TradeStats) -> float:
        """
        Compute full Kelly fraction from trade statistics.

        f* = (p * b - q) / b
        Where:
          p = win rate
          q = 1 - p
          b = win/loss ratio (avg win / avg loss)

        Returns fractional Kelly percentage.
        """
        if stats.total_trades < self.config.min_samples:
            _log.warning(
                "Insufficient samples (%d < %d), using default Kelly %.1f%%",
                stats.total_trades, self.config.min_samples, self.config.default_kelly_pct
            )
            return self.config.default_kelly_pct

        if stats.win_loss_ratio <= 0:
            _log.warning("Invalid win/loss ratio: %.2f", stats.win_loss_ratio)
            return 0.0

        p = stats.win_rate
        q = 1.0 - p
        b = stats.win_loss_ratio

        # Full Kelly
        kelly_full = (p * b - q) / b

        # Clamp to [0, 1]
        kelly_full = max(0.0, min(kelly_full, 1.0))

        # Apply fractional multiplier
        kelly_fractional = kelly_full * self.config.kelly_fraction

        # Convert to percentage
        kelly_pct = kelly_fractional * 100

        # Cap at max risk per trade
        kelly_pct = min(kelly_pct, self.config.max_risk_per_trade_pct)

        _log.info(
            "Kelly: p=%.2f, b=%.2f, f*=%.4f, fractional=%.4f%%",
            p, b, kelly_full, kelly_pct
        )

        return round(kelly_pct, 2)

    def size_position(
        self,
        equity: float,
        entry_price: float,
        stop_loss_price: float,
        stats: Optional[TradeStats] = None,
        strategy_confidence: float = 1.0,
    ) -> Dict:
        """
        Compute position size based on fractional Kelly and risk per trade.

        Returns dict with:
          - quantity: number of units to buy
          - risk_amount: dollar amount at risk
          - risk_pct: risk as % of equity
          - kelly_pct: Kelly fraction used
          - position_value: total position value
          - position_pct: position as % of equity
        """
        # Default conservative sizing if no stats
        if stats is None:
            kelly_pct = self.config.default_kelly_pct
        else:
            kelly_pct = self.compute_kelly(stats)

        # Adjust by confidence
        adjusted_kelly = kelly_pct * strategy_confidence

        # Risk amount = Kelly% * equity
        risk_amount = equity * (adjusted_kelly / 100)

        # Cap at max risk per trade
        max_risk = equity * (self.config.max_risk_per_trade_pct / 100)
        risk_amount = min(risk_amount, max_risk)

        # Position size from risk
        risk_per_unit = abs(entry_price - stop_loss_price)
        if risk_per_unit <= 0:
            _log.warning("Invalid risk per unit: entry=%.4f, sl=%.4f", entry_price, stop_loss_price)
            return {
                "quantity": 0,
                "risk_amount": 0,
                "risk_pct": 0,
                "kelly_pct": kelly_pct,
                "position_value": 0,
                "position_pct": 0,
            }

        quantity = risk_amount / risk_per_unit
        position_value = quantity * entry_price

        # Cap position size
        max_position = equity * (self.config.max_position_pct / 100)
        if position_value > max_position:
            quantity = max_position / entry_price
            position_value = max_position

        # Check total portfolio exposure
        # (This would need current exposure passed in for real-time check)

        return {
            "quantity": round(quantity, 8),
            "risk_amount": round(risk_amount, 2),
            "risk_pct": round(risk_amount / equity * 100, 2) if equity > 0 else 0,
            "kelly_pct": round(kelly_pct, 2),
            "adjusted_kelly_pct": round(adjusted_kelly, 2),
            "position_value": round(position_value, 2),
            "position_pct": round(position_value / equity * 100, 2) if equity > 0 else 0,
            "risk_per_unit": round(risk_per_unit, 4),
        }

    def size_for_strategy(
        self,
        equity: float,
        entry_price: float,
        stop_loss_price: float,
        win_rate: float,
        avg_win_r: float,
        avg_loss_r: float,
        total_trades: int,
        confidence: float = 1.0,
    ) -> Dict:
        """
        Convenience method: size position from raw strategy stats.
        """
        stats = TradeStats(
            total_trades=total_trades,
            winning_trades=int(total_trades * win_rate),
            losing_trades=int(total_trades * (1 - win_rate)),
            avg_win=avg_win_r,
            avg_loss=avg_loss_r,
            win_rate=win_rate,
            win_loss_ratio=avg_win_r / avg_loss_r if avg_loss_r > 0 else 0,
            expectancy=(win_rate * avg_win_r) - ((1 - win_rate) * avg_loss_r),
        )

        return self.size_position(
            equity=equity,
            entry_price=entry_price,
            stop_loss_price=stop_loss_price,
            stats=stats,
            strategy_confidence=confidence,
        )

    def compute_optimal_fraction(
        self,
        win_rate: float,
        win_loss_ratio: float,
    ) -> float:
        """
        Compute theoretical optimal Kelly fraction.
        Used for comparison with fractional Kelly.
        """
        if win_loss_ratio <= 0:
            return 0.0

        p = win_rate
        q = 1.0 - p
        b = win_loss_ratio

        kelly = (p * b - q) / b
        return max(0.0, min(kelly, 1.0))

    def kelly_table(
        self,
        win_rates: List[float] = None,
        rr_ratios: List[float] = None,
    ) -> Dict:
        """
        Generate a Kelly fraction table for different win rates and R:R ratios.
        Useful for strategy evaluation.
        """
        if win_rates is None:
            win_rates = [0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70]
        if rr_ratios is None:
            rr_ratios = [1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0]

        table = {}
        for wr in win_rates:
            row = {}
            for rr in rr_ratios:
                full_kelly = self.compute_optimal_fraction(wr, rr)
                fractional = full_kelly * self.config.kelly_fraction * 100
                row[f"{rr:.1f}x"] = round(fractional, 2)
            table[f"{wr*100:.0f}%"] = row

        return table

    def expected_growth_rate(
        self,
        win_rate: float,
        win_loss_ratio: float,
        kelly_fraction: float,
    ) -> float:
        """
        Compute expected logarithmic growth rate at given Kelly fraction.
        G(f) = p * ln(1 + f*b) + q * ln(1 - f)
        """
        p = win_rate
        q = 1.0 - p
        b = win_loss_ratio
        f = kelly_fraction

        if f <= 0 or f >= 1:
            return 0.0

        growth = p * math.log(1 + f * b) + q * math.log(1 - f)
        return growth

    def status(self) -> Dict:
        """Return sizer status."""
        return {
            "sizer": "FractionalKelly",
            "kelly_fraction": self.config.kelly_fraction,
            "max_risk_per_trade_pct": self.config.max_risk_per_trade_pct,
            "max_portfolio_exposure_pct": self.config.max_portfolio_exposure_pct,
            "min_samples": self.config.min_samples,
        }

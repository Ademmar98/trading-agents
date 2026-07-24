"""
Core Risk Module
================
Risk management components for the trading system.

Components:
  - KellyPositionSizer: Fractional Kelly Criterion position sizing
  - CircuitBreakers: Portfolio-level risk controls
"""

from .kelly_sizer import KellyPositionSizer, KellyConfig, TradeStats
from .circuit_breakers import CircuitBreakers, CircuitBreakerConfig

__all__ = [
    "KellyPositionSizer",
    "KellyConfig",
    "TradeStats",
    "CircuitBreakers",
    "CircuitBreakerConfig",
]

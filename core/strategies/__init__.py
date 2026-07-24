"""
Core Strategies Module
======================
Modular strategy implementations for the trading-agents system.

Available Strategies:
  - ZScoreFundingSqueezeStrategy: Z-score funding rate mean-reversion (PF 2.93)
  - FundingRateCapture: Delta-neutral funding rate arbitrage
"""

from .zscore_funding_squeeze import (
    ZScoreFundingSqueezeStrategy,
    ZScoreFundingScanner,
    StrategyParams,
    BacktestResult,
    TradeRecord,
)

__all__ = [
    "ZScoreFundingSqueezeStrategy",
    "ZScoreFundingScanner",
    "StrategyParams",
    "BacktestResult",
    "TradeRecord",
]

"""
Core Strategies Module
======================
Modular strategy implementations for the trading-agents system.

Strategies:
  - ZScoreFundingSqueezeStrategy: Z-score funding rate mean-reversion (PF 2.93)
  - SpotBasketRebalancer: Structural basket rebalancing (3-7% monthly)
  - MicrostructureAbsorptionEngine: Order book absorption (3-5% swings)
  - MultiAlphaOrchestrator: Multi-strategy capital allocator
"""

from .zscore_funding_squeeze import (
    ZScoreFundingSqueezeStrategy,
    ZScoreFundingScanner,
    StrategyParams,
    BacktestResult,
    TradeRecord,
)

from .spot_basket_rebalancer import (
    SpotBasketRebalancer,
    BasketConfig,
    BasketAsset,
)

from .microstructure_absorption import (
    MicrostructureAbsorptionEngine,
    AbsorptionConfig,
    AbsorptionSignal,
)

from .multi_alpha_runner import (
    MultiAlphaOrchestrator,
    AlphaConfig,
    AlphaSignal,
)

__all__ = [
    # Module 1: Z-Score Funding
    "ZScoreFundingSqueezeStrategy",
    "ZScoreFundingScanner",
    "StrategyParams",
    "BacktestResult",
    "TradeRecord",
    # Module 2: Basket Rebalancing
    "SpotBasketRebalancer",
    "BasketConfig",
    "BasketAsset",
    # Module 3: Microstructure Absorption
    "MicrostructureAbsorptionEngine",
    "AbsorptionConfig",
    "AbsorptionSignal",
    # Orchestrator
    "MultiAlphaOrchestrator",
    "AlphaConfig",
    "AlphaSignal",
]

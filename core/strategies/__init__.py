"""
Core Strategies Module
======================
Modular strategy implementations for the trading-agents system.

Strategies (NONE are validated -- see Research Audit Study #10):
  - ZScoreFundingSqueezeStrategy: Z-score funding rate mean-reversion.
    UNVALIDATED, ex-outlier PF 0.91.
  - SpotBasketRebalancer: Structural basket rebalancing. FAILED validation
    (-6.15%/mo); disabled.
  - MicrostructureAbsorptionEngine: Order book absorption. UNVALIDATED -- its
    backtest measures a different signal than the live engine computes.
  - MultiAlphaOrchestrator: Multi-strategy capital allocator.

Kept for research reference. MULTI_ALPHA_ENABLED defaults to false and
execute_signals() is a non-executing stub.
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

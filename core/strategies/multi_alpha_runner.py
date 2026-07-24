"""
Multi-Alpha Strategy Orchestrator
==================================
Manages signal generation and capital distribution across three alpha modules:

  Module 1: Z-Score Funding Rate Squeeze Engine (High Asymmetry)
  Module 2: Structural Spot Basket Rebalancing Engine (Consistent Cashflow)
  Module 3: Microstructure Order Book Absorption Engine (High Win-Rate Swings)

Orchestrates:
  - Multi-asset scanning every 15 minutes
  - Fractional Kelly position sizing
  - Circuit breaker enforcement
  - Capital allocation across strategies
"""

import logging
import time
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Dict, List

from core.strategies.zscore_funding_squeeze import (
    ZScoreFundingSqueezeStrategy,
    ZScoreFundingScanner,
    StrategyParams,
)
from core.strategies.spot_basket_rebalancer import (
    SpotBasketRebalancer,
    BasketConfig,
)
from core.strategies.microstructure_absorption import (
    MicrostructureAbsorptionEngine,
    AbsorptionConfig,
)
from core.risk.kelly_sizer import KellyPositionSizer, KellyConfig, TradeStats
from core.risk.circuit_breakers import CircuitBreakers, CircuitBreakerConfig

_log = logging.getLogger("multi_alpha_orchestrator")


@dataclass
class AlphaConfig:
    """Configuration for the multi-alpha orchestrator."""
    # Scan interval (seconds)
    scan_interval: int = 900  # 15 minutes

    # Capital allocation across modules (% of equity)
    # Module 1: Z-Score Funding (high asymmetry, lower frequency)
    module1_allocation_pct: float = 40.0
    # Module 2: Basket Rebalancing (consistent cashflow)
    module2_allocation_pct: float = 35.0
    # Module 3: Microstructure Absorption (high win-rate swings)
    module3_allocation_pct: float = 25.0

    # Kelly parameters
    kelly_fraction: float = 0.25
    max_risk_per_trade_pct: float = 2.0
    max_portfolio_exposure_pct: float = 80.0

    # Circuit breaker parameters
    monthly_max_drawdown_pct: float = 15.0
    max_total_positions: int = 10
    per_strategy_max_open: int = 3

    # Capital
    capital: float = 10_000

    # Enable/disable individual modules
    module1_enabled: bool = True
    module2_enabled: bool = True
    module3_enabled: bool = True


@dataclass
class AlphaSignal:
    """Signal from any alpha module."""
    module: str  # 'module1', 'module2', 'module3'
    symbol: str
    action: str  # 'BUY', 'SELL', 'REBALANCE'
    confidence: float
    entry_price: float
    stop_loss: float
    take_profit: float
    risk_reward: float
    size_usd: float
    kelly_pct: float
    strategy_stats: Optional[Dict] = None
    metadata: Dict = field(default_factory=dict)


class MultiAlphaOrchestrator:
    """
    Multi-Alpha Strategy Orchestrator.

    Coordinates three alpha modules, applies fractional Kelly sizing,
    enforces circuit breakers, and manages capital allocation.
    """

    def __init__(self, config: Optional[AlphaConfig] = None):
        self.config = config or AlphaConfig()

        # Initialize modules
        self.module1 = ZScoreFundingSqueezeStrategy()
        self.module1_scanner = ZScoreFundingScanner()
        self.module2 = SpotBasketRebalancer(BasketConfig(
            cash_reserve_pct=100 - self.config.module2_allocation_pct,
        ))
        self.module3 = MicrostructureAbsorptionEngine()

        # Risk management
        self.kelly = KellyPositionSizer(KellyConfig(
            kelly_fraction=self.config.kelly_fraction,
            max_risk_per_trade_pct=self.config.max_risk_per_trade_pct,
            max_portfolio_exposure_pct=self.config.max_portfolio_exposure_pct,
        ))
        self.circuit_breakers = CircuitBreakers(CircuitBreakerConfig(
            monthly_max_drawdown_pct=self.config.monthly_max_drawdown_pct,
            max_portfolio_exposure_pct=self.config.max_portfolio_exposure_pct,
            per_strategy_max_open=self.config.per_strategy_max_open,
            max_total_positions=self.config.max_total_positions,
        ))

        # State
        self._last_scan_time: float = 0
        self._signals: List[AlphaSignal] = []
        self._positions: Dict[str, Dict] = {}  # symbol -> position info
        self._trade_history: List[Dict] = []

    def scan_all_modules(self, equity: float) -> List[AlphaSignal]:
        """
        Scan all enabled modules for signals.
        Returns combined signal list sorted by confidence.
        """
        # Check circuit breakers first
        risk_check = self.circuit_breakers.is_trading_allowed(equity)
        if not risk_check["allowed"]:
            _log.warning("Trading halted: %s", risk_check["reasons"])
            return []

        all_signals = []

        # Module 1: Z-Score Funding Squeeze
        if self.config.module1_enabled:
            try:
                signals_m1 = self._scan_module1(equity)
                all_signals.extend(signals_m1)
                _log.info("Module 1: %d signals", len(signals_m1))
            except Exception as e:
                _log.error("Module 1 scan failed: %s", e)

        # Module 2: Basket Rebalancing
        if self.config.module2_enabled:
            try:
                signals_m2 = self._scan_module2(equity)
                all_signals.extend(signals_m2)
                _log.info("Module 2: %d signals", len(signals_m2))
            except Exception as e:
                _log.error("Module 2 scan failed: %s", e)

        # Module 3: Microstructure Absorption
        if self.config.module3_enabled:
            try:
                signals_m3 = self._scan_module3(equity)
                all_signals.extend(signals_m3)
                _log.info("Module 3: %d signals", len(signals_m3))
            except Exception as e:
                _log.error("Module 3 scan failed: %s", e)

        # Sort by confidence * risk-reward
        all_signals.sort(
            key=lambda s: s.confidence * s.risk_reward,
            reverse=True,
        )

        self._signals = all_signals
        self._last_scan_time = time.time()

        return all_signals

    def _scan_module1(self, equity: float) -> List[AlphaSignal]:
        """Scan Z-Score Funding module."""
        signals = []
        allocation = equity * (self.config.module1_allocation_pct / 100)

        # Get qualified pairs
        pairs = self.module1_scanner.get_qualified_pairs()[:20]

        for p in pairs[:5]:  # Top 5 for signal generation
            try:
                df = self.module1_scanner._fetch_data(p["symbol"])
                if df is None or len(df) < 50:
                    continue

                df = self.module1.compute_indicators(df)
                last = df.iloc[-1]

                zscore = last.get('funding_zscore', 0)
                if zscore is None or zscore >= -2.0:
                    continue

                # Compute position size
                entry_price = last['spot_price']
                atr = last.get('atr14', entry_price * 0.02)
                stop_loss = entry_price - 1.5 * atr
                take_profit = entry_price + 3.0 * atr

                sizing = self.kelly.size_position(
                    equity=allocation,
                    entry_price=entry_price,
                    stop_loss_price=stop_loss,
                    stats=TradeStats(
                        total_trades=18,
                        winning_trades=8,
                        losing_trades=10,
                        avg_win=6.54,
                        avg_loss=2.0,
                        win_rate=0.444,
                        win_loss_ratio=3.27,
                        expectancy=0.93,
                    ),
                )

                signal = AlphaSignal(
                    module="module1",
                    symbol=f"{p['symbol']}/USDT",
                    action="BUY",
                    confidence=min(1.0, abs(zscore) / 3),
                    entry_price=entry_price,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    risk_reward=3.0 / 1.5,  # 2.0
                    size_usd=sizing["position_value"],
                    kelly_pct=sizing["kelly_pct"],
                    metadata={
                        "zscore": zscore,
                        "funding_rate": last['perp_funding_rate'],
                        "volume_24h": p["volume"],
                    },
                )
                signals.append(signal)

            except Exception as e:
                _log.warning("Module 1 scan failed for %s: %s", p["symbol"], e)

        return signals

    def _scan_module2(self, equity: float) -> List[AlphaSignal]:
        """Scan Basket Rebalancing module."""
        signals = []
        allocation = equity * (self.config.module2_allocation_pct / 100)

        # Get current basket status
        status = self.module2.status()

        # If basket is empty, initialize it
        if status["basket_size"] == 0:
            # Would initialize basket here
            pass

        # Generate rebalance plan
        # For now, return empty - basket needs initialization
        return signals

    def _scan_module3(self, equity: float) -> List[AlphaSignal]:
        """Scan Microstructure Absorption module."""
        signals = []
        allocation = equity * (self.config.module3_allocation_pct / 100)

        # Get top pairs
        pairs = self.module3.scan_universe(max_pairs=10)

        for p in pairs[:3]:
            try:
                # Would fetch trade and kline data here
                # For now, return empty
                pass
            except Exception as e:
                _log.warning("Module 3 scan failed for %s: %s", p["symbol"], e)

        return signals

    def allocate_capital(self, signals: List[AlphaSignal], equity: float) -> List[AlphaSignal]:
        """
        Allocate capital across signals using fractional Kelly.
        Respects per-module allocation limits.
        """
        allocated = []
        total_allocated = 0

        # Group by module
        module_allocations = {
            "module1": equity * (self.config.module1_allocation_pct / 100),
            "module2": equity * (self.config.module2_allocation_pct / 100),
            "module3": equity * (self.config.module3_allocation_pct / 100),
        }

        module_used = {"module1": 0, "module2": 0, "module3": 0}

        for signal in signals:
            module = signal.module
            available = module_allocations[module] - module_used[module]

            if available <= 0:
                continue

            # Size within module allocation
            sized_amount = min(signal.size_usd, available)

            # Check total portfolio exposure
            if total_allocated + sized_amount > equity * (self.config.max_portfolio_exposure_pct / 100):
                break

            signal.size_usd = sized_amount
            allocated.append(signal)
            module_used[module] += sized_amount
            total_allocated += sized_amount

        return allocated

    def execute_signals(self, signals: List[AlphaSignal]) -> List[Dict]:
        """
        Execute signals through the execution engine.
        Returns execution results.
        """
        results = []

        for signal in signals:
            try:
                # Check strategy position limits
                if self.circuit_breakers.check_strategy_limits(signal.module):
                    _log.warning("Strategy limit reached for %s", signal.module)
                    continue

                # Would execute through broker here
                result = {
                    "signal": signal,
                    "status": "pending",
                    "timestamp": time.time(),
                }
                results.append(result)

                # Update position count
                self.circuit_breakers.update_position_count(signal.module, 1)

            except Exception as e:
                _log.error("Execution failed for %s: %s", signal.symbol, e)

        return results

    def run_cycle(self, equity: float) -> Dict:
        """
        Run one complete orchestration cycle.
        Returns cycle summary.
        """
        start_time = time.time()

        # Scan all modules
        signals = self.scan_all_modules(equity)

        # Allocate capital
        allocated = self.allocate_capital(signals, equity)

        # Execute
        results = self.execute_signals(allocated)

        # Get risk status
        risk_status = self.circuit_breakers.get_risk_status(equity)

        cycle_time = time.time() - start_time

        summary = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "cycle_time_ms": round(cycle_time * 1000, 1),
            "equity": round(equity, 2),
            "signals_generated": len(signals),
            "signals_allocated": len(allocated),
            "signals_executed": len(results),
            "total_allocated_usd": round(sum(s.size_usd for s in allocated), 2),
            "risk_status": risk_status,
            "module_status": {
                "module1": self.module1.status(),
                "module2": self.module2.status(),
                "module3": self.module3.status(),
            },
        }

        _log.info(
            "Cycle complete: %d signals -> %d allocated -> %d executed (%.1fms)",
            len(signals), len(allocated), len(results), cycle_time * 1000,
        )

        return summary

    def status(self) -> Dict:
        """Return orchestrator status."""
        return {
            "orchestrator": "MultiAlpha",
            "config": {
                "module1_enabled": self.config.module1_enabled,
                "module2_enabled": self.config.module2_enabled,
                "module3_enabled": self.config.module3_enabled,
                "allocations": {
                    "module1": self.config.module1_allocation_pct,
                    "module2": self.config.module2_allocation_pct,
                    "module3": self.config.module3_allocation_pct,
                },
            },
            "modules": {
                "module1": self.module1.status(),
                "module2": self.module2.status(),
                "module3": self.module3.status(),
            },
            "risk": self.circuit_breakers.status(),
            "kelly": self.kelly.status(),
        }

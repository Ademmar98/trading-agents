"""
Module 3: Microstructure Order Book Absorption Engine
======================================================
Detects institutional passive limit-order absorption at key structural
demand zones for high win-rate swing trades.

Target: 3% to 5% per swing trade.

Trigger Logic:
  1. Cumulative Volume Delta (CVD) divergence: Aggressive sell market orders
     absorbed without breaking spot support.
  2. Positive Spot Premium: Spot Price > Perp Price (spot absorption).

Risk Parameters:
  - Stop Loss: Swing Low - 0.5 * ATR(14)
  - Take Profit: Nearest Volume Profile Node / Point of Control (POC)
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

_log = logging.getLogger("microstructure_absorption")

BINANCE_SPOT_BASE = "https://api.binance.com"
BINANCE_FUTURES_BASE = "https://fapi.binance.com"


@dataclass
class AbsorptionConfig:
    """Configuration for the absorption engine."""
    # CVD divergence detection
    cvd_lookback: int = 50  # bars to look back for CVD calculation
    cvd_divergence_threshold: float = 0.3  # min divergence ratio

    # Spot premium filter
    min_spot_premium_pct: float = 0.05  # spot must be > perp by this %

    # Volume profile
    vp_lookback: int = 100  # bars for volume profile
    vp_num_bins: int = 50  # price bins for profile
    poc_min_volume_ratio: float = 1.5  # POC must have this x avg volume

    # Risk parameters
    atr_period: int = 14
    sl_atr_mult: float = 0.5  # stop loss = swing low - 0.5*ATR
    min_rr_ratio: float = 2.0  # minimum risk:reward

    # Filtering
    min_volume_24h: float = 1_000_000  # minimum daily volume
    min_price: float = 0.01
    max_spread_pct: float = 0.5  # skip if bid-ask spread > this

    # Execution
    maker_fee: float = 0.0002
    taker_fee: float = 0.0007

    # Capital
    capital: float = 10_000
    risk_per_trade_pct: float = 1.0  # max risk per trade as % of equity


@dataclass
class AbsorptionSignal:
    """Single absorption signal."""
    symbol: str
    timestamp: str
    signal_type: str  # 'absorption_buy', 'absorption_sell'
    spot_price: float
    perp_price: float
    spot_premium_pct: float
    cvd_divergence: float
    support_level: float
    poc_price: float
    atr: float
    entry_price: float
    stop_loss: float
    take_profit: float
    risk_reward: float
    confidence: float
    volume_24h: float


class MicrostructureAbsorptionEngine:
    """
    Microstructure Order Book Absorption Engine.

    Detects institutional absorption by analyzing:
      1. CVD divergence (price flat but cumulative delta diverging)
      2. Spot/perp spread (spot premium = spot absorption)
      3. Volume profile (identify support/resistance zones)

    Entry: When aggressive selling is absorbed at support with positive spot premium.
    Exit: At volume profile POC or resistance, or stop below swing low.
    """

    def __init__(self, config: Optional[AbsorptionConfig] = None):
        self.config = config or AbsorptionConfig()
        self._signal_history: List[AbsorptionSignal] = []

    def compute_cvd(self, trades: List[Dict]) -> np.ndarray:
        """
        Compute Cumulative Volume Delta from trade data.
        Each trade has 'price', 'qty', 'is_buyer_maker'.
        """
        deltas = []
        cumulative = 0.0

        for t in trades:
            qty = float(t.get("qty", 0))
            is_buyer_maker = t.get("is_buyer_maker", False)

            # If buyer is maker, seller is aggressor (market sell)
            # If seller is maker, buyer is aggressor (market buy)
            delta = -qty if is_buyer_maker else qty
            cumulative += delta
            deltas.append(cumulative)

        return np.array(deltas)

    def detect_cvd_divergence(
        self,
        prices: np.ndarray,
        cvd: np.ndarray,
        lookback: int = 50,
    ) -> float:
        """
        Detect divergence between price and CVD.
        Returns divergence ratio (0-1, higher = stronger divergence).

        Bullish divergence: Price making lower lows but CVD making higher lows
        (selling pressure absorbed by passive bids).
        """
        if len(prices) < lookback or len(cvd) < lookback:
            return 0.0

        recent_prices = prices[-lookback:]
        recent_cvd = cvd[-lookback:]

        # Find local minima in price
        price_mins = []
        cvd_at_mins = []

        for i in range(2, len(recent_prices) - 2):
            if (recent_prices[i] <= recent_prices[i-1] and
                recent_prices[i] <= recent_prices[i-2] and
                recent_prices[i] <= recent_prices[i+1] and
                recent_prices[i] <= recent_prices[i+2]):
                price_mins.append((i, recent_prices[i]))
                cvd_at_mins.append((i, recent_cvd[i]))

        if len(price_mins) < 2:
            return 0.0

        # Check for bullish divergence: price lower low, CVD higher low
        p1_idx, p1_val = price_mins[-2]
        p2_idx, p2_val = price_mins[-1]
        c1_val = cvd_at_mins[-2][1]
        c2_val = cvd_at_mins[-1][1]

        if p2_val < p1_val and c2_val > c1_val:
            # Bullish divergence detected
            price_drop = abs(p2_val - p1_val) / p1_val
            cvd_rise = abs(c2_val - c1_val) / abs(c1_val) if abs(c1_val) > 0 else 0
            divergence = min(1.0, cvd_rise / max(price_drop, 0.001))
            return divergence

        return 0.0

    def compute_volume_profile(
        self,
        prices: np.ndarray,
        volumes: np.ndarray,
        num_bins: int = 50,
    ) -> Dict:
        """
        Compute volume profile and identify Point of Control (POC).
        Returns POC price and volume distribution.
        """
        if len(prices) < 10:
            return {"poc": 0, "profile": {}}

        # Create price bins
        price_min = np.min(prices)
        price_max = np.max(prices)
        bins = np.linspace(price_min, price_max, num_bins + 1)
        bin_centers = (bins[:-1] + bins[1:]) / 2

        # Aggregate volume per bin
        profile = np.zeros(num_bins)
        for i, p in enumerate(prices):
            bin_idx = np.searchsorted(bins[1:], p)
            bin_idx = min(bin_idx, num_bins - 1)
            profile[bin_idx] += volumes[i] if i < len(volumes) else 0

        # Find POC (highest volume bin)
        poc_idx = np.argmax(profile)
        poc_price = bin_centers[poc_idx]
        poc_volume = profile[poc_idx]
        avg_volume = np.mean(profile) if np.mean(profile) > 0 else 1

        return {
            "poc": poc_price,
            "poc_volume": poc_volume,
            "avg_volume": avg_volume,
            "poc_ratio": poc_volume / avg_volume,
            "bins": bin_centers.tolist(),
            "profile": profile.tolist(),
            "support_levels": self._find_support_levels(bins, profile),
        }

    def _find_support_levels(self, bins: np.ndarray, profile: np.ndarray) -> List[float]:
        """Find support levels from volume profile (high-volume nodes)."""
        supports = []
        avg_vol = np.mean(profile) if np.mean(profile) > 0 else 1

        for i in range(1, len(profile) - 1):
            if (profile[i] > profile[i-1] and
                profile[i] > profile[i+1] and
                profile[i] > avg_vol * 1.3):
                supports.append(float(bins[i]))

        return sorted(supports)

    def find_swing_low(self, prices: np.ndarray, lookback: int = 20) -> float:
        """Find recent swing low for stop loss placement."""
        if len(prices) < lookback:
            return float(np.min(prices))

        recent = prices[-lookback:]
        return float(np.min(recent))

    def compute_atr(self, highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int = 14) -> float:
        """Compute Average True Range."""
        if len(closes) < period + 1:
            return 0.0

        tr = np.maximum(
            highs[-period:] - lows[-period:],
            np.maximum(
                abs(highs[-period:] - closes[-period-1:-1]),
                abs(lows[-period:] - closes[-period-1:-1])
            )
        )

        return float(np.mean(tr))

    def scan_for_signals(
        self,
        symbol: str,
        spot_trades: List[Dict],
        spot_klines: List[List],
        perp_klines: Optional[List[List]] = None,
    ) -> Optional[AbsorptionSignal]:
        """
        Scan a single symbol for absorption signals.
        Returns signal if conditions are met, None otherwise.
        """
        try:
            # Extract price and volume data from klines
            if len(spot_klines) < self.config.vp_lookback:
                return None

            closes = np.array([float(k[4]) for k in spot_klines])
            highs = np.array([float(k[2]) for k in spot_klines])
            lows = np.array([float(k[3]) for k in spot_klines])
            volumes = np.array([float(k[5]) for k in spot_klines])

            current_price = closes[-1]

            # Volume filter
            vol_24h = np.sum(volumes[-24:]) * current_price  # rough 24h volume
            if vol_24h < self.config.min_volume_24h:
                return None

            # Price filter
            if current_price < self.config.min_price:
                return None

            # Compute CVD from trades
            if spot_trades and len(spot_trades) > 10:
                cvd = self.compute_cvd(spot_trades[-self.config.cvd_lookback:])
                cvd_divergence = self.detect_cvd_divergence(closes, cvd)
            else:
                cvd_divergence = 0.0

            # Spot premium
            perp_price = current_price
            if perp_klines and len(perp_klines) > 0:
                perp_price = float(perp_klines[-1][4])

            spot_premium = ((current_price - perp_price) / perp_price * 100) if perp_price > 0 else 0

            # Volume profile
            vp = self.compute_volume_profile(
                closes[-self.config.vp_lookback:],
                volumes[-self.config.vp_lookback:],
                self.config.vp_num_bins,
            )

            # ATR
            atr = self.compute_atr(highs, lows, closes, self.config.atr_period)

            # Swing low for stop loss
            swing_low = self.find_swing_low(lows, 20)

            # Entry conditions
            has_absorption = cvd_divergence > self.config.cvd_divergence_threshold
            has_premium = spot_premium > self.config.min_spot_premium_pct
            has_structure = vp.get("poc_ratio", 0) > self.config.poc_min_volume_ratio

            if not (has_absorption and has_premium):
                return None

            # Compute entry/exit levels
            stop_loss = swing_low - self.config.sl_atr_mult * atr
            take_profit = vp.get("poc", current_price * 1.05)
            risk = current_price - stop_loss
            reward = take_profit - current_price

            if risk <= 0:
                return None

            rr_ratio = reward / risk
            if rr_ratio < self.config.min_rr_ratio:
                return None

            # Confidence score
            confidence = min(1.0, (
                0.4 * cvd_divergence +
                0.3 * min(1.0, spot_premium / 0.5) +
                0.3 * min(1.0, vp.get("poc_ratio", 1) / 3)
            ))

            signal = AbsorptionSignal(
                symbol=symbol,
                timestamp=datetime.now(timezone.utc).isoformat(),
                signal_type="absorption_buy",
                spot_price=current_price,
                perp_price=perp_price,
                spot_premium_pct=round(spot_premium, 2),
                cvd_divergence=round(cvd_divergence, 3),
                support_level=round(swing_low, 4),
                poc_price=round(vp.get("poc", 0), 4),
                atr=round(atr, 4),
                entry_price=round(current_price, 4),
                stop_loss=round(stop_loss, 4),
                take_profit=round(take_profit, 4),
                risk_reward=round(rr_ratio, 2),
                confidence=round(confidence, 3),
                volume_24h=round(vol_24h, 2),
            )

            self._signal_history.append(signal)
            return signal

        except Exception as e:
            _log.warning("Signal scan failed for %s: %s", symbol, e)
            return None

    def scan_universe(self, max_pairs: int = 20) -> List[Dict]:
        """Scan top volume pairs for absorption signals."""
        try:
            r = requests.get(f"{BINANCE_SPOT_BASE}/api/v3/ticker/24hr", timeout=30)
            tickers = r.json()
        except Exception as e:
            _log.error("Failed to fetch tickers: %s", e)
            return []

        skip_bases = {"USDC", "USDT", "BUSD", "TUSD", "FDUSD", "DAI"}
        pairs = []

        for t in tickers:
            s = t["symbol"]
            if not s.endswith("USDT"):
                continue
            base = s[:-4]
            if base in skip_bases:
                continue
            vol = float(t.get("quoteVolume", 0))
            if vol < self.config.min_volume_24h:
                continue
            pairs.append({"symbol": base, "volume": vol})

        pairs.sort(key=lambda x: x["volume"], reverse=True)
        return pairs[:max_pairs]

    def backtest_historical(
        self,
        symbol: str,
        spot_klines: List[List],
        perp_klines: Optional[List[List]] = None,
    ) -> Dict:
        """
        Backtest absorption signals on historical data.
        """
        if len(spot_klines) < 100:
            return {"error": "Insufficient data"}

        closes = np.array([float(k[4]) for k in spot_klines])
        highs = np.array([float(k[2]) for k in spot_klines])
        lows = np.array([float(k[3]) for k in spot_klines])
        volumes = np.array([float(k[5]) for k in spot_klines])

        trades = []
        in_position = False
        entry_price = 0
        stop_loss = 0
        take_profit = 0

        for i in range(100, len(closes)):
            if in_position:
                # Check exits
                if closes[i] <= stop_loss:
                    pnl = (stop_loss - entry_price) / entry_price
                    trades.append({"exit": "stop_loss", "pnl_pct": round(pnl * 100, 2)})
                    in_position = False
                elif closes[i] >= take_profit:
                    pnl = (take_profit - entry_price) / entry_price
                    trades.append({"exit": "take_profit", "pnl_pct": round(pnl * 100, 2)})
                    in_position = False
            else:
                # Look for entry (simplified: check for volume spike + support hold)
                if i < 5:
                    continue

                vol_spike = volumes[i] > np.mean(volumes[i-20:i]) * 1.5
                support_hold = lows[i] > lows[i-1]

                if vol_spike and support_hold:
                    atr = self.compute_atr(highs[i-14:i+1], lows[i-14:i+1], closes[i-14:i+1])
                    swing_low = np.min(lows[i-20:i+1])

                    entry_price = closes[i]
                    stop_loss = swing_low - 0.5 * atr
                    take_profit = entry_price + 2.5 * atr  # 2.5x R:R

                    risk = entry_price - stop_loss
                    if risk > 0 and (take_profit - entry_price) / risk >= self.config.min_rr_ratio:
                        in_position = True

        # Compute metrics
        if not trades:
            return {"total_trades": 0, "net_return": 0}

        wins = [t for t in trades if t["pnl_pct"] > 0]
        losses = [t for t in trades if t["pnl_pct"] <= 0]

        total_pnl = sum(t["pnl_pct"] for t in trades)
        win_rate = len(wins) / len(trades) * 100 if trades else 0

        return {
            "symbol": symbol,
            "total_trades": len(trades),
            "win_rate": round(win_rate, 1),
            "total_return_pct": round(total_pnl, 2),
            "avg_win": round(np.mean([t["pnl_pct"] for t in wins]), 2) if wins else 0,
            "avg_loss": round(np.mean([t["pnl_pct"] for t in losses]), 2) if losses else 0,
            "profit_factor": round(
                sum(t["pnl_pct"] for t in wins) / abs(sum(t["pnl_pct"] for t in losses))
                if losses and sum(t["pnl_pct"] for t in losses) != 0 else 0, 2
            ),
        }

    def status(self) -> Dict:
        """Return engine status."""
        return {
            "engine": "MicrostructureAbsorptionEngine",
            "config": {
                "cvd_lookback": self.config.cvd_lookback,
                "cvd_threshold": self.config.cvd_divergence_threshold,
                "min_spot_premium": self.config.min_spot_premium_pct,
                "min_rr": self.config.min_rr_ratio,
            },
            "signals_generated": len(self._signal_history),
        }

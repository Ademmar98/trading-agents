"""
Propr Challenge Passer — Main trading bot for Propr funded challenges.

Uses Microstructure Absorption (M3) strategy with risk management adapted for prop firm rules.
Runs on Hyperliquid perpetuals via Propr SDK.

Usage:
    1. Set PROPR_API_KEY in .env
    2. python -m core.exchange.propr.challenge_passer
"""
import sys
import os
import time
import json
import logging
import signal
import threading
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import numpy as np
import requests

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from core.exchange.propr.config import ProprConfig, ChallengeType, AccountSize
from core.exchange.propr.client import ProprRiskClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("propr_passer")

PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
DATA_DIR = PROJECT_ROOT / "analysis" / "propr_data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

RUNNING = True


def signal_handler(sig, frame):
    global RUNNING
    logger.info("Shutdown signal received")
    RUNNING = False


signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)


class HyperliquidFeed:
    def __init__(self, config: ProprConfig):
        self.config = config
        self.base_url = "https://api.hyperliquid.xyz/info"
        self._meta_cache = {}
        self._load_meta()

    def _load_meta(self):
        try:
            resp = requests.post(self.base_url, json={"type": "meta"}, timeout=10)
            resp.raise_for_status()
            meta = resp.json()
            for asset in meta.get("universe", []):
                self._meta_cache[asset["name"]] = {
                    "szDecimals": asset.get("szDecimals", 2),
                    "maxLeverage": asset.get("maxLeverage", 20),
                }
        except Exception as e:
            logger.warning(f"Failed to load Hyperliquid meta: {e}")

    def get_candles(self, symbol: str, interval: str = "1h", limit: int = 200) -> pd.DataFrame:
        try:
            now = datetime.now(timezone.utc)
            interval_ms = {"1m": 60000, "5m": 300000, "15m": 900000,
                           "1h": 3600000, "4h": 14400000}.get(interval, 3600000)
            end_time = int(now.timestamp() * 1000)
            start_time = end_time - (limit * interval_ms)

            resp = requests.post(self.base_url, json={
                "type": "candleSnapshot",
                "req": {
                    "coin": symbol,
                    "interval": interval,
                    "startTime": start_time,
                    "endTime": end_time,
                }
            }, timeout=15)
            resp.raise_for_status()
            data = resp.json()

            if not data:
                return pd.DataFrame()

            rows = []
            for c in data:
                rows.append({
                    "open": float(c.get("o", 0)),
                    "high": float(c.get("h", 0)),
                    "low": float(c.get("l", 0)),
                    "close": float(c.get("c", 0)),
                    "volume": float(c.get("v", 0)),
                    "timestamp": pd.to_datetime(int(c.get("t", 0)), unit="ms", utc=True),
                })

            df = pd.DataFrame(rows)
            if not df.empty:
                df.set_index("timestamp", inplace=True)
                df.sort_index(inplace=True)
            return df

        except Exception as e:
            logger.error(f"Failed to fetch candles for {symbol}: {e}")
            return pd.DataFrame()

    def get_mid_price(self, symbol: str) -> float | None:
        try:
            resp = requests.post(self.base_url, json={
                "type": "allMids",
            }, timeout=10)
            resp.raise_for_status()
            mids = resp.json()
            return float(mids.get(symbol, 0))
        except Exception:
            return None

    def get_funding_rate(self, symbol: str) -> float | None:
        try:
            resp = requests.post(self.base_url, json={
                "type": "metaAndAssetCtxs",
            }, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            meta = data[0]
            ctxs = data[1]
            for i, asset in enumerate(meta.get("universe", [])):
                if asset["name"] == symbol and i < len(ctxs):
                    return float(ctxs[i].get("funding", 0))
        except Exception:
            return None


class MicrostructureAbsorptionM3:
    def __init__(self, config: ProprConfig):
        self.config = config
        self.lookback = 60
        self.cvd_lookback = 20
        self.rv_lookback = 20

    def compute_features(self, df: pd.DataFrame) -> dict | None:
        if len(df) < self.lookback:
            return None

        close = df["close"].values
        volume = df["volume"].values
        high = df["high"].values
        low = df["low"].values

        # CVD approximation (buy vs sell volume)
        buy_vol = np.where(close[1:] > close[:-1], volume[1:], 0)
        sell_vol = np.where(close[1:] < close[:-1], volume[1:], 0)
        buy_vol = np.append([0], buy_vol)
        sell_vol = np.append([0], sell_vol)

        cvd_raw = buy_vol - sell_vol
        cvd_cumsum = np.cumsum(cvd_raw)
        cvd_z = (cvd_cumsum[-1] - np.mean(cvd_cumsum[-self.cvd_lookback:])) / (
            np.std(cvd_cumsum[-self.cvd_lookback:]) + 1e-10
        )

        # Realized volatility
        returns = np.diff(np.log(close + 1e-10))
        rv = np.std(returns[-self.rv_lookback:]) * np.sqrt(365)

        # Price action
        current_price = close[-1]
        recent_high = np.max(high[-self.lookback:])
        recent_low = np.min(low[-self.lookback:])
        price_range = (recent_high - recent_low) / recent_high if recent_high > 0 else 0
        dip_pct = (recent_high - current_price) / recent_high if recent_high > 0 else 0

        return {
            "cvd_zscore": cvd_z,
            "rv_annualized": rv,
            "current_price": current_price,
            "dip_pct": dip_pct,
            "price_range": price_range,
            "volume_ratio": volume[-1] / np.mean(volume[-20:]) if np.mean(volume[-20:]) > 0 else 1,
        }

    def generate_signal(self, features: dict) -> tuple[bool, str]:
        cfg = self.config

        if features["cvd_zscore"] > cfg.m3_cvd_thresh:
            return False, f"CVD z={features['cvd_zscore']:.2f} > thresh"

        if features["rv_annualized"] < cfg.m3_rv_thresh:
            return False, f"RV={features['rv_annualized']:.2f} < thresh"

        if features["dip_pct"] < cfg.m3_dip_pct:
            return False, f"Dip={features['dip_pct']:.3f} < thresh"

        return True, f"BUY: CVD={features['cvd_zscore']:.2f}, RV={features['rv_annualized']:.2f}, Dip={features['dip_pct']:.3f}"

    def calculate_stops(self, entry_price: float, atr: float) -> tuple[float, float]:
        sl = entry_price - (atr * self.config.m3_sl_multiplier)
        tp = entry_price + (atr * self.config.m3_tp_multiplier)
        return sl, tp


class ChallengePasser:
    def __init__(self, config: ProprConfig):
        self.config = config
        self.client = ProprRiskClient(config)
        self.feed = HyperliquidFeed(config)
        self.strategy = MicrostructureAbsorptionM3(config)
        self._trade_log: list[dict] = []
        self._cycle_count = 0

    def setup(self):
        self.client.setup()
        logger.info(f"Challenge Passer initialized")
        logger.info(f"Type: {self.config.challenge_type.value}")
        logger.info(f"Account: ${self.config.account_size.value:,}")
        logger.info(f"Target: {self.config.rules.profit_target_pct:.0%} (${self.config.rules.profit_target:,.0f})")
        logger.info(f"Max DD: {self.config.rules.max_drawdown_pct:.0%} (${self.config.rules.max_drawdown_usd:,.0f})")
        logger.info(f"Daily Loss: {self.config.rules.max_daily_loss_pct:.0%} (${self.config.rules.max_daily_loss_usd:,.0f})")

    def _fetch_hyperliquid_candles(self, symbol: str) -> pd.DataFrame:
        for attempt in range(3):
            try:
                return self.feed.get_candles(symbol, interval="1h", limit=200)
            except Exception:
                time.sleep(1 + attempt)
        return pd.DataFrame()

    def _calculate_atr(self, df: pd.DataFrame, period: int = 14) -> float:
        if len(df) < period + 1:
            return df["close"].iloc[-1] * 0.01
        high = df["high"].values
        low = df["low"].values
        close = df["close"].values
        tr = np.maximum(
            high[1:] - low[1:],
            np.maximum(
                np.abs(high[1:] - close[:-1]),
                np.abs(low[1:] - close[:-1])
            )
        )
        return float(np.mean(tr[-period:]))

    def _execute_trade(self, symbol: str, features: dict):
        price = features["current_price"]
        if price <= 0:
            return

        df = self.feed.get_candles(symbol, "1h", 100)
        if df.empty:
            return

        atr = self._calculate_atr(df)
        sl, tp = self.strategy.calculate_stops(price, atr)

        quantity = self.client.calculate_position_size(symbol, price, sl)
        if quantity <= 0:
            logger.warning(f"Zero position size for {symbol}")
            return

        meta = self.feed._meta_cache.get(symbol, {})
        sz_decimals = meta.get("szDecimals", 4)
        quantity = round(quantity, sz_decimals)
        if quantity <= 0:
            return

        stop_distance_pct = abs(price - sl) / price
        if stop_distance_pct > 0.05:
            logger.warning(f"Stop too wide for {symbol}: {stop_distance_pct:.1%}")
            return

        orders = self.client.open_long(
            asset=symbol,
            quantity=quantity,
            stop_price=sl,
            take_profit_price=tp,
        )

        if orders:
            trade = {
                "symbol": symbol,
                "entry_price": price,
                "quantity": quantity,
                "stop_loss": sl,
                "take_profit": tp,
                "features": features,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "order_ids": [o.get("orderId") for o in orders],
            }
            self._trade_log.append(trade)
            self._save_trades()

    def _scan_opportunities(self):
        open_positions = self.client.get_open_positions()
        held_assets = {p["base"] for p in open_positions}

        for symbol in self.config.symbols:
            if not RUNNING:
                break

            try:
                if symbol in held_assets:
                    continue

                can, reason = self.client.can_trade()
                if not can:
                    logger.info(f"Cannot trade: {reason}")
                    break

                df = self._fetch_hyperliquid_candles(symbol)
                if df.empty or len(df) < 30:
                    continue

                features = self.strategy.compute_features(df)
                if not features:
                    continue

                signal_ok, reason = self.strategy.generate_signal(features)
                if signal_ok:
                    logger.info(f"SIGNAL: {symbol} — {reason}")
                    self._execute_trade(symbol, features)

                time.sleep(0.3)

            except Exception as e:
                logger.error(f"Error scanning {symbol}: {e}")
                time.sleep(0.5)

    def _manage_positions(self):
        open_positions = self.client.get_open_positions()

        for pos in open_positions:
            asset = pos["base"]
            entry = float(pos["entryPrice"])
            mark = float(pos["markPrice"])
            qty = float(pos["quantity"])
            pnl = float(pos["unrealizedPnl"])
            side = pos["positionSide"]

            pnl_pct = (mark - entry) / entry if entry > 0 else 0

            # Trailing stop: if up 2%, move stop to breakeven
            if pnl_pct > 0.02:
                logger.info(f"{asset} up {pnl_pct:.1%} — consider trailing stop")

            # Hard stop: close if down 4%
            if pnl_pct < -0.04:
                logger.warning(f"{asset} down {pnl_pct:.1%} — closing to protect equity")
                self.client.close_position(asset)

    def _print_status(self):
        summary = self.client.get_account_summary()
        logger.info(f"=== CYCLE {self._cycle_count} ===")
        logger.info(f"Equity: ${summary['equity']:,.2f} | Profit: {summary['profit_pct']:.1%} | "
                     f"Target: {summary['target_pct']:.0%}")
        logger.info(f"Daily: {summary['daily_used_pct']:.0%} | DD: {summary['dd_used_pct']:.0%} | "
                     f"Trades: {summary['trades_today']}")
        if summary['target_reached']:
            logger.info("*** PROFIT TARGET REACHED! ***")

    def _save_trades(self):
        path = DATA_DIR / "propr_trades.json"
        with open(path, "w") as f:
            json.dump(self._trade_log, f, indent=2)

    def run(self, scan_interval_sec: int = 1):
        logger.info("Starting Challenge Passer...")
        logger.info(f"Scan interval: {scan_interval_sec}s")

        while RUNNING:
            try:
                self._cycle_count += 1
                self._scan_opportunities()
                self._manage_positions()

                if self._cycle_count % 30 == 0:
                    self._print_status()

                if self.client.get_account_summary()["target_reached"]:
                    logger.info("*** CHALLENGE PASSED! ***")
                    break

                time.sleep(scan_interval_sec)

            except KeyboardInterrupt:
                break
            except Exception as e:
                logger.error(f"Cycle error: {e}")
                time.sleep(30)

        logger.info("Challenge Passer stopped")
        self._save_trades()


def main():
    config = ProprConfig(
        challenge_type=ChallengeType.CLASSIC_1STEP,
        account_size=AccountSize.K5,
        api_key=os.getenv("PROPR_API_KEY", ""),
        symbols=["BTC", "ETH", "SOL", "DOGE", "XRP", "AVAX", "LINK", "SUI", "NEAR", "AAVE", "INJ", "FET"],
        max_position_pct=0.40,
        max_daily_loss_pct=0.025,
        m3_cvd_thresh=-0.05,
        m3_rv_thresh=0.03,
        m3_dip_pct=0.008,
    )

    if not config.api_key:
        logger.error("Set PROPR_API_KEY in .env file")
        logger.error("Get your key at https://app.propr.xyz/settings")
        sys.exit(1)

    passer = ChallengePasser(config)
    passer.setup()
    passer.run(scan_interval_sec=1)


if __name__ == "__main__":
    main()

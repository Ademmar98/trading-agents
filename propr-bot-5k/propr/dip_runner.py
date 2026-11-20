"""
Propr Dip Runner — the ONE strategy in this repo with walk-forward evidence.

Signal (unchanged from the validated backtest, analysis/deep_hunt_v4_propr.py):
    dip >= 5% off the 50h high
    AND RSI(14) < 30
    AND volume > 1.8x its 20-bar average
    AND CVD z-score < -0.8
  -> long spot, stop -10%, target +4%, trail 2% once +2.5% up, 36h max hold.

WHAT THE EVIDENCE ACTUALLY SAYS (analysis/deep_hunt_v4_walkforward.py,
6 non-overlapping 30-day windows, 2026-01-27 -> 2026-07-26, config frozen):

  - 0/6 challenge failures. Buy & hold breached the 6% wall in 6/6
    (drawdowns 8.5, 28.2, 14.7, 11.3, 17.5, 36.0%). Worst here: 4.06%.
  - Positive in BOTH crash months: buy & hold -18.88% -> +2.74% (80% WR),
    buy & hold -27.69% -> +1.59% (71% WR).
  - Out-of-sample return is only +0.50%/month. The +3.30% headline was the
    in-sample window and the best of the six.

So this is a DEFENSIVE profile, not an alpha. It is here because it is the only
thing that survived out-of-sample -- not because it will pass a challenge
quickly. At +0.50%/mo the +10% target is roughly 20 months.

TWO CONSTRAINTS THAT ARE NOT STYLE CHOICES (analysis/deep_hunt_v5_improve.py):

  1. THE UNIVERSE MUST STAY NARROW. Widening 12 majors -> 30 coins breached the
     6% wall in 3 of 6 windows and turned the return negative. max_concurrent
     bounds ticket count, not correlation: with a bigger pool the three slots
     fill with whichever alts dipped hardest, and in a selloff those keep
     falling together. Do not add symbols.
  2. DO NOT WIDEN THE TARGET. TP 4% -> 6% -> 8% took out-of-sample from
     +0.50% -> +0.27% -> +0.06%. The high win rate comes FROM the tight target.

Runs DRY by default. Pass --live to place real orders.
"""
import argparse
import logging
import signal
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT_ROOT))

from core.exchange.propr.challenge_passer import HyperliquidFeed  # noqa: E402
from core.exchange.propr.client import ProprRiskClient  # noqa: E402
from core.exchange.propr.config import ProprConfig  # noqa: E402

logger = logging.getLogger("dip_runner")

# Frozen at the walk-forward-tested values. See module docstring before editing.
SYMBOLS = ["BTC", "ETH", "SOL", "DOGE", "XRP", "AVAX",
           "LINK", "SUI", "NEAR", "AAVE", "INJ", "FET"]
LOOKBACK = 50
DIP_THRESHOLD = 0.05
RSI_PERIOD = 14
RSI_OVERSOLD = 30
VOLUME_SPIKE_MULT = 1.8
CVD_LOOKBACK = 20
CVD_THRESHOLD = -0.8
SL_PCT = 0.10
TP_PCT = 0.04
TRAIL_ACTIVATION = 0.025
TRAIL_DISTANCE = 0.02
POS_SIZE_PCT = 0.20
MAX_CONCURRENT = 3
MAX_HOLD_HOURS = 36

RUNNING = True


def _stop(sig, frame):
    global RUNNING
    logger.info("shutdown requested")
    RUNNING = False


signal.signal(signal.SIGINT, _stop)
signal.signal(signal.SIGTERM, _stop)


def rsi(closes, period=RSI_PERIOD):
    d = closes.diff()
    gain = d.where(d > 0, 0.0)
    loss = -d.where(d < 0, 0.0)
    ag = gain.ewm(alpha=1 / period, min_periods=period).mean()
    al = loss.ewm(alpha=1 / period, min_periods=period).mean()
    return 100 - 100 / (1 + ag / (al + 1e-10))


def cvd_zscore(volume, closes, lookback=CVD_LOOKBACK):
    cvd = (volume * np.sign(closes.diff())).cumsum()
    return (cvd - cvd.rolling(lookback).mean()) / (cvd.rolling(lookback).std() + 1e-10)


def signal_for(df):
    """Return (fires, features) for the LAST CLOSED bar.

    df must exclude the forming bar -- the caller drops it. Reading a partial
    bar is the lookahead that faked a 68.5% win rate in the swing_htf study.
    """
    if df is None or len(df) < 220:
        return False, {}
    c, v = df["close"], df["volume"]
    r = rsi(c).iloc[-1]
    z = cvd_zscore(v, c).iloc[-1]
    spike = v.iloc[-1] / (v.rolling(20).mean().iloc[-1] + 1e-10)
    high = df["high"].rolling(LOOKBACK).max().iloc[-1]
    dip = (high - c.iloc[-1]) / high if high else 0.0

    feats = {"price": float(c.iloc[-1]), "rsi": float(r), "cvd_z": float(z),
             "vol_spike": float(spike), "dip_pct": float(dip)}
    fires = (dip >= DIP_THRESHOLD and r < RSI_OVERSOLD
             and spike > VOLUME_SPIKE_MULT and z < CVD_THRESHOLD)
    return bool(fires), feats


class DipRunner:
    def __init__(self, config: ProprConfig, live: bool = False):
        self.config = config
        self.live = live
        self.feed = HyperliquidFeed(config)
        self.client = ProprRiskClient(config)
        self.entry_bar = {}       # symbol -> monotonic seconds at entry
        self.trail_stop = {}      # symbol -> current trailing stop price

    def setup(self):
        acct = self.client.setup()
        eq = self.client.equity
        mode = "LIVE" if self.live else "DRY RUN (no orders will be placed)"
        logger.info("=" * 62)
        logger.info(f"Propr Dip Runner - {mode}")
        logger.info(f"account {acct}   equity ${eq:,.2f}")
        logger.info(f"universe {len(SYMBOLS)} majors (frozen - widening breaks it)")
        logger.info(f"dip>={DIP_THRESHOLD:.0%} RSI<{RSI_OVERSOLD} "
                    f"vol>{VOLUME_SPIKE_MULT}x cvd_z<{CVD_THRESHOLD}")
        logger.info(f"SL {SL_PCT:.0%}  TP {TP_PCT:.0%}  size {POS_SIZE_PCT:.0%}  "
                    f"max {MAX_CONCURRENT} concurrent")
        logger.info(f"expected ~+0.50%/mo OOS - this is drawdown control, not alpha")
        logger.info("=" * 62)

    def _held(self):
        try:
            return {p["base"] for p in self.client.get_open_positions()}
        except Exception as e:
            # Fail closed: if positions are unreadable, do not open more.
            logger.warning(f"cannot read positions: {e}")
            return None

    def scan(self):
        held = self._held()
        if held is None:
            return
        if len(held) >= MAX_CONCURRENT:
            return

        for sym in SYMBOLS:
            if not RUNNING or sym in held or len(held) >= MAX_CONCURRENT:
                continue
            try:
                df = self.feed.get_candles(sym, "1h", limit=260)
                if df.empty:
                    continue
                df = df.iloc[:-1]          # drop the forming bar
                fires, f = signal_for(df)
                if not fires:
                    continue

                entry = f["price"]
                sl = entry * (1 - SL_PCT)
                tp = entry * (1 + TP_PCT)
                qty = (self.client.equity * POS_SIZE_PCT) / entry

                logger.info(
                    f"SIGNAL {sym}: dip {f['dip_pct']:.1%} rsi {f['rsi']:.0f} "
                    f"vol {f['vol_spike']:.1f}x cvd_z {f['cvd_z']:.2f} "
                    f"-> qty {qty:.4f} @ {entry:.4f} sl {sl:.4f} tp {tp:.4f}")

                if not self.live:
                    logger.info(f"  DRY RUN - not placing {sym}")
                    continue

                orders = self.client.open_long(
                    asset=sym, quantity=qty, stop_price=sl,
                    take_profit_price=tp, entry_price=entry)
                if orders:
                    held.add(sym)
                    self.entry_bar[sym] = time.monotonic()
                    self.trail_stop[sym] = sl
                else:
                    logger.warning(f"  {sym} entry refused (risk gate or stop guard)")
                time.sleep(0.3)
            except Exception as e:
                logger.error(f"scan {sym}: {e}")

    def manage(self):
        """Trailing stop + max-hold. SL/TP themselves rest on the exchange."""
        try:
            positions = self.client.get_open_positions()
        except Exception as e:
            logger.warning(f"cannot read positions: {e}")
            return

        for p in positions:
            sym = p.get("base")
            try:
                entry = float(p.get("entryPrice", 0) or 0)
                px = self.feed.get_mid_price(sym)
                if not entry or not px:
                    continue
                gain = (px - entry) / entry

                if gain >= TRAIL_ACTIVATION:
                    new_stop = px * (1 - TRAIL_DISTANCE)
                    if new_stop > self.trail_stop.get(sym, 0):
                        self.trail_stop[sym] = new_stop
                        logger.info(f"{sym} +{gain:.2%} - trail to {new_stop:.4f}")

                if self.trail_stop.get(sym) and px <= self.trail_stop[sym]:
                    logger.info(f"{sym} hit trailing stop {self.trail_stop[sym]:.4f}")
                    if self.live:
                        self.client.close_position(sym)
                    self.trail_stop.pop(sym, None)
                    self.entry_bar.pop(sym, None)
                    continue

                t0 = self.entry_bar.get(sym)
                if t0 and (time.monotonic() - t0) > MAX_HOLD_HOURS * 3600:
                    logger.info(f"{sym} max hold {MAX_HOLD_HOURS}h reached - closing")
                    if self.live:
                        self.client.close_position(sym)
                    self.trail_stop.pop(sym, None)
                    self.entry_bar.pop(sym, None)
            except Exception as e:
                logger.error(f"manage {sym}: {e}")

    def run(self, interval_sec=300):
        self.setup()
        n = 0
        while RUNNING:
            try:
                n += 1
                self.scan()
                self.manage()
                if n % 12 == 1:
                    s = self.client.get_account_summary()
                    logger.info(
                        f"equity ${s['equity']:,.2f} | "
                        f"open risk ${self.client.open_risk_usd():,.2f} | "
                        f"daily used ${s['daily_used']:,.2f} ({s['daily_used_pct']:.0%}) | "
                        f"dd used ${s['dd_used']:,.2f} ({s['dd_used_pct']:.0%}) | "
                        f"profit {s['profit_pct']:.2%} of {s['target_pct']:.0%}")
                time.sleep(interval_sec)
            except KeyboardInterrupt:
                break
            except Exception as e:
                logger.error(f"cycle: {e}")
                time.sleep(60)
        logger.info("dip runner stopped")


def main():
    ap = argparse.ArgumentParser(description="Propr dip-buy runner (v4 validated)")
    ap.add_argument("--live", action="store_true",
                    help="place real orders (default is a dry run)")
    ap.add_argument("--interval", type=int, default=300,
                    help="seconds between scans (default 300; the signal is hourly)")
    a = ap.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    import os
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")

    cfg = ProprConfig(api_key=os.getenv("PROPR_API_KEY", ""))
    if not cfg.api_key:
        logger.error("PROPR_API_KEY not set in .env")
        return 1
    if a.live:
        logger.warning("LIVE MODE - real orders will be placed")

    DipRunner(cfg, live=a.live).run(interval_sec=a.interval)
    return 0


if __name__ == "__main__":
    sys.exit(main())

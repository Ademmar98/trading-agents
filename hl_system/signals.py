"""
Signal modules A, B, C and the SignalManager that combines them.

Every module returns a `Signal` or None. A module NEVER places an order and
never reads the exchange — it is a pure function of DataEngine state, which is
what makes the same code runnable in a backtest.

DESIGN NOTE — the CVD source (this is the important one)
--------------------------------------------------------
This repo previously shipped an absorption module whose backtest built CVD by
cumsum-ing 4h kline `taker_buy_base`, while the live code built it from the
last ~50 tape trades. Those are different signals, so the backtest never
tested what ran. Here Module A consumes `DataEngine.trades` — the raw tape —
and NOTHING ELSE. A backtest must replay the same tape. If you ever find
yourself approximating CVD from kline volume, you are testing a different
strategy and the result does not transfer.

DIRECTION SEMANTICS
-------------------
Under the halal default (`long_only=True`) there are exactly two actionable
directions: LONG (open/add) and EXIT (close to flat). A module that wants to
be short emits EXIT. `SHORT` exists in the enum only so the perp path stays
expressible; SignalManager refuses to emit it while `long_only` is set.
"""
from __future__ import annotations

import logging
import math
import statistics
from dataclasses import dataclass, field
from enum import Enum
from typing import Sequence

from .data_engine import Candle, DataEngine, INTERVAL_MS, Trade

log = logging.getLogger("hl.signals")


class Direction(str, Enum):
    LONG = "LONG"
    EXIT = "EXIT"
    SHORT = "SHORT"      # perp-only; blocked under long_only
    FLAT = "FLAT"


@dataclass
class Signal:
    symbol: str
    module: str
    direction: Direction
    strength: float                      # 0..1, used for conviction sizing
    reason: str
    ts: int
    meta: dict = field(default_factory=dict)


# ------------------------------------------------------------- helpers -----


#: hard bound on _trend_score, so one pathological series cannot dominate sizing
TREND_SCORE_CLAMP = 10.0


def _trend_score(series: Sequence[float]) -> float:
    """
    Signed, scale-free trend strength: total least-squares drift over the
    window, divided by the window's own dispersion. Clamped to +/-10.

        score = (beta * n) / stdev(series)

    Normalising by the SERIES stdev rather than the REGRESSION RESIDUAL stdev
    is deliberate and was a bug fix. Dividing by residual sd inverts the
    measure at exactly the interesting end: a perfectly linear ramp has zero
    residual, so it scored 0.0 (weakest) when it is in fact the strongest
    possible trend — and a near-linear CVD series produced 9.9e15, which would
    have driven conviction sizing straight to its cap on a numerical artifact.

    Flat series -> 0.0. Rising -> positive. Falling -> negative.
    """
    n = len(series)
    if n < 4:
        return 0.0
    xs = list(range(n))
    mx, my = sum(xs) / n, sum(series) / n
    var_x = sum((x - mx) ** 2 for x in xs)
    if var_x == 0:
        return 0.0
    beta = sum((x - mx) * (y - my) for x, y in zip(xs, series)) / var_x
    sd = statistics.pstdev(series)
    if sd <= 0:
        return 0.0                       # genuinely flat: no trend, not an error
    return max(-TREND_SCORE_CLAMP, min(TREND_SCORE_CLAMP, (beta * n) / sd))


def vwap_with_bands(candles: Sequence[Candle], sigma: float) -> tuple[float, float, float]:
    """
    Volume-weighted average price with +/- `sigma` volume-weighted std bands.

    Returns (vwap, lower_band, upper_band). Uses typical price (h+l+c)/3 per
    bar, which is the standard anchored-VWAP construction, and a genuinely
    volume-weighted variance rather than an unweighted stdev of closes.
    """
    tot_v = sum(c.volume for c in candles)
    if tot_v <= 0 or not candles:
        return (0.0, 0.0, 0.0)
    tps = [(c.high + c.low + c.close) / 3.0 for c in candles]
    vwap = sum(tp * c.volume for tp, c in zip(tps, candles)) / tot_v
    var = sum(c.volume * (tp - vwap) ** 2 for tp, c in zip(tps, candles)) / tot_v
    sd = math.sqrt(max(var, 0.0))
    return (vwap, vwap - sigma * sd, vwap + sigma * sd)


# ------------------------------------ Module A: microstructure / CVD -------


class MicrostructureSignal:
    """
    Cumulative Volume Delta divergence against resting liquidity walls (15m).

    Long  : price makes a lower low while CVD makes a higher low (sellers are
            hitting bids without moving price down = absorption), AND the top
            of book shows a bid wall (bid depth >= `wall_ratio` x ask depth).
    Exit  : the mirror — price higher high, CVD lower high, ask wall overhead.

    The wall condition is what keeps this from being a generic divergence
    indicator: the divergence says pressure is being absorbed, the wall says
    by whom.
    """

    name = "A_microstructure"

    def __init__(self, cfg) -> None:
        self.cfg = cfg

    def _cvd_series(self, trades: Sequence[Trade], interval_ms: int,
                    buckets: int) -> tuple[list[float], list[float]]:
        """Bucket the tape into `interval_ms` bins -> (cumulative CVD, close px)."""
        if not trades:
            return ([], [])
        end = (trades[-1].ts // interval_ms) * interval_ms
        start = end - buckets * interval_ms
        delta: dict[int, float] = {}
        last_px: dict[int, float] = {}
        for t in trades:
            if t.ts < start:
                continue
            b = (t.ts // interval_ms) * interval_ms
            delta[b] = delta.get(b, 0.0) + t.signed_size
            last_px[b] = t.px
        if not delta:
            return ([], [])
        keys = sorted(delta)
        cvd, run = [], 0.0
        for k in keys:
            run += delta[k]
            cvd.append(run)
        return (cvd, [last_px[k] for k in keys])

    def evaluate(self, symbol: str, data: DataEngine) -> Signal | None:
        c = self.cfg
        trades = list(data.trades.get(symbol, ()))
        book = data.books.get(symbol)
        if len(trades) < 50 or book is None or not book.bids or not book.asks:
            return None

        step = INTERVAL_MS[c.a_interval]
        cvd, px = self._cvd_series(trades, step, c.a_cvd_lookback)
        k = c.a_divergence_lookback
        if len(cvd) < k or len(px) < k:
            return None

        cvd_w, px_w = cvd[-k:], px[-k:]
        cvd_z = _trend_score(cvd_w)
        px_z = _trend_score(px_w)

        bid_d = book.depth("bid", c.a_wall_depth_levels)
        ask_d = book.depth("ask", c.a_wall_depth_levels)
        if bid_d <= 0 or ask_d <= 0:
            return None
        bid_wall = bid_d >= c.a_wall_ratio * ask_d
        ask_wall = ask_d >= c.a_wall_ratio * bid_d

        ts = trades[-1].ts
        imbalance = (bid_d - ask_d) / (bid_d + ask_d)

        # Bullish divergence: price down, CVD up, resting bid wall underneath.
        if px_z < 0 and cvd_z > c.a_min_cvd_z and bid_wall:
            strength = min(1.0, abs(cvd_z) / (2 * c.a_min_cvd_z)) * min(1.0, bid_d / ask_d / c.a_wall_ratio)
            return Signal(symbol, self.name, Direction.LONG, strength,
                          f"bullish CVD divergence (cvd_z={cvd_z:+.2f}, px_z={px_z:+.2f}) "
                          f"at bid wall {bid_d/ask_d:.1f}x",
                          ts, {"cvd_z": cvd_z, "px_z": px_z, "imbalance": imbalance})

        # Bearish divergence: price up, CVD down, ask wall overhead -> exit.
        if px_z > 0 and cvd_z < -c.a_min_cvd_z and ask_wall:
            strength = min(1.0, abs(cvd_z) / (2 * c.a_min_cvd_z))
            return Signal(symbol, self.name, Direction.EXIT, strength,
                          f"bearish CVD divergence (cvd_z={cvd_z:+.2f}, px_z={px_z:+.2f}) "
                          f"at ask wall {ask_d/bid_d:.1f}x",
                          ts, {"cvd_z": cvd_z, "px_z": px_z, "imbalance": imbalance})
        return None


# ------------------------------------ Module B: funding-rate Z-score -------


class FundingZScoreSignal:
    """
    Rolling Z-score of Hyperliquid 1h funding (window 72).

        Z = (F_now - mu) / sigma

    Z < -2.5  => funding deeply negative => shorts are paying longs => LONG.
    Z > +2.5  => funding deeply positive => longs are paying          => EXIT.
    |Z| crossing 0 => revert to neutral.

    ⚠ EVIDENCE WARNING. This module is study #10 in this repo and it did not
    survive. Reported PF 2.93 was really 1.54 across 54 trades / 18 pairs;
    removing the two best pairs (NFP +112%, SYN +49%) took it to PF 0.91,
    mean −2.74%. It is implemented faithfully here because it was asked for.
    Anything built on it must report PER-PAIR results, not an aggregate — the
    aggregate is what hid the problem last time.

    On SPOT there is no funding to collect. In spot mode this module is a pure
    SENTIMENT read: deeply negative perp funding is used as a crowding signal
    for a spot long. That is a weaker claim than the arbitrage the name
    implies, and it is stated here so nobody mistakes one for the other.
    """

    name = "B_funding_z"

    def __init__(self, cfg) -> None:
        self.cfg = cfg

    def zscore(self, rates: Sequence[float]) -> float | None:
        c = self.cfg
        if len(rates) < c.b_min_samples:
            return None
        w = list(rates[-c.b_zscore_window:])
        mu = statistics.fmean(w)
        sd = statistics.pstdev(w)
        if sd == 0:
            return None
        return (w[-1] - mu) / sd

    def evaluate(self, symbol: str, data: DataEngine) -> Signal | None:
        rows = list(data.funding.get(symbol, ()))
        if not rows:
            return None
        z = self.zscore([r for _, r in rows])
        if z is None:
            return None
        ts = rows[-1][0]
        c = self.cfg

        if z < c.b_entry_z:
            strength = min(1.0, abs(z - c.b_entry_z) / abs(c.b_entry_z) + 0.5)
            return Signal(symbol, self.name, Direction.LONG, strength,
                          f"funding Z={z:+.2f} < {c.b_entry_z}", ts, {"z": z})
        if z > c.b_exit_z:
            return Signal(symbol, self.name, Direction.EXIT, min(1.0, z / c.b_exit_z),
                          f"funding Z={z:+.2f} > {c.b_exit_z}", ts, {"z": z})
        if abs(z) < 0.25:
            return Signal(symbol, self.name, Direction.FLAT, 0.0,
                          f"funding Z={z:+.2f} ~ neutral", ts, {"z": z})
        return None


# ------------------------------- Module C: multi-anchor VWAP reversion -----


class VwapReversionSignal:
    """
    Mean reversion to VWAP from a -2 sigma touch, on 1h with a 4h check.

    Entry : 1h low pierces the lower band of EITHER anchor (24h rolling
            session VWAP or the 7-day anchored VWAP) AND that bar's volume
            exceeds `volume_expansion` x its 20-period SMA.
    Target: the anchor's VWAP (the mean itself) — supplied to the executor as
            `meta["target"]`.
    Exit  : price closes back above the upper band (reversion complete/failed).

    The 4h confirm exists to veto knife-catching: if the 4h trend is strongly
    down, a -2 sigma touch is not a stretched mean, it is a downtrend.
    """

    name = "C_vwap_reversion"

    def __init__(self, cfg) -> None:
        self.cfg = cfg

    def evaluate(self, symbol: str, data: DataEngine) -> Signal | None:
        c = self.cfg
        h1 = list(data.candles.get((symbol, c.c_interval), ()))
        h4 = list(data.candles.get((symbol, c.c_confirm_interval), ()))
        if len(h1) < max(c.c_volume_sma, c.c_session_hours) + 2:
            return None

        bars_per_hour = 3_600_000 // INTERVAL_MS[c.c_interval]
        session_n = max(2, c.c_session_hours * bars_per_hour)
        anchor_n = max(2, c.c_anchor_days * 24 * bars_per_hour)

        session = h1[-session_n:]
        anchor = h1[-anchor_n:] if len(h1) >= anchor_n else h1

        s_vwap, s_lo, s_hi = vwap_with_bands(session, c.c_band_sigma)
        a_vwap, a_lo, a_hi = vwap_with_bands(anchor, c.c_band_sigma)
        if s_vwap <= 0 or a_vwap <= 0:
            return None

        last = h1[-1]
        vol_sma = statistics.fmean([x.volume for x in h1[-c.c_volume_sma:]])
        expanded = vol_sma > 0 and last.volume >= c.c_volume_expansion * vol_sma

        # 4h veto: don't catch a knife. Measured as decline from the recent 4h
        # HIGH, not distance above the recent low — the earlier form
        # (close >= min * 1.005) was unsatisfiable in a flat market, where
        # close == min, so it vetoed every quiet tape including valid setups.
        trend_ok = True
        if len(h4) >= 6:
            recent = [x.close for x in h4[-6:]]
            peak = max(recent)
            if peak > 0:
                trend_ok = (peak - recent[-1]) / peak <= c.c_trend_veto_pct

        touched_session = last.low <= s_lo
        touched_anchor = last.low <= a_lo

        if (touched_session or touched_anchor) and expanded and trend_ok:
            which, vwap, band = (("session", s_vwap, s_lo) if touched_session
                                 else ("weekly_anchor", a_vwap, a_lo))
            stretch = (vwap - last.close) / vwap if vwap else 0.0
            return Signal(
                symbol, self.name, Direction.LONG,
                min(1.0, max(0.0, stretch / 0.05)),
                f"{which} VWAP -{c.c_band_sigma}sigma touch "
                f"(low {last.low:.6g} <= {band:.6g}), vol {last.volume/vol_sma:.1f}x SMA",
                last.ts,
                {"target": vwap, "band_low": band, "anchor": which,
                 "vol_ratio": last.volume / vol_sma if vol_sma else 0.0},
            )

        if last.close >= s_hi and s_hi > 0:
            return Signal(symbol, self.name, Direction.EXIT, 0.6,
                          f"close above session VWAP +{c.c_band_sigma}sigma", last.ts,
                          {"target": s_vwap})
        return None


# ------------------------------------------------------- SignalManager -----


@dataclass
class Decision:
    symbol: str
    direction: Direction
    conviction: float                     # 0..1 aggregate
    signals: list[Signal]
    reasons: list[str]
    target: float | None = None


class SignalManager:
    """Runs all three modules and reconciles them into one Decision per symbol."""

    def __init__(self, cfg, long_only: bool = True) -> None:
        self.cfg = cfg
        self.long_only = long_only
        self.modules = [MicrostructureSignal(cfg),
                        FundingZScoreSignal(cfg),
                        VwapReversionSignal(cfg)]

    def evaluate(self, symbol: str, data: DataEngine) -> Decision | None:
        sigs: list[Signal] = []
        for m in self.modules:
            try:
                s = m.evaluate(symbol, data)
            except Exception:
                log.exception("module %s failed on %s", m.name, symbol)
                continue
            if s is not None and s.direction is not Direction.FLAT:
                sigs.append(s)
        if not sigs:
            return None

        # EXIT dominates. A single module asking to be flat outranks any number
        # asking to be long — the asymmetry is deliberate: being wrongly flat
        # costs opportunity, being wrongly long costs drawdown, and drawdown is
        # the constraint that ends the account.
        exits = [s for s in sigs if s.direction is Direction.EXIT]
        if exits:
            return Decision(symbol, Direction.EXIT,
                            max(s.strength for s in exits), exits,
                            [s.reason for s in exits])

        longs = [s for s in sigs if s.direction is Direction.LONG]
        if len(longs) < self.cfg.min_module_agreement:
            return None

        conviction = min(1.0, sum(s.strength for s in longs) / len(self.modules))
        if not self.cfg.conviction_scales_size:
            conviction = 1.0
        target = next((s.meta.get("target") for s in longs if s.meta.get("target")), None)
        return Decision(symbol, Direction.LONG, conviction, longs,
                        [s.reason for s in longs], target)

"""
RiskManager — drawdown circuit breakers, freeze logic, and position sizing.

Two things here are deliberate corrections of bugs this repo has actually
shipped, and they are the reason the class looks the way it does:

1. EQUITY IS TAKEN FROM THE EXCHANGE, NEVER RECONSTRUCTED.
   Study #16 found a max_dd that reported ~24% where the truth was ~0.4%,
   because the equity curve added open positions' entry cost on top of their
   mark-to-market. `accountValue` from `Info.user_state` already nets
   everything. We never rebuild it from fills.

2. ATR USES TRUE RANGE.
   Study #10 found an ATR computed from close-to-close differences, which
   understates range on gappy assets and therefore OVERSIZES exactly the
   positions that gap. `_true_range` below is the real Wilder definition.

The hard lock is persisted to disk. A 5% breach must not be undone by someone
restarting the process.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, asdict
from enum import Enum
from pathlib import Path
from typing import Sequence

from .data_engine import AssetMeta, Candle

log = logging.getLogger("hl.risk")

_DAY_MS = 86_400_000


class RiskState(str, Enum):
    NORMAL = "NORMAL"
    REDUCED = "REDUCED"          # >=4.0% dd: half size
    LIQUIDATING = "LIQUIDATING"  # >=4.8% dd: flatten passively
    HALTED = "HALTED"            # >=5.0% dd: permanent
    FROZEN = "FROZEN"            # daily loss limit: no NEW entries, 12h


@dataclass
class RiskSnapshot:
    equity: float
    peak_equity: float
    drawdown_pct: float
    state: str
    daily_pnl_pct: float
    frozen_until: float
    open_positions: int
    gross_exposure_pct: float

    def render(self) -> str:
        return (f"equity=${self.equity:,.2f} peak=${self.peak_equity:,.2f} "
                f"dd={self.drawdown_pct:.2f}% state={self.state} "
                f"day={self.daily_pnl_pct:+.2f}% pos={self.open_positions} "
                f"gross={self.gross_exposure_pct:.1f}%")


# ------------------------------------------------------------- ATR ---------


def _true_range(prev_close: float, c: Candle) -> float:
    """Wilder true range: max(H-L, |H-Cprev|, |L-Cprev|)."""
    return max(c.high - c.low, abs(c.high - prev_close), abs(c.low - prev_close))


def atr(candles: Sequence[Candle], period: int) -> float | None:
    """
    Wilder-smoothed ATR over `period` bars. Returns None if history is short —
    the caller must then refuse to size, not fall back to a guess.
    """
    if len(candles) < period + 1:
        return None
    trs = [_true_range(candles[i - 1].close, candles[i])
           for i in range(1, len(candles))]
    a = sum(trs[:period]) / period                     # seed = simple mean
    for tr in trs[period:]:                            # then Wilder smoothing
        a = (a * (period - 1) + tr) / period
    return a


# ------------------------------------------------------- RiskManager -------


class RiskManager:
    def __init__(self, cfg, state_path: Path) -> None:
        self.cfg = cfg.risk
        self.sys_cfg = cfg
        self.state_path = Path(state_path)

        self.equity: float = 0.0
        self.peak_equity: float = 0.0
        self.state: RiskState = RiskState.NORMAL
        self.frozen_until: float = 0.0
        self.hard_locked: bool = False
        #: (timestamp_sec, equity) samples for the rolling 24h PnL test
        self._equity_history: list[tuple[float, float]] = []

        self._load()

    # -- persistence --------------------------------------------------------

    def _load(self) -> None:
        if not self.state_path.exists():
            return
        try:
            d = json.loads(self.state_path.read_text(encoding="utf-8"))
        except Exception:
            log.exception("could not read risk state; starting clean")
            return
        self.peak_equity = float(d.get("peak_equity", 0.0))
        self.frozen_until = float(d.get("frozen_until", 0.0))
        self.hard_locked = bool(d.get("hard_locked", False))
        self._equity_history = [tuple(x) for x in d.get("equity_history", [])][-2000:]
        if self.hard_locked:
            self.state = RiskState.HALTED
            log.critical("HARD LOCK restored from disk — this process will not trade")

    def _save(self) -> None:
        tmp = self.state_path.with_suffix(".tmp")
        payload = {
            "peak_equity": self.peak_equity,
            "frozen_until": self.frozen_until,
            "hard_locked": self.hard_locked,
            "equity_history": self._equity_history[-2000:],
            "saved_at": time.time(),
        }
        try:
            tmp.write_text(json.dumps(payload), encoding="utf-8")
            tmp.replace(self.state_path)          # atomic; a crash mid-write can't corrupt
        except Exception:
            log.exception("could not persist risk state")

    # -- the circuit breaker ------------------------------------------------

    def update(self, equity: float, open_positions: int = 0,
               gross_exposure: float = 0.0) -> RiskSnapshot:
        """
        Feed the exchange's account value in. Returns a snapshot and, as a side
        effect, moves the state machine. Call this every loop BEFORE deciding
        anything.
        """
        now = time.time()
        if equity <= 0:
            log.error("non-positive equity %.2f — refusing to update risk state", equity)
            return self.snapshot(open_positions, gross_exposure)

        self.equity = equity
        self.peak_equity = max(self.peak_equity, equity)
        self._equity_history.append((now, equity))
        self._equity_history = [(t, e) for t, e in self._equity_history
                                if now - t <= 3 * 86_400][-2000:]

        dd = self.drawdown_pct

        # --- daily loss freeze: evaluated ALWAYS, independent of the tiers ---
        # This must not live in an elif chain with the drawdown tiers. It is an
        # orthogonal gate: a 4.2% drawdown that ALSO lost 2% today has to be
        # both REDUCED and frozen. An earlier version stored both in `state`,
        # so crossing the 4% tier silently cancelled the freeze and let entries
        # through at half size. `frozen_until` is now the single source of
        # truth for the freeze and `can_open` checks it regardless of tier.
        day = self.daily_pnl_pct
        if day <= -self.cfg.daily_loss_limit_pct and now >= self.frozen_until:
            self.frozen_until = now + self.cfg.freeze_hours * 3600
            log.warning("24h PnL %.2f%% <= -%.2f%% — new entries frozen for %.0fh",
                        day, self.cfg.daily_loss_limit_pct, self.cfg.freeze_hours)

        # --- tier 3: permanent hard lock -----------------------------------
        if dd >= self.cfg.dd_hard_lock_pct:
            if not self.hard_locked:
                log.critical(
                    "EMERGENCY: %.2f%% peak-to-trough drawdown >= %.2f%% HARD LOCK. "
                    "equity=$%.2f peak=$%.2f. Trading halted permanently; "
                    "clear %s to reset after manual review.",
                    dd, self.cfg.dd_hard_lock_pct, equity, self.peak_equity,
                    self.state_path)
            self.hard_locked = True
            self.state = RiskState.HALTED

        # --- tier 2: passive liquidation -----------------------------------
        elif dd >= self.cfg.dd_liquidate_pct:
            if self.state is not RiskState.LIQUIDATING:
                log.critical("drawdown %.2f%% >= %.2f%% — cancelling all orders and "
                             "passively flattening to USDC", dd, self.cfg.dd_liquidate_pct)
            self.state = RiskState.LIQUIDATING

        # --- tier 1: halve size --------------------------------------------
        elif dd >= self.cfg.dd_reduce_pct:
            if self.state is not RiskState.REDUCED:
                log.warning("drawdown %.2f%% >= %.2f%% — position sizing halved",
                            dd, self.cfg.dd_reduce_pct)
            self.state = RiskState.REDUCED

        # --- no tier breached: NORMAL, or FROZEN if the daily gate is live ---
        else:
            self.state = RiskState.FROZEN if now < self.frozen_until else RiskState.NORMAL

        if self.hard_locked:
            self.state = RiskState.HALTED

        self._save()
        return self.snapshot(open_positions, gross_exposure)

    # -- derived ------------------------------------------------------------

    @property
    def drawdown_pct(self) -> float:
        if self.peak_equity <= 0:
            return 0.0
        return max(0.0, (self.peak_equity - self.equity) / self.peak_equity * 100.0)

    @property
    def daily_pnl_pct(self) -> float:
        """Rolling 24h account PnL. Uses the oldest sample within the window."""
        if not self._equity_history:
            return 0.0
        cutoff = time.time() - 86_400
        window = [(t, e) for t, e in self._equity_history if t >= cutoff]
        base = window[0][1] if window else self._equity_history[0][1]
        if base <= 0:
            return 0.0
        return (self.equity - base) / base * 100.0

    def snapshot(self, open_positions: int = 0, gross_exposure: float = 0.0) -> RiskSnapshot:
        return RiskSnapshot(
            equity=self.equity, peak_equity=self.peak_equity,
            drawdown_pct=self.drawdown_pct, state=self.state.value,
            daily_pnl_pct=self.daily_pnl_pct, frozen_until=self.frozen_until,
            open_positions=open_positions,
            gross_exposure_pct=gross_exposure * 100.0,
        )

    # -- gates --------------------------------------------------------------

    def can_open(self, gross_exposure: float, open_positions: int) -> tuple[bool, str]:
        """Single gate every new entry must pass. Fails closed."""
        if self.hard_locked or self.state is RiskState.HALTED:
            return (False, "HARD LOCK: 5% drawdown breached")
        if self.state is RiskState.LIQUIDATING:
            return (False, "liquidating at 4.8% drawdown")
        if time.time() < self.frozen_until:
            mins = (self.frozen_until - time.time()) / 60
            return (False, f"daily-loss freeze, {mins:.0f} min remaining")
        if open_positions >= self.cfg.max_concurrent_positions:
            return (False, f"at max {self.cfg.max_concurrent_positions} concurrent positions")
        if gross_exposure >= self.cfg.max_gross_exposure:
            return (False, f"gross exposure {gross_exposure:.1%} at cap "
                           f"{self.cfg.max_gross_exposure:.0%} (zero-leverage mandate)")
        return (True, "ok")

    @property
    def should_flatten(self) -> bool:
        return self.state in (RiskState.LIQUIDATING, RiskState.HALTED)

    @property
    def size_multiplier(self) -> float:
        if self.state is RiskState.REDUCED:
            return self.cfg.size_reduction_factor
        if self.state in (RiskState.LIQUIDATING, RiskState.HALTED):
            return 0.0
        return 1.0

    # -- sizing -------------------------------------------------------------

    def position_size(self, symbol: str, price: float, daily_candles: Sequence[Candle],
                      meta: AssetMeta, conviction: float = 1.0) -> tuple[float, str]:
        """
        Volatility-parity size, in BASE UNITS.

            size = (capital * risk_factor) / (ATR * atr_multiplier)

        then clamped by the per-asset exposure cap (15% majors / 3% alts),
        the drawdown size multiplier, and conviction.

        Returns (size, explanation). Size 0.0 means "do not trade" and the
        explanation says why — a caller must not treat 0 as an error.
        """
        if price <= 0:
            return (0.0, "invalid price")
        if self.equity <= 0:
            return (0.0, "unknown equity")

        a = atr(daily_candles, self.cfg.atr_period)
        if a is None or a <= 0:
            return (0.0, f"insufficient history for {self.cfg.atr_period}-period ATR")

        risk_capital = self.equity * self.cfg.risk_factor
        stop_distance = a * self.cfg.atr_multiplier
        size = risk_capital / stop_distance                  # base units

        cap_frac = (self.cfg.max_exposure_major if symbol in self.cfg.majors
                    else self.cfg.max_exposure_alt)
        cap_units = (self.equity * cap_frac) / price

        size = min(size, cap_units)
        size *= self.size_multiplier * max(0.0, min(1.0, conviction))

        if size <= 0:
            return (0.0, "size floored to zero by risk state")

        notional = size * price
        why = (f"ATR({self.cfg.atr_period})={a:.6g} stop={stop_distance:.6g} "
               f"risk=${risk_capital:.2f} cap={cap_frac:.0%}(${self.equity*cap_frac:.2f}) "
               f"mult={self.size_multiplier:.2f} conv={conviction:.2f} "
               f"-> {size:.6g} units (${notional:.2f})")
        return (size, why)

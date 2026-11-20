"""
Entry point — wires DataEngine, SignalManager, RiskManager and ExecutionEngine
into one async loop.

Run:
    export HL_SECRET_KEY=0x...          # API wallet (agent) key, NOT your master key
    export HL_ACCOUNT_ADDRESS=0x...     # the master account it trades for
    python -m hl_system.main --dry-run

`--dry-run` is the default. Nothing reaches the exchange until you pass
`--live`, and `--live` additionally requires typing the account address back.

LOOP ORDER IS LOAD-BEARING
--------------------------
    sync -> risk -> (flatten?) -> manage orders -> signals -> entries

Risk is evaluated from freshly synced exchange state BEFORE any decision, and
the flatten check sits ahead of signal evaluation. A loop ordered any other
way can place an entry in the same pass that discovers it has breached a
drawdown tier.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import logging.handlers
import signal as os_signal
import sys
import time
from pathlib import Path

from eth_account import Account
from hyperliquid.exchange import Exchange
from hyperliquid.info import Info

from .config import SystemConfig
from .data_engine import DataEngine
from .execution import ExecutionEngine
from .risk_manager import RiskManager, RiskState
from .signals import Decision, Direction, SignalManager

log = logging.getLogger("hl.main")


def setup_logging(path: Path, verbose: bool = False) -> None:
    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-8s %(name)-12s %(message)s", "%Y-%m-%d %H:%M:%S")
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)

    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    root.addHandler(sh)

    path.parent.mkdir(parents=True, exist_ok=True)
    fh = logging.handlers.RotatingFileHandler(path, maxBytes=20_000_000, backupCount=5,
                                              encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)

    logging.getLogger("websocket").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


class TradingSystem:
    def __init__(self, cfg: SystemConfig) -> None:
        self.cfg = cfg
        self.running = True
        self.symbols: list[str] = []

        self.info = Info(cfg.api.base_url, skip_ws=False, timeout=cfg.api.timeout)
        wallet = Account.from_key(cfg.api.secret_key)
        self.exchange = Exchange(wallet, cfg.api.base_url,
                                 account_address=cfg.api.account_address,
                                 timeout=cfg.api.timeout)
        log.info("signing wallet %s trading for account %s",
                 wallet.address, cfg.api.account_address)

        self.data = DataEngine(self.info, cfg)
        self.signals = SignalManager(cfg.signals, long_only=cfg.long_only)
        self.risk = RiskManager(cfg, cfg.state_path)
        self.execution = ExecutionEngine(self.exchange, self.info, self.data, cfg)

        self._last_funding_refresh = 0.0
        self._last_daily_refresh = 0.0

    # -- startup ------------------------------------------------------------

    async def startup(self) -> None:
        self.data._loop = asyncio.get_running_loop()

        self.symbols = await self.data.discover_universe()
        if not self.symbols:
            raise RuntimeError("empty universe — check min_daily_volume_usd / spot_mode")

        await self.execution.initialize_leverage(self.symbols)

        s = self.cfg.signals
        intervals = sorted({s.a_interval, s.b_interval, s.c_interval,
                            s.c_confirm_interval})

        # Stream only the top slice; the WS has a subscription budget and 200
        # symbols x 4 intervals would exhaust it.
        streamed = self.symbols[: self.cfg.execution.max_streamed_symbols]
        log.info("backfilling %d symbols x %s ...", len(streamed), intervals)
        for i, sym in enumerate(streamed, 1):
            for iv in intervals:
                await self.data.fetch_candles(sym, iv, 400)
            await self.data.fetch_candles(sym, self.cfg.risk.atr_interval,
                                          self.cfg.risk.atr_period + 5)
            if not self.cfg.spot_mode:
                await self.data.fetch_funding(sym, s.b_zscore_window + 10)
            await self.data.subscribe_symbol(sym, intervals)
            if i % 10 == 0:
                log.info("  [%d/%d]", i, len(streamed))

        self.symbols = streamed
        await self.data.refresh_mids()
        equity, positions = await self.execution.sync_state()
        snap = self.risk.update(equity, len(positions),
                                self.execution.gross_exposure(equity))
        log.info("startup complete | %s", snap.render())

        if self.risk.hard_locked:
            log.critical("account is HARD LOCKED from a previous 5%% breach. "
                         "Review, then delete %s to resume.", self.cfg.state_path)

    # -- periodic refreshes -------------------------------------------------

    async def _refresh_slow_data(self) -> None:
        now = time.time()
        if not self.cfg.spot_mode and now - self._last_funding_refresh > 1800:
            for sym in self.symbols:
                await self.data.fetch_funding(sym, self.cfg.signals.b_zscore_window + 10)
            self._last_funding_refresh = now
        if now - self._last_daily_refresh > 3600:
            for sym in self.symbols:
                await self.data.fetch_candles(sym, self.cfg.risk.atr_interval,
                                              self.cfg.risk.atr_period + 5)
            self._last_daily_refresh = now
            await self.data.refresh_mids()

    # -- the loop -----------------------------------------------------------

    async def run(self) -> None:
        await self.startup()
        asyncio.create_task(self.data.watchdog())

        while self.running:
            t0 = time.monotonic()
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("tick failed — continuing")
            elapsed = time.monotonic() - t0
            await asyncio.sleep(max(0.5, self.cfg.execution.loop_interval_sec - elapsed))

    async def _tick(self) -> None:
        # 1. exchange truth
        equity, positions = await self.execution.sync_state()
        if equity <= 0:
            log.warning("equity unknown this tick; skipping (fail closed)")
            return

        gross = self.execution.gross_exposure(equity)
        snap = self.risk.update(equity, len(positions), gross)

        # 2. circuit breakers, before anything can open
        if self.risk.state is RiskState.HALTED:
            log.critical("HALTED — %s", snap.render())
            await self.execution.flatten_all()
            self.running = False
            return
        if self.risk.should_flatten:
            await self.execution.flatten_all()
            return

        # 3. housekeeping
        await self.execution.manage_working_orders()
        await self._refresh_slow_data()

        # 4. signals
        decisions: list[Decision] = []
        for sym in self.symbols:
            d = self.signals.evaluate(sym, self.data)
            if d is not None:
                decisions.append(d)

        # exits first — always allowed, even while frozen or reduced
        for d in [x for x in decisions if x.direction is Direction.EXIT]:
            await self._handle_exit(d)

        longs = sorted([x for x in decisions if x.direction is Direction.LONG],
                       key=lambda x: -x.conviction)
        for d in longs:
            if not await self._handle_entry(d, equity):
                break        # gate closed; no later signal will pass either

        if decisions:
            log.info("%s | %d signals (%d long, %d exit)", snap.render(), len(decisions),
                     len(longs), len(decisions) - len(longs))

    async def _handle_exit(self, d: Decision) -> None:
        pos = self.execution.positions.get(d.symbol)
        if pos is None or pos.size == 0:
            return
        if self.execution.has_working(d.symbol):
            return
        log.info("EXIT %s: %s", d.symbol, "; ".join(d.reasons))
        await self.execution.place_passive(d.symbol, is_buy=pos.size < 0,
                                           size=abs(pos.size), reduce_only=True,
                                           meta={"exit": True})

    async def _handle_entry(self, d: Decision, equity: float) -> bool:
        """Returns False when the risk gate is closed, so the caller can stop."""
        ok, why = self.risk.can_open(self.execution.gross_exposure(equity),
                                     len(self.execution.positions))
        if not ok:
            log.info("entry gate closed: %s", why)
            return False

        if d.symbol in self.execution.positions or self.execution.has_working(d.symbol):
            return True                    # already engaged; not a gate failure

        if self.cfg.long_only and d.direction is not Direction.LONG:
            return True

        meta = self.data.assets.get(d.symbol)
        px = self.data.mids.get(d.symbol)
        daily = list(self.data.candles.get((d.symbol, self.cfg.risk.atr_interval), ()))
        if meta is None or not px or not daily:
            return True

        size, why_size = self.risk.position_size(d.symbol, px, daily, meta, d.conviction)
        if size <= 0:
            log.info("%s: not sized — %s", d.symbol, why_size)
            return True

        log.info("ENTRY %s conviction=%.2f | %s", d.symbol, d.conviction,
                 "; ".join(d.reasons))
        log.info("  sizing: %s", why_size)
        w = await self.execution.place_passive(d.symbol, is_buy=True, size=size,
                                               meta={"target": d.target})
        if w and d.target:
            log.info("  target (VWAP mean): %.8g", d.target)
        return True

    async def shutdown(self) -> None:
        log.info("shutting down — cancelling working orders (positions are LEFT OPEN)")
        self.running = False
        try:
            await self.execution.cancel_all()
        finally:
            await self.data.close()


async def _amain(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Hyperliquid 3-module trading system")
    p.add_argument("--live", action="store_true", help="actually send orders")
    p.add_argument("--perp", action="store_true",
                   help="trade 1x perps instead of spot (accrues funding)")
    p.add_argument("--allow-short", action="store_true",
                   help="permit short entries (violates the repo halal constraint)")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)

    base = SystemConfig.default(dry_run=not args.live)
    cfg = SystemConfig(
        api=base.api, risk=base.risk, signals=base.signals, execution=base.execution,
        universe=base.universe, min_daily_volume_usd=base.min_daily_volume_usd,
        long_only=not args.allow_short, spot_mode=not args.perp,
        state_path=base.state_path, log_path=base.log_path, dry_run=not args.live,
    )
    setup_logging(cfg.log_path, args.verbose)

    log.info("mode: %s | %s | %s",
             "LIVE" if args.live else "DRY-RUN",
             "PERP 1x" if args.perp else "SPOT",
             "long-only" if cfg.long_only else "LONG+SHORT")
    if args.perp:
        log.warning("perp mode: positions accrue funding (riba) — this violates the "
                    "repo's standing halal constraint")
    if args.live:
        typed = input(f"Type the account address to trade live "
                      f"({cfg.api.account_address}): ").strip()
        if typed.lower() != cfg.api.account_address.lower():
            log.error("address mismatch — aborting")
            return 2

    system = TradingSystem(cfg)
    loop = asyncio.get_running_loop()
    stop = asyncio.Event()
    for s in (os_signal.SIGINT, os_signal.SIGTERM):
        try:
            loop.add_signal_handler(s, stop.set)
        except NotImplementedError:
            pass                                  # Windows: fall back to KeyboardInterrupt

    runner = asyncio.create_task(system.run())
    stopper = asyncio.create_task(stop.wait())
    try:
        done, _ = await asyncio.wait({runner, stopper},
                                     return_when=asyncio.FIRST_COMPLETED)
        if runner in done:
            runner.result()
    except KeyboardInterrupt:
        pass
    finally:
        runner.cancel()
        stopper.cancel()
        await system.shutdown()
    return 0


def main() -> int:
    try:
        return asyncio.run(_amain())
    except KeyboardInterrupt:
        return 130
    except Exception:
        logging.getLogger("hl.main").exception("fatal")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

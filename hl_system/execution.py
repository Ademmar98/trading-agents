"""
ExecutionEngine — 100% passive order placement against the Hyperliquid SDK.

THE MAKER GUARANTEE
-------------------
Every order goes out as `{"limit": {"tif": "Alo"}}`. ALO is Add-Liquidity-Only:
if the order would cross the book and take liquidity, the EXCHANGE REJECTS IT
rather than filling it as taker. That makes "never pay taker fees" a property
enforced by Hyperliquid, not a hope about our own price maths. The cost is
that some orders are rejected instead of filled, which is what the re-quote
loop below exists to handle.

Consequence for liquidation: at the 4.8% drawdown tier we flatten PASSIVELY.
A passive flatten is not guaranteed to complete. That is a real, accepted
trade-off of an all-maker mandate and it is logged loudly rather than papered
over — if you need a guaranteed exit under stress, you need an IOC escape
hatch, and that means paying taker. `allow_taker_liquidation` exposes that
choice explicitly; it defaults False to honour the brief.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from hyperliquid.exchange import Exchange
from hyperliquid.info import Info
from hyperliquid.utils.types import Cloid

from .data_engine import AssetMeta, DataEngine, OrderBook, round_price, round_size

log = logging.getLogger("hl.exec")

ALO: dict[str, Any] = {"limit": {"tif": "Alo"}}     # post-only
IOC: dict[str, Any] = {"limit": {"tif": "Ioc"}}     # escape hatch only


@dataclass
class WorkingOrder:
    symbol: str
    cloid: str
    oid: int | None
    is_buy: bool
    size: float
    price: float
    reduce_only: bool
    placed_at: float
    requotes: int = 0
    meta: dict = field(default_factory=dict)


@dataclass
class Position:
    symbol: str
    size: float            # base units, always >= 0 under long_only
    entry_px: float
    opened_at: float
    target: float | None = None

    def notional(self, px: float) -> float:
        return abs(self.size) * px


def _tick_for(px: float, meta: AssetMeta) -> float:
    """
    Smallest legal price increment, given HL's 5-sig-fig + decimal-cap rules.

    At px=64000 the binding rule is 5 sig figs -> tick 1.0.
    At px=0.5 with szDecimals=2 the binding rule is 4 decimals -> tick 0.0001.
    """
    import math
    if px <= 0:
        return 10 ** -max(0, meta.max_price_decimals)
    exp = math.floor(math.log10(abs(px)))
    sig_tick = 10.0 ** (exp - 4)                       # 5 significant figures
    dec_tick = 10.0 ** -max(0, meta.max_price_decimals)
    return max(sig_tick, dec_tick)


def passive_price(book: OrderBook, is_buy: bool, meta: AssetMeta,
                  offset_ticks: int = 0) -> float | None:
    """
    A price that rests on our own side of the book.

    Joining the best bid (offset 0) is the most aggressive passive quote. A
    positive offset steps further away — better queue economics, worse fill
    probability. We never quote through the spread, because ALO would just
    bounce the order.
    """
    if not book.bids or not book.asks:
        return None
    tick = _tick_for(book.bids[0].px if is_buy else book.asks[0].px, meta)
    raw = (book.bids[0].px - offset_ticks * tick if is_buy
           else book.asks[0].px + offset_ticks * tick)
    if raw <= 0:
        return None
    px = round_price(raw, meta, is_buy)
    # Final safety: never let rounding push us across the spread.
    if is_buy and px >= book.asks[0].px:
        px = round_price(book.asks[0].px - tick, meta, True)
    if not is_buy and px <= book.bids[0].px:
        px = round_price(book.bids[0].px + tick, meta, False)
    return px if px > 0 else None


class ExecutionEngine:
    def __init__(self, exchange: Exchange, info: Info, data: DataEngine, cfg) -> None:
        self.ex = exchange
        self.info = info
        self.data = data
        self.cfg = cfg
        self.ecfg = cfg.execution
        self.dry_run = cfg.dry_run
        self.allow_taker_liquidation = False

        self.working: dict[str, WorkingOrder] = {}        # cloid -> order
        self.positions: dict[str, Position] = {}
        self._lock = asyncio.Lock()

    # -- setup --------------------------------------------------------------

    async def initialize_leverage(self, symbols: list[str]) -> None:
        """
        Force leverage to 1x on every perp asset.

        No-op in spot mode: spot has no leverage concept, so there is nothing
        to set and calling update_leverage would error. Silence here is
        correct, not a skipped safety step.
        """
        if self.cfg.spot_mode:
            log.info("spot mode — leverage is not applicable (0x borrow by construction)")
            return
        if self.ecfg.leverage != 1:
            raise RuntimeError("refusing to set leverage != 1")
        for s in symbols:
            if self.dry_run:
                log.info("[dry-run] update_leverage(1, %s)", s)
                continue
            try:
                r = await asyncio.to_thread(self.ex.update_leverage, 1, s, True)
                if isinstance(r, dict) and r.get("status") != "ok":
                    log.warning("update_leverage %s -> %s", s, r)
            except Exception as e:
                log.warning("update_leverage %s failed: %s", s, e)
            await asyncio.sleep(0.05)                  # be kind to the rate limiter
        log.info("leverage pinned to 1x on %d assets", len(symbols))

    # -- state sync ---------------------------------------------------------

    async def sync_state(self) -> tuple[float, dict[str, Position]]:
        """
        Re-read account value, positions, and resting orders from the exchange.

        This is the state-recovery path: after a restart or a disconnect the
        exchange is the single source of truth, never our in-memory guess.
        Returns (account_value, positions).
        """
        addr = self.cfg.api.account_address
        try:
            if self.cfg.spot_mode:
                st = await asyncio.to_thread(self.info.spot_user_state, addr)
                mids = self.data.mids or await self.data.refresh_mids()
                equity, positions = 0.0, {}
                for b in st.get("balances", []):
                    coin, total = b["coin"], float(b["total"])
                    if total <= 0:
                        continue
                    if coin in ("USDC", "USD"):
                        equity += total
                        continue
                    px = float(mids.get(coin, 0) or 0)
                    equity += total * px
                    if px > 0 and total * px >= self.ecfg.min_order_notional_usd:
                        prev = self.positions.get(coin)
                        positions[coin] = Position(
                            coin, total, prev.entry_px if prev else px,
                            prev.opened_at if prev else time.time(),
                            prev.target if prev else None)
            else:
                st = await asyncio.to_thread(self.info.user_state, addr)
                equity = float(st["marginSummary"]["accountValue"])
                positions = {}
                for ap in st.get("assetPositions", []):
                    p = ap["position"]
                    szi = float(p["szi"])
                    if szi == 0:
                        continue
                    prev = self.positions.get(p["coin"])
                    positions[p["coin"]] = Position(
                        p["coin"], szi, float(p["entryPx"] or 0),
                        prev.opened_at if prev else time.time(),
                        prev.target if prev else None)

            self.positions = positions

            # reconcile resting orders (drop anything the exchange no longer has)
            live = await asyncio.to_thread(self.info.open_orders, addr)
            live_oids = {int(o["oid"]) for o in live}
            for cloid, w in list(self.working.items()):
                if w.oid is not None and w.oid not in live_oids:
                    log.info("order %s (%s) no longer resting — filled or cancelled",
                             cloid[:10], w.symbol)
                    self.working.pop(cloid, None)
            return (equity, positions)
        except Exception:
            log.exception("sync_state failed — treating as unknown, no new entries")
            return (0.0, self.positions)

    # -- order placement ----------------------------------------------------

    async def place_passive(self, symbol: str, is_buy: bool, size: float,
                            reduce_only: bool = False,
                            meta: dict | None = None) -> WorkingOrder | None:
        """Place one ALO (post-only) order at the near touch. Returns None if not placed."""
        amet = self.data.assets.get(symbol)
        book = self.data.books.get(symbol)
        if amet is None:
            log.warning("%s: no asset metadata", symbol)
            return None
        if book is None or not book.bids or not book.asks:
            book = await self.data.fetch_book(symbol)
            if book is None or not book.bids or not book.asks:
                log.warning("%s: no book, cannot quote passively", symbol)
                return None

        px = passive_price(book, is_buy, amet, self.ecfg.passive_offset_ticks)
        if px is None:
            return None
        sz = round_size(size, amet)
        if sz <= 0:
            log.info("%s: size rounds to 0 at szDecimals=%d", symbol, amet.sz_decimals)
            return None
        if sz * px < self.ecfg.min_order_notional_usd:
            log.info("%s: notional $%.2f below $%.2f minimum",
                     symbol, sz * px, self.ecfg.min_order_notional_usd)
            return None

        cloid_hex = "0x" + uuid.uuid4().hex
        w = WorkingOrder(symbol, cloid_hex, None, is_buy, sz, px,
                         reduce_only, time.time(), meta=meta or {})

        if self.dry_run:
            log.info("[dry-run] ALO %s %s sz=%.8g px=%.8g ro=%s",
                     "BUY" if is_buy else "SELL", symbol, sz, px, reduce_only)
            self.working[cloid_hex] = w
            return w

        try:
            res = await asyncio.to_thread(
                self.ex.order, symbol, is_buy, sz, px, ALO, reduce_only,
                Cloid.from_str(cloid_hex))
        except Exception as e:
            log.error("order %s failed: %s", symbol, e)
            return None

        oid = self._parse_order_response(res, symbol)
        if oid is None:
            return None
        w.oid = oid
        self.working[cloid_hex] = w
        log.info("resting ALO %s %s sz=%.8g px=%.8g oid=%s",
                 "BUY" if is_buy else "SELL", symbol, sz, px, oid)
        return w

    @staticmethod
    def _parse_order_response(res: Any, symbol: str) -> int | None:
        """
        Unpack the SDK order response.

        An ALO order that would have crossed comes back as an `error` status —
        that is expected and benign (the book moved between our snapshot and
        the exchange), so it logs at INFO, not ERROR.
        """
        if not isinstance(res, dict) or res.get("status") != "ok":
            log.error("%s: order rejected: %s", symbol, res)
            return None
        try:
            statuses = res["response"]["data"]["statuses"]
        except (KeyError, TypeError):
            log.error("%s: unparseable order response: %s", symbol, res)
            return None
        for st in statuses:
            if "resting" in st:
                return int(st["resting"]["oid"])
            if "filled" in st:
                # Possible on a marketable-but-not-crossing quote. Still maker.
                log.info("%s: filled immediately at %s", symbol, st["filled"].get("avgPx"))
                return int(st["filled"].get("oid", 0)) or None
            if "error" in st:
                log.info("%s: ALO not placed (%s)", symbol, st["error"])
                return None
        return None

    # -- cancel / re-quote --------------------------------------------------

    async def cancel(self, w: WorkingOrder) -> bool:
        if self.dry_run:
            self.working.pop(w.cloid, None)
            return True
        try:
            res = await asyncio.to_thread(self.ex.cancel_by_cloid, w.symbol,
                                          Cloid.from_str(w.cloid))
            ok = isinstance(res, dict) and res.get("status") == "ok"
        except Exception as e:
            log.warning("cancel %s failed: %s", w.symbol, e)
            ok = False
        self.working.pop(w.cloid, None)
        return ok

    async def cancel_all(self) -> int:
        n = 0
        for w in list(self.working.values()):
            if await self.cancel(w):
                n += 1
        log.info("cancelled %d working orders", n)
        return n

    async def manage_working_orders(self) -> None:
        """
        Cancel and re-quote anything unfilled past `unfilled_timeout_candles`.

        Passive orders that sit forever are the main failure mode of an
        all-maker system: the signal decays but the order stays, so you get
        filled on a stale idea. After `max_requotes` we abandon the signal
        entirely rather than chase it.
        """
        from .data_engine import INTERVAL_MS
        timeout = (self.ecfg.unfilled_timeout_candles
                   * INTERVAL_MS[self.cfg.signals.a_interval] / 1000.0)
        now = time.time()
        async with self._lock:
            for w in list(self.working.values()):
                if now - w.placed_at < timeout:
                    continue
                await self.cancel(w)
                if w.requotes >= self.ecfg.max_requotes:
                    log.info("%s: abandoning after %d re-quotes", w.symbol, w.requotes)
                    continue
                nw = await self.place_passive(w.symbol, w.is_buy, w.size,
                                              w.reduce_only, w.meta)
                if nw:
                    nw.requotes = w.requotes + 1
                    log.info("%s: re-quote #%d at %.8g", w.symbol, nw.requotes, nw.price)

    # -- liquidation --------------------------------------------------------

    async def flatten_all(self) -> None:
        """
        Cancel everything, then post passive reduce-only exits for each position.

        Called at the 4.8% tier. Passive by mandate — see the module docstring
        for why that is not a guaranteed exit.
        """
        await self.cancel_all()
        if not self.positions:
            log.info("flatten: no open positions")
            return
        log.critical("flatten: %d positions -> USDC (passive)", len(self.positions))
        for sym, pos in list(self.positions.items()):
            if pos.size == 0:
                continue
            is_buy = pos.size < 0                      # buy back a short, sell a long
            order_type = IOC if self.allow_taker_liquidation else ALO
            if self.allow_taker_liquidation and not self.dry_run:
                log.critical("%s: TAKER liquidation (allow_taker_liquidation=True)", sym)
                amet = self.data.assets[sym]
                book = self.data.books.get(sym) or await self.data.fetch_book(sym)
                if book:
                    px = round_price(book.bids[0].px * 0.995 if not is_buy
                                     else book.asks[0].px * 1.005, amet, is_buy)
                    await asyncio.to_thread(self.ex.order, sym, is_buy,
                                            round_size(abs(pos.size), amet), px,
                                            order_type, True, None)
                continue
            await self.place_passive(sym, is_buy, abs(pos.size), reduce_only=True,
                                     meta={"liquidation": True})

    # -- helpers ------------------------------------------------------------

    def gross_exposure(self, equity: float) -> float:
        if equity <= 0:
            return 0.0
        total = 0.0
        for sym, p in self.positions.items():
            px = self.data.mids.get(sym, p.entry_px)
            total += p.notional(px)
        return total / equity

    def has_working(self, symbol: str) -> bool:
        return any(w.symbol == symbol for w in self.working.values())

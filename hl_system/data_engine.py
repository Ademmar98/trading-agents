"""
DataEngine — market data for the Hyperliquid system.

Responsibilities
----------------
* Asset discovery (perp + spot universes) and the szDecimals metadata that
  every order must be quantised against.
* REST: candle history, funding history, L2 snapshots — behind a token-bucket
  rate limiter so a burst of symbols cannot trip Hyperliquid's IP limit.
* WebSocket: live candles, trades (for CVD), and L2 book, bridged from the
  SDK's background thread onto the asyncio loop.
* State recovery: on WS disconnect, resubscribe and backfill the REST gap so
  the signal modules never see a hole they would silently treat as flat.

Threading note
--------------
`hyperliquid.info.Info.subscribe` invokes its callback on the SDK's own
WebSocket thread, NOT the asyncio loop. Every callback here does the minimum
possible work and hands the message to the loop via `call_soon_threadsafe`.
Doing real work in those callbacks would block the socket's read loop.
"""
from __future__ import annotations

import asyncio
import logging
import math
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Callable, Deque

from hyperliquid.info import Info

log = logging.getLogger("hl.data")

#: Hyperliquid candle intervals we support, in milliseconds.
INTERVAL_MS: dict[str, int] = {
    "1m": 60_000, "5m": 300_000, "15m": 900_000, "30m": 1_800_000,
    "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000,
}

MAX_CANDLES_PER_REQUEST = 5000   # HL caps candleSnapshot; see repo study #16


# --------------------------------------------------------------- types -----


@dataclass
class Candle:
    ts: int           # open time, ms
    open: float
    high: float
    low: float
    close: float
    volume: float
    n_trades: int = 0
    closed: bool = True

    @staticmethod
    def from_wire(d: dict[str, Any], closed: bool = True) -> "Candle":
        return Candle(
            ts=int(d["t"]), open=float(d["o"]), high=float(d["h"]),
            low=float(d["l"]), close=float(d["c"]), volume=float(d["v"]),
            n_trades=int(d.get("n", 0)), closed=closed,
        )


@dataclass
class Trade:
    """One tape print. `side` is the AGGRESSOR side: 'B' buy, 'A' sell."""
    ts: int
    px: float
    sz: float
    side: str

    @property
    def signed_size(self) -> float:
        """Base-units signed by aggressor. This is the CVD increment."""
        return self.sz if self.side == "B" else -self.sz


@dataclass
class BookLevel:
    px: float
    sz: float
    n: int


@dataclass
class OrderBook:
    ts: int
    bids: list[BookLevel] = field(default_factory=list)
    asks: list[BookLevel] = field(default_factory=list)

    def depth(self, side: str, levels: int) -> float:
        """Notional depth over the top `levels` of one side."""
        rows = self.bids if side == "bid" else self.asks
        return sum(l.px * l.sz for l in rows[:levels])

    @property
    def mid(self) -> float | None:
        if not self.bids or not self.asks:
            return None
        return (self.bids[0].px + self.asks[0].px) / 2.0


@dataclass
class AssetMeta:
    """Everything needed to quantise an order for one asset."""
    name: str
    sz_decimals: int
    is_spot: bool
    max_leverage: int = 1
    #: spot pair name as the exchange knows it, e.g. "PURR/USDC"; None for perps
    pair_name: str | None = None

    @property
    def max_price_decimals(self) -> int:
        """
        Hyperliquid price rule: at most (MAX_DECIMALS - szDecimals) decimal
        places, where MAX_DECIMALS is 6 for perps and 8 for spot.
        """
        return (8 if self.is_spot else 6) - self.sz_decimals


# ---------------------------------------------------------- quantisation ---


def round_size(sz: float, meta: AssetMeta) -> float:
    """Round DOWN to szDecimals. Down, so we never exceed an exposure cap."""
    q = 10 ** meta.sz_decimals
    return math.floor(abs(sz) * q) / q * (1 if sz >= 0 else -1)


def round_price(px: float, meta: AssetMeta, is_buy: bool) -> float:
    """
    Quantise a price to Hyperliquid's two simultaneous rules:

      1. at most 5 significant figures
      2. at most `meta.max_price_decimals` decimal places

    Integer prices are exempt from rule 1. Rounding is directional — a passive
    BUY rounds DOWN and a passive SELL rounds UP — so quantisation can only
    ever make an order more passive, never cross the spread and turn a
    post-only order into a rejection.

    `signing.float_to_wire` raises on any float it cannot represent exactly, so
    getting this wrong is a hard failure at order time, not a silent slip.
    """
    if px <= 0:
        raise ValueError(f"non-positive price {px}")

    # rule 1: 5 significant figures (skip for prices >= 100000, already integral)
    if px >= 100_000:
        sig = float(round(px))
    else:
        exp = math.floor(math.log10(abs(px)))
        factor = 10 ** (4 - exp)                       # 5 sig figs => 4 after the leading digit
        sig = (math.floor(px * factor) if is_buy else math.ceil(px * factor)) / factor

    # rule 2: decimal-place cap
    dp = max(0, meta.max_price_decimals)
    q = 10 ** dp
    out = (math.floor(sig * q) if is_buy else math.ceil(sig * q)) / q

    # float_to_wire refuses anything needing >8dp to round-trip
    return float(f"{out:.8f}")


# ------------------------------------------------------- rate limiting -----


class RateLimiter:
    """
    Token bucket. Hyperliquid's REST info limit is ~1200 weight/minute per IP;
    default here is deliberately conservative (600/min) because the WS carries
    the hot path and REST is only used for backfill.
    """

    def __init__(self, rate_per_min: int = 600) -> None:
        self._capacity = rate_per_min
        self._tokens = float(rate_per_min)
        self._rate = rate_per_min / 60.0
        self._last = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self, weight: int = 1) -> None:
        async with self._lock:
            while True:
                now = time.monotonic()
                self._tokens = min(self._capacity,
                                   self._tokens + (now - self._last) * self._rate)
                self._last = now
                if self._tokens >= weight:
                    self._tokens -= weight
                    return
                await asyncio.sleep((weight - self._tokens) / self._rate)


# ---------------------------------------------------------- DataEngine -----


class DataEngine:
    """Owns all market data. Signal modules read from it; nobody else calls the SDK."""

    def __init__(self, info: Info, cfg, loop: asyncio.AbstractEventLoop | None = None) -> None:
        self.info = info
        self.cfg = cfg
        self._loop = loop
        self.limiter = RateLimiter()

        self.assets: dict[str, AssetMeta] = {}
        self.candles: dict[tuple[str, str], Deque[Candle]] = defaultdict(lambda: deque(maxlen=1000))
        self.trades: dict[str, Deque[Trade]] = defaultdict(lambda: deque(maxlen=5000))
        self.books: dict[str, OrderBook] = {}
        self.funding: dict[str, Deque[tuple[int, float]]] = defaultdict(lambda: deque(maxlen=500))
        self.mids: dict[str, float] = {}

        self._subs: list[tuple[dict, int]] = []
        self._last_msg_ts: float = time.monotonic()
        self._ws_lock = threading.Lock()
        self._closed = False

    # -- lifecycle ----------------------------------------------------------

    async def discover_universe(self) -> list[str]:
        """
        Enumerate tradeable assets and cache their szDecimals.

        In spot_mode we return spot pairs (halal default). Otherwise perps.
        Assets are filtered by `min_daily_volume_usd` using the asset contexts
        that ship alongside the metadata, so an illiquid listing never enters
        the universe in the first place.
        """
        await self.limiter.acquire(20)
        names: list[str] = []

        if self.cfg.spot_mode:
            spot_meta, ctxs = await asyncio.to_thread(self.info.spot_meta_and_asset_ctxs)
            tokens = {t["index"]: t for t in spot_meta["tokens"]}
            for pair, ctx in zip(spot_meta["universe"], ctxs):
                base = tokens[pair["tokens"][0]]
                day_ntl = float(ctx.get("dayNtlVlm", 0) or 0)
                if day_ntl < self.cfg.min_daily_volume_usd:
                    continue
                # `name` is what Info/Exchange expect for spot: "@{index}" or the
                # canonical pair name. The SDK resolves both via name_to_asset.
                name = pair["name"]
                self.assets[name] = AssetMeta(
                    name=name, sz_decimals=int(base["szDecimals"]),
                    is_spot=True, pair_name=name,
                )
                names.append(name)
        else:
            meta, ctxs = await asyncio.to_thread(self.info.meta_and_asset_ctxs)
            for a, ctx in zip(meta["universe"], ctxs):
                if a.get("isDelisted"):
                    continue
                if float(ctx.get("dayNtlVlm", 0) or 0) < self.cfg.min_daily_volume_usd:
                    continue
                self.assets[a["name"]] = AssetMeta(
                    name=a["name"], sz_decimals=int(a["szDecimals"]),
                    is_spot=False, max_leverage=int(a.get("maxLeverage", 1)),
                )
                names.append(a["name"])

        if self.cfg.universe:
            allow = set(self.cfg.universe)
            names = [n for n in names if n in allow]

        log.info("universe: %d assets (%s mode)", len(names),
                 "spot" if self.cfg.spot_mode else "perp")
        return names

    # -- REST ---------------------------------------------------------------

    async def fetch_candles(self, name: str, interval: str, bars: int) -> list[Candle]:
        """
        Backfill candle history. HL caps a snapshot at ~5000 bars (study #16),
        so a larger request is paginated rather than silently truncated.
        """
        step = INTERVAL_MS[interval]
        end = int(time.time() * 1000)
        out: list[Candle] = []
        remaining = bars

        while remaining > 0:
            take = min(remaining, MAX_CANDLES_PER_REQUEST)
            start = end - take * step
            await self.limiter.acquire(2)
            try:
                raw = await asyncio.to_thread(
                    self.info.candles_snapshot, name, interval, start, end)
            except Exception as e:
                log.warning("candles %s %s failed: %s", name, interval, e)
                break
            if not raw:
                break
            out = [Candle.from_wire(c) for c in raw] + out
            if len(raw) < take:
                break                      # asset has no deeper history
            end = int(raw[0]["t"])
            remaining -= len(raw)

        out.sort(key=lambda c: c.ts)
        # Drop the still-forming last candle. The repo has shipped studies that
        # kept it and effectively read the future (study #16).
        now_bucket = (int(time.time() * 1000) // step) * step
        out = [c for c in out if c.ts < now_bucket]

        key = (name, interval)
        self.candles[key] = deque(out[-1000:], maxlen=1000)
        return list(self.candles[key])

    async def fetch_funding(self, name: str, samples: int) -> list[tuple[int, float]]:
        """Hourly funding history as (timestamp_ms, rate) — perp assets only."""
        start = int(time.time() * 1000) - samples * INTERVAL_MS["1h"]
        await self.limiter.acquire(2)
        try:
            raw = await asyncio.to_thread(self.info.funding_history, name, start)
        except Exception as e:
            log.warning("funding %s failed: %s", name, e)
            return []
        rows = sorted(((int(r["time"]), float(r["fundingRate"])) for r in raw),
                      key=lambda x: x[0])
        self.funding[name] = deque(rows, maxlen=500)
        return rows

    async def fetch_book(self, name: str) -> OrderBook | None:
        await self.limiter.acquire(2)
        try:
            raw = await asyncio.to_thread(self.info.l2_snapshot, name)
        except Exception as e:
            log.warning("l2 %s failed: %s", name, e)
            return None
        book = _book_from_wire(raw)
        self.books[name] = book
        return book

    async def refresh_mids(self) -> dict[str, float]:
        await self.limiter.acquire(2)
        try:
            raw = await asyncio.to_thread(self.info.all_mids)
            self.mids = {k: float(v) for k, v in raw.items()}
        except Exception as e:
            log.warning("all_mids failed: %s", e)
        return self.mids

    # -- WebSocket ----------------------------------------------------------

    def _emit(self, fn: Callable[..., None], *args: Any) -> None:
        """Hand a WS-thread message to the asyncio loop. Never blocks the socket."""
        self._last_msg_ts = time.monotonic()
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        try:
            loop.call_soon_threadsafe(fn, *args)
        except RuntimeError:
            pass                                     # loop shutting down

    def _on_candle(self, msg: dict[str, Any]) -> None:
        d = msg.get("data") or {}
        if not d:
            return
        name, interval = d.get("s"), d.get("i")
        # A candle message repeats for the forming bar; it is only final once
        # the close time has passed. Store closed bars only.
        closed = int(d.get("T", 0)) <= int(time.time() * 1000)
        self._emit(self._store_candle, name, interval, Candle.from_wire(d, closed))

    def _store_candle(self, name: str, interval: str, c: Candle) -> None:
        if not c.closed:
            return
        dq = self.candles[(name, interval)]
        if dq and dq[-1].ts == c.ts:
            dq[-1] = c
        elif not dq or c.ts > dq[-1].ts:
            dq.append(c)

    def _on_trades(self, msg: dict[str, Any]) -> None:
        for t in msg.get("data") or []:
            self._emit(self._store_trade, t.get("coin"),
                       Trade(ts=int(t["time"]), px=float(t["px"]),
                             sz=float(t["sz"]), side=t["side"]))

    def _store_trade(self, coin: str, t: Trade) -> None:
        self.trades[coin].append(t)

    def _on_book(self, msg: dict[str, Any]) -> None:
        d = msg.get("data") or {}
        if d.get("coin"):
            self._emit(self._store_book, d["coin"], _book_from_wire(d))

    def _store_book(self, coin: str, b: OrderBook) -> None:
        self.books[coin] = b

    async def subscribe_symbol(self, name: str, intervals: list[str]) -> None:
        """Subscribe candles for each interval, plus trades and L2 for Module A."""
        subs: list[dict] = [{"type": "trades", "coin": name},
                            {"type": "l2Book", "coin": name}]
        subs += [{"type": "candle", "coin": name, "interval": iv} for iv in intervals]
        cbs = [self._on_trades, self._on_book] + [self._on_candle] * len(intervals)
        for sub, cb in zip(subs, cbs):
            try:
                sid = await asyncio.to_thread(self.info.subscribe, sub, cb)
                with self._ws_lock:
                    self._subs.append((sub, sid))
            except Exception as e:
                log.warning("subscribe %s %s failed: %s", name, sub["type"], e)

    async def watchdog(self, stale_after: float = 90.0) -> None:
        """
        Reconnect if the socket goes quiet.

        A silent WS is the dangerous failure: signals keep computing on stale
        candles and look perfectly healthy. If nothing has arrived in
        `stale_after` seconds we tear down, reconnect, and REST-backfill every
        series so no module sees a gap it would read as a flat market.
        """
        while not self._closed:
            await asyncio.sleep(15)
            quiet = time.monotonic() - self._last_msg_ts
            if quiet < stale_after:
                continue
            log.error("websocket stale for %.0fs — reconnecting", quiet)
            try:
                await asyncio.to_thread(self.info.disconnect_websocket)
            except Exception:
                pass
            try:
                self.info = await asyncio.to_thread(
                    Info, self.cfg.api.base_url, False)
                with self._ws_lock:
                    old, self._subs = list(self._subs), []
                names = {s["coin"] for s, _ in old if "coin" in s}
                intervals = sorted({s["interval"] for s, _ in old if "interval" in s})
                for n in names:
                    await self.subscribe_symbol(n, intervals)
                    for iv in intervals:
                        await self.fetch_candles(n, iv, 300)
                self._last_msg_ts = time.monotonic()
                log.info("reconnected: %d symbols resubscribed + backfilled", len(names))
            except Exception as e:
                log.exception("reconnect failed: %s", e)

    async def close(self) -> None:
        self._closed = True
        try:
            await asyncio.to_thread(self.info.disconnect_websocket)
        except Exception:
            pass


def _book_from_wire(raw: dict[str, Any]) -> OrderBook:
    levels = raw.get("levels") or [[], []]
    def side(rows: list[dict]) -> list[BookLevel]:
        return [BookLevel(float(r["px"]), float(r["sz"]), int(r.get("n", 0))) for r in rows]
    return OrderBook(ts=int(raw.get("time", 0)), bids=side(levels[0]), asks=side(levels[1]))

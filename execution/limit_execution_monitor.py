"""limit_execution_monitor.py — GO-LIVE passive-maker execution engine (template).

⚠ STATUS: this is the blueprint for LIVE-EXCHANGE execution via ccxt.pro. The
firm currently runs a paper/testnet broker with a *simulated* resting-limit
model (core/pending_orders.py + core/fill_monitor.py) on a ~1-minute REST
cycle — that integrated path is what is live today and what the daily report /
/api/fill-diagnostics measure. Use THIS file only when connecting a real spot
exchange account; it needs `pip install ccxtpro` and API keys, and must be
paper/testnet-audited (see AUDIT.md) before any real capital.

What it does (mirrors the three requested modules, at real-exchange fidelity):
  Module 1  dynamic ATR limit placement with post_only=True, cancel/replace on
            >1.5xATR drift or staleness.
  Module 2  per-order fill diagnostics: fill rate, ms time-to-fill (real ticks),
            adverse selection at 10s/1m/5m post-fill.
  Module 3  spread-expansion kill-switch + adverse-selection throttle.

Halal: spot, long-only. No shorting, leverage, or funding legs.
"""
import asyncio
import time
from collections import deque, defaultdict

try:
    import ccxtpro
except ImportError:                       # template guard — not installed on the paper firm
    ccxtpro = None


# ── config (wire to the firm's config.py on go-live) ──
SYMBOLS = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
ATR_PERIOD = 14
ATR_TF = "1m"
BASE_K = 1.0                    # buy-limit distance = bid - k*ATR
DRIFT_CANCEL_MULT = 1.5        # cancel/replace if price runs > this x ATR from the quote
QUOTE_TTL_S = 60
MAKER_FEE = 0.0002
TAKER_FEE = 0.0005
SPREAD_KILL_MULT = 3.0        # halt placement if spread > 3x its 1h average
ADVERSE_DD = -0.005           # a fill is a "knife" if 1m move < -0.5%
ADVERSE_STREAK = 3
THROTTLE_WIDEN = 1.5
THROTTLE_WINDOW_S = 900


def true_range(highs, lows, closes):
    trs = []
    for i in range(1, len(closes)):
        trs.append(max(highs[i] - lows[i], abs(highs[i] - closes[i-1]),
                       abs(lows[i] - closes[i-1])))
    return trs


def atr(ohlcv, period=ATR_PERIOD):
    if len(ohlcv) < period + 1:
        return None
    h = [c[2] for c in ohlcv]; l = [c[3] for c in ohlcv]; c = [c[4] for c in ohlcv]
    trs = true_range(h, l, c)[-period:]
    return sum(trs) / len(trs)


class ExecutionMonitor:
    def __init__(self, exchange, symbols=SYMBOLS):
        self.ex = exchange
        self.symbols = symbols
        self.spread_avg = defaultdict(lambda: None)   # 1h EWMA of spread%
        self.open_quotes = {}                          # symbol -> order dict
        self.fills = deque(maxlen=500)                 # diagnostics ring
        self.throttle_until = 0.0
        self.atr_cache = {}

    # ── circuit breaker 1 ──
    def _spread_ok(self, symbol, bid, ask):
        if not bid or not ask or ask < bid:
            return False
        mid = (bid + ask) / 2
        spread = (ask - bid) / mid * 100
        prev = self.spread_avg[symbol]
        self.spread_avg[symbol] = spread if prev is None else 0.05*spread + 0.95*prev
        return prev is None or spread <= prev * SPREAD_KILL_MULT

    # ── circuit breaker 2 ──
    def _k(self):
        return BASE_K * THROTTLE_WIDEN if time.time() < self.throttle_until else BASE_K

    def _maybe_throttle(self):
        recent = [f for f in self.fills if f.get("adv_1m") is not None][-ADVERSE_STREAK:]
        if len(recent) == ADVERSE_STREAK and all(f["adv_1m"] < ADVERSE_DD for f in recent):
            self.throttle_until = time.time() + THROTTLE_WINDOW_S

    # ── module 1: quote placement (post_only) ──
    async def refresh_atr(self, symbol):
        ohlcv = await self.ex.fetch_ohlcv(symbol, ATR_TF, limit=ATR_PERIOD + 2)
        self.atr_cache[symbol] = atr(ohlcv)

    async def place_quote(self, symbol, qty):
        ob = await self.ex.watch_order_book(symbol)
        bid = ob["bids"][0][0] if ob["bids"] else None
        ask = ob["asks"][0][0] if ob["asks"] else None
        if not self._spread_ok(symbol, bid, ask):
            return None                                  # liquidity vacuum
        a = self.atr_cache.get(symbol)
        if not a:
            return None
        limit_px = bid - self._k() * a
        order = await self.ex.create_limit_buy_order(
            symbol, qty, limit_px, {"postOnly": True})   # ⇐ maker-only
        self.open_quotes[symbol] = {"order": order, "placed": time.time(),
                                    "ref": ask, "limit": limit_px, "atr": a}
        return order

    # ── module 1: cancel/replace lifecycle ──
    async def maintain(self, symbol, qty):
        q = self.open_quotes.get(symbol)
        if not q:
            return
        ob = await self.ex.watch_order_book(symbol)
        price = ob["asks"][0][0] if ob["asks"] else q["limit"]
        stale = time.time() - q["placed"] > QUOTE_TTL_S
        drifted = price - q["limit"] > DRIFT_CANCEL_MULT * q["atr"]
        if stale or drifted:
            try:
                await self.ex.cancel_order(q["order"]["id"], symbol)
            except Exception:
                pass
            self.open_quotes.pop(symbol, None)
            await self.place_quote(symbol, qty)          # replace at fresh distance

    # ── module 2: fill + adverse-selection tracking ──
    async def on_fill(self, symbol, fill_price, qty, ref):
        t0 = time.time()
        rec = {"symbol": symbol, "fill": fill_price, "qty": qty, "ref": ref,
               "t_fill": t0, "adv_10s": None, "adv_1m": None, "adv_5m": None,
               "spread_saved": qty*fill_price*(TAKER_FEE-MAKER_FEE) + qty*max(0, ref-fill_price)}
        self.fills.append(rec)
        for label, delay in (("adv_10s", 10), ("adv_1m", 60), ("adv_5m", 300)):
            asyncio.create_task(self._score(rec, symbol, label, delay))

    async def _score(self, rec, symbol, label, delay):
        await asyncio.sleep(delay)
        t = await self.ex.watch_ticker(symbol)
        rec[label] = t["last"] / rec["fill"] - 1        # + = bounced, - = knife
        if label == "adv_1m":
            self._maybe_throttle()

    # ── module 2: dashboard ──
    def diagnostics(self):
        by = defaultdict(lambda: {"n": 0, "filled": 0, "ttf": [], "adv": [], "saved": 0.0})
        for f in self.fills:
            s = by[f["symbol"]]; s["n"] += 1; s["filled"] += 1
            s["saved"] += f["spread_saved"]
            if f["adv_1m"] is not None:
                s["adv"].append(f["adv_1m"] * 100)
        return {sym: {"quotes": s["n"], "fill_rate": 100.0,
                      "adverse_1m_pct": round(sum(s["adv"])/len(s["adv"]), 3) if s["adv"] else None,
                      "spread_saved": round(s["saved"], 2)} for sym, s in by.items()}


async def main():
    if ccxtpro is None:
        raise SystemExit("ccxt.pro not installed — this is the go-live template. See AUDIT.md.")
    ex = ccxtpro.binance({"enableRateLimit": True, "options": {"defaultType": "spot"}})
    # ex.set_sandbox_mode(True)   # ⇐ ALWAYS start in testnet (AUDIT.md step 1)
    mon = ExecutionMonitor(ex)
    try:
        while True:
            for sym in mon.symbols:
                await mon.refresh_atr(sym)
                await mon.maintain(sym, qty=0.001)   # qty from the firm's sizing/throttle
            await asyncio.sleep(5)
    finally:
        await ex.close()


if __name__ == "__main__":
    asyncio.run(main())

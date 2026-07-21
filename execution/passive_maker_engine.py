"""passive_maker_engine.py — go-live passive-maker spot execution engine (template).

⚠ HONESTY (read before trusting the "alpha" label): Module 1's cross-sectional
momentum tilt is NOT a validated edge. research/cross_sectional_2026_07 tested
exactly this — 14-day relative strength, top-K on a spot majors universe — and
found NO selection skill: top-K never beat a random-K null, and the long-short
spread was weak and flipped sign out of sample. So this engine's real value is
Modules 2-4 (passive maker execution, fill diagnostics, hard risk controls),
which research/limit_exec_2026_07 DID validate (~16 bps/trade less execution
drag). The cross-sectional ranking here is a neutral, cheap, halal rule for
deciding WHAT to hold — judge the engine on spread-saved and adverse-selection,
never on beating the market.

⚠ STATUS: blueprint for a REAL ccxt.pro spot exchange. The firm currently runs a
paper broker; this needs real/testnet API keys and full staging (DEPLOY.md)
before any capital. Long-only spot, halal: NO shorts, NO leverage, NO funding.
"""
import asyncio
import time
from collections import defaultdict, deque

try:
    import ccxt.pro as ccxtpro
except ImportError:                       # template guard — not installed on the paper firm
    ccxtpro = None


# ── configuration ──
UNIVERSE_SIZE = 10          # top-N spot majors by 24h volume (fetched at runtime)
TOP_K = 3                   # overweight the top-K momentum names; rest held in cash
MOM_LOOKBACK_D = 14         # cross-sectional relative-strength window
ATR_TF = "5m"
ATR_PERIOD = 14
ENTRY_K = 0.5              # buy limit at bid - ENTRY_K * ATR
CANCEL_AFTER_S = 45        # order lifecycle timeout
CANCEL_DRIFT_MULT = 1.0    # cancel/replace if price runs > this x ATR from the quote
MAKER_FEE = 0.0002         # 0.02% paid on a passive fill
TAKER_FEE = 0.0008         # 0.08%/side that a market order would have paid
ADVERSE_DD = -0.005        # a fill is a "knife" if the 60s move is below this
ADVERSE_WIDEN = 1.25       # widen ENTRY_K by 25% after a knife fill
DAILY_KILL_PCT = -1.5      # halt + cancel-all if 24h equity change breaches this
SPREAD_KILL_MULT = 2.5     # pause placement if spread > this x its 1h average

STABLES = {"USDT", "USDC", "DAI", "TUSD", "FDUSD", "USDD", "BUSD"}


# ── indicators (stdlib) ──
def atr(ohlcv, period=ATR_PERIOD):
    if len(ohlcv) < period + 1:
        return None
    trs = []
    for i in range(1, len(ohlcv)):
        h, l, pc = ohlcv[i][2], ohlcv[i][3], ohlcv[i - 1][4]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs[-period:]) / period


def rel_strength(closes):
    """14-day return; the cross-sectional ranking key (NOT alpha — see header)."""
    if len(closes) < MOM_LOOKBACK_D + 1:
        return None
    return closes[-1] / closes[-1 - MOM_LOOKBACK_D] - 1


class PassiveMakerEngine:
    def __init__(self, exchange, capital_usd):
        self.ex = exchange
        self.capital = capital_usd
        self.universe = []
        self.targets = []                       # top-K symbols to hold
        self.k = defaultdict(lambda: ENTRY_K)   # per-symbol offset (widens on adverse fills)
        self.spread_avg = {}                    # 1h EWMA of spread %
        self.open_orders = {}                   # symbol -> {id, limit, atr, placed}
        self.fills = deque(maxlen=1000)
        self.quotes_placed = 0
        self.quotes_cancelled = 0
        self.equity_24h = deque()               # (ts, equity) for the daily kill switch
        self.halted = False

    # ── Module 1: cross-sectional universe ranking (allocation, not alpha) ──
    async def rank_universe(self):
        tickers = await self.ex.fetch_tickers()
        spot = [(s, t.get("quoteVolume") or 0) for s, t in tickers.items()
                if s.endswith("/USDT") and s.split("/")[0] not in STABLES]
        spot.sort(key=lambda x: -x[1])
        self.universe = [s for s, _ in spot[:UNIVERSE_SIZE]]
        scored = []
        for sym in self.universe:
            ohlcv = await self.ex.fetch_ohlcv(sym, "1d", limit=MOM_LOOKBACK_D + 2)
            rs = rel_strength([c[4] for c in ohlcv])
            if rs is not None:
                scored.append((sym, rs))
        scored.sort(key=lambda x: -x[1])
        self.targets = [s for s, _ in scored[:TOP_K]]      # long-only: bottom names get 0 (no shorting)
        return self.targets

    def target_qty(self, symbol, price):
        """Equal-weight the deployed capital across the top-K, spot/long-only."""
        if symbol not in self.targets or price <= 0:
            return 0.0
        return (self.capital / TOP_K) / price

    # ── Module 4: risk controls ──
    def _spread_ok(self, symbol, bid, ask):
        if not bid or not ask or ask <= bid:
            return False
        mid = (bid + ask) / 2
        spread = (ask - bid) / mid * 100
        prev = self.spread_avg.get(symbol)
        self.spread_avg[symbol] = spread if prev is None else 0.02 * spread + 0.98 * prev
        return prev is None or spread <= prev * SPREAD_KILL_MULT

    async def check_daily_kill(self, equity):
        now = time.time()
        self.equity_24h.append((now, equity))
        while self.equity_24h and now - self.equity_24h[0][0] > 86400:
            self.equity_24h.popleft()
        start = self.equity_24h[0][1] if self.equity_24h else equity
        if start > 0 and (equity - start) / start * 100 <= DAILY_KILL_PCT and not self.halted:
            self.halted = True
            await self.cancel_all()
            print(f"[KILL SWITCH] 24h equity {((equity-start)/start*100):.2f}% <= {DAILY_KILL_PCT}% — halted.")

    # ── Module 2: passive maker placement with post_only enforcement ──
    async def place_quote(self, symbol):
        if self.halted:
            return None
        ob = await self.ex.watch_order_book(symbol)
        bid = ob["bids"][0][0] if ob["bids"] else None
        ask = ob["asks"][0][0] if ob["asks"] else None
        if not self._spread_ok(symbol, bid, ask):
            return None                                   # Module 4: spread expansion filter
        ohlcv = await self.ex.fetch_ohlcv(symbol, ATR_TF, limit=ATR_PERIOD + 2)
        a = atr(ohlcv)
        if not a:
            return None
        limit_px = bid - self.k[symbol] * a
        # post_only enforcement: a passive buy MUST rest below the bid. If the
        # computed quote would cross the book (>= ask), REJECT rather than pay
        # taker — the exchange's postOnly is the second guard.
        if limit_px >= ask:
            return None
        qty = self.target_qty(symbol, limit_px)
        if qty <= 0:
            return None
        order = await self.ex.create_limit_buy_order(
            symbol, qty, limit_px, {"postOnly": True})     # ⇐ maker-only
        self.open_orders[symbol] = {"id": order["id"], "limit": limit_px,
                                    "atr": a, "ref": ask, "placed": time.time()}
        self.quotes_placed += 1
        asyncio.create_task(self._lifecycle(symbol))
        return order

    # ── Module 2: cancel/replace lifecycle ──
    async def _lifecycle(self, symbol):
        q = self.open_orders.get(symbol)
        if not q:
            return
        while symbol in self.open_orders:
            await asyncio.sleep(1)
            q = self.open_orders.get(symbol)
            if not q:
                return
            try:
                o = await self.ex.fetch_order(q["id"], symbol)
            except Exception:
                continue
            if o["status"] == "closed" and o.get("filled"):
                await self._on_fill(symbol, o["average"] or q["limit"], o["filled"], q["ref"])
                self.open_orders.pop(symbol, None)
                return
            ob = await self.ex.watch_order_book(symbol)
            price = ob["asks"][0][0] if ob["asks"] else q["limit"]
            stale = time.time() - q["placed"] > CANCEL_AFTER_S
            drifted = price - q["limit"] > CANCEL_DRIFT_MULT * q["atr"]
            if stale or drifted:
                try:
                    await self.ex.cancel_order(q["id"], symbol)
                except Exception:
                    pass
                self.open_orders.pop(symbol, None)
                self.quotes_cancelled += 1
                await self.place_quote(symbol)             # recalculate & re-quote
                return

    # ── Module 3: fill diagnostics + adverse-selection feedback ──
    async def _on_fill(self, symbol, fill_price, qty, ref):
        rec = {"symbol": symbol, "fill": fill_price, "qty": qty, "t": time.time(),
               "spread_saved": qty * fill_price * (TAKER_FEE - MAKER_FEE)
               + qty * max(0.0, ref - fill_price), "adverse_60s": None}
        self.fills.append(rec)
        asyncio.create_task(self._score_adverse(rec, symbol))

    async def _score_adverse(self, rec, symbol):
        await asyncio.sleep(60)
        t = await self.ex.watch_ticker(symbol)
        move = t["last"] / rec["fill"] - 1
        rec["adverse_60s"] = move
        if move < ADVERSE_DD:                              # knife -> widen this symbol's offset 25%
            self.k[symbol] *= ADVERSE_WIDEN
            print(f"[ADVERSE] {symbol} {move*100:.2f}% 60s post-fill — widening k to {self.k[symbol]:.2f}")

    async def cancel_all(self):
        for symbol, q in list(self.open_orders.items()):
            try:
                await self.ex.cancel_order(q["id"], symbol)
            except Exception:
                pass
            self.open_orders.pop(symbol, None)

    # ── Module 3: diagnostics snapshot (dashboard reads this) ──
    def diagnostics(self):
        n = self.quotes_placed
        filled = len(self.fills)
        adv = [f["adverse_60s"] * 100 for f in self.fills if f["adverse_60s"] is not None]
        return {
            "quotes_placed": n, "filled": filled, "cancelled": self.quotes_cancelled,
            "fill_rate_pct": round(filled / n * 100, 1) if n else 0,
            "net_spread_saved_usd": round(sum(f["spread_saved"] for f in self.fills), 2),
            "adverse_60s_pct": round(sum(adv) / len(adv), 3) if adv else None,
            "targets": self.targets, "halted": self.halted,
            "offsets": {s: round(k, 2) for s, k in self.k.items() if k != ENTRY_K},
        }


async def main():
    if ccxtpro is None:
        raise SystemExit("ccxt.pro not installed — this is the go-live template. See DEPLOY.md.")
    import json
    ex = ccxtpro.binance({"enableRateLimit": True, "options": {"defaultType": "spot"}})
    # ex.set_sandbox_mode(True)   # ⇐ ALWAYS start in testnet (DEPLOY.md step 1)
    eng = PassiveMakerEngine(ex, capital_usd=1000.0)      # small staging capital
    last_rebal = 0.0
    try:
        while True:
            now = time.time()
            if now - last_rebal > 86400:                   # Module 1: daily rebalance
                await eng.rank_universe()
                last_rebal = now
            bal = await ex.fetch_balance()
            await eng.check_daily_kill(bal["total"].get("USDT", eng.capital))
            for sym in eng.targets:
                if sym not in eng.open_orders and not eng.halted:
                    await eng.place_quote(sym)
            json.dump(eng.diagnostics(), open("engine_diagnostics.json", "w"), indent=1)
            await asyncio.sleep(5)
    finally:
        await eng.cancel_all()
        await ex.close()


if __name__ == "__main__":
    asyncio.run(main())

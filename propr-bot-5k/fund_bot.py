#!/usr/bin/env python3
"""
Funding-momentum challenge runner (LOCAL DEPLOY).

    python fund_bot.py --once            # dry run: one cycle, log the plan
    python fund_bot.py                    # dry run, loop
    python fund_bot.py --live             # place real orders

WHY THIS STRATEGY
-----------------
A 200-day walk-forward on Hyperliquid 1h data (50 majors) showed the old
long-only price-momentum signal has no edge, but a *funding* signal does:

  Rank the universe by TRAILING FUNDING (K hours). Long the top-q names,
  equal-weight. This beat every price-momentum / reversal / carry variant
  tested, and the effect was monotone across lookbacks and robust to fees.

Measured forward-return spread, top vs bottom funding quintile:
    24h  +0.36%   72h +1.14%   168h +2.48%   336h +3.47%
Price-momentum's spread was ~0. The edge is funding-implied CONTINUATION
(high funding = sustained demand), not the carry itself.

Challenge profile (33 rolling 30d windows, halts on, K=336 q=10 gross=1.0):
    pass 21/33 (+10%), breach 3/33, mean +4.7% per window.

PORTFOLIO RULES
  - Long only (no short execution path on this account) -- top-q by funding.
  - Weekly rebalance (168h): close names that left the top-q, open new ones.
  - Each position carries an exchange-side stop (auto-sized to the gate) and a
    take-profit at tp_mult x the stop distance, so both exits survive a
    process death. Portfolio HALTS still protect the account as a whole:
        daily  : flatten if equity <= day_start*(1 - daily_halt)
        dd     : flatten + stop if equity <= start*(1 - dd_halt)
        target : stop when equity >= start*(1 + target)
  - News halo blocks NEW entries during scheduled macro events.

Reads .env next to this file (same as bot.py).
"""
import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

import sim
import notify
import news
from propr import ProprConfig, ProprRiskClient, ProprAPIError

log = logging.getLogger("fundbot")

HERE = Path(__file__).parent
CACHE = HERE / "cache"
FUND_PATH = CACHE / "funding.pkl"
STATE_PATH = CACHE / "fund_state.json"
ONE_M = Path(r"C:\Users\DELL\OneDrive\1m")

# Load the deployment's .env BEFORE any account/start constant is evaluated.
# Previously these constants were read at import time with beta defaults and
# the .env was loaded later in main(), so a direct run (preflight, --once,
# manual restart) could target the wrong account with the wrong starting
# balance. The watchdog happened to inherit env, which hid the bug.
try:
    from dotenv import load_dotenv
    load_dotenv(HERE / ".env", override=True)
    if not os.getenv("PROPR_API_KEY"):
        load_dotenv(ONE_M / ".env")
except ImportError:
    pass

ACCOUNT_ID = os.getenv("PROPR_ACCOUNT_ID", "urn:prp-account:LDA3R3vW74Ym")
CHALLENGE_START = float(os.getenv("CHALLENGE_START", "1000.0"))
TOKEN_PREFIX = os.getenv("FUND_TOKEN_PREFIX", "pk_beta_")

SCAN_SEC = 300
SEED_DAYS = 18            # fundingHistory seed window (> K hours of margin)
MIN_HIST_H = 72           # min finite funding hours to rank a coin
# Rate limits removed - chase filter + limit orders + trailing stops provide sufficient protection
ENTRY_MAX_PER_30MIN = 999999
ENTRY_MAX_PER_6H = 999999
TRAIL_ACTIVATE = 0.08     # arm the trailing stop once peak >= entry*(1+this)
TRAIL_DISTANCE = 0.12     # once armed, stop = peak*(1-this). Ratchets up only.

# Entry timing filters
CHASE_MAX_24H_RET = 0.08      # skip if already up >8% in last 24h
CHASE_MAX_ABOVE_EMA = 0.05   # skip if >5% above 4H 20-EMA
USE_LIMIT_ENTRIES = True
LIMIT_OFFSET_BPS = 3         # place limit 3bps below mid
LIMIT_TIMEOUT_S = 12         # wait up to 12s for fill
LIMIT_FALLBACK_MARKET = True # if unfilled, fall back to market (conservative)
MAX_ENTRY_SLIP_BPS = 25.0    # re-read the book at timeout; crossing more than this is a chase, not a fill
OVERSIZE_TOL = 1.35          # trim a fresh position once it exceeds 1.35x slot
ENTRY_SANITY_HOURS = 2       # ...only within this window after entry


# --------------------------------------------------------------------------
# funding data
# --------------------------------------------------------------------------
def _now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def fetch_funding_history(coin: str, days: int) -> pd.Series:
    """Hourly funding for the last `days` via pagination (500 rows/call)."""
    now = _now_ms()
    start = now - days * 86400 * 1000
    rows, cur = [], start
    for _ in range(40):
        d = sim.hl_post({"type": "fundingHistory", "coin": coin,
                         "startTime": cur, "endTime": now})
        if not d:
            break
        rows.extend(d)
        last = d[-1]["time"]
        if len(d) < 500 or last >= now - 3600 * 1000:
            break
        cur = last + 3600 * 1000
        time.sleep(0.12)
    if not rows:
        return pd.Series(dtype=float)
    df = pd.DataFrame(rows)
    df["ts"] = pd.to_datetime(df["time"], unit="ms", utc=True)
    s = df.set_index("ts")["fundingRate"].astype(float).sort_index()
    return s[~s.index.duplicated(keep="last")]


def load_funding() -> dict:
    try:
        return pd.read_pickle(FUND_PATH)
    except Exception:
        return {}


def save_funding(F: dict) -> None:
    CACHE.mkdir(exist_ok=True)
    try:
        pd.to_pickle(F, FUND_PATH)
    except Exception as e:
        log.warning(f"could not persist funding cache: {e}")


def current_funding() -> dict:
    """One metaAndAssetCtxs call -> current hourly funding for every asset."""
    try:
        meta, ctxs = sim.hl_post({"type": "metaAndAssetCtxs"})
    except Exception as e:
        log.warning(f"metaAndAssetCtxs failed: {e}")
        return {}
    out = {}
    for u, c in zip(meta["universe"], ctxs):
        try:
            out[u["name"]] = float(c.get("funding", 0) or 0)
        except Exception:
            pass
    return out


def refresh_funding(universe: list[str], force: bool = False) -> dict:
    """Seed history if the cache is missing/stale, then append the current
    hour's funding (from one bulk call) so the trailing mean stays fresh."""
    F = load_funding()
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    newest = max((s.index[-1] for s in F.values() if len(s)), default=None)
    stale = (newest is None) or ((now - newest.to_pydatetime()).total_seconds()
                                 > 6 * 3600)
    if force or stale:
        log.info(f"seeding funding history ({len(universe)} coins, "
                 f"{SEED_DAYS}d)…")
        for i, c in enumerate(universe, 1):
            try:
                s = fetch_funding_history(c, SEED_DAYS)
            except Exception as e:
                log.warning(f"  funding {c}: {type(e).__name__} {e}")
                continue
            if len(s):
                F[c] = s
            time.sleep(0.12)
            if i % 10 == 0:
                log.info(f"  seeded {i}/{len(universe)}")
        save_funding(F)

    cur = current_funding()
    for c, r in cur.items():
        if c not in F or not len(F[c]):
            F[c] = pd.Series([r], index=pd.DatetimeIndex([now]))
        else:
            s = F[c]
            if s.index[-1] < now:
                F[c] = pd.concat([s, pd.Series([r], index=[now])])
    save_funding(F)
    return F


def trailing_funding(F: dict, universe: list[str], K: int) -> dict:
    now = datetime.now(timezone.utc)
    out = {}
    for c in universe:
        s = F.get(c)
        if s is None or not len(s):
            continue
        tail = s[s.index >= now - pd.Timedelta(hours=K + 12)]
        if tail.notna().sum() >= MIN_HIST_H:
            out[c] = float(tail.tail(K).mean())
    return out


# --------------------------------------------------------------------------
# runner
# --------------------------------------------------------------------------
def get_universe() -> list[str]:
    """Prefer the local candle cache (matches the backtest universe); fall back
    to a live top-N-by-volume query so the runner still works on a fresh host
    with no cache (e.g. a cloud container)."""
    try:
        if (sim.CACHE / "candles.pkl").exists():
            return sorted(sim.load())
    except Exception:
        pass
    return sorted(sim.universe())


class FundBot:
    def __init__(self, args):
        self.q = args.q
        self.K = args.K
        self.gross = args.gross
        self.rebal_h = args.rebal_h
        self.daily_halt = args.daily_halt
        self.dd_halt = args.dd_halt
        self.target_pct = args.target
        self.stop_target = args.stop
        self.tp_mult = args.tp_mult
        self.daily_wall = args.daily_wall
        self.dd_wall = args.dd_wall
        self.live = args.live
        self.universe = get_universe()
        self.start_equity = None
        self.day_start = None
        self._day = None
        self.last_rebal = 0.0
        self.halted_day = False
        self.entry_times: list[float] = []   # epoch seconds of recent LONG entries
        self.peaks: dict[str, float] = {}    # asset -> peak price since entry

        pcfg = ProprConfig(
            api_key=os.getenv("PROPR_API_KEY", ""),
            api_url=os.getenv("PROPR_API_URL", "https://api.propr.xyz/v1"),
            ws_url=os.getenv("PROPR_WS_URL", "wss://api.propr.xyz/ws"),
            rule_starting_balance=CHALLENGE_START,
            rule_profit_target=CHALLENGE_START * 0.10,
            rule_max_daily_loss_pct=self.daily_wall,
            rule_max_drawdown_pct=self.dd_wall,
        )
        pcfg.max_daily_loss_pct = self.daily_halt
        pcfg.max_monthly_dd_pct = self.dd_halt
        pcfg.max_concurrent_positions = self.q * 2
        self.client = ProprRiskClient(pcfg)

        self._load_state()

    def seed_entry_times(self):
        """Rebuild the recent-entry log from the venue so a restart cannot
        forget entries it made just before dying. Without this the rate cap
        resets to empty on every watchdog respawn, which is exactly when a
        burst is most likely."""
        try:
            trades = self.client.sdk.get_trades(limit=100)
        except Exception as e:
            log.warning(f"entry-seed: cannot read trades: {e}")
            return
        seen = set(self.entry_times)
        added = 0
        for t in trades:
            if t.get("type") != "open" or t.get("side") not in ("buy", None):
                continue
            ts = t.get("executedAt") or t.get("createdAt")
            try:
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except Exception:
                continue
            ep = dt.timestamp()
            if ep not in seen and (time.time() - ep) < 86400:
                self.entry_times.append(ep)
                seen.add(ep)
                added += 1
        if added:
            log.info(f"entry-seed: recovered {added} recent entry(ies) from venue")
            self._save_state()

    def _load_state(self):
        try:
            d = json.loads(STATE_PATH.read_text())
            self.last_rebal = float(d.get("last_rebal", 0.0))
            self.entry_times = [float(t) for t in d.get("entry_times", [])]
            self.peaks = {str(k): float(v) for k, v in d.get("peaks", {}).items()}
        except Exception:
            pass

    def _save_state(self):
        CACHE.mkdir(exist_ok=True)
        try:
            STATE_PATH.write_text(json.dumps(
                {"last_rebal": self.last_rebal,
                 "entry_times": self.entry_times,
                 "peaks": self.peaks}))
        except Exception as e:
            log.warning(f"state save failed: {e}")

    # -- entry rate limits -------------------------------------------------
    # Cap how fast the book fills so a rebalance cannot spray 5 orders at once
    # (rate-limit rejections, slippage, and an unauditable burst). Only new
    # LONG entries count; stops, take-profits, rotations and halts are exempt.
    def _recent_entries(self, window_s: float) -> list[float]:
        now = time.time()
        self.entry_times = [t for t in self.entry_times if now - t < 86400]
        return [t for t in self.entry_times if now - t < window_s]

    def entry_allowed(self) -> tuple[bool, str]:
        """(ok, why). ENTRY_MAX_PER_30MIN / ENTRY_MAX_PER_6H applied to opens."""
        now = time.time()
        last30 = self._recent_entries(1800)
        if len(last30) >= ENTRY_MAX_PER_30MIN:
            wait = 1800 - (now - min(last30))
            return False, (f"rate cap {ENTRY_MAX_PER_30MIN}/30min "
                           f"(next in {wait/60:.1f}min)")
        last6h = self._recent_entries(6 * 3600)
        if len(last6h) >= ENTRY_MAX_PER_6H:
            wait = 6 * 3600 - (now - min(last6h))
            return False, (f"rate cap {ENTRY_MAX_PER_6H}/6h "
                           f"(next in {wait/60:.1f}min)")
        return True, ""

    def record_entry(self):
        self.entry_times.append(time.time())
        self._save_state()

    # -- trailing stop -----------------------------------------------------
    # The fixed entry stop is placed at entry. Once a position's peak reaches
    # +TRAIL_ACTIVATE above entry, the stop is raised to peak*(1-TRAIL_DISTANCE)
    # and kept there as the peak advances. It only ever ratchets UP, so it can
    # never widen risk; a failed replace leaves the old (lower) stop in place.
    def manage_trails(self):
        try:
            positions = [p for p in self.client.get_open_positions()
                         if float(p.get("quantity", 0) or 0) > 0]
        except Exception as e:
            log.warning(f"trail: cannot read positions: {e}")
            return
        live = {p["base"] for p in positions}
        for gone in [a for a in self.peaks if a not in live]:
            del self.peaks[gone]                      # position closed/rotated
        for p in positions:
            a = p["base"]
            try:
                entry = float(p.get("entryPrice", 0) or 0)
                mark = float(p.get("markPrice", 0) or 0)
            except Exception:
                entry = mark = 0.0
            if entry <= 0 or mark <= 0:
                continue
            peak = max(self.peaks.get(a, entry), mark, entry)
            self.peaks[a] = peak
            stop = self.client.resting_stop(a)
            if self.live and stop is None:
                # Naked position — late fill, crash mid-entry, or a lost leg.
                # Rebuild both legs from the real entry price before anything
                # else; an unprotected name is the one loss the walls can't cap.
                sp = entry * (1 - self.stop_target)
                tp = entry * (1 + self.stop_target * self.tp_mult)
                log.warning(f"NAKED {a}: no resting stop — placing "
                            f"${sp:,.4f} / tp ${tp:,.4f}")
                self._ensure_protected(a, sp, tp)
                stop = self.client.resting_stop(a)
            if peak < entry * (1 + TRAIL_ACTIVATE):
                continue                              # not armed yet
            target = peak * (1 - TRAIL_DISTANCE)
            cur = float(stop.get("triggerPrice", 0) or 0) if stop else 0.0
            if target <= cur:
                continue                              # already at/above target
            log.info(f"TRAIL {a}: peak ${peak:,.4f} "
                     f"(+{peak/entry-1:.1%}) -> stop ${cur:,.4f} => ${target:,.4f}")
            if self.live:
                if self.client.replace_stop(a, round(target, 6)):
                    notify.send("TRAIL", f"{a} stop raised",
                                [f"peak +{peak/entry-1:.1%}, "
                                 f"stop ${target:,.4f} ({-TRAIL_DISTANCE:.0%})"])
        self._save_state()

    def manage_sizing(self, equity: float):
        """Cut a *fresh* position that is materially bigger than its slot.

        A limit fill that races its market fallback leaves a name at 2x. Stops
        never cap that: the extra notional sits outside the risk budget and is
        what turns a normal -20% stop into a wall breach. Only recent entries
        are checked so a genuine momentum winner is never cut short.
        """
        if not self.live:
            return
        per = equity * self.gross / self.q
        cutoff = datetime.now(timezone.utc) - timedelta(hours=ENTRY_SANITY_HOURS)
        try:
            positions = [p for p in self.client.get_open_positions()
                         if float(p.get("quantity", 0) or 0) > 0]
        except Exception as e:
            log.warning(f"sizing: cannot read positions: {e}")
            return
        for p in positions:
            a = p["base"]
            try:
                q = float(p.get("quantity"))
                mark = float(p.get("markPrice"))
                opened = datetime.fromisoformat(
                    str(p.get("createdAt")).replace("Z", "+00:00"))
            except Exception:
                continue
            if mark <= 0 or opened < cutoff:
                continue
            notional = q * mark
            if notional <= per * OVERSIZE_TOL:
                continue
            sell_q = (notional - per) / mark
            log.warning(f"TRIM {a}: ${notional:,.0f} vs ${per:,.0f} slot "
                        f"— selling {sell_q:.6f}")
            try:
                self.client.sdk.market_sell(a, f"{sell_q:.6f}")
            except Exception as e:
                log.warning(f"  TRIM {a} failed: {e}")
        self._save_state()

    # -- portfolio --------------------------------------------------------
    def held(self) -> set[str]:
        try:
            return {p["base"] for p in self.client.get_open_positions()}
        except Exception as e:
            log.warning(f"cannot read positions: {e}")
            return set()

    def flatten(self, reason: str):
        for a in sorted(self.held()):
            log.warning(f"FLATTEN {a} ({reason})")
            if self.live:
                try:
                    self.client.close_position(a)
                except Exception as e:
                    log.error(f"flatten {a} failed: {e}")
        notify.send("FLATTEN", reason, [f"closed all positions"])

    def rebalance(self, targets: list[str], equity: float, top_up: bool = False):
        held = self.held()
        tgt = set(targets)
        for a in sorted(held - tgt):
            log.info(f"REBAL close {a} (left top-{self.q})")
            if self.live:
                self.client.close_position(a)
        per = equity * self.gross / self.q
        # Per-name stop distance sized so the whole book's stop risk fits the
        # client's gate: sum(notional*stop) <= CHALLENGE_START*dd_wall. Sizing on
        # the START balance (not live equity) leaves headroom. A wide stop is
        # what the strategy needs -- tight stops crushed the backtest.
        budget = CHALLENGE_START * self.dd_wall
        stop_pct = min(self.stop_target,
                       0.95 * budget / max(equity * self.gross, 1e-9))
        for a in targets:
            if a in held:
                continue
            ok, why = self.entry_allowed()
            if not ok:
                log.info(f"  {a}: entry deferred — {why}")
                if self.live:
                    notify.send("RATE", f"{a} deferred", [why])
                break
            px = self._mid(a)
            if not px or px <= 0:
                log.warning(f"  {a}: no live price, skip")
                continue
            chase_ok, chase_why = self._chase_ok(a)
            if not chase_ok:
                log.info(f"  {a}: skipped ({chase_why})")
                notify.send("CHASE", f"{a} skipped", [chase_why])
                continue
            lev = 5.0 if a in ("BTC", "ETH") else 2.0
            notional = min(per, equity * lev)
            qty = notional / px
            sl = px * (1 - stop_pct)
            tp = px * (1 + stop_pct * self.tp_mult)
            log.info(f"REBAL open LONG {a} qty {qty:.6f} @ ${px:,.4f} "
                     f"(${notional:,.0f}) SL ${sl:,.4f} (-{stop_pct:.1%}) "
                     f"TP ${tp:,.4f} (+{stop_pct * self.tp_mult:.1%})")
            notify.send_entry(a, qty, px, notional, sl, tp,
                              notional * stop_pct,
                              f"top-{self.q} funding (K={self.K}h), "
                              f"weekly rebalance, stop -{stop_pct:.1%} "
                              f"/ tp +{stop_pct * self.tp_mult:.1%}")
            if self.live:
                try:
                    self.client._last_reject = None
                    if USE_LIMIT_ENTRIES:
                        res = self._limit_entry(a, qty, px, sl, tp, px)
                    else:
                        res = self.client.open_long(asset=a, quantity=qty,
                                                    stop_price=sl, take_profit_price=tp,
                                                    entry_price=px)
                    if not res and getattr(self.client, "_last_reject", None):
                        rj = self.client._last_reject
                        log.warning(f"  {a}: rejected ({rj.get('reason')})")
                        notify.send_rejected(a, rj.get("reason", "?"),
                                             str(rj.get("reason", "")))
                        # A limit fill can land after the fallback was refused.
                        # Protect whatever actually opened instead of leaving
                        # it naked.
                        if self.live:
                            q, _ = self._live_pos(a)
                            if q:
                                log.warning(f"  {a}: protecting stray fill "
                                            f"{q:.6f} after rejection")
                                self._ensure_protected(a, sl, tp)
                    elif res:
                        self.record_entry()
                except ProprAPIError as e:
                    log.warning(f"  {a}: rejected ({e.code})")
                    notify.send_rejected(a, e.code, str(e))
        if not top_up:
            self.last_rebal = time.time()
        self._save_state()

    def _ema(self, series, span):
        if len(series) < span:
            return None
        ema = series.iloc[:span].mean()
        k = 2 / (span + 1)
        for v in series.iloc[span:]:
            if not (v == v):  # nan
                continue
            ema = v * k + ema * (1 - k)
        return ema

    def _chase_ok(self, asset: str) -> tuple[bool, str]:
        try:
            df = sim.fetch_candles(asset, days=2)
            if df is None or len(df) < 30:
                return True, ""
            close = df["close"]
            ret24 = close.iloc[-1] / close.iloc[-25] - 1 if len(close) >= 25 else 0.0
            if ret24 > CHASE_MAX_24H_RET:
                return False, f"chased +{ret24:.1%} 24h"
            ema = self._ema(close.resample('4h').last().dropna(), 20)
            if ema and close.iloc[-1] > ema * (1 + CHASE_MAX_ABOVE_EMA):
                return False, f"chased >{CHASE_MAX_ABOVE_EMA:.0%} above 4H EMA20"
            return True, ""
        except Exception as e:
            log.debug(f"chase check {asset} skipped: {e}")
            return True, ""  # fail open if data missing

    def _live_pos(self, asset: str):
        """(quantity, position_dict) for an open long, or (0.0, None)."""
        try:
            pos = self.client.sdk.get_open_positions(base=asset) or []
        except Exception:
            return None, None
        for p in pos:
            try:
                q = float(p.get("quantity") or 0)
            except Exception:
                continue
            if abs(q) > 0:
                return abs(q), p
        return 0.0, None

    def _ensure_protected(self, asset: str, stop_price: float | None,
                          tp_price: float | None) -> bool:
        """Guarantee the live position carries both a stop and a TP.

        `closePosition: True` means any one of them closes the whole position,
        so the only unsafe state is a missing leg. Re-placing both at the live
        quantity also repairs a half-filled entry.
        """
        q, p = self._live_pos(asset)
        if q is None:
            return False                      # unreadable: caller retries next cycle
        if not q:
            return True                       # nothing open, nothing to protect
        try:
            orders = self.client.sdk.get_orders(status="pending", base=asset,
                                                limit=50) or []
        except Exception:
            orders = []
        legs = {o.get("type") for o in orders
                if o.get("status") == "pending" and o.get("side") == "sell"}
        if "stop_market" in legs and "take_profit_market" in legs:
            return True
        for o in orders:
            if o.get("status") == "pending" and o.get("type") in (
                    "stop_market", "take_profit_market"):
                try:
                    self.client.sdk.cancel_order(o["orderId"])
                except Exception:
                    pass
        pid, qstr = p["positionId"], f"{q:.6f}"
        ok = True
        if stop_price:
            ok = bool(self.client._place_conditional(
                "stop_market", asset, qstr, str(stop_price), pid)) and ok
        if tp_price:
            ok = bool(self.client._place_conditional(
                "take_profit_market", asset, qstr, str(tp_price), pid)) and ok
        return ok

    def _cancel_limit(self, order_ids: list[str], asset: str) -> bool:
        """Cancel our limit orders and confirm they are gone. A blind fallback
        market order placed while the limit is still live doubles the position."""
        if not order_ids:
            return True
        for _ in range(8):
            for oid in order_ids:
                try:
                    self.client.sdk.cancel_order(oid)
                except Exception:
                    pass
            try:
                live = {o.get("orderId") for o in
                        (self.client.sdk.get_orders(status="pending", base=asset,
                                                    limit=50) or [])
                        if o.get("orderId") in order_ids}
            except Exception:
                live = set()
            if not live:
                return True
            time.sleep(1.0)
        log.error(f"{asset}: limit cancel unconfirmed — refusing to double up")
        return False

    def _market_fallback(self, asset: str, quantity: float, stop_price: float,
                         tp_price: float, entry_price: float):
        res = self.client.open_long(asset=asset, quantity=quantity,
                                    stop_price=stop_price,
                                    take_profit_price=tp_price,
                                    entry_price=entry_price)
        # Even a rejected fallback can race a late limit fill. Never walk away
        # from that with a naked position.
        self._ensure_protected(asset, stop_price, tp_price)
        return res

    def _limit_entry(self, asset: str, quantity: float, px: float,
                      stop_price: float, take_profit_price: float,
                      entry_price: float):
        """Post a limit buy just below mid, wait briefly for fill, else market.

        The limit is cancelled *and confirmed dead* before any market order is
        sent. Without that handshake a fill that lands at the timeout is joined
        by the fallback order and the book ends up 2x sized per name.
        """
        base_q, _ = self._live_pos(asset)
        base_q = base_q or 0.0
        limit_px = px * (1 - LIMIT_OFFSET_BPS / 10000)
        qty_str, px_str = f"{quantity:.6f}", f"{limit_px:.6f}"
        try:
            placed = self.client.sdk.limit_buy(asset, qty_str, px_str) or []
            log.info(f"LIMIT {asset} {qty_str} @ ${limit_px:.6f} (mid ${px:.6f})")
        except Exception as e:
            log.warning(f"LIMIT {asset} failed: {e}, falling back to market")
            if LIMIT_FALLBACK_MARKET:
                return self._market_fallback(asset, quantity, stop_price,
                                             take_profit_price, entry_price)
            return []
        order_ids = [o.get("orderId") for o in placed if o.get("orderId")]

        deadline = time.time() + LIMIT_TIMEOUT_S
        while time.time() < deadline:
            time.sleep(min(2.0, max(0.2, deadline - time.time())))
            q, _ = self._live_pos(asset)
            if q is not None and q - base_q >= quantity * 0.98:
                log.info(f"LIMIT {asset} filled")
                self._ensure_protected(asset, stop_price, take_profit_price)
                self.client._last_trade_time = time.time()
                return placed

        if not self._cancel_limit(order_ids, asset):
            q, _ = self._live_pos(asset)
            if q is not None and q - base_q >= quantity * 0.98:
                self._ensure_protected(asset, stop_price, take_profit_price)
                self.client._last_trade_time = time.time()
                return placed
            return []

        q, _ = self._live_pos(asset)
        filled = max(0.0, (q if q is not None else base_q) - base_q)
        if filled >= quantity * 0.98:
            log.info(f"LIMIT {asset} filled late (cancel race)")
            self._ensure_protected(asset, stop_price, take_profit_price)
            self.client._last_trade_time = time.time()
            return placed
        if filled > 0:
            log.warning(f"LIMIT {asset} partial {filled:.6f}/{quantity:.6f}"
                        f" — topping up remainder")
            self._ensure_protected(asset, stop_price, take_profit_price)
            try:
                self.client.sdk.market_buy(asset, f"{quantity - filled:.6f}")
                log.info(f"OPENED rest of LONG {asset} qty={quantity - filled:.6f}")
            except Exception as e:
                log.warning(f"  {asset} top-up failed: {e}")
            self._ensure_protected(asset, stop_price, take_profit_price)
            self.client._last_trade_time = time.time()
            return placed

        log.warning(f"LIMIT {asset} unfilled, checking book before any market "
                    f"fallback")
        if LIMIT_FALLBACK_MARKET:
            try:
                b = sim.hl_post({"type": "l2Book", "coin": asset})
                bid = float(b["levels"][0][0]["px"])
                ask = float(b["levels"][1][0]["px"])
                fresh_mid = (bid + ask) / 2.0
            except Exception:
                fresh_mid = 0.0
                ask = 0.0
            if not fresh_mid > 0:
                log.warning(f"LIMIT {asset}: no fresh book at timeout — "
                            f"deferring (retry next cycle)")
                return []
            slip_bps = (ask / entry_price - 1.0) * 1e4
            if slip_bps > MAX_ENTRY_SLIP_BPS:
                log.warning(f"LIMIT {asset}: timeout slip {slip_bps:.1f}bps "
                            f"above original entry ref > "
                            f"{MAX_ENTRY_SLIP_BPS:.0f}bps — deferring")
                return []
            stop_pct = 1.0 - stop_price / entry_price
            tp_mult = ((take_profit_price - entry_price) /
                       max(entry_price - stop_price, 1e-12))
            new_entry = ask
            new_stop = new_entry * (1.0 - stop_pct)
            new_tp = new_entry * (1.0 + stop_pct * tp_mult)
            return self._market_fallback(asset, quantity, new_stop,
                                         new_tp, new_entry)
        return []

    def _mid(self, asset: str) -> float | None:
        try:
            b = sim.hl_post({"type": "l2Book", "coin": asset})
            bid = float(b["levels"][0][0]["px"])
            ask = float(b["levels"][1][0]["px"])
            return (bid + ask) / 2
        except Exception:
            try:
                df = sim.fetch_candles(asset, days=1)
                return float(df["close"].iloc[-1]) if len(df) else None
            except Exception:
                return None

    # -- cycle ------------------------------------------------------------
    def cycle(self):
        eq = self.client.equity
        now = datetime.now(timezone.utc)
        if self._day != now.date():
            self._day = now.date()
            self.day_start = eq
            self.halted_day = False
            self.dd_halted = False
        # The flag must LATCH until a new UTC day. The old unconditional reset
        # here made `if not self.dd_halted` always true, so a DD breach
        # re-flattened and re-notified on every cycle instead of firing once.

        if eq >= self.start_equity * (1 + self.target_pct):
            log.info(f"TARGET reached — equity ${eq:,.2f}")
            notify.send_target(eq)
            return "STOP"
        if eq <= self.start_equity * (1 - self.dd_halt):
            if not self.dd_halted:
                log.warning(f"dd halt {self.dd_halt:.1%} from start")
                self.flatten(f"dd halt {self.dd_halt:.1%} from start")
                notify.send_halt(f"dd halt: equity ${eq:,.2f} <= "
                                 f"${self.start_equity * (1 - self.dd_halt):,.2f}")
                self.dd_halted = True
            return "STOP"
        if self.day_start and eq <= self.day_start * (1 - self.daily_halt):
            if not self.halted_day:
                log.warning(f"daily halt {self.daily_halt:.1%}")
                self.flatten(f"daily halt {self.daily_halt:.1%}")
                self.halted_day = True
            return "DAYHALT"
        if self.halted_day:
            return "DAYHALT"

        self.manage_trails()
        self.manage_sizing(eq)

        F = refresh_funding(self.universe)
        tf = trailing_funding(F, self.universe, self.K)
        if len(tf) < self.q + 2:
            log.warning(f"only {len(tf)} ranked assets — skip")
            return "SKIP"
        targets = [c for c, _ in sorted(tf.items(), key=lambda kv: -kv[1])
                   [:self.q]]

        due = (time.time() - self.last_rebal) >= self.rebal_h * 3600
        held = self.held()
        # A lost state file must not force a full churn: if positions are
        # already open but we have no rebalance timestamp, assume we are on
        # schedule and simply resume it. Fresh installs (no positions) still
        # rebalance immediately on the first cycle.
        if held and self.last_rebal <= 0.0:
            log.warning("state file lost but positions are open — "
                        "resuming schedule instead of rebalancing")
            self.last_rebal = time.time()
            self._save_state()
            due = False
        if held and not due:
            # Under-filled book (e.g. a rebalance deferred by the entry rate
            # cap): top up the missing slots without resetting the rotation
            # clock. Never churns — existing positions are left untouched.
            missing = [c for c in targets if c not in held]
            ok, why = self.entry_allowed()
            if missing and ok and len(held) < self.q:
                log.info(f"top-up: {len(held)}/{self.q} open, "
                         f"{len(missing)} target(s) missing — filling")
                self.rebalance(targets, eq, top_up=True)
                return "TOPUP"
            if missing and not ok:
                log.info(f"hold: {len(held)}/{self.q} open, top-up "
                         f"deferred — {why}")
            else:
                log.info(f"hold: {len(held)} open, next rebalance in "
                         f"{(self.rebal_h * 3600 - (time.time() - self.last_rebal)) / 3600:.1f}h")
            return "HOLD"

        eve = news.in_halo()
        if eve is not None:
            log.warning(f"NEWS HALO {news.describe(eve)} — no new entries")
            notify.send("NEWS", "halo — no new entries",
                        [news.describe(eve)])
            if not held:
                return "HALO"

        log.info(f"top-{self.q} by funding ({self.K}h): "
                 + ", ".join(f"{c}({tf[c]*1e4:+.2f}bp)" for c in targets))
        self.rebalance(targets, eq)
        return "REBAL"

    def run(self, interval=SCAN_SEC, once=False):
        acct = self.client.setup(ACCOUNT_ID, starting_balance=CHALLENGE_START)
        self.start_equity = CHALLENGE_START
        halt = CACHE / "HALT"
        if halt.exists():
            # Same latch as the beta deployment: a halted account must stay
            # halted across restarts, including a manual start.
            try:
                why = halt.read_text(encoding="utf-8").splitlines()[0]
            except Exception:
                why = "halt marker present"
            log.warning(f"HALTED - refusing to start: {why}")
            notify.send_halt(why.replace("&", "&amp;").replace("<", "&lt;"))
            return 0
        if self.live:
            self.seed_entry_times()
        eq = self.client.equity
        self.day_start = eq
        self._day = datetime.now(timezone.utc).date()
        log.info("=" * 70)
        log.info(f"FUNDING runner — {'LIVE' if self.live else 'DRY RUN'}  "
                 f"account {acct}  equity ${eq:,.2f}")
        log.info(f"K={self.K}h q={self.q} gross={self.gross} "
                 f"rebal={self.rebal_h}h stop<={self.stop_target:.0%} "
                 f"tp x{self.tp_mult:.1f} "
                 f"walls {self.daily_wall:.0%}/{self.dd_wall:.0%} "
                 f"halts {self.daily_halt:.0%}/{self.dd_halt:.0%} "
                 f"target={self.target_pct:.0%}")
        log.info("=" * 70)
        notify.send("START", f"funding runner {'LIVE' if self.live else 'DRY'}",
                    [f"equity ${eq:,.2f}", f"K={self.K}h q={self.q} "
                     f"gross={self.gross} rebal={self.rebal_h}h"])
        n = 0
        while True:
            try:
                n += 1
                r = self.cycle()
                s = self.client.get_account_summary()
                log.info(f"[{n}] {r} | equity ${s['equity']:,.2f} | profit "
                         f"{s['profit_pct']:+.2%} | daily "
                         f"{s['daily_used_pct']:.0%} | dd {s['dd_used_pct']:.0%}"
                         f" | open {len(self.held())}")
                if r == "STOP":
                    return 0
                if once:
                    return 0
                time.sleep(interval)
            except KeyboardInterrupt:
                return 0
            except Exception as e:
                log.error(f"cycle: {e}")
                time.sleep(60)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--interval", type=int, default=SCAN_SEC)
    ap.add_argument("--q", type=int, default=5)
    ap.add_argument("--K", type=int, default=336)
    ap.add_argument("--gross", type=float, default=0.5)
    ap.add_argument("--rebal-h", type=float, default=168.0)
    ap.add_argument("--stop", type=float, default=0.20,
                    help="desired per-name stop distance (auto-fit to gate)")
    ap.add_argument("--tp-mult", type=float, default=1.5,
                    help="take-profit as a multiple of the stop distance")
    ap.add_argument("--daily-wall", type=float, default=0.05)
    ap.add_argument("--dd-wall", type=float, default=0.10)
    ap.add_argument("--daily-halt", type=float, default=0.03)
    ap.add_argument("--dd-halt", type=float, default=0.07)
    ap.add_argument("--target", type=float, default=0.10)
    ap.add_argument("--seed", action="store_true",
                    help="force re-seed funding history then exit")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                        format="%(asctime)s %(levelname)s %(message)s")
    if a.seed:
        refresh_funding(get_universe(), force=True)
        return 0
    key = os.getenv("PROPR_API_KEY", "")
    if not key.startswith(("pk_beta_", "pk_live_")):
        log.error(f"PROPR_API_KEY looks wrong; got {key[:12]}… — refusing")
        return 1
    if a.live:
        log.warning("LIVE MODE — real orders")
    return FundBot(a).run(a.interval, once=a.once)


if __name__ == "__main__":
    sys.exit(main())
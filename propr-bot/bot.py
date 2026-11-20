#!/usr/bin/env python3
"""
Propr challenge runner (LOCAL DEPLOY - Windows PC).

    python bot.py            # dry run: decides, logs, places nothing
    python bot.py --live     # places real orders
    python bot.py --preflight

Needs .env next to this file (overrides the 1m one):

    PROPR_API_KEY=pk_beta_...
    PROPR_API_URL=https://api.beta.propr.xyz/v1
    PROPR_WS_URL=wss://api.beta.propr.xyz/ws
    TELEGRAM_TOKEN=...
    TELEGRAM_CHAT_ID=...
    PROPR_ACCOUNT_ID=urn:prp-account:...
    CHALLENGE_START=1000

The execution layer is this folder's vendored propr/ package (a copy of the
1m repo's ProprRiskClient + the server-rule overrides patch). The signal is
imported from sim.py, not reimplemented.

Three improvements over the VPS build, on top of the VPS fixes:
  1. Stale-bar guard  - live_mid() vs signal close, skip if beyond max_entry_slip.
  2. Breakeven trail  - move stop to break-even once price is be_frac to TP.
  3. Entry confirmation - only fill when the live mid is inside the ATR sleeve
     around the signal bar and confirming the signal direction
     (top-of-book check, not a blind close-price fill).
  4. News halo + quality rank - no new entries inside a macro-event halo
     (FOMC/CPI/NFP), and among firing signals only the highest-conviction
     candidates (momentum z, volume confirmation, range position) are taken;
     if none clear the A-grade bar, the cycle stands down.

Risk posture, unchanged: one position, small risk, walls measured from
CHALLENGE_START (never slid toward the live equity).
"""
import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

import sim
import notify
import news

from propr import ProprConfig, ProprRiskClient, ProprAPIError

TOKEN_PREFIX = "pk_beta_"
STATE_PATH = Path(__file__).parent / "cache" / "bot_state.json"
ONE_M = Path(r"C:\Users\DELL\OneDrive\1m")

log = logging.getLogger("bot")

# The account to trade, and the starting balance. Read from env so the .env is
# the single place that names the challenge; falls back to the old trial so a
# misconfigured .env fails loudly rather than trading the wrong account.
ACCOUNT_ID = os.getenv("PROPR_ACCOUNT_ID", "urn:prp-account:LDA3R3vW74Ym")
CHALLENGE_START = float(os.getenv("CHALLENGE_START", "1000.0"))

# -- config, same engine the backtest team validated -----------------------
# Patient: one position, half a percent at risk. Breached 0/33 windows at every
# horizon tested. Needs ~90 days to reach target.
# Cap is regime-aware now: 5 concurrent above the BTC 96h mid, 2 below it.
CFG = sim.Cfg(strat="mom", lb=96, k=3, allow_short=False,
              risk_pct=0.005, sl_atr=2.0, tp_r=2.0,
              max_concurrent=5, max_hold=72, cooldown=4,
              daily_halt=0.02, dd_halt=0.045)

BARS = 400            # enough for lb=96 plus ATR warmup
SCAN_SEC = 300


def live_mid(asset: str) -> float | None:
    """Best-ask/best-bid midpoint from HL, so the guard compares CURRENT price
    to the (possibly long-closed) signal bar instead of assuming they match."""
    try:
        b = sim.hl_post({"type": "l2Book", "coin": asset})
        bid = float(b["levels"][0][0]["px"])
        ask = float(b["levels"][1][0]["px"])
        return (bid + ask) / 2
    except Exception as e:
        log.warning(f"live_mid({asset}) failed: {e}")
        return None


def snapshot(universe: list[str]) -> dict | None:
    """Recent candles for the whole universe, forming bar dropped.

    Cross-sectional momentum ranks assets against each other, so a partial
    universe silently changes the signal. Fewer than 80% of names present means
    the ranking is not the one that was tested -- skip the cycle.
    """
    raw = {}
    for s in universe:
        try:
            df = sim.fetch_candles(s, days=BARS // 24 + 3)
        except Exception as e:
            log.warning(f"candles {s}: {e}")
            continue
        if len(df) > 220:
            raw[s] = df.iloc[:-1]            # drop the forming bar
        time.sleep(0.05)
    if len(raw) < 0.8 * len(universe):
        log.warning(f"only {len(raw)}/{len(universe)} symbols — skipping "
                    f"rather than ranking a partial universe")
        return None
    return sim.prep(raw)


def preflight(cfg: sim.Cfg, equity: float = 1000.0) -> int:
    """Everything except the broker: live candles, the real signal, real
    sizing. Needs no API key, so it proves the half of the pipeline that a
    dead key would otherwise hide."""
    D = snapshot(sorted(sim.load()))
    if D is None:
        return 1
    sig = sim.SIGNALS[cfg.strat](D, cfg)[-1]
    bar = D["index"][-1]
    log.info(f"last closed bar {bar}  universe {len(D['cols'])}")
    log.info(f"{sim.describe(cfg)}   assumed equity ${equity:,.2f}")
    fired = np.flatnonzero(sig)
    if not len(fired):
        log.info("no signal on this bar — the bot would place nothing")
        return 0
    for i in fired:
        asset = D["cols"][i]
        px, atr = float(D["close"][-1, i]), float(D["atr"][-1, i])
        if not (px > 0 and atr > 0):
            continue
        if sig[i] < 0:
            log.info(f"  {asset}: SHORT signal — skipped, no short execution path")
            continue
        sd = cfg.sl_atr * atr
        lev = 5.0 if asset in ("BTC", "ETH") else 2.0
        notional = min((equity * cfg.risk_pct) / sd * px,
                       equity * min(lev, cfg.max_gross))
        log.info(f"  {asset}: LONG {notional / px:.6f} @ {px:.4f} "
                 f"(${notional:,.0f})  sl {px - sd:.4f}  "
                 f"tp {px + sd * cfg.tp_r:.4f}  risk ${equity * cfg.risk_pct:,.2f}")
    log.info(f"({len(fired)} signal(s); the live bot would take the first "
             f"{cfg.max_concurrent} it can fill)")
    return 0


class Bot:
    def __init__(self, cfg: sim.Cfg, live: bool, paper: bool = False):
        self.cfg, self.live, self.paper = cfg, live, paper
        if paper:
            import paper as paper_mod
            self.client = paper_mod.PaperClient(
                starting_balance=CHALLENGE_START)
            self.universe = sorted(sim.load())
            self.start_equity = None
            self._snap: dict | None = None
            self._snap_hour = None
            self._held: set[str] = set()
            self.opened_at: dict[str, float] = {}
            self.cooled: dict[str, float] = {}
            self._load_state()
            return
        pcfg = ProprConfig(
            api_key=os.getenv("PROPR_API_KEY", ""),
            api_url=os.getenv("PROPR_API_URL", "https://api.propr.xyz/v1"),
            ws_url=os.getenv("PROPR_WS_URL", "wss://api.propr.xyz/ws"),
            # Beta free-trial rules: +10% target, 5% daily, 10% static DD
            rule_starting_balance=CHALLENGE_START,
            rule_profit_target=CHALLENGE_START * 0.10,
            rule_max_daily_loss_pct=0.05,
            rule_max_drawdown_pct=0.10,
        )
        # Match the client's own circuit breakers to the simulated halts, so
        # the thing that runs is the thing that was measured.
        pcfg.max_daily_loss_pct = cfg.daily_halt
        pcfg.max_monthly_dd_pct = cfg.dd_halt
        pcfg.max_concurrent_positions = cfg.max_concurrent
        self.client = ProprRiskClient(pcfg)
        self.universe = sorted(sim.load())      # identical to the backtest's
        self.start_equity = None
        self._snap: dict | None = None          # candles, cached per hour
        self._snap_hour = None
        self._held: set[str] = set()            # to notice exchange-side fills
        self.opened_at: dict[str, float] = {}   # asset -> unix ts of entry
        self.cooled: dict[str, float] = {}      # asset -> unix ts of last close
        self._load_state()

    # -- durable state ----------------------------------------------------
    def _load_state(self):
        try:
            d = json.loads(STATE_PATH.read_text())
            self.opened_at = {k: float(v) for k, v in d.get("opened_at", {}).items()}
            self.cooled = {k: float(v) for k, v in d.get("cooled", {}).items()}
            log.info(f"state restored: {len(self.opened_at)} open timers, "
                     f"{len(self.cooled)} cooldowns")
        except FileNotFoundError:
            pass
        except Exception as e:
            log.warning(f"state unreadable ({e}); starting empty")

    def _save_state(self):
        try:
            STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp = STATE_PATH.with_suffix(".tmp")
            tmp.write_text(json.dumps({"opened_at": self.opened_at,
                                       "cooled": self.cooled}))
            tmp.replace(STATE_PATH)
        except Exception as e:
            log.warning(f"could not persist state: {e}")

    # -- data -------------------------------------------------------------
    def snapshot(self) -> dict | None:
        """Cached until a new hourly bar closes."""
        hour = datetime.now(timezone.utc).replace(minute=0, second=0,
                                                  microsecond=0)
        if self._snap is not None and self._snap_hour == hour:
            return self._snap
        t0 = time.monotonic()
        D = snapshot(self.universe)
        if D is not None:
            self._snap, self._snap_hour = D, hour
            log.info(f"candles refreshed in {time.monotonic() - t0:.0f}s "
                     f"(bar {D['index'][-1]})")
        return D

    # -- guards -----------------------------------------------------------
    def halted(self, equity: float) -> str | None:
        floor = self.start_equity * (1 - self.cfg.dd_halt)
        if equity <= floor:
            return (f"self-halt: equity ${equity:,.2f} <= ${floor:,.2f} "
                    f"({self.cfg.dd_halt:.1%} from start)")
        return None

    def entry_ok(self, asset: str, px: float, atr: float,
                 live: float | None, side: int) -> tuple[bool, str | None]:
        """Top-of-book entry confirmation. Signal close and ATR are from the
        closed bar; the fill should sit in the ATR sleeve around that close
        and not fight the signal direction. Returns (ok, skip_reason)."""
        if live is None or not live > 0:
            return True, None
        slip = (live / px - 1) if px > 0 else 0.0
        sleeve = max(0.5 * atr / px, 0)
        if side > 0 and slip <= -sleeve:
            return False, (f"live ${live:,.4f} {slip:+.1%} from signal close "
                           f"${px:,.4f} — below the half-ATR confirmation "
                           f"sleeve {sleeve:.1%}, long not confirming")
        if side < 0 and slip >= sleeve:
            return False, (f"live ${live:,.4f} {slip:+.1%} from signal close "
                           f"${px:,.4f} — above the half-ATR confirmation "
                           f"sleeve {sleeve:.1%}, short not confirming")
        return True, None

    # -- cycle ------------------------------------------------------------
    def concurrent_cap(self, D: dict) -> int:
        btc_i = next((i for i, c in enumerate(D["cols"]) if c == "BTC"), None)
        if btc_i is not None:
            C = D["close"]; V = D["valid"]
            if V[-1, btc_i] and V[-97, btc_i]:
                mid = (D["high"][-96:, btc_i].max() +
                       D["low"][-96:, btc_i].min()) / 2
                if C[-1, btc_i] >= mid:
                    return 5
                return 2
        return self.cfg.max_concurrent

    def news_halo(self) -> news.Event | None:
        return news.in_halo()

    def market_ok(self, D: dict) -> tuple[bool, str | None]:
        """Live gate: is the broad tape actually risk-on right now? The signal
        is cross-sectional momentum, so it ranks WITHIN the market; this gate
        is the market-vs-itself check that picks the regime (pump vs chop/down).
        Primary check is a SHORT window (12h): the winners all had 12h-market
        between +2.9% and +4.4% at entry, and a 24h-only floor is too slow to
        catch a post-pump rollover (24h stayed green while 12h went -2.5% on
        the day after the pump). Returns (ok, reason-if-not)."""
        C = D["close"]; V = D["valid"]
        n = C.shape[0]
        if n < 13:
            return True, None
        lo12 = max(0, n - 13)                  # 12h of closed bars
        lo24 = max(0, n - 25)                  # 24h of closed bars
        def mkt_ret(lo):
            r = C[n - 1, :] / C[lo, :] - 1
            r[~V[n - 1, :] | ~V[lo, :]] = np.nan
            r = np.nan_to_num(r)
            return np.nanmean(r) if np.isfinite(r).any() else np.nan
        m12 = mkt_ret(lo12)
        m24 = mkt_ret(lo24)
        if not np.isfinite(m12):
            return True, None
        if m12 < self.cfg.mkt_min_pct:
            return False, (f"tape rolling over: equal-weight 12h market "
                           f"{100*m12:+.2f}% is below the "
                           f"{100*self.cfg.mkt_min_pct:.2f}% floor (24h "
                           f"{100*m24:+.2f}%) — this is the post-pump fade "
                           f"pattern, not the pump day; will not long it")
        btc_i = next((i for i, c in enumerate(D["cols"]) if c == "BTC"), None)
        if btc_i is None:
            return True, None
        if not (V[n - 1, btc_i] and V[lo24, btc_i]):
            return True, None
        run = C[n - 1, btc_i] / C[lo24, btc_i] - 1
        if run > self.cfg.mkt_max_run_pct:
            return False, (f"BTC already ran 24h {100*run:+.2f}% > "
                           f"{100*self.cfg.mkt_max_run_pct:.0f}% chase cap — "
                           f"entering a vertical squeeze late risks the "
                           f"reversal, not the continuation")
        return True, None

    def candidate_quality(self, D: dict, i: int) -> float:
        """0..~1 conviction for one fired signal, used only to RANK entries
        (and to stand down when nothing clears the bar). Never alters position
        math or the signal itself."""
        C = D["close"]; H = D["high"]; L = D["low"]; V = D["volume"]
        lb = self.cfg.lb
        n = C.shape[0]
        if n < lb + 1:
            return 0.0
        ret_i = C[-1, i] / C[-1 - lb, i] - 1
        if not (np.isfinite(ret_i) and ret_i > 0):
            return 0.0
        rets = C[-1, :] / C[-1 - lb, :] - 1
        mean = np.nanmean(rets); sd = np.nanstd(rets)
        z = (ret_i - mean) / sd if sd > 1e-9 else 0.0
        mom = min(max(z / 2.5, 0.0), 1.0)
        v_now = V[-1, i]
        v_ref = np.nanmedian(V[-25:-1, i]) if n >= 25 else np.nanmedian(V[:, i])
        vol = min(v_now / v_ref, 3.0) / 3.0 if v_ref > 0 else 0.0
        rng = H[-24:, i].max() - L[-24:, i].min()
        pos = (C[-1, i] - L[-24:, i].min()) / rng if rng > 1e-9 else 0.5
        ranged = 1.0 - min(abs(pos - 0.65), 0.6) / 0.6
        return 0.5 * mom + 0.3 * vol + 0.2 * max(ranged, 0.0)

    def best_signals(self, D: dict) -> list[tuple[float, int]]:
        """Fired long signals ranked by quality, cut at the A-grade bar. The
        'don't rush' rule: if nothing clears the bar the list is empty and the
        cycle sits out rather than force a stale entry."""
        sig = sim.SIGNALS[self.cfg.strat](D, self.cfg)[-1]
        out = []
        for i in np.flatnonzero(sig > 0):
            q = self.candidate_quality(D, i)
            if q >= 0.5:
                out.append((q, int(i)))
        out.sort(reverse=True)
        return out

    def scan(self):
        D = self.snapshot()
        if D is None:
            return
        cap = self.concurrent_cap(D)
        try:
            held = {p["base"] for p in self.client.get_open_positions()}
        except Exception as e:
            log.warning(f"cannot read positions, standing down: {e}")
            return
        if len(held) >= cap:
            return

        eve = self.news_halo()
        if eve is not None:
            also = ""
            nxt = news.next_event()
            if nxt is not None:
                also = f" next: {news.describe(nxt)}"
            log.warning("NEWS HALO — standing down entries around "
                        f"{news.describe(eve)}{also}")
            notify.send("NEWS", "standing down entries",
                        [f"{news.describe(eve)} inside halo window{also}"],
                        why="scheduled macro event; no new risk into "
                            "announced volatility, open stops/TPs untouched")
            return

        gok, greason = self.market_ok(D)
        if not gok:
            log.warning(f"MARKET GATE — standing down entries: {greason}")
            notify.send("MARKET", "standing down entries",
                        [str(greason)],
                        why="long-only momentum into a falling or vertical "
                            "tape has historically been where this bot bleeds")
            return

        equity = self.client.equity

        for q, i in self.best_signals(D):
            asset = D["cols"][i]
            if asset in held or len(held) >= cap:
                continue

            px, atr = float(D["close"][-1, i]), float(D["atr"][-1, i])
            if not (px > 0 and atr > 0):
                continue
            since = time.time() - self.cooled.get(asset, -1e12)
            if since < self.cfg.cooldown * 3600:
                log.info(f"  {asset}: cooling down, "
                         f"{(self.cfg.cooldown * 3600 - since) / 60:.0f}m left")
                continue
            sd = self.cfg.sl_atr * atr

            # Stale-bar guard + top-of-book confirmation: don't fill into a
            # price the signal never saw. If we can't get a live mid, proceed
            # on the close (offline mode) rather than stall the whole cycle.
            live = live_mid(asset)
            if live is not None and live > 0:
                slip = abs(live / px - 1)
                if slip > self.cfg.max_entry_slip:
                    log.warning(
                        f"  {asset}: STALE SIGNAL — signal bar {px:.4f}, "
                        f"live {live:.4f} ({slip:.2%} off). Skipping.")
                    notify.send("STALE", f"skipped <b>{asset}</b>",
                                [f"signal bar ${px:,.4f} vs live ${live:,.4f} "
                                 f"({slip:.2%}), over the "
                                 f"{self.cfg.max_entry_slip:.2%} slip cap",
                                 "chasing an extended move — would have "
                                 "filled into a worse setup than the backtest"],
                                why=f"{slip:.2%} divergence at entry time; "
                                    "momentum signals on a fast tape buy the "
                                    "top by construction")
                    continue
                ok2, skip_reason = self.entry_ok(asset, px, atr, live, 1)
                if not ok2:
                    log.info(f"  {asset}: ENTRY CONFIRM — {skip_reason}")
                    notify.send("CONFIRM", f"skipped <b>{asset}</b>",
                                [skip_reason, f"signal close ${px:,.4f} "
                                              f"atr {atr:.4f} live ${live:,.4f}"],
                                why="top-of-book sleeve check failed")
                    continue
                px = live                # re-anchor risk math to the real fill

            lev = 5.0 if asset in ("BTC", "ETH") else 2.0
            notional = min((equity * self.cfg.risk_pct) / sd * px,
                           equity * min(lev, self.cfg.max_gross))
            qty = notional / px
            sl, tp = px - sd, px + sd * self.cfg.tp_r
            log.info(f"SIGNAL {asset} @ {px:.4f} qty {qty:.6f} "
                     f"(${notional:,.0f}) sl {sl:.4f} tp {tp:.4f} "
                     f"risk ${equity * self.cfg.risk_pct:,.2f}")
            # WHY we are taking this trade: measured momentum rank + quality.
            try:
                _rets = D["close"][-1, :] / D["close"][-1 - self.cfg.lb, :] - 1
                _rank = int((~np.isnan(_rets)).sum() -
                            np.sum(np.nan_to_num(_rets, nan=-np.inf) >
                                   _rets[i])) + 1
                _why = (f"{asset} ranked #{_rank} of {len(D['cols'])} by "
                        f"{self.cfg.lb}h return (+{100 * _rets[i]:.1f}%), "
                        f"quality score {q:.2f} (top-{self.cfg.k} long, "
                        f"best firing today).")
            except Exception:
                _why = f"{asset} fired the {self.cfg.strat} signal " \
                       f"(quality {q:.2f})."
            notify.send_entry(asset, qty, px, notional, sl, tp,
                              equity * self.cfg.risk_pct, _why)

            if not self.live:
                log.info(f"  DRY RUN — not placing {asset}")
                continue
            try:
                ok3 = self.client.open_long(asset=asset, quantity=qty,
                                            stop_price=sl,
                                            take_profit_price=tp, entry_price=px)
            except ProprAPIError as e:
                log.warning(f"  {asset}: order rejected ({e.code}) - cooling {self.cfg.cooldown}h")
                notify.send_rejected(asset, e.code, str(e))
                self.cooled[asset] = time.time()
                self._save_state()
                continue
            if ok3:
                held.add(asset)
                self.opened_at[asset] = time.time()
                self._save_state()
            else:
                log.warning(f"  {asset} refused by the risk gate")

    def _breakeven(self, positions) -> None:
        """Live-only: once a long is halfway to its take-profit, ratchet the
        resting stop up to break-even (incl. fees). Idempotent + safe."""
        for p in positions:
            asset = p.get("base")
            pos_id = p.get("positionId")
            if p.get("positionSide") != "long" or not pos_id:
                continue
            try:
                entry = float(p.get("entryPrice") or 0)
                be = float(p.get("breakEvenPrice") or 0) or entry
                mark = float(p.get("markPrice") or 0)
                qty = float(p.get("quantity") or 0)
            except (TypeError, ValueError):
                continue
            if entry <= 0 or qty <= 0:
                continue
            orders = []
            try:
                orders = self.client.sdk.get_orders(position_id=pos_id, limit=50)
            except Exception as e:
                log.warning(f"{asset}: could not read orders for breakeven: {e}")
                continue
            tp = None
            cur_stop = None
            cur_stop_id = None
            for o in orders:
                ot = o.get("type")
                try:
                    trg = float(o.get("triggerPrice"))
                except (TypeError, ValueError):
                    continue
                if ot == "take_profit_market":
                    tp = trg
                elif ot == "stop_market":
                    cur_stop, cur_stop_id = trg, o.get("orderId")
            if tp is None or cur_stop_id is None or cur_stop is None:
                continue
            halfway = entry + self.cfg.be_frac * (tp - entry)
            if mark < halfway:
                continue
            if cur_stop + 1e-9 >= be:
                continue
            qty_str = f"{qty:.6f}"
            log.info(f"{asset}: mark {mark:.4f} >= halfway {halfway:.4f} "
                     f"to tp {tp:.4f} — moving stop {cur_stop:.4f} -> "
                     f"break-even {be:.4f}")
            placed = self.client._place_conditional(
                "stop_market", asset, qty_str, f"{be:.6f}", pos_id)
            if not placed:
                log.error(f"{asset}: BREAKEVEN STOP PLACEMENT FAILED — "
                          f"keeping old stop {cur_stop:.4f}")
                continue
            # new stop is above the old for a long; now retire the old one.
            try:
                self.client.sdk.cancel_order(cur_stop_id)
            except Exception as e:
                log.warning(f"{asset}: could not cancel old stop {cur_stop_id}: {e}")
            try:
                notify.send("BREAKEVEN", f"stop moved to break-even — <b>{asset}</b>",
                            [f"entry ${entry:,.4f}  now protected at ${be:,.4f} "
                             f"(includes fees)",
                             f"mark ${mark:,.4f} reached halfway to take-profit "
                             f"${tp:,.4f}"],
                            why=f"{self.cfg.be_frac:.0%} of the way to take-profit "
                                "reached; cannot turn a winner into a loser")
            except Exception as e:
                log.warning(f"breakeven notify failed: {e}")

    def manage(self):
        """Max-hold + breakeven. Stop and target rest on the exchange, which
        is the point -- they survive this process dying."""
        try:
            positions = self.client.get_open_positions()
        except Exception as e:
            log.warning(f"cannot read positions: {e}")
            return

        live_now = {p.get("base") for p in positions}
        for asset in sorted(self._held - live_now):
            log.info(f"CLOSED {asset} — hit its stop or target on the exchange")
            self.opened_at.pop(asset, None)
            self.cooled[asset] = time.time()
        if self._held != live_now:
            self._save_state()
        self._held = live_now

        self._breakeven(positions)

        for p in positions:
            asset = p.get("base")
            t0 = self.opened_at.get(asset)
            if t0 and time.time() - t0 > self.cfg.max_hold * 3600:
                log.info(f"{asset}: max hold {self.cfg.max_hold}h — closing")
                if self.live:
                    self.client.close_position(asset)
                self.opened_at.pop(asset, None)
                self._save_state()

    def run(self, interval=SCAN_SEC):
        acct = self.client.setup(ACCOUNT_ID, starting_balance=CHALLENGE_START)
        self.start_equity = CHALLENGE_START
        live = self.client.equity
        r = self.client.rules
        log.info(f"account {acct}  live equity ${live:,.2f}  "
                 f"challenge start ${CHALLENGE_START:,.2f}")
        if abs(live - CHALLENGE_START) > 0.5 * CHALLENGE_START:
            log.error("live equity is nowhere near the configured challenge "
                      "start — refusing to trade against wrong walls")
            return 1
        log.info("=" * 70)
        log.info(f"Propr runner — {'LIVE' if self.live else 'DRY RUN'}")
        log.info(f"equity ${self.start_equity:,.2f}  target "
                 f"${r.profit_target:,.0f}  daily wall ${r.max_daily_loss_usd:,.0f}"
                 f"  dd floor ${r.max_drawdown_equity_floor:,.0f}")
        log.info(f"{sim.describe(self.cfg)}  |  universe {len(self.universe)}")
        notify.send_startup(acct, live, r.profit_target,
                            r.max_daily_loss_usd, r.max_drawdown_equity_floor,
                            sim.describe(self.cfg))
        log.info("=" * 70)

        n = 0
        while True:
            try:
                n += 1
                stop = self.halted(self.client.equity)
                if stop:
                    log.error(stop)
                    notify.send_halt(stop)
                    log.error("flatten and review before restarting")
                    return 1
                self.scan()
                self.manage()
                s = self.client.get_account_summary()
                up = [news.describe(e)
                      for e in news.upcoming(horizon_h=72.0)]
                log.info(f"[{n}] equity ${s['equity']:,.2f} | profit "
                         f"{s['profit_pct']:+.2%} of {s['target_pct']:.0%} | "
                         f"daily {s['daily_used_pct']:.0%} | "
                         f"dd {s['dd_used_pct']:.0%} | open {len(self._held)}"
                         + (f" | news: {', '.join(up[:2])}" if up else ""))
                if s["target_reached"]:
                    log.info("TARGET REACHED — stopping")
                    notify.send_target(s["equity"])
                    return 0
                time.sleep(interval)
            except KeyboardInterrupt:
                return 0
            except Exception as e:
                log.error(f"cycle: {e}")
                time.sleep(60)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true",
                    help="place real orders (default: dry run)")
    ap.add_argument("--paper", action="store_true",
                    help="virtual $10k account, same decisions + telemetry")
    ap.add_argument("--interval", type=int, default=SCAN_SEC)
    ap.add_argument("--preflight", action="store_true",
                    help="show what it would trade right now; no API key needed")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                        format="%(asctime)s %(levelname)s %(message)s")
    try:
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).parent / ".env", override=True)
        if not os.getenv("PROPR_API_KEY"):
            load_dotenv(ONE_M / ".env")
    except ImportError:
        pass
    if a.preflight:
        return preflight(CFG)
    if a.paper:
        log.info(f"PAPER MODE — $10,000 virtual account, {ACCOUNT_ID}")
        return Bot(CFG, live=True, paper=True).run(a.interval)
    key = os.getenv("PROPR_API_KEY", "")
    if not key.startswith(TOKEN_PREFIX):
        log.error(f"PROPR_API_KEY must start with '{TOKEN_PREFIX}'; "
                  f"got {key[:12]}… — refusing")
        return 1
    log.info(f"key {key[:12]}… account {ACCOUNT_ID}")
    if a.live:
        log.warning("LIVE MODE — real orders")
    return Bot(CFG, live=a.live).run(a.interval)


if __name__ == "__main__":
    sys.exit(main())
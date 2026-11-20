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
  - No per-name stops (they whipsaw this signal); protect with portfolio HALTS:
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
from datetime import datetime, timezone
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

ACCOUNT_ID = os.getenv("PROPR_ACCOUNT_ID", "urn:prp-account:LDA3R3vW74Ym")
CHALLENGE_START = float(os.getenv("CHALLENGE_START", "1000.0"))
TOKEN_PREFIX = os.getenv("FUND_TOKEN_PREFIX", "pk_beta_")

SCAN_SEC = 300
SEED_DAYS = 18            # fundingHistory seed window (> K hours of margin)
MIN_HIST_H = 72           # min finite funding hours to rank a coin


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
        self.daily_wall = args.daily_wall
        self.dd_wall = args.dd_wall
        self.live = args.live
        self.universe = get_universe()
        self.start_equity = None
        self.day_start = None
        self._day = None
        self.last_rebal = 0.0
        self.halted_day = False
        self._load_state()

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

    def _load_state(self):
        try:
            d = json.loads(STATE_PATH.read_text())
            self.last_rebal = float(d.get("last_rebal", 0.0))
        except Exception:
            pass

    def _save_state(self):
        CACHE.mkdir(exist_ok=True)
        try:
            STATE_PATH.write_text(json.dumps({"last_rebal": self.last_rebal}))
        except Exception as e:
            log.warning(f"state save failed: {e}")

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

    def rebalance(self, targets: list[str], equity: float):
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
            px = self._mid(a)
            if not px or px <= 0:
                log.warning(f"  {a}: no live price, skip")
                continue
            lev = 5.0 if a in ("BTC", "ETH") else 2.0
            notional = min(per, equity * lev)
            qty = notional / px
            sl = px * (1 - stop_pct)
            log.info(f"REBAL open LONG {a} qty {qty:.6f} @ ${px:,.4f} "
                     f"(${notional:,.0f}) SL ${sl:,.4f} (-{stop_pct:.1%})")
            notify.send_entry(a, qty, px, notional, sl, 0.0,
                              notional * stop_pct,
                              f"top-{self.q} funding (K={self.K}h), "
                              f"weekly rebalance, stop -{stop_pct:.1%}")
            if self.live:
                try:
                    self.client.open_long(asset=a, quantity=qty,
                                          stop_price=sl, entry_price=px)
                except ProprAPIError as e:
                    log.warning(f"  {a}: rejected ({e.code})")
                    notify.send_rejected(a, e.code, str(e))
        self.last_rebal = time.time()
        self._save_state()

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

        if eq >= self.start_equity * (1 + self.target_pct):
            log.info(f"TARGET reached — equity ${eq:,.2f}")
            notify.send_target(eq)
            return "STOP"
        if eq <= self.start_equity * (1 - self.dd_halt):
            self.flatten(f"dd halt {self.dd_halt:.1%} from start")
            notify.send_halt(f"dd halt: equity ${eq:,.2f} <= "
                             f"${self.start_equity * (1 - self.dd_halt):,.2f}")
            return "STOP"
        if self.day_start and eq <= self.day_start * (1 - self.daily_halt):
            if not self.halted_day:
                log.warning(f"daily halt {self.daily_halt:.1%}")
                self.flatten(f"daily halt {self.daily_halt:.1%}")
                self.halted_day = True
            return "DAYHALT"
        if self.halted_day:
            return "DAYHALT"

        F = refresh_funding(self.universe)
        tf = trailing_funding(F, self.universe, self.K)
        if len(tf) < self.q + 2:
            log.warning(f"only {len(tf)} ranked assets — skip")
            return "SKIP"
        targets = [c for c, _ in sorted(tf.items(), key=lambda kv: -kv[1])
                   [:self.q]]

        due = (time.time() - self.last_rebal) >= self.rebal_h * 3600
        held = self.held()
        if held and not due:
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
        eq = self.client.equity
        self.day_start = eq
        self._day = datetime.now(timezone.utc).date()
        log.info("=" * 70)
        log.info(f"FUNDING runner — {'LIVE' if self.live else 'DRY RUN'}  "
                 f"account {acct}  equity ${eq:,.2f}")
        log.info(f"K={self.K}h q={self.q} gross={self.gross} "
                 f"rebal={self.rebal_h}h stop<={self.stop_target:.0%} "
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
    try:
        from dotenv import load_dotenv
        load_dotenv(HERE / ".env", override=True)
        if not os.getenv("PROPR_API_KEY"):
            load_dotenv(ONE_M / ".env")
    except ImportError:
        pass
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
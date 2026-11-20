#!/usr/bin/env python3
"""
Paper trading engine — a virtual Propr account.

Implements the SAME surface the live Bot drives (equity, rules,
get_open_positions, open_long, close_position, get_account_summary,
sdk.get_orders / cancel_order, _place_conditional) but against a simulated
$START balance and live Hyperliquid marks, so the 4 improvements (stale-bar
guard, breakeven, market-regime filter, entry confirmation) run byte-for-byte
identical to live — the only difference is nothing touches a real account.

Fills:
  - entries fill at the price the Bot hands over (already re-anchored to the
    live mid by the stale-bar guard) minus taker fee
  - stops fill at the stop trigger minus taker fee
  - take-profits fill at the TP trigger minus maker fee
  - each position also pays 8h funding drag at the sim rate

State persists to cache/paper_state.json so restarts keep open positions and
P&L, the same way the real challenge survives a redeploy.
"""
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from propr.config import ChallengeRules, ChallengeType, AccountSize

log = logging.getLogger("paper")

HERE = Path(__file__).parent
STATE = HERE / "cache" / "paper_state.json"

FEE = 0.00075          # taker
FEE_MAKER = 0.00075    # maker (TP rests)
FUNDING_8H = 0.0001    # perp carry, per 8h


class PaperSDK:
    """Closed subset of ProprClient.sdk used by Bot.manage/_breakeven."""

    def __init__(self, owner):
        self._o = owner

    def get_orders(self, position_id: str, limit: int = 50) -> list[dict]:
        try:
            return list(self._o._orders.get(position_id, []))[:limit]
        except Exception as e:
            log.warning(f"paper get_orders {position_id}: {e}")
            return []

    def cancel_order(self, order_id: str) -> dict:
        for pos in self._o._orders.values():
            pos[:] = [o for o in pos if o.get("orderId") != order_id]
        return {"ok": True}


class PaperClient:
    """Virtual ProprRiskClient. Starts with $START (no challenge walls — see
    `rules`, which carry the same +10/5/10% shapes the beta trial uses)."""

    def __init__(self, starting_balance: float = 10000.0,
                 state_path: Path = STATE):
        self.starting_balance = float(starting_balance)
        self.account_id = f"urn:prp-account:PAPER_{int(self.starting_balance)}"
        self._cash = float(starting_balance)
        self._high_water_mark = self.starting_balance
        self._day_start = self.starting_balance
        self._trades_today = 0
        self._n = 0
        self._positions: dict[str, dict] = {}
        self._orders: dict[str, list] = {}
        self._closed: list[dict] = []
        self._state_path = state_path
        self.sdk = PaperSDK(self)
        self._load()
        self.account_id = f"urn:prp-account:PAPER_{int(self.starting_balance)}"

    # ------------------------------------------------------------------ rules
    @property
    def rules(self) -> ChallengeRules:
        return ChallengeRules(
            ChallengeType.CLASSIC_1STEP, AccountSize.K25,
            override_starting_balance=self.starting_balance,
            override_profit_target=self.starting_balance * 0.10,
            override_max_daily_loss_pct=0.05,
            override_max_drawdown_pct=0.10,
        )

    # ------------------------------------------------------------- persistence
    def _load(self):
        try:
            d = json.loads(self._state_path.read_text())
            self._cash = float(d.get("cash", self.starting_balance))
            self._high_water_mark = float(d.get("hwm", self.starting_balance))
            self._day_start = float(d.get("day_start", self.starting_balance))
            self._trades_today = int(d.get("trades_today", 0))
            self._positions = {k: dict(v) for k, v in d.get("positions", {}).items()}
            self._orders = {k: [dict(o) for o in v]
                            for k, v in d.get("orders", {}).items()}
            self._closed = [dict(c) for c in d.get("closed", [])]
            if self._positions:
                log.info(f"paper restored: {len(self._positions)} open, "
                         f"cash ${self._cash:,.2f}")
        except FileNotFoundError:
            pass
        except Exception as e:
            log.warning(f"paper state unreadable ({e}); fresh $"
                        f"{self.starting_balance:,.0f} account")

    def _save(self):
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._state_path.with_suffix(".tmp")
            tmp.write_text(json.dumps({
                "cash": self._cash,
                "hwm": self._high_water_mark,
                "day_start": self._day_start,
                "trades_today": self._trades_today,
                "positions": self._positions,
                "orders": self._orders,
                "closed": self._closed[-200:],
            }))
            tmp.replace(self._state_path)
        except Exception as e:
            log.warning(f"paper persist failed: {e}")

    # --------------------------------------------------------------- positions
    def setup(self, account_id: str | None = None,
              starting_balance: float | None = None) -> str:
        if starting_balance is not None:
            self.starting_balance = float(starting_balance)
            self.account_id = f"urn:prp-account:PAPER_{int(self.starting_balance)}"
        return self.account_id

    def _mark(self, asset: str) -> float | None:
        try:
            from bot import live_mid
            return live_mid(asset)
        except Exception as e:
            log.warning(f"paper mark {asset}: {e}")
            return None

    @property
    def unrealized_pnl(self) -> float:
        return sum(p["qty"] * (self._mark(p["asset"]) or p["entry"]) - p["qty"] * p["entry"]
                   for p in self._positions.values())

    @property
    def equity(self) -> float:
        # refresh marks + fill stops/tps before reporting, so the Bot's sizing
        # and halts read the truth, not a stale book.
        self._refresh()
        return self._cash + self.unrealized_pnl

    @property
    def balance(self) -> float:
        return self.equity

    def get_open_positions(self) -> list[dict]:
        self._refresh()
        out = []
        for pid, p in self._positions.items():
            mark = self._mark(p["asset"]) or p["entry"]
            p["markPrice"] = mark
            out.append({
                "base": p["asset"],
                "positionId": pid,
                "positionSide": "long",
                "entryPrice": p["entry"],
                "breakEvenPrice": p["breakEvenPrice"],
                "markPrice": mark,
                "quantity": p["qty"],
            })
        return out

    def _refresh(self):
        """Realize any stop/TP the tape touched. Uses the current forming 1h
        candle's low/high as well as the live mid, so a wick that crossed a
        stop is captured even if price recovered by the time we poll."""
        live_bar = {}
        for pid in list(self._positions):
            p = self._positions[pid]
            asset = p["asset"]
            bar = live_bar.get(asset)
            if bar is None:
                try:
                    import sim
                    bar = sim.fetch_candles(asset, days=1).iloc[-1]
                    live_bar[asset] = bar
                except Exception:
                    bar = None
            stop = p["stop"]; tp = p["tp"]
            fill = None; reason = None
            lo = hi = None
            if bar is not None:
                lo = float(bar["low"]); hi = float(bar["high"])
            if stop:
                hit = (lo is not None and lo <= stop)
                if hit:
                    fill, reason = stop, "stop"
            if not fill and tp:
                hit = (hi is not None and hi >= tp)
                if hit:
                    fill, reason = tp, "tp"
            if fill is not None:
                self._close_pos(pid, fill, reason)

    def _close_pos(self, pid: str, px: float, reason: str) -> None:
        if pid not in self._positions:
            return
        p = self._positions.pop(pid)
        self._orders.pop(pid, None)
        qty, entry, t0 = p["qty"], p["entry"], p["opened_at"]
        fee = FEE if reason == "stop" else FEE_MAKER
        pnl = (px - entry) * qty
        pnl -= qty * px * fee
        pnl -= qty * entry * FUNDING_8H * ((time.time() - t0) / 3600.0) / 8
        self._cash += pnl
        self._trades_today += 1
        self._closed.append({"asset": p["asset"], "reason": reason,
                             "fill": px, "pnl": pnl,
                             "ts": datetime.now(timezone.utc).isoformat()})
        log.info(f"PAPER CLOSE {p['asset']} ({reason}) @ {px} pnl ${pnl:,.2f}")
        try:
            import notify
            notify.send_close(p["asset"], reason, pnl)
        except Exception as e:
            log.warning(f"paper close notify failed: {e}")
        self._save()

    def open_long(self, asset: str, quantity: float,
                  stop_price: float | None = None,
                  take_profit_price: float | None = None,
                  entry_price: float | None = None) -> list[dict]:
        px = entry_price or self._mark(asset)
        if not px or px <= 0 or quantity <= 0:
            return []
        pid = f"PAPER-{int(time.time() * 1000)}"
        qty = float(quantity)
        self._n += 1
        self._positions[pid] = {
            "asset": asset, "qty": qty, "entry": px,
            "stop": float(stop_price) if stop_price else None,
            "tp": float(take_profit_price) if take_profit_price else None,
            "breakEvenPrice": px * (1 + FEE),     # includes fees, like the real one
            "opened_at": time.time(),
        }
        orders = []
        if stop_price:
            oid = f"{pid}-stop"
            orders.append({"orderId": oid, "type": "stop_market",
                           "triggerPrice": float(stop_price),
                           "positionId": pid})
        if take_profit_price:
            oid = f"{pid}-tp"
            orders.append({"orderId": oid, "type": "take_profit_market",
                           "triggerPrice": float(take_profit_price),
                           "positionId": pid})
        self._orders[pid] = orders
        self._cash -= qty * px * FEE
        log.info(f"PAPER LONG {asset} qty {qty:.6f} @ {px:.4f} "
                 f"(sl {stop_price} tp {take_profit_price})")
        self._save()
        return [{"ok": True, "positionId": pid}]

    def _place_conditional(self, order_type: str, asset: str, qty: str,
                           trigger: str, position_id: str) -> list[dict]:
        for pid in self._positions:
            if pid == position_id:
                self._orders.setdefault(pid, [])
                self._orders[pid] = [o for o in self._orders[pid]
                                     if o.get("type") != order_type]
                oid = f"{pid}-{order_type}-{int(time.time() * 1000)}"
                self._orders[pid].append({
                    "orderId": oid, "type": order_type,
                    "triggerPrice": float(trigger), "positionId": pid,
                })
                if order_type == "stop_market":
                    self._positions[pid]["stop"] = float(trigger)
                self._save()
                return [{"ok": True, "orderId": oid}]
        return []

    def close_position(self, asset: str) -> dict:
        for pid in list(self._positions):
            if self._positions[pid]["asset"] == asset:
                mark = self._mark(asset) or self._positions[pid]["entry"]
                self._close_pos(pid, mark, "max_hold")
                return {"ok": True}
        return {"ok": False}

    def get_account_summary(self) -> dict:
        eq = self.equity
        self._refresh()
        eq = self._cash + self.unrealized_pnl
        r = self.rules
        profit = eq - self.starting_balance
        profit_pct = profit / self.starting_balance
        daily_used = max(0.0, self._day_start - eq)
        dd_used = max(0.0, self.starting_balance - eq)
        return {
            "equity": eq,
            "balance": self._cash + self.unrealized_pnl,
            "unrealized_pnl": self.unrealized_pnl,
            "starting_balance": self.starting_balance,
            "profit": profit,
            "profit_pct": profit_pct,
            "target_pct": r.profit_target_pct,
            "target_reached": profit_pct >= r.profit_target_pct,
            "daily_floor": self._day_start - r.max_daily_loss_usd,
            "daily_used": daily_used,
            "daily_used_pct": daily_used / r.max_daily_loss_usd,
            "dd_floor": r.max_drawdown_equity_floor,
            "dd_used": dd_used,
            "dd_used_pct": dd_used / r.max_drawdown_usd,
            "high_water_mark": self._high_water_mark,
            "trades_today": self._trades_today,
        }
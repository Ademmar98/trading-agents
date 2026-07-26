"""
Propr Risk-Managed Client — Wraps SDK with position sizing, drawdown tracking, and circuit breakers.
"""
import time
import logging
from datetime import datetime, timezone

from .propr_sdk import ProprClient, ProprAPIError
from .config import ProprConfig

logger = logging.getLogger("propr_client")


class ProprRiskClient:
    def __init__(self, config: ProprConfig):
        self.config = config
        self.sdk = ProprClient(api_key=config.api_key, base_url=config.api_url)
        self.account_id: str | None = None
        self._starting_balance: float | None = None
        self._high_water_mark: float = 0.0
        self._day_start_balance: float | None = None
        self._day_start_date: str | None = None
        self._daily_realized_pnl: float = 0.0
        self._monthly_realized_pnl: float = 0.0
        self._last_trade_time: float = 0.0
        self._circuit_breaker_until: float = 0.0
        self._trades_today: int = 0
        self._closed_positions: list[dict] = []

    def setup(self) -> str:
        self.account_id = self.sdk.setup()
        account = self.sdk.get_account()
        self._starting_balance = float(account["balance"])
        self._high_water_mark = self._starting_balance
        self._day_start_balance = self._starting_balance
        self._day_start_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        logger.info(f"Propr client ready. Account: {self.account_id}, Balance: ${self._starting_balance:.2f}")
        return self.account_id

    @property
    def equity(self) -> float:
        account = self.sdk.get_account()
        balance = float(account["balance"])
        unrealized = float(account["totalUnrealizedPnl"])
        return balance + unrealized

    @property
    def balance(self) -> float:
        account = self.sdk.get_account()
        return float(account["balance"])

    @property
    def unrealized_pnl(self) -> float:
        account = self.sdk.get_account()
        return float(account["totalUnrealizedPnl"])

    @property
    def rules(self):
        return self.config.rules

    def _update_day(self):
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if today != self._day_start_date:
            self._day_start_balance = self.balance
            self._day_start_date = today
            self._daily_realized_pnl = 0.0
            self._trades_today = 0
            logger.info(f"New trading day: {today}, day-start balance: ${self._day_start_balance:.2f}")

    def _check_circuit_breakers(self) -> str | None:
        self._update_day()

        if time.time() < self._circuit_breaker_until:
            remaining = int(self._circuit_breaker_until - time.time())
            return f"Circuit breaker active, {remaining}s remaining"

        current_equity = self.equity
        starting = self._starting_balance

        # Daily loss check
        if self._day_start_balance and self._day_start_balance > 0:
            daily_loss_limit = self._day_start_balance * self.config.max_daily_loss_pct
            daily_floor = self._day_start_balance - daily_loss_limit
            if current_equity <= daily_floor:
                return f"DAILY LOSS BREACH: equity ${current_equity:.2f} <= floor ${daily_floor:.2f}"

        # Max drawdown check (static for 1-step)
        if current_equity <= self.rules.max_drawdown_equity_floor:
            return f"MAX DRAWDOWN BREACH: equity ${current_equity:.2f} <= floor ${self.rules.max_drawdown_equity_floor:.2f}"

        # Conservative daily loss check (our own limit)
        day_start = self._day_start_balance or starting
        conservative_daily_floor = day_start * (1 - self.config.max_daily_loss_pct)
        if current_equity <= conservative_daily_floor:
            self._circuit_breaker_until = time.time() + self.config.circuit_breaker_cooldown_sec
            return f"CONSERVATIVE DAILY LIMIT: equity ${current_equity:.2f} <= ${conservative_daily_floor:.2f}"

        # Conservative monthly DD check
        if self._high_water_mark > 0:
            monthly_dd = (self._high_water_mark - current_equity) / self._high_water_mark
            if monthly_dd >= self.config.max_monthly_dd_pct:
                self._circuit_breaker_until = time.time() + self.config.circuit_breaker_cooldown_sec
                return f"CONSERVATIVE MONTHLY DD: {monthly_dd:.1%} >= {self.config.max_monthly_dd_pct:.1%}"

        # Min trade interval
        if time.time() - self._last_trade_time < self.config.min_trade_interval_sec:
            return None  # Not a breach, just a skip

        return None

    def can_trade(self) -> tuple[bool, str]:
        reason = self._check_circuit_breakers()
        if reason:
            return False, reason

        positions = self.get_open_positions()
        if len(positions) >= self.config.max_concurrent_positions:
            return False, f"Max concurrent positions ({self.config.max_concurrent_positions}) reached"

        account = self.sdk.get_account()
        available = float(account["availableBalance"])
        if available < 100:
            return False, f"Insufficient balance: ${available:.2f}"

        return True, "OK"

    def get_open_positions(self) -> list[dict]:
        return self.sdk.get_open_positions()

    def get_account_summary(self) -> dict:
        self._update_day()
        current_equity = self.equity
        starting = self._starting_balance

        daily_loss_limit = self.rules.max_daily_loss_usd
        day_start = self._day_start_balance or starting
        daily_floor = day_start - daily_loss_limit
        daily_used = max(0, day_start - current_equity)
        daily_pct = daily_used / daily_loss_limit if daily_loss_limit > 0 else 0

        dd_floor = self.rules.max_drawdown_equity_floor
        dd_used = max(0, starting - current_equity)
        dd_pct = dd_used / self.rules.max_drawdown_usd if self.rules.max_drawdown_usd > 0 else 0

        profit = current_equity - starting
        profit_pct = profit / starting if starting > 0 else 0
        target_pct = self.rules.profit_target_pct

        return {
            "equity": current_equity,
            "balance": self.balance,
            "unrealized_pnl": self.unrealized_pnl,
            "starting_balance": starting,
            "profit": profit,
            "profit_pct": profit_pct,
            "target_pct": target_pct,
            "target_reached": profit_pct >= target_pct,
            "daily_floor": daily_floor,
            "daily_used": daily_used,
            "daily_used_pct": daily_pct,
            "dd_floor": dd_floor,
            "dd_used": dd_used,
            "dd_used_pct": dd_pct,
            "high_water_mark": self._high_water_mark,
            "trades_today": self._trades_today,
        }

    def calculate_position_size(self, asset: str, entry_price: float, stop_price: float) -> float:
        current_equity = self.equity
        notional = self.config.position_size_usd(current_equity)
        max_leverage = self.config.max_leverage_for(asset)

        risk_per_trade = current_equity * 0.02
        stop_distance = abs(entry_price - stop_price)
        if stop_distance <= 0:
            return 0

        risk_based_size = (risk_per_trade / stop_distance) * entry_price
        notional = min(notional, risk_based_size, current_equity * max_leverage)

        quantity = notional / entry_price
        return max(0, quantity)

    def open_long(self, asset: str, quantity: float, stop_price: float | None = None,
                  take_profit_price: float | None = None) -> list[dict]:
        can, reason = self.can_trade()
        if not can:
            logger.warning(f"Cannot trade {asset}: {reason}")
            return []

        qty_str = f"{quantity:.6f}"
        orders = self.sdk.market_buy(asset, qty_str)

        if orders:
            self._last_trade_time = time.time()
            self._trades_today += 1
            logger.info(f"OPENED LONG {asset} qty={qty_str}")

            time.sleep(3)

            positions = self.sdk.get_open_positions(base=asset)
            pos = positions[0] if positions else None
            if pos:
                position_id = pos["positionId"]
                if stop_price:
                    self._place_conditional("stop_market", asset, qty_str, str(stop_price), position_id)
                if take_profit_price:
                    self._place_conditional("take_profit_market", asset, qty_str, str(take_profit_price), position_id)
            else:
                logger.warning(f"No position found for {asset} after entry")

        return orders

    def _place_conditional(self, order_type: str, asset: str, quantity: str,
                           trigger_price: str, position_id: str) -> list[dict]:
        from ulid import ULID
        import requests as req

        order = {
            "accountId": self.account_id,
            "intentId": str(ULID()),
            "exchange": "hyperliquid",
            "type": order_type,
            "side": "sell",
            "positionSide": "short",
            "productType": "perp",
            "timeInForce": "GTC",
            "asset": asset,
            "base": asset,
            "quote": "USDC",
            "quantity": quantity,
            "triggerPrice": trigger_price,
            "reduceOnly": True,
            "closePosition": True,
            "positionId": position_id,
        }

        url = f"{self.config.api_url}/accounts/{self.account_id}/orders"
        headers = {
            "X-API-Key": self.config.api_key,
            "Content-Type": "application/json",
        }
        try:
            r = req.post(url, headers=headers, json={"orders": [order]}, timeout=10)
            if r.status_code >= 400:
                logger.warning(f"SL/TP failed for {asset}: {r.status_code} {r.text[:200]}")
                return []
            result = r.json().get("data", [])
            if result:
                status = result[0].get("status", "unknown")
                logger.info(f"  {order_type} placed for {asset}: status={status}")
            return result
        except Exception as e:
            logger.warning(f"SL/TP failed for {asset}: {e}")
            return []

    def close_position(self, asset: str) -> list[dict]:
        orders = self.sdk.close_position(asset)
        if orders:
            self._last_trade_time = time.time()
            logger.info(f"CLOSED position {asset}")
        return orders

    def _place_sl_tp(self, asset: str, order_type: str, side: str, trigger_price: str,
                     quantity: str) -> list[dict]:
        positions = self.sdk.get_open_positions(base=asset)
        position_id = positions[0]["positionId"] if positions else None

        position_side = "short" if side == "sell" else "long"

        try:
            return self.sdk.create_order(
                side=side,
                position_side=position_side,
                order_type=order_type,
                asset=asset,
                base=asset,
                quote="USDC",
                quantity=quantity,
                trigger_price=trigger_price,
                reduce_only=True,
            )
        except ProprAPIError as e:
            logger.warning(f"SL/TP order failed for {asset}: {e}")
            return []

    def get_pnl_summary(self) -> str:
        summary = self.get_account_summary()
        lines = [
            f"=== PROPR ACCOUNT SUMMARY ===",
            f"Equity:       ${summary['equity']:>12,.2f}",
            f"Balance:      ${summary['balance']:>12,.2f}",
            f"Unreal PnL:   ${summary['unrealized_pnl']:>12,.2f}",
            f"Realized PnL: ${summary['profit']:>12,.2f}",
            f"Profit:       {summary['profit_pct']:>11.1%}",
            f"Target:       {summary['target_pct']:>11.1%}",
            f"Target Hit:   {'YES' if summary['target_reached'] else 'NO'}",
            f"---",
            f"Daily Used:   {summary['daily_used_pct']:>11.0%} (${summary['daily_used']:.2f})",
            f"DD Used:      {summary['dd_used_pct']:>11.0%} (${summary['dd_used']:.2f})",
            f"Trades Today: {summary['trades_today']}",
            f"===============================",
        ]
        return "\n".join(lines)

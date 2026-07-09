import os
import sys
import time

from agents.base_agent import BaseAgent
from core.database import get_meta, set_meta, get_strategy_stats_list
from core.optimizer import test_single_param


class OptimizerAgent(BaseAgent):
    name = "optimizer"

    def run(self):
        audit = self.memory.read("reports", "audit") or {}
        summary = audit.get("summary", {})
        total_trades = summary.get("total_trades", 0)

        if total_trades < 10:
            self.log(f"Skip: only {total_trades} trades — need ≥10 for meaningful backtest")
            return

        stats = get_strategy_stats_list()
        if not stats:
            self.log("Skip: no strategy stats yet")
            return

        last_run = float(get_meta("optimizer_last_run", "0"))
        min_interval = 7200  # 2 hours between optimization tests
        if time.time() - last_run < min_interval:
            self.log(f"Skip: last run was {(time.time() - last_run) / 60:.0f}m ago")
            return

        from config import TUNABLE_PARAMS, RISK_TUNABLE_PARAMS
        param_name, meta = self._pick_weakest_param(summary, stats)
        if not param_name:
            self.log("No weak param identified — all look acceptable")
            return

        current_value = getattr(sys.modules.get("config"), param_name, meta["default"])
        if not isinstance(current_value, (int, float)):
            current_value = meta["default"]
        increment = meta["increment"]
        self.log(f"Testing {param_name} (current={current_value}, ±{increment})")

        try:
            best_val, result = test_single_param(param_name, current_value, increment)
        except Exception as e:
            self.log(f"Backtest failed for {param_name}: {e}")
            return

        if result is None:
            self.log(f"Backtest returned no result for {param_name}")
            return

        improvement = result["score"]
        old_score = 0
        _, current_result = test_single_param(param_name, current_value, increment)
        if current_result:
            old_score = current_result["score"]

        if improvement > old_score * 1.01:
            change_log = f"Proposal: {param_name} {current_value} -> {best_val} (score {old_score:.1f} -> {improvement:.1f})"
            self.log(change_log)

            # Risk guard: never auto-apply a change that widens a risk limit
            if param_name in RISK_TUNABLE_PARAMS and float(best_val) > float(current_value):
                self.notifier.on_agent_action("optimizer",
                    f"BLOCKED auto-widen of {param_name}: {current_value} -> {best_val} would increase risk")
                self.log(f"BLOCKED: {param_name}={best_val} would widen risk limit (kept {current_value})")
                set_meta("optimizer_last_run", str(time.time()))
                return

            set_meta(f"opt_{param_name}", str(best_val))
            set_meta(f"opt_{param_name}_score", str(improvement))
            set_meta(f"opt_{param_name}_at", str(time.time()))
            set_meta("optimizer_last_run", str(time.time()))
            os.environ[param_name] = str(best_val)
            cfg = sys.modules.get("config")
            if cfg:
                setattr(cfg, param_name, type(getattr(cfg, param_name, best_val))(best_val))
            self.notifier.on_agent_action("optimizer",
                f"Applied {param_name}={best_val} (was {current_value}, score {old_score:.1f}->{improvement:.1f})")
            self.log(f"Applied {param_name}={best_val} (score {improvement:.1f}, was {old_score:.1f})")
        else:
            set_meta("optimizer_last_run", str(time.time()))
            self.log(f"No improvement: {param_name}={current_value} (score {improvement:.1f} ≤ {old_score:.1f})")

    def _pick_weakest_param(self, summary, stats):
        from config import TUNABLE_PARAMS

        win_rate = summary.get("win_rate", 50)
        sharpe = summary.get("analytics", {}).get("sharpe", 0)
        profit_factor = summary.get("analytics", {}).get("profit_factor", 0) or 0
        total_pnl_pct = summary.get("total_pnl_pct", 0)

        candidates = []

        if win_rate < 45:
            candidates.append(("STOP_LOSS_PCT", "tighten SL"))
        if sharpe < 0.8:
            candidates.append(("SL_VOL_MULT", "adjust SL multiplier"))
        if profit_factor < 1.3:
            candidates.append(("TP_VOL_MULT", "adjust TP multiplier"))
        if total_pnl_pct < 0:
            candidates.append(("RISK_PER_TRADE_PCT", "reduce risk"))

        if not candidates:
            return None, None

        param_name = candidates[0][0]
        meta = TUNABLE_PARAMS.get(param_name)
        if not meta:
            return None, None
        return param_name, meta

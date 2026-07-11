import json
import re
import time

import requests

from config import (
    HERMES_API_KEY, HERMES_API_URL, HERMES_MODEL, HERMES_FALLBACK_MODEL,
    HEAD_TRADER_INTERVAL_MIN,
)
from agents.base_agent import BaseAgent
from core.analytics import compute_analytics
from core.database import get_meta, set_meta
from core.portfolio import load_portfolio

SYSTEM_PROMPT = """You are the head trader of an autonomous algorithmic trading firm.
You review the firm's own performance numbers and write a short internal memo.

Rules:
- Be blunt and specific. 5-10 lines maximum. No preamble, no pleasantries.
- Judge strategies by net PnL and expectancy, never win rate alone.
- Flag: strategies bleeding money, exit-reason patterns (winners clipped vs
  losers running), concentration in one asset cluster, and any metric that
  contradicts another.
- You have NO execution power. Your only lever is per-strategy confidence.
- End with exactly one line of JSON mapping strategy names to a confidence
  multiplier between 0.8 and 1.1 — ONLY for strategies with at least 5
  trades where you have a firm view. Example: {"ema_cross": 0.85, "fvg": 1.1}
- If you have no firm view, end with: {}
"""


class HeadTrader(BaseAgent):
    """LLM review layer over the firm's own numbers.

    Runs inside the cycle but self-throttles to HEAD_TRADER_INTERVAL_MIN.
    Output is advisory only: a memo for the dashboard/Telegram plus
    per-strategy confidence nudges, clamped to [0.8, 1.1], consumed by the
    PortfolioManager. Every trade still passes risk and compliance gates,
    and any failure here degrades to a no-op — the pipeline never blocks
    on an LLM.
    """

    name = "head_trader"

    def run(self):
        if not HERMES_API_KEY:
            return None
        now = time.time()
        last = float(get_meta("head_trader_last_run", "0") or 0)
        if now - last < HEAD_TRADER_INTERVAL_MIN * 60:
            return None
        # Stamp before calling out so an erroring API can't cause a retry storm
        set_meta("head_trader_last_run", str(now))

        context = self._gather_context()
        try:
            memo, model_used = self._ask(context)
        except Exception as e:
            self.log(f"Hermes review failed: {e}")
            return None

        report = {
            "memo": memo.strip(),
            "strategy_confidence": self._extract_confidence(memo),
            "model": model_used,
            "timestamp": now,
        }
        self.memory.write("reports", "head_trader", report)
        first_line = report["memo"].splitlines()[0][:160] if report["memo"] else "(empty memo)"
        self.log(f"Review ({model_used}): {first_line}")
        self.notifier.on_agent_action("head_trader", first_line)
        return report

    def _gather_context(self):
        analytics = compute_analytics()
        p = load_portfolio()
        regime = self.memory.read("analyses", "regime_scan") or {}
        gate = self.memory.read("decisions", "compliance_gate") or {}
        news = self.memory.read("reports", "news_scan") or {}
        breakdown = analytics.get("strategy_breakdown") or []
        context = {
            "equity": round(p.equity, 2),
            "cash": round(p.cash, 2),
            "initial_balance": p.initial_balance,
            "open_positions": len(p.positions),
            "exposure_pct": round(p.exposure_pct, 1),
            "total_trades": analytics.get("total_trades", 0),
            "win_rate": analytics.get("win_rate", 0),
            "profit_factor": analytics.get("profit_factor"),
            "expectancy": analytics.get("expectancy", 0),
            "avg_win": analytics.get("avg_win", 0),
            "avg_loss": analytics.get("avg_loss", 0),
            "max_drawdown_pct": analytics.get("max_drawdown_pct", 0),
            "strategy_breakdown": breakdown[:12],
            "regime_summary": regime.get("summary", {}),
            "compliance_warnings": gate.get("warnings", []),
            "news_tone": news.get("overall"),
            "news_by_symbol": {k: v.get("score") for k, v in (news.get("symbols") or {}).items()},
        }
        return json.dumps(context, default=str)

    def _ask(self, context):
        user_msg = (
            "Current firm state (JSON):\n" + context +
            "\n\nWrite your memo now, ending with the single-line JSON of "
            "strategy confidence multipliers."
        )
        errors = []
        for model in (HERMES_MODEL, HERMES_FALLBACK_MODEL):
            if not model:
                continue
            r = requests.post(
                HERMES_API_URL,
                headers={"Authorization": f"Bearer {HERMES_API_KEY}",
                         "Content-Type": "application/json"},
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": user_msg},
                    ],
                    # Reasoning models spend most of this thinking before the
                    # visible answer; too small a budget yields empty content.
                    "max_tokens": 6000,
                    "temperature": 0.3,
                },
                timeout=90,
            )
            data = r.json()
            content = (data.get("choices") or [{}])[0].get("message", {}).get("content")
            if content:
                return content, model
            errors.append(f"{model}: {str(data.get('message') or data)[:150]}")
        raise RuntimeError("; ".join(errors) or "no model produced a memo")

    @staticmethod
    def _extract_confidence(memo):
        """Pull the last valid JSON object out of the memo and clamp values.
        The clamp is the safety boundary: whatever the model says, a strategy
        can only be nudged, never disabled or supercharged."""
        out = {}
        for block in reversed(re.findall(r"\{[^{}]*\}", memo or "")):
            try:
                parsed = json.loads(block)
            except (ValueError, TypeError):
                continue
            if not isinstance(parsed, dict):
                continue
            for k, v in parsed.items():
                try:
                    out[str(k)] = round(max(0.8, min(1.1, float(v))), 3)
                except (ValueError, TypeError):
                    continue
            if out:
                break
        return out

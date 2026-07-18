# UNLEASHED TEST PROFILE — applied to VPS .env 2026-07-18
# Purpose: remove ALL risk brakes for the 1-week judging cycle (paper money),
# so per-strategy/agent performance data is collected unfiltered.
# Real limits get re-set from the week's data afterwards.
# Restore: on VPS, cp /root/firm/.env.pre_unleashed /root/firm/.env && systemctl restart trading-firm

SCOUT_MODE_ENABLED=true        # keep risk_off deployable (dial would else force full cash)
SCOUT_MAX_DEPLOY_PCT=100       # deploy up to 100% even while BTC < SMA200
SCOUT_RISK_PER_TRADE_PCT=2.0   # == RISK_PER_TRADE_PCT -> clamp is neutral
SMA200_DEPLOY_TARGET=1.0
SMA200_UNKNOWN_TARGET=1.0

RISK_PER_TRADE_PCT=2.0         # meaningful size per trade (was 0.5)
SWING_RISK_PER_TRADE_PCT=2.0
MAX_POSITION_SIZE_PCT=100
MAX_PORTFOLIO_RISK_PCT=100

MAX_PEAK_DRAWDOWN_PCT=100      # no drawdown halt
STREAK_LOSS_HALT_PCT=100       # no streak breaker
DAILY_LOSS_LIMIT_PCT=100
MAX_DAILY_LOSS_USD=10000000
MAX_WEEKLY_LOSS_PCT=100

MAX_TRADES_PER_DAY=0           # 0 = unlimited (verified compliance_agent.py:116)
MAX_TRADES_PER_HOUR=0
MAX_OPEN_RISK_PCT=0            # 0 = off (:138)
MAX_POSITIONS_PER_CLUSTER=0    # 0 = off (:198)
MAX_GROUP_POSITIONS=0          # 0 = off (:204)
MAX_PAIR_CORRELATION=0         # 0 = off
PER_STRATEGY_MAX_OPEN=999      # NOTE: knob is currently dead code (no enforcement found)
MACRO_DIP_PCT=100              # interlock never fires
SESSION_RISK_MULTS=1.0,1.0,1.0,1.0
SCALP_MIN_WIN_PROB=0           # win-prob gate open -> all setups trade, all get judged

# KEPT ON PURPOSE (integrity, not limits):
# BUY_ONLY=true (long-only firm policy), MAX_GROSS_LEVERAGE=1.0 (no margin, ever),
# BROKEN_SL_PCT=20 (corrupt-pricing rejection), MIN/MAX SL/TP geometry guards,
# MIN_TP_PROFIT_USD=1.0 (dust-trade filter), TRADE_FEE_PCT + slippage model,
# exit mechanics (trailing/breakeven/partial-TP), DEBATE agent ON (we WANT to
# observe the agents argue; its verdicts are part of the firm's behavior data).

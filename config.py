import os
import sys
import zlib
from pathlib import Path

BASE_DIR = Path(__file__).parent
# Overridable so tests can run against a throwaway data dir
DATA_DIR = Path(os.getenv("TRADING_DATA_DIR", BASE_DIR / "data"))

# ── Environment variable loading ──
# Railway / production: inject secrets via the cloud dashboard (env vars).
# Local dev: create a .env file beside this script (git-ignored) as a fallback.
# System env vars ALWAYS take priority over .env (setdefault).
_env_file = BASE_DIR / ".env"
if _env_file.exists():
    for _line in _env_file.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _key, _, _value = _line.partition("=")
            os.environ.setdefault(_key.strip(), _value.strip())

INITIAL_BALANCE = float(os.getenv("TRADING_CAPITAL", "10000"))
# Crypto-only firm. Stocks/metals/forex trading was removed 2026-07-14 —
# a spot-crypto book on 24/7 markets with a single high-quality data feed.
MARKET_TYPE = "crypto"

ALPACA_API_KEY = os.getenv("ALPACA_API_KEY", "")
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY", "")
ALPACA_PAPER = os.getenv("ALPACA_PAPER", "true").lower() == "true"

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-20250514")

# Nous Research Hermes — powers the HeadTrader review agent (OpenAI-compatible)
HERMES_API_KEY = os.getenv("HERMES_API_KEY", "")
HERMES_API_URL = os.getenv("HERMES_API_URL", "https://inference-api.nousresearch.com/v1/chat/completions")
HERMES_MODEL = os.getenv("HERMES_MODEL", "nousresearch/hermes-4-70b")
# Used when the primary model is unavailable (e.g. no credits on the account)
HERMES_FALLBACK_MODEL = os.getenv("HERMES_FALLBACK_MODEL", "stepfun/step-3.7-flash:free")
HEAD_TRADER_INTERVAL_MIN = int(os.getenv("HEAD_TRADER_INTERVAL_MIN", "60"))

# ── Debate agent (adversarial bull/bear/arbiter review of the portfolio plan) ──
# Runs after PortfolioManager and before Compliance: the top DEBATE_TOP_N
# candidates by confidence each get a bull argument FOR, a bear argument
# AGAINST, and an arbiter verdict in {APPROVE, DOWNGRADE, REJECT}.
# HARD SAFETY BOUNDS: the arbiter can never create a trade, never raise
# confidence, and never widen size — APPROVE passes through unchanged,
# DOWNGRADE multiplies confidence by DEBATE_DOWNGRADE_MULT, REJECT removes
# the candidate. All existing deterministic gates (compliance, execution,
# risk) stay downstream and unchanged. Any LLM trouble fails OPEN: the plan
# passes through untouched. DEBATE_ENABLED=false makes the agent a no-op.
DEBATE_ENABLED = os.getenv("DEBATE_ENABLED", "true").lower() == "true"
DEBATE_TOP_N = int(os.getenv("DEBATE_TOP_N", "3"))
DEBATE_DOWNGRADE_MULT = float(os.getenv("DEBATE_DOWNGRADE_MULT", "0.85"))
# Total wall-clock budget for one cycle's debates (all candidates, all
# rounds). Per-request timeouts shrink to fit inside this budget.
DEBATE_TIMEOUT_SEC = int(os.getenv("DEBATE_TIMEOUT_SEC", "90"))
# When the SMA200 dial has the firm at 0% deployment, compliance will reject
# every candidate anyway — skip the debate round entirely (zero LLM spend,
# no 90s stall) and pass the plan through untouched.
DEBATE_SKIP_WHEN_CAPPED = os.getenv("DEBATE_SKIP_WHEN_CAPPED", "true").lower() == "true"
# LLM circuit breaker: after this many consecutive total LLM failures, stop
# calling the endpoint for the cooldown and let the deterministic engine
# judge instantly (a dead endpoint was stretching every cycle by ~90s).
DEBATE_LLM_BREAKER_FAILS = int(os.getenv("DEBATE_LLM_BREAKER_FAILS", "3"))
DEBATE_LLM_BREAKER_COOLDOWN_SEC = int(os.getenv("DEBATE_LLM_BREAKER_COOLDOWN_SEC", "1800"))

# ── Scout mode (test cycle, 2026-07-18) ──
# The SMA200 dial says risk_off (BTC below SMA200) for possibly the whole
# week; with the cap absolute the expanded-pool test cycle would collect ZERO
# forward trades. Scout mode keeps the insurance but allows tiny probe
# entries while risk_off: total deployment is still capped (SCOUT_MAX_DEPLOY_PCT
# of equity) and per-trade risk is clamped to SCOUT_RISK_PER_TRADE_PCT, so the
# week produces per-strategy forward stats at trivial cost. Set false to make
# the dial absolute again (full cash in risk_off).
SCOUT_MODE_ENABLED = os.getenv("SCOUT_MODE_ENABLED", "true").lower() == "true"
SCOUT_MAX_DEPLOY_PCT = float(os.getenv("SCOUT_MAX_DEPLOY_PCT", "10"))
SCOUT_RISK_PER_TRADE_PCT = float(os.getenv("SCOUT_RISK_PER_TRADE_PCT", "0.1"))

# ── Firm direction policy ──
# BUY-only: all sell-side signal generation, analysis, and routing is
# disabled — agents spend their entire effort on long setups. Closing an
# open long is unaffected (that SELL is an exit, not a position).
BUY_ONLY = os.getenv("BUY_ONLY", "true").lower() == "true"

# ── Optimizer ──
# Disabled 2026-07-15. It fits parameters to the classic strategies (proven
# net-negative and now switched off) using a COST-FREE backtest on BTC only,
# then mutates live risk params (RISK_PER_TRADE_PCT, SL_VOL_MULT, ...) that
# size real trades. Tuning dead signals on a cost-free model is worse than not
# tuning at all — it silently moves live risk based on noise.
OPTIMIZER_ENABLED = os.getenv("OPTIMIZER_ENABLED", "false").lower() == "true"

# ── News agent (advisory only) ──
# The scan still runs and publishes a memo for the dashboard/Telegram, but it
# no longer nudges confidence — nothing unproven touches ranking or routing.
NEWS_AGENT_ENABLED = os.getenv("NEWS_AGENT_ENABLED", "true").lower() == "true"
NEWS_INTERVAL_MIN = int(os.getenv("NEWS_INTERVAL_MIN", "15"))

# ── BUY-limit entries (microstructure) ──
# When price is extended above session VWAP, rest a limit at VWAP instead of
# paying up at market; unfilled limits expire.
USE_LIMIT_ENTRIES = os.getenv("USE_LIMIT_ENTRIES", "true").lower() == "true"
LIMIT_ENTRY_EXT_PCT = float(os.getenv("LIMIT_ENTRY_EXT_PCT", "0.3"))
LIMIT_ORDER_TTL_MIN = int(os.getenv("LIMIT_ORDER_TTL_MIN", "60"))

# ── 15-minute scalping stack ──
# Entry: price above/below EMA (trend filter) + MACD crossover in the trend
# direction + RSI guard against buying tops / selling bottoms.
# Exits: SL = SCALP_ATR_SL_MULT x ATR(14); TP from the win-rate/R:R matrix.
# ── Signal policy (Phase 1 evidence, 2026-07-15) ──
# The 28-strategy classic battery (core/strategies.py) was proven to have
# NEGATIVE net expectancy after costs on EVERY strategy and in EVERY regime —
# analysis/strategy_expectancy.py: 0 of 24 survivors, t-stats -5 to -21 over
# ~70k trades. They (and the multiframe aggregator + the scalping_signals
# scorer built on the same signals) are disabled as live sources. The library
# ── TEST CYCLE (2026-07-17): full pool re-enabled for the 1-week forward
# experiment. The registry now carries 174 strategies (28 legacy + 146 new in
# core/strats/) with corrected indicator math (Donchian/HA/ADX/RSI-div/Ichimoku/
# VWAP were broken when the negative-expectancy verdict was measured). The
# week's forward record + a fresh harness re-run decide what stays; the
# per-strategy auto-cull (get_unprofitable_strategies) removes live losers.
CLASSIC_STRATEGIES_ENABLED = os.getenv("CLASSIC_STRATEGIES_ENABLED", "true").lower() == "true"

# ── Deployment dial: the ONE mechanism this firm has evidence for ──
# Rule: deploy while the bellwether (BTC) closes above its SMA200; sit in cash
# below it. Validated over 6.6 years incl. the 2022 bear (analysis/edge_hunt.py,
# 2019-11 -> 2026-07): vs buy & hold BTC it cut max drawdown 76.6% -> 63.9% at a
# HIGHER Sharpe (0.93 vs 0.85), costing ~19% of return across 26 trades.
#
# This is NOT alpha — it is drawdown insurance, and it is the only mechanism in
# the codebase the data endorses. It replaces the previous ADX/regime-label dial,
# which was never validated and sat on a arithmetic bug in core/regime.py.
SMA200_PERIOD = int(os.getenv("SMA200_PERIOD", "200"))
# Deployed fraction while risk-on. The backtest was fully invested; 0.85 keeps a
# cash buffer for costs and the no-leverage (gross <= equity) invariant.
SMA200_DEPLOY_TARGET = float(os.getenv("SMA200_DEPLOY_TARGET", "0.85"))
# Deployment when there isn't enough history to compute the SMA200 — minimal,
# because an unknown state is not a licence to deploy.
SMA200_UNKNOWN_TARGET = float(os.getenv("SMA200_UNKNOWN_TARGET", "0.20"))
FIRM_BELLWETHER = os.getenv("FIRM_BELLWETHER", "BTC/USD")

# DISABLED — evidence twice over. Phase 1b (analysis/scalp_swing_expectancy.py,
# 3097 trades) and the 2026-07 scalp study (research/scalp_2026_07/, 8490
# bracket-simulated trades over 6 majors × 2y against the deployed code) both
# find the stack net-negative on EVERY timeframe: realized win rate 37-40% is
# statistically identical to random entry, net -0.124%/trade — the ~0.14%
# round-trip cost buries a signal that adds no edge. The 1-week test cycle
# (2026-07-17 → 2026-07-21) is over and confirmed the negative. Off as a live
# source; the module stays for research. Re-enable with SCALP_15M_ENABLED=true.
SCALP_15M_ENABLED = os.getenv("SCALP_15M_ENABLED", "false").lower() == "true"
# The same EMA/MACD/RSI stack runs independently on every listed timeframe;
# each gets its own strategy tag (scalp_1m ... scalp_4h) so the learning
# loop judges them separately.
SCALP_TIMEFRAMES = [t for t in os.getenv(
    "SCALP_TIMEFRAMES", "1m,5m,15m,30m,1h,4h").split(",") if t.strip()]
SCALP_EMA_PERIOD = int(os.getenv("SCALP_EMA_PERIOD", "50"))
SCALP_ATR_SL_MULT = float(os.getenv("SCALP_ATR_SL_MULT", "1.5"))
SCALP_RSI_OVERBOUGHT = float(os.getenv("SCALP_RSI_OVERBOUGHT", "70"))
SCALP_RSI_OVERSOLD = float(os.getenv("SCALP_RSI_OVERSOLD", "30"))
# Predictive gate before order routing: scalp setups whose estimated win
# probability is below this are aborted. The estimate is a Laplace-smoothed
# empirical win rate + indicator-synergy bonus — an honest heuristic, NOT a
# calibrated probability. A fresh strategy starts near 0.5-0.6 and can only
# climb by closing winners, so a 0.92 bar is a permanent off-switch (no
# trades -> no history -> estimate never rises). Bootstrap at 0.60 and raise
# the bar (e.g. toward 0.92) as the live record earns it.
SCALP_MIN_WIN_PROB = float(os.getenv("SCALP_MIN_WIN_PROB", "0.60"))

# ── Swing desk (runs beside the scalp stack) ──
# Multi-day BUY positions from daily structure with 4h alignment; each setup
# carries its own strategy tag (swing_breakout / swing_pullback /
# swing_momentum) so the learning loop judges styles separately.
# Requirements per entry: daily uptrend (SMA20>SMA50 and price>SMA50) AND
# 4h close above its EMA50. Exits are swing-scale by firm policy:
# SL = SWING_ATR_SL_MULT x daily ATR clamped to 1-25% below entry;
# TP = SL x SWING_RR clamped to 3-100% above entry.
SWING_ENABLED = os.getenv("SWING_ENABLED", "true").lower() == "true"
SWING_SCAN_INTERVAL_MIN = int(os.getenv("SWING_SCAN_INTERVAL_MIN", "30"))
SWING_RISK_PER_TRADE_PCT = float(os.getenv("SWING_RISK_PER_TRADE_PCT", "0.5"))
SWING_ATR_SL_MULT = float(os.getenv("SWING_ATR_SL_MULT", "2.0"))
SWING_RR = float(os.getenv("SWING_RR", "3.0"))
SWING_MIN_SL_PCT = float(os.getenv("SWING_MIN_SL_PCT", "1.0"))
SWING_MAX_SL_PCT = float(os.getenv("SWING_MAX_SL_PCT", "25.0"))
SWING_MIN_TP_PCT = float(os.getenv("SWING_MIN_TP_PCT", "3.0"))
SWING_MAX_TP_PCT = float(os.getenv("SWING_MAX_TP_PCT", "100.0"))

# ── Firm goals (reporting targets, not trade gates) ──
# Daily: +0.5% to +3% of equity per day. Total: +10% to +50% of capital.
# Progress is shown on the dashboard/daily summary; hitting a goal pings
# Telegram once per day / once per milestone.
DAILY_PROFIT_TARGET_MIN = float(os.getenv("DAILY_PROFIT_TARGET_MIN", "0.5"))
DAILY_PROFIT_TARGET_MAX = float(os.getenv("DAILY_PROFIT_TARGET_MAX", "3.0"))
TOTAL_PROFIT_TARGET_MIN = float(os.getenv("TOTAL_PROFIT_TARGET_MIN", "10.0"))
TOTAL_PROFIT_TARGET_MAX = float(os.getenv("TOTAL_PROFIT_TARGET_MAX", "50.0"))

BROKER_TYPE = os.getenv("BROKER_TYPE", "paper")  # paper, binance, alpaca (crypto venues only)

BINANCE_API_KEY = os.getenv("BINANCE_API_KEY", "")
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET", "")
BINANCE_USE_TESTNET = os.getenv("BINANCE_USE_TESTNET", "true").lower() == "true"

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

LEVERAGE_ENABLED = False  # spot-only, no margin
MAX_POSITION_SIZE_PCT = float(os.getenv("MAX_POSITION_SIZE_PCT", "15"))
MAX_PORTFOLIO_RISK_PCT = float(os.getenv("MAX_PORTFOLIO_RISK_PCT", "15"))
# Peak-relative drawdown halt (compliance gate): entries stop when equity
# falls this % below its high-water mark. The old check anchored at INITIAL
# balance — a book that ran up then bled back still looked "inside limit",
# and a slow grind down from a peak never fired (readiness audit §4 lists it
# as a kill-switch; it must measure from the peak to work as one).
MAX_PEAK_DRAWDOWN_PCT = float(os.getenv("MAX_PEAK_DRAWDOWN_PCT", "10"))
TRADE_FEE_PCT = float(os.getenv("TRADE_FEE_PCT", "0.1"))
TRADING_TIMEFRAME = os.getenv("TRADING_TIMEFRAME", "5m")
# Half-spread cost per side applied in backtests, mirroring live fills that
# pay the ask / hit the bid instead of the mid (0.05 = 0.05% per leg).
BACKTEST_SPREAD_PCT = float(os.getenv("BACKTEST_SPREAD_PCT", "0.05"))
BACKTEST_BARS = int(os.getenv("BACKTEST_BARS", "2500"))
# Legacy count-based halt (kept for compatibility; the live breaker is now
# STREAK_LOSS_HALT_PCT — money, not count).
MAX_CONSECUTIVE_LOSSES = int(os.getenv("MAX_CONSECUTIVE_LOSSES", "3"))
# Streak breaker in money: a run of consecutive losing positions whose
# combined loss reaches this % of equity halts new entries. Three dust
# losses shouldn't stop the firm; a streak that bleeds real capital should.
STREAK_LOSS_HALT_PCT = float(os.getenv("STREAK_LOSS_HALT_PCT", "1.2"))
STOP_LOSS_PCT = float(os.getenv("STOP_LOSS_PCT", "2.0"))
DAILY_LOSS_LIMIT_PCT = float(os.getenv("DAILY_LOSS_LIMIT_PCT", "3"))
# ── Paper-cycle loss rails (added for the 1-week expanded-pool paper test) ──
# Absolute daily loss floor alongside the %-based breaker above: on a $10k
# book 3% is $300; the USD cap keeps the same bite if TRADING_CAPITAL is
# raised for the cycle, so a bigger bankroll can't lose more per day.
MAX_DAILY_LOSS_USD = float(os.getenv("MAX_DAILY_LOSS_USD", "300"))
# Weekly loss budget (% of equity): a judged cycle must survive to its
# verdict — past -5% on the week the firm stops entering and lets the open
# book play out rather than bleeding the sample to zero.
MAX_WEEKLY_LOSS_PCT = float(os.getenv("MAX_WEEKLY_LOSS_PCT", "5"))
# Per-strategy concurrency cap (open positions per strategy tag): with an
# expanded pool one hot strategy must not fill every slot — capping each tag
# keeps the cycle's per-strategy samples comparable.
PER_STRATEGY_MAX_OPEN = int(os.getenv("PER_STRATEGY_MAX_OPEN", "2"))
TRAILING_STOP_PCT = float(os.getenv("TRAILING_STOP_PCT", "0.5"))
TRAILING_ACTIVATION_PCT = float(os.getenv("TRAILING_ACTIVATION_PCT", "0.8"))
# Scaled exits: bank part of a winner at a multiple of initial risk (R), move
# the stop to breakeven, and let the remainder trail. R-based trailing replaces
# the percent-of-price trail, which clipped winners at a fraction of what a
# full stop-loss costs.
PARTIAL_TP_ENABLED = os.getenv("PARTIAL_TP_ENABLED", "true").lower() == "true"
PARTIAL_TP_R = float(os.getenv("PARTIAL_TP_R", "1.5"))
PARTIAL_TP_FRACTION = float(os.getenv("PARTIAL_TP_FRACTION", "0.5"))
TRAILING_ACTIVATION_R = float(os.getenv("TRAILING_ACTIVATION_R", "1.0"))
TRAILING_STOP_R = float(os.getenv("TRAILING_STOP_R", "0.5"))
BREAKEVEN_ENABLED = os.getenv("BREAKEVEN_ENABLED", "true").lower() == "true"
# Breakeven activates when profit reaches BREAKEVEN_ACTIVATION_PCT% of the
# entry-to-SL distance (1R). Default 100 = exactly at 1:1 R:R.
# After breakeven the stop stays parked at entry + buffer — no further
# trailing — so the trade has room to reach the final TP.
BREAKEVEN_ACTIVATION_PCT = float(os.getenv("BREAKEVEN_ACTIVATION_PCT", "100"))
# Tiny buffer above entry for the breakeven stop to avoid noise/commission
# exits. Applied after breakeven activates (default 0.15%).
# Must exceed the round-trip fee (2 × TRADE_FEE_PCT = 0.2%) or a
# "breakeven" exit still loses money after commissions.  0.3 % gives
# a 0.1 % safety margin above the default 0.2 % round-trip cost.
BREAKEVEN_BUFFER_PCT = float(os.getenv("BREAKEVEN_BUFFER_PCT", "0.3"))
# Fallback SL/TP multipliers — used by ExecutionAgent when pricing data is absent
SL_VOL_MULT = float(os.getenv("SL_VOL_MULT", "1.5"))
TP_VOL_MULT = float(os.getenv("TP_VOL_MULT", "2.0"))
# Hard caps on any computed stop/target distance. A scalping firm has no
# business holding a 29% stop — that was daily-range volatility leaking into
# 15m trade pricing. Caps are the last line of defense; the primary fix is
# pricing from the trading timeframe's own ATR.
MAX_SL_PCT = float(os.getenv("MAX_SL_PCT", "3.0"))
MAX_TP_PCT = float(os.getenv("MAX_TP_PCT", "6.0"))
# Must clear the round-trip fee (2 x TRADE_FEE_PCT = 0.2%) with real margin,
# or trades that hit minimum TP still lose money after costs.
MIN_TP_PCT = float(os.getenv("MIN_TP_PCT", "0.5"))
# Minimum absolute profit at TP (USD): skip setups whose take-profit, at the
# final position size, would earn less than this. Tiny sub-dollar scalps burn
# a position slot and fees for pennies — not worth taking.
MIN_TP_PROFIT_USD = float(os.getenv("MIN_TP_PROFIT_USD", "1.0"))
# Position-size multiplier. Back to 1.0 (plain fractional risk) 2026-07-15:
# with no signal yet proven to have edge (classics + scalp proven negative,
# swing statistically flat), doubling size would only double the losses.
# Sizing is now pure fractional risk (RISK_PER_TRADE_PCT) under the regime
# cash-dial. Raise POSITION_SIZE_MULT only once a signal proves positive
# expectancy on forward data.
POSITION_SIZE_MULT = float(os.getenv("POSITION_SIZE_MULT", "1.0"))
# Broken-geometry bound: a stop farther than this from entry means the
# volatility inputs are corrupt (the 29.5%-SL class of bug), not a trade.
# Distinct from MAX_SL_PCT: that caps normal pricing, this rejects garbage.
BROKEN_SL_PCT = float(os.getenv("BROKEN_SL_PCT", "20"))
# Quiet Telegram: only trade opens/closes, daily summary, halts/errors and
# rejected-signal alerts. Per-agent chatter stays in the logs/dashboard.
TELEGRAM_QUIET = os.getenv("TELEGRAM_QUIET", "true").lower() == "true"
# Trade-frequency caps. 0 = unlimited. Set a positive number to cap entries
# per UTC day / per rolling hour — risk data says overtrading loses, but the
# caps also idle the bot once hit.
# 150/day was no cap at all for a swing-only firm (readiness audit 1.8): a
# 30-minute swing scan over ~20 symbols cannot legitimately produce 150
# entries. 20/day still leaves headroom for the expanded pool's paper cycle
# (~1 entry per symbol per day) while bounding overtrading.
MAX_TRADES_PER_DAY = int(os.getenv("MAX_TRADES_PER_DAY", "20"))
MAX_TRADES_PER_HOUR = int(os.getenv("MAX_TRADES_PER_HOUR", "0"))
# Portfolio heat: total open risk (distance to stop x qty, summed over open
# positions) as % of equity. Blocks new entries only while above the cap —
# exits always run. 0 = off. Breakeven'd runners contribute zero risk.
MAX_OPEN_RISK_PCT = float(os.getenv("MAX_OPEN_RISK_PCT", "10"))
# Concurrent positions per asset cluster (crypto / stock / forex+metals):
# 15 crypto longs are one BTC-beta bet, not 15 independent trades. 0 = off.
MAX_POSITIONS_PER_CLUSTER = int(os.getenv("MAX_POSITIONS_PER_CLUSTER", "8"))
# When a candidate's 30d returns correlate >= this with an already-open
# position, the risk manager BLOCKS the entry — it adds beta, not
# diversification. 0.9 never fired: alt pairs run 0.7-0.85 in a BTC-led tape
# (post-mortem 2026-07-12: six correlated alts stopped at the cap in one
# dip), and merely halving size still let the cluster through. 0 = off.
MAX_PAIR_CORRELATION = float(os.getenv("MAX_PAIR_CORRELATION", "0.7"))
# ── Correlated-selloff defenses (post-mortem 2026-07-12: 6 alt BUYs all
# stopped at the cap during one Asian-session BTC dip) ──
# Correlation groups: assets that move as one. Cap simultaneous positions
# per group; only exceptional confidence may exceed it.
CORRELATION_GROUPS = {
    "crypto_alts": ["AAVE/USD", "UNI/USD", "ADA/USD", "BCH/USD", "DOT/USD",
                    "LTC/USD", "LINK/USD", "AVAX/USD", "SOL/USD", "XRP/USD",
                    "DOGE/USD", "ATOM/USD", "TRX/USD", "MATIC/USD", "APT/USD",
                    "ARB/USD", "OP/USD", "BNB/USD"],
    "crypto_majors": ["BTC/USD", "ETH/USD"],
}
MAX_GROUP_POSITIONS = int(os.getenv("MAX_GROUP_POSITIONS", "2"))
GROUP_OVERRIDE_CONF = float(os.getenv("GROUP_OVERRIDE_CONF", "0.85"))
# Session-aware risk sizing: low-liquidity hours amplify moves and spreads.
# Asian (00-08 UTC) x0.5, European (08-14) x0.8, US overlap (14-22) x1.0,
# late-US/pre-Asian (22-24) x0.5.
SESSION_RISK_MULTS = os.getenv("SESSION_RISK_MULTS", "0.5,0.8,1.0,0.5")
# Macro dip interlock: bellwether down more than this % in ~30min pauses new
# entries in its whole asset class until it stabilizes.
MACRO_DIP_PCT = float(os.getenv("MACRO_DIP_PCT", "1.0"))
MACRO_DIP_OVERRIDE_CONF = float(os.getenv("MACRO_DIP_OVERRIDE_CONF", "0.9"))
MACRO_BELLWETHERS = {"crypto": "BTC/USD"}
# SL floor: ATR-first placement may never be tighter than this (noise floor).
# 0.3% was a fee trap: the round trip costs 2x0.1% fee + 2x0.05% spread =
# 0.3%, so a minimum-distance stop spent its entire 1R on costs before any
# slippage. 1.0% keeps worst-case costs near 1/3 of risk and matches the
# swing desk's own SWING_MIN_SL_PCT floor.
MIN_SL_PCT = float(os.getenv("MIN_SL_PCT", "1.0"))

# Hard no-leverage rule (halal requirement): total open notional may never
# exceed equity x this factor. 1.0 = strict cash-only trading — no margin,
# ever, on any venue. This is firm policy, not a tunable risk knob.
MAX_GROSS_LEVERAGE = float(os.getenv("MAX_GROSS_LEVERAGE", "1.0"))
# Base risk percentage fed to PricingAgent's per-opportunity calculated_risk_pct
RISK_PER_TRADE_PCT = float(os.getenv("RISK_PER_TRADE_PCT", "0.5"))
_LOCK_PORT_OVERRIDE = int(os.getenv("TRADING_LOCK_PORT", "0"))
if _LOCK_PORT_OVERRIDE:
    LOCK_PORT = _LOCK_PORT_OVERRIDE
else:
    LOCK_PORT = 48620 + (zlib.crc32(str(DATA_DIR).encode()) % 1000)

TRADING_INTERVAL_MINUTES = int(os.getenv("TRADING_INTERVAL_MINUTES", "1"))

# ── Multi-agent desk (asyncio actor runtime) ──
# When on, agents run concurrently on a message bus and every trade goes
# through a deliberation (propose → review → counter/veto → verdict).
# Default OFF: the legacy sequential pipeline stays the production path
# until the desk has soaked; flip MULTI_AGENT_MODE=true to opt in.
MULTI_AGENT_MODE = os.getenv("MULTI_AGENT_MODE", "false").lower() == "true"
NEGOTIATION_ROUNDS = int(os.getenv("NEGOTIATION_ROUNDS", "2"))
REVIEW_TIMEOUT_SECONDS = float(os.getenv("REVIEW_TIMEOUT_SECONDS", "20"))
PROPOSALS_PER_SCAN = int(os.getenv("PROPOSALS_PER_SCAN", "3"))
# After a rejected proposal, the analyst won't re-propose the same
# symbol+direction for this many scan ticks.
PROPOSAL_COOLDOWN_TICKS = int(os.getenv("PROPOSAL_COOLDOWN_TICKS", "3"))

# Startup backtests (informational only). With the 174-strategy registry the
# per-bar legacy battery scan takes HOURS of CPU at 100% and blocks the entire
# trading loop (found 2026-07-18: bot sat in run_all_backtests for 4.5h after
# restart, zero cycles). Default OFF — the service must start trading within
# seconds; the same harness runs offline via analysis/ scripts when wanted.
STARTUP_BACKTESTS_ENABLED = os.getenv("STARTUP_BACKTESTS_ENABLED", "false").lower() == "true"

# Tunable parameters — each entry defines a range and step for the optimizer
TUNABLE_PARAMS = {
    "SL_VOL_MULT":       {"default": 1.5, "min": 0.3,  "max": 3.0, "increment": 0.3},
    "TP_VOL_MULT":       {"default": 2.0, "min": 0.5,  "max": 5.0, "increment": 0.5},
    "RISK_PER_TRADE_PCT":{"default": 0.5, "min": 0.1,  "max": 2.0, "increment": 0.1},
    "STOP_LOSS_PCT":     {"default": 2.0, "min": 0.3,  "max": 5.0, "increment": 0.3},
}

# Params whose values must never increase via auto-tuning (risk limits)
RISK_TUNABLE_PARAMS = {"MAX_POSITION_SIZE_PCT", "RISK_PER_TRADE_PCT", "STOP_LOSS_PCT", "SL_VOL_MULT"}

# Breakeven SL: when price reaches this % of the TP distance, move SL to entry
# Set BREAKEVEN_ENABLED=false to disable, BREAKEVEN_ACTIVATION_PCT=0 to use 1x SL distance only

# Crypto spot universe (BASE/QUOTE). Crypto-only firm — no stocks/metals/forex.
CRYPTO_SYMBOLS = [s for s in os.getenv(
    "CRYPTO_SYMBOLS",
    "BTC/USD,ETH/USD,SOL/USD,BNB/USD,XRP/USD,ADA/USD,DOGE/USD,DOT/USD,AVAX/USD,LINK/USD,UNI/USD,ATOM/USD,LTC/USD,BCH/USD,TRX/USD,AAVE/USD,MATIC/USD,APT/USD,ARB/USD,OP/USD"
).split(",") if s.strip()]

# An explicit WATCHED_SYMBOLS env var wins as-is (crypto pairs only);
# otherwise the firm watches CRYPTO_SYMBOLS.
_env_watched = os.getenv("WATCHED_SYMBOLS", "")
if _env_watched:
    WATCHED_SYMBOLS = [s for s in _env_watched.split(",") if s.strip() and "/" in s]
    if not WATCHED_SYMBOLS:
        WATCHED_SYMBOLS = list(CRYPTO_SYMBOLS)
else:
    WATCHED_SYMBOLS = list(CRYPTO_SYMBOLS)

# Watch ALL Binance testnet USDT spot pairs when BINANCE_ALL_SYMBOLS=true.
if os.getenv("BINANCE_ALL_SYMBOLS", "").lower() in ("true", "1", "yes"):
    try:
        import requests as _req
        _base = "https://testnet.binance.vision" if BINANCE_USE_TESTNET else "https://api.binance.com"
        _resp = _req.get(f"{_base}/api/v3/exchangeInfo", timeout=15)
        _data = _resp.json()
        _all = []
        for _s in _data.get("symbols", []):
            if _s.get("quoteAsset") == "USDT" and _s.get("status") == "TRADING":
                _sym = _s["symbol"].replace("USDT", "/USD").replace("BUSD", "/USD")
                if _sym.endswith("/USD"):
                    _all.append(_sym)
        if _all:
            WATCHED_SYMBOLS = sorted(_all)
    except Exception:
        pass

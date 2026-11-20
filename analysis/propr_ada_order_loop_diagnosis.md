# ADA order_create_failed loop — Diagnosis

Date: 2026-08-30
Source: /var/log/propr-bot.log on VPS 69.48.202.100

## Symptom
`[400] 13051: order_create_failed` — 248 occurrences, all clustered 2026-08-01
13:37 -> 14:28, driving repeated errors every ~60s (one per scan cycle).

## Root cause: live cooldown not enforced
A single ADA signal re-fired every cycle. The bot opened ADA long on 2026-07-31
(`OPENED LONG ADA qty=12815.732174`, stop + TP placed), then on 08-01 the same
signal produced a new ADA entry attempt every ~60s that failed.

The code in bot.py explicitly documents the bug:

    "Cooldown was configured but never enforced here, only in the backtest --
    so the live bot could re-enter a name the moment it closed. That is the
    cooldown=1 setting, which measured 13/33 windows passed against 18/33 at
    cooldown=4. Trading a worse variant than the one that was validated."

The validated CFG uses cooldown=4 (and CFG_PATIENT cooldown=4, max_concurrent=1),
but the live scan path only LOGS "cooling down" and continues; the actual gate
is not applied. Combined with the account already sitting at max_concurrent, the
loop hammered market_buy for ~90 minutes.

## Impact
- 248 wasted API calls / order attempts
- 15 closed trades total in the run; ADA entered 3 times across Aug (closed
  @08-01, 08-06, 08-09) — over-trading the same name
- Consistent with the net-negative realized P&L on closes (~-$81)

## Recommended fix (NOT applied — live production bot)
Enforce the cooldown in the live scan the same way the backtest does:
    since = time.time() - self.cooled.get(asset, -1e12)
    if since < self.cfg.cooldown * 3600:
        continue        # hard block, not just a log line
and only set self.cooled[asset] on a REAL close, not on an entry attempt.

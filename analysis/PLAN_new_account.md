# PLAN — the new challenge account, end to end

Written 2026-11-14, before any money is spent. Everything below is either
(a) pulled live from the Propr API today, (b) read out of the code that will
trade it, or (c) arithmetic on those two. Nothing here is a restatement of an
old claim.

---

## 0. Bottom line, stated once

Buying a challenge is a **$99 toll for a ~50% shot at a funded $100k account
that still has to earn.** At zero drift the purchase is negative EV — that has
not changed and a new account does not change it. What *can* change is the
odds, and this product has the best odds on the board by a factor of 1.3x.
The rest of this document is about how the machine behaves once it is running,
because the user's instruction was "tell me how it's going to work".

---

## 1. What is actually for sale (live API, 2026-11-14)

| Product | Balance | Price | Phases | Target | Daily loss | Max DD | DD type |
|---|---|---|---|---|---|---|---|
| Silver Challenge | $200,000 | $199.99 | 2 | 10% | 5% | 10% | trailing |
| Professional | $1,000,000 | $1,000 | 1 | 10% | 5% | 10% | static |
| Bronze Challenge | $100,000 | $99.99 | 2 | 10% | 5% | 10% | static |
| **Pass Fast B Book** | **$100,000** | **$99** | **1** | **10%** | **10%** | **20%** | **static** |
| Pass Phase1 Fast | $10,000 | $99 | 2 | 10% | 10% | 20% | trailing |
| Polymarket Bronze 1-Step | $50,000 | $49.99 | 1 | 9% | 3% | 6% | static |
| Free Trial | $1,000 | $0 | 1 | 10% | 5% | 10% | static |
| Bronze 1-Step Turbo | $25,000 | — | 1 | 9% | 3% | 3% | static |
| Bronze 1-Step PRO | $25,000 | — | 0 | — | — | — | — |

Two are not purchasable (no price row): Turbo and PRO. Free Trial is capped at
one purchase per user and was already consumed by the beta account.

### The unit bug, resolved
`profitTargetPercent` reads `"0.1"` on some phases. It is **10%**, not 0.1%.
Proof: "Pass Phase1 Fast" lists phase 1 as `0.1` and phase 2 as `10` for the
same product — same target, different formatting. So Pass Fast B Book's
`0.1` is a 10% target. No need to guess.

### The hidden difference
Polymarket Bronze and Pass Fast B Book carry a `b_book_account` reward:
`userSplitPercent 80 / systemSplitPercent 20`. That means Propr is the
counterparty rather than a broker passing you to Hyperliquid. The account
still pays (that is what the $1.73M October payout line is), but fills are
generated on their side. Our measured fill quality (+21bp mean, p90 +51bp)
came from the real venue, so treat the b-book fill assumption as unverified
until we see the first week of `entry_quality.csv`.

---

## 2. Recommendation: Pass Fast B Book, $99

Why, in odds rather than adjectives. With no drift, the probability of
touching +target before −DD is `DD / (DD + target)`:

| Product | target/DD | P(pass), no drift | price | cost per pass | phases |
|---|---|---|---|---|---|
| Polymarket $50k | 9 / 6 | 40% | $49.99 | $125 | 1 |
| Bronze $100k | 10 / 10 | 50% | $99.99 | $200 | 2 |
| Silver $200k | 10 / 10 trail | <50% | $199.99 | >$400 | 2 |
| Professional $1M | 10 / 10 | 50% | $1,000 | $2,000 | 1 |
| **Pass Fast B Book $100k** | **10 / 20** | **67%** | **$99** | **$148** | **1** |

The 20% drawdown is the whole reason to pick it. It buys two things:

1. **Room for the bot's own halt.** The runner stops itself at half the
   product wall. On a 10% product that means halting at −5%, i.e. the account
   dies 5 points later anyway. On this product the bot halts at −10% with
   another 10 points of product wall still intact — the account is **not
   failed** when the bot stops, so the halt is a decision point, not a funeral.
2. **One phase instead of two.** Bronze and Silver each require 10% twice:
   0.5 × 0.5 = 25% before you are funded at all.

Trailing-DD products (Silver) are strictly worse than static at equal numbers
because the floor ratchets up with the high-water mark.

---

## 3. The wiring, exactly

Purchase happens at app.propr.xyz. Everything after that is four steps.

**Step 1 — the account appears on the production venue, not beta.**
Verified today: the existing key is `pk_beta_…` and returns `401 / 10002
unauthorized` against `api.propr.xyz`. A purchased product lives on the
production API, so a **new API key** must be created in the app's settings
after purchase. Without it nothing starts.

**Step 2 — find the account URN.** The old beta attempt is still `active`
(2026-09-09, never expired), so after purchase there will be **two active
attempts**. Auto-discovery returns `attempts[0]` — whichever the API lists
first — which is exactly how you trade the wrong account. So:

```powershell
cd C:\Users\DELL\OneDrive\1m\propr-bot
python -c "import os;from dotenv import load_dotenv;load_dotenv('.env',override=True);from propr import ProprClient;c=ProprClient(api_key=os.getenv('PROPR_API_KEY'),base_url='https://api.propr.xyz/v1');[print(x['accountId'],x['createdAt'],x['status']) for x in c.get_challenge_attempts(status='active')]"
```

Take the row whose `createdAt` is today. That string goes in `.env`.

**Step 3 — new folder, new `.env`.** The single-instance lock port is derived
from the folder path (`47000 + crc32(path) % 500`), so a second deployment in
the *same* folder cannot start, and two bots in one folder would double the
book. Recommended: `C:\Users\DELL\OneDrive\1m\propr-100k`, copied from
`propr-bot` with no `cache\HALT`.

```dotenv
PROPR_API_KEY=pk_live_...
PROPR_API_URL=https://api.propr.xyz/v1
PROPR_WS_URL=wss://api.propr.xyz/ws
TELEGRAM_TOKEN=...            # reuse
TELEGRAM_CHAT_ID=...          # reuse
PROPR_ACCOUNT_ID=urn:prp-account:<new>
CHALLENGE_START=100000
```

`CHALLENGE_START` must be the **starting** balance, not the current one. If it
is wrong the target moves with equity and the bot reports a pass it did not
earn — that is the documented failure in `client.py:39-46`.

`fund_bot.py` refuses to start live if the key is `pk_live_` while
`PROPR_ACCOUNT_ID` is still the beta default. That guard exists because it has
already happened once.

**Step 4 — dry cycle, then live.**

```powershell
python fund_bot.py --once                     # prints the plan, places nothing
python fund_bot.py --live --interval 300 --gross 0.25 --stop 0.24
python watchdog.py                            # owns the process
```

The first log line must read `account urn:prp-account:<new>  start $100,000`.
If it says `LDA3R3vW74Ym`, stop and fix `.env`. The old beta and 5k
deployments stay halted; deleting their `cache\HALT` is the only thing that
can wake them, and nothing will.

---

## 4. Parameters, derived from this product's rulebook

| Knob | Value | Why this number |
|---|---|---|
| `--target` | 0.10 | product target |
| `--dd-wall` (product) | 0.20 | product floor, static from $100,000 |
| `--daily-wall` (product) | 0.10 | product daily loss |
| `--dd-halt` (bot) | 0.10 | half the product wall — halt is survivable |
| `--daily-halt` (bot) | 0.05 | half the product daily wall |
| `--gross` | 0.25 | 25% of equity deployed |
| `--q` | 5 | five names |
| `--stop` | 0.24 | capped anyway by the risk budget |
| `--tp-mult` | 1.5 | TP at +36% per name |
| `--K` | 336 | trailing funding window, 14 days |
| `--rebal-h` | 168 | weekly rotation |

Sizing arithmetic at $100,000:

- per name = 100,000 × 0.25 / 5 = **$5,000 notional** (5% of equity)
- stop distance = min(0.24, 0.95 × 100,000×0.20 / (equity×0.25)) = **24%**
- risk per name = $5,000 × 24% = **$1,200**
- whole book at stop = **$6,000 = 6% of equity**, against a 20% product budget
- daily halt at $5,000 of day-start equity needs a 20% adverse move on a 25%
  book in one UTC day — rare, and it flattens rather than fails.

---

## 5. What it does every five minutes

`cycle()`, in this order, every 300s:

1. Read equity (balance + unrealized).
2. Persisted `cache\HALT` present → stop, exit process, watchdog stands down.
3. New UTC day → reset `day_start`, clear the daily lockout.
4. Equity ≥ $110,000 → write HALT, Telegram "target", exit. **Pass.**
5. Equity ≤ $90,000 → write HALT, flatten, Telegram. **Bot stop, account alive.**
6. Equity ≤ day_start × 0.95 → flatten once, no new entries until 00:00 UTC.
7. `manage_trails` — raise stops on winners; rebuild any naked position.
8. `manage_sizing` — trim a position that filled over 1.35× its slot.
9. Universe refresh every 24h: top-100 perps by 24h volume.
10. Refresh funding history, rank by trailing 336h funding, take top 5.
11. Hold, top up a missing slot, or rebalance if 168h have passed.

Entries are limit orders at best bid +1bp, market fallback after 45s, skipped
if the book is wider than 12bp, and refused when a name is already +8% in 24h
or 5% above its 4H 20-EMA. Macro halo: no new entries ±6h/+3h around NFP/CPI,
±12h/+6h around FOMC.

Turnover is the one thing this design gets right: one rotation a week, maybe
one or two names change, so ~0.1–0.2 gross/week — under 10%/yr at 10bp/side,
versus the 22%+/yr that killed every daily cross-sectional idea tested.

---

## 6. The halt ladder, and what each rung means for your money

| Trigger | Action | Account state | Resume by |
|---|---|---|---|
| +10% target | latch HALT, exit | passed — nothing more to earn here | n/a |
| −10% (bot) | latch HALT, flatten | alive, 10 points of product wall left | delete `cache\HALT`, then `--once` |
| −5% intraday | flatten, lock until 00:00 UTC | alive | automatic at UTC midnight |
| process dies / silent 15 min | watchdog respawn | unchanged | automatic |
| 2+ open positions, no stop | rebuild stop+TP from real entry | protected again | automatic |

The distinction that matters: **only the product's −20% fails you.** Every
other stop is a circuit breaker I can reset. On the 10% products the bot's
halt and the product's failure are five points apart; here they are ten.

---

## 7. What to actually expect

Time, not just odds. Book vol at 25% gross across five alts with ~0.6
correlation is roughly **0.8–1.2%/day**. First passage to a ±10% boundary
scales as a²/σ², so:

- median time to touch ±10% → **order of 70–150 days**
- the distribution has a long right tail; "no time limit" is real (no deadline
  fields exist in the schema, and a 2026-09-09 attempt is still active)
- roughly half of these runs end at the −10% bot halt, half at +10%

So the realistic picture is: two to five months of a machine placing about one
order a week, mostly doing nothing, ending in either a pass or a −10% halt you
can restart. Anyone expecting a two-week verdict is going to intervene at the
worst possible moment, which is the single most expensive behaviour available.

The honest EV, one line: **$99 buys a ~50–67% shot at a funded $100k b-book
account with an 80/20 split, and the funded phase is where the walls actually
bite** (STUDY_24). The purchase is the cheap part.

---

## 8. Four ways this breaks, and the guard for each

1. **Wrong account.** Auto-discovery picks `attempts[0]`; the beta attempt is
   still active. Guard: explicit `PROPR_ACCOUNT_ID` + a live-key/is-beta-ID
   refusal + I check the first log line.
2. **Beta key on production.** Verified 401 today. Guard: new key, and a
   one-shot `/challenges` call before the bot starts.
3. **Double book.** Two bots, or a stale `HALT` deleted twice. Guard: the
   folder-derived lock port; one folder per account; old HALTs untouched.
4. **b-book fills ≠ venue fills.** Unverified. Guard: after the first week,
   compare `entry_quality.csv` mean against the +21bp venue measurement. If it
   is materially worse, the sizing and the stop budget get rebuilt around the
   real number before anything compounds.

---

## 9. Open items before the first order

- [ ] Product chosen (recommendation: Pass Fast B Book, $99)
- [ ] Production API key created
- [ ] Account URN captured (the row with today's `createdAt`)
- [ ] Folder created, `.env` written, `CHALLENGE_START=100000`
- [ ] `--once` dry cycle reviewed: correct account, correct target line
- [ ] Old two deployments confirmed still halted

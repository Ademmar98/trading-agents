# Study 18 — Pump Catalyst Census (whole market, 1 year)

**Question:** across every pair, which printed a green candle of >10% on 15m /
30m / 1h / 4h in the last year, why did it rise, and which groups of ≥5 pairs
rose for the *same* reason.

**Run:** `analysis/pump_catalyst_study.py` → `analysis/pump_catalyst_attribute.py`
**Window:** 2025-07-28 → 2026-07-28 (365d) · **Universe:** 456 Binance spot
USDT pairs · **Source:** `data-api.binance.vision`

This is a **census, not a strategy test.** It measures what moved and attributes
cause. It does not establish that any of it is tradeable, and nothing here has
been through the study-11/17 bar (all-configs-OOS, window-slide, cost-fair null).

---

## Method

- **Data.** 15m klines for the full year per symbol (35,038 bars), reindexed
  onto a complete 15m grid — intervals Binance omits (zero trades on illiquid
  pairs) are filled as flat `o=h=l=c=prev_close`, volume 0, which is what
  actually happened. 30m/1h/4h are **resampled from that grid**, so every
  higher-TF bar is built from a complete set of 15m bars and no data gap can
  manufacture a candle. Verified: 0 filled bars on majors, bar counts exactly
  35038/17519/8760/2190.
- **Event.** A single candle with `(close − open)/open ≥ 10%`. Body, not wick.
- **Market-wide detector = breadth**, not BTC: the share of the *whole universe*
  closing ≥ +3% on that same bar. BTC alone is the wrong test — alts routinely
  bounce as a block while BTC lags, and 2025-10-10 is exactly that shape.
- **Sector clusters are day-level, market-wide is bar-level.** A cascade rebound
  is simultaneous to the minute; a narrative rotation builds over hours. Using
  one resolution for both destroys one of them.
- **≥5 pairs *of the sector*** — not "5 pairs of which 60% share a sector",
  which passes trivially on a group of 6.

## Results

**11,169 events** — 15m 2,394 · 30m 2,853 · 1h 2,762 · 4h 3,160.
Collapsed to distinct pair-days: **4,691**.

| reason | pair-days | share |
|---|---|---|
| SOLO (single-coin news) | 2,760 | 58.8% |
| MARKET_WIDE (breadth ≥10%) | 976 | 20.8% |
| BROAD_ALT_BID | 665 | 14.2% |
| SECTOR rotation | 198 | 4.2% |
| NEW_LISTING (first 24h) | 92 | 2.0% |

Breadth distribution (share of universe up ≥3% on a bar) — median 0.2% (15m) to
2.8% (4h); the ≥10% threshold fires on 0.1% of 15m bars and 8.0% of 4h bars.

### Groups of ≥5 pairs sharing one reason

**1. Market-wide moves — 20 days, the largest group by far.**

| date | peak breadth | pairs ≥10% | median | BTC (bar) |
|---|---|---|---|---|
| **2025-10-10** | **97.3%** | **339** | +27.6% | +4.6% |
| 2025-10-21 | 91.2% | 10 | +12.0% | +4.5% |
| 2025-08-22 | 90.5% | 25 | +11.4% | +3.6% |
| 2025-12-02 | 89.6% | 32 | +11.9% | +4.0% |
| 2025-11-07 | 88.5% | 66 | +12.4% | +2.6% |
| 2026-02-06 | 87.8% | 10 | +12.8% | +3.5% |

2025-10-10 is the single biggest event in the dataset: **334 pairs printed a
≥10% green 30m candle in the same bar** (21:30 UTC), 308 in the same 15m bar.
Cause is documented: Trump's 100% China tariff post triggered the largest
liquidation cascade in crypto history (~$19.1B, 1.6M accounts, BTC −14%); the
+10% candles are the **V-rebound off the cascade low**, not a rally. BTC's own
daily was −7.31% that day.

**2. New listings — 73 pairs, the biggest candles in the dataset.**
Median first-24h max candle **+58.5%**; 30 pairs ≥100%, 16 ≥300%.
Top: 2Z +1370%, RE +1028%, FF +804%, XPL +785%, OPEN +662%, MMT +653%,
MIRA +584%, HOLO +555%. The listing *is* the reason — no headline needed.

**3. Sector rotations — 56 clusters over 31 distinct days.**

| sector | days | dates |
|---|---|---|
| defi | 13 | see caveat below |
| l1 | 7 | 2025-11-08/11, 2026-01-24, 02-12, 02-15, 04-01, 04-02 |
| gaming_nft | 4 | 2025-10-15, 2025-11-08, 2026-04-18, 2026-04-20 |
| **privacy** | 3 | 2025-11-03, 11-04, 11-05 |
| fan_token | 2 | 2025-08-10, 2025-08-15 |
| meme | 2 | 2025-12-31, 2026-04-16 |
| **ai** | 2 | 2025-11-21, 2026-05-29 |
| depin_infra | 1 | 2025-11-07 |

**Catalysts confirmed against reporting:**

- **Privacy, 2025-11-03→05** — DASH, DCR, PIVX, SCRT, XVG, ZEN, RVN, DGB,
  +12% to +64% while BTC was flat and breadth only 7-8%. A genuine countertrend
  rotation: ZEC to a 7-year high, DASH to a 3-year high, driven by the upcoming
  Zcash halving, a technical breakout that forced short covering, and the
  CBDC/surveillance narrative. Sector cap reached ~$24-25B. Cleanest
  news-attributable cluster in the year.
- **AI, 2026-05-29** — AI, AIGENSYN, ALLO, IO, NIL, SAPIEN, median +14.8%,
  max +50.9% (ALLO). Q2-2026 decentralized-AI narrative: Allora's Cobot
  trading-tool launch (2026-05-18) plus a network upgrade, Gensyn's `AI` token
  Binance listing (2026-05-14); AI sector crossed $25B market value in May.
- **Gaming/meme, 2026-04-16→20** — a "selective altseason" window; Altcoin
  Season Index rose to 34, ENJ +50% in a session. Broad-based, weaker
  attribution than the two above.
- **Fan tokens, 2025-08-10/15** — ACM, CITY, JUV, LAZIO, OG, PORTO, SANTOS,
  ALPINE. Pre-season football calendar; no single headline located.

**⚠ The `defi` label is wrong.** 13 days looks like the dominant narrative but
it is not a narrative at all: **37 distinct pairs carry all 26 defi clusters**,
and the same names recur — REQ ×11, BANK ×11, PROM ×9, QI ×8, GTC ×7, DEXE ×7,
BEL ×7, DODO ×7. This is a **recurring rotation through a fixed small basket of
mid-cap Binance listings**, a market-structure phenomenon, not a sector story.
Named honestly, it is the single most *repeatable* pattern found.

**4. Broad alt bid — 86 day-groups** of ≥10 pairs with no sector reaching 5 and
BTC/breadth quiet. Mechanism not established; listed, not explained.

### What is NOT the explanation

**Illiquidity is not the driver.** Sector-cluster members sit at the **48.8th
liquidity percentile** against **50.1 for the universe** — statistically
indistinguishable. Only privacy (20.8th) and fan_token (29.1st) skew genuinely
illiquid; `defi` is mild at 37.3rd. The clusters are not an artifact of thin
books, which was the first thing worth ruling out.

### Solo moves (58.8% — the majority)

The largest single-pair, no-cluster, no-market-move events, each needing its own
headline: BROCCOLI714 +236%, PIVX +116%, TNSR +114%, CYBER +113%, FF +99%,
ESP +87%, BABY +83%, API3 +82%, TREE +82%, SUPER +73%.

## Caveats

- **Survivorship.** Only pairs still `TRADING` on 2026-07-28 are in the
  universe. Coins delisted during the year are absent, and delisting correlates
  with having pumped and died. **Every count here is a floor.**
- **Newly listed pairs have partial history**, so per-pair event counts are not
  comparable across symbols without normalising by days listed.
- **Attribution ≠ causation.** NEW_LISTING and MARKET_WIDE are derived from the
  data and are solid. SECTOR is derived from a hand-maintained sector map (8
  bases unmapped: LAYER, NOM, WCT, XTZ, XVS, YB, ZRO, ZRX). The *named*
  catalysts are from reporting and are the weakest link — 2 of 8 sector groups
  are well-sourced, the rest are not.
- **No edge claim.** Nothing here was run through fees, an OOS split, a
  window-slide, or a cost-fair null. A pump that already happened is not a
  signal; the entries in [[firm-strategy-research-findings]] apply unchanged.

## Files

- `analysis/pump_catalyst_study.py` — fetch + detect (re-runnable, caches to scratchpad)
- `analysis/pump_catalyst_attribute.py` — breadth, sectors, clustering
- `analysis/pump_catalyst_events.json` — all 11,169 events
- `analysis/pump_catalyst_reasons.json` — clusters + reason mix
- `analysis/pump_catalyst_events_labelled.json` — per-event reason labels

## Sources

- [CoinGecko — What Is October 10th?](https://www.coingecko.com/learn/october-10-crypto-crash-explained)
- [CCN — $19B liquidation / Trump 100% China tariff](https://www.ccn.com/education/crypto/cryptos-19-billion-liquidation-explained-trump-china-tariff-leverage-crash/)
- [Forbes — Crypto's Black Friday](https://www.forbes.com/sites/digital-assets/2025/10/13/cryptos-black-friday-inside-the-19-billion-market-meltdown/)
- [CoinDesk — Zcash, Monero and the return of anonymity](https://www.coindesk.com/business/2025/11/05/crypto-s-anti-surveillance-boom-zcash-monero-and-the-return-of-anonymity)
- [Invezz — Privacy coins defy crypto crash](https://invezz.com/news/2025/11/04/privacy-coins-defy-crypto-crash-decred-dash-and-zcash-lead-stunning-rally/)
- [TradingView/Cointelegraph — Privacy coins surge 80%](https://www.tradingview.com/news/cointelegraph:880c9ee6b094b:0-privacy-coins-surge-80-why-zcash-and-dash-are-back-in-the-spotlight/)
- [CoinDesk — Hyperliquid, AI tokens leading next altcoin rally (2026-05-23)](https://www.coindesk.com/markets/2026/05/23/crypto-trader-sees-hyperliquid-ai-tokens-leading-next-altcoin-rally)
- [CoinMarketCap — Allora (ALLO)](https://coinmarketcap.com/cmc-ai/allora/price-prediction/)
- [BYDFi — Altcoin Season Index, 2026-04-17](https://www.bydfi.com/en/cointalk/altcoin-season-index-update-april-17-2026)

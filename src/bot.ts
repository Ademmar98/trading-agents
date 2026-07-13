// The strategy core, shared verbatim by both replay paths. Raw path = no
// filter; memory path = same code with an AdaptiveFilter. Long-only spot
// (no shorts — halal constraint carried over from the firm): a bullish
// 9/21 SMA crossover opens, the bearish crossover closes. Signals form on
// a bar's CLOSE and fill at the NEXT bar's OPEN, so no bar ever trades on
// information from its own future.
import type { Candle } from "./candles.ts";
import type { AdaptiveFilter } from "./adaptiveFilter.ts";

export const FEE_PCT = 0.1; // per side, matches the firm's fee-honest paper accounting
export const NOTIONAL_USD = 1000; // fixed paper stake per trade
const FAST = 9;
const SLOW = 21;
const REGIME_SMA = 50;
const ATR_PERIOD = 14;
const HIGH_VOL_ATR_PCT = 0.4; // fixed threshold — not fitted to the series

export interface Trade {
  symbol: string;
  setupKey: string;
  entryTime: string;
  entryPrice: number;
  exitTime: string;
  exitPrice: number;
  qty: number;
  pnl: number;
  outcome: "win" | "loss";
  exitReason: string;
  signalExit: boolean; // true = closed by the strategy, false = end-of-replay mark
}

export interface Skip {
  symbol: string;
  time: string;
  price: number;
  setupKey: string;
  reason: string;
}

function sma(values: number[], period: number, i: number): number | null {
  if (i + 1 < period) return null;
  let sum = 0;
  for (let k = i - period + 1; k <= i; k++) sum += values[k];
  return sum / period;
}

function atrPct(candles: Candle[], i: number): number | null {
  if (i < ATR_PERIOD) return null;
  let sum = 0;
  for (let k = i - ATR_PERIOD + 1; k <= i; k++) {
    const prevClose = candles[k - 1].c;
    sum += Math.max(
      candles[k].h - candles[k].l,
      Math.abs(candles[k].h - prevClose),
      Math.abs(candles[k].l - prevClose),
    );
  }
  return (sum / ATR_PERIOD / candles[i].c) * 100;
}

// Setup fingerprint used by the memory: what kind of signal, in what trend
// regime, at what volatility. Everything is computed causally up to bar i.
export function classifySetup(candles: Candle[], closes: number[], i: number): string {
  const regimeSma = sma(closes, REGIME_SMA, i);
  const regime = regimeSma === null ? "unknown" : closes[i] > regimeSma ? "uptrend" : "downtrend";
  const vol = atrPct(candles, i);
  const volBucket = vol === null ? "unknown" : vol >= HIGH_VOL_ATR_PCT ? "high-vol" : "low-vol";
  return `bullish-cross|${regime}|${volBucket}`;
}

function iso(t: number): string {
  return new Date(t).toISOString().replace(/\.\d{3}Z$/, "Z");
}

export interface ReplayResult {
  trades: Trade[];
  skips: Skip[];
}

export function runBot(symbol: string, candles: Candle[], filter?: AdaptiveFilter): ReplayResult {
  const closes = candles.map((c) => c.c);
  const trades: Trade[] = [];
  const skips: Skip[] = [];
  let open: { entryTime: string; entryPrice: number; qty: number; setupKey: string } | null = null;

  const closeTrade = (exitPrice: number, exitTime: string, exitReason: string, signalExit: boolean) => {
    if (!open) return;
    const cost = open.entryPrice * (1 + FEE_PCT / 100) * open.qty;
    const proceeds = exitPrice * (1 - FEE_PCT / 100) * open.qty;
    const pnl = proceeds - cost;
    trades.push({
      symbol,
      setupKey: open.setupKey,
      entryTime: open.entryTime,
      entryPrice: open.entryPrice,
      exitTime,
      exitPrice,
      qty: open.qty,
      pnl,
      outcome: pnl > 0 ? "win" : "loss",
      exitReason,
      signalExit,
    });
    open = null;
  };

  // i stops one bar early: a signal on bar i fills at bar i+1's open.
  for (let i = REGIME_SMA; i < candles.length - 1; i++) {
    const fastPrev = sma(closes, FAST, i - 1);
    const slowPrev = sma(closes, SLOW, i - 1);
    const fastNow = sma(closes, FAST, i);
    const slowNow = sma(closes, SLOW, i);
    if (fastPrev === null || slowPrev === null || fastNow === null || slowNow === null) continue;

    const bullishCross = fastPrev <= slowPrev && fastNow > slowNow;
    const bearishCross = fastPrev >= slowPrev && fastNow < slowNow;
    const fill = candles[i + 1];

    if (!open && bullishCross) {
      const setupKey = classifySetup(candles, closes, i);
      if (filter) {
        const verdict = filter.shouldSkip(symbol, setupKey);
        if (verdict.skip) {
          skips.push({ symbol, time: iso(fill.t), price: fill.o, setupKey, reason: verdict.reason });
          continue;
        }
      }
      open = {
        entryTime: iso(fill.t),
        entryPrice: fill.o,
        qty: Number((NOTIONAL_USD / fill.o).toFixed(8)),
        setupKey,
      };
    } else if (open && bearishCross) {
      closeTrade(fill.o, iso(fill.t), "bearish-cross exit", true);
    }
  }

  // A position still open when the data runs out is marked to the last real
  // close. That is a genuine liquidation value, not a strategy exit, so it
  // never feeds a lesson.
  if (open) {
    const last = candles[candles.length - 1];
    closeTrade(last.c, iso(last.t), "end-of-replay mark-to-market close", false);
  }

  return { trades, skips };
}

// Replay runner for both paths.
//   raw    — the honest baseline. Ignores memory entirely, trades every
//            signal, then appends its REAL outcomes to ledger.csv and
//            distills losing setups into learnings.md lessons.
//   memory — same strategy, same candles, but consults the two memory
//            files before every entry and SKIPs setups with a real prior
//            loss. Appends its trades and SKIP rows to the ledger.
import { getCandles, type Candle } from "./candles.ts";
import { runBot, FEE_PCT, NOTIONAL_USD, type Trade, type Skip } from "./bot.ts";
import {
  ensureFiles, readLedger, readWarnings, appendLedgerRows, addLesson, type LedgerRow,
} from "./memory.ts";
import { AdaptiveFilter } from "./adaptiveFilter.ts";

export const SYMBOLS = ["BTC_USD", "ETH_USD", "SOL_USD"];
const TIMEFRAME = "M15";
const BARS = 300;

function money(x: number): string {
  return `${x < 0 ? "-" : "+"}$${Math.abs(x).toFixed(2)}`;
}

function describeSetup(setupKey: string): string {
  const [signal, regime, vol] = setupKey.split("|");
  const signalTxt = signal === "bullish-cross" ? "bullish 9/21 SMA crossover" : signal;
  return `${signalTxt} in a ${regime} with ${vol === "high-vol" ? "high" : "low"} volatility`;
}

function tradeRows(trades: Trade[], mode: string): LedgerRow[] {
  const rows: LedgerRow[] = [];
  for (const t of trades) {
    rows.push({
      timestamp: t.entryTime, symbol: t.symbol, action: "BUY",
      price: t.entryPrice.toString(), quantity: t.qty.toString(),
      reason: `entry ${t.setupKey}`, mode, outcome: t.outcome, pnl: "",
    });
    rows.push({
      timestamp: t.exitTime, symbol: t.symbol, action: "SELL",
      price: t.exitPrice.toString(), quantity: t.qty.toString(),
      reason: `${t.exitReason} setup=${t.setupKey}`, mode,
      outcome: t.outcome, pnl: t.pnl.toFixed(2),
    });
  }
  return rows;
}

function skipRows(skips: Skip[]): LedgerRow[] {
  return skips.map((s) => ({
    timestamp: s.time, symbol: s.symbol, action: "SKIP",
    price: s.price.toString(), quantity: "0",
    reason: s.reason, mode: "memory", outcome: "skipped", pnl: "",
  }));
}

function summarize(trades: Trade[]): { n: number; wins: number; losses: number; pnl: number } {
  const wins = trades.filter((t) => t.outcome === "win").length;
  return {
    n: trades.length, wins, losses: trades.length - wins,
    pnl: trades.reduce((a, t) => a + t.pnl, 0),
  };
}

function printTrades(trades: Trade[]): void {
  for (const t of trades) {
    console.log(
      `  ${t.outcome === "win" ? "WIN " : "LOSS"} ${t.symbol} ${t.entryTime} ` +
      `$${t.entryPrice} -> ${t.exitTime} $${t.exitPrice} | ${money(t.pnl)} | ` +
      `${t.setupKey}${t.signalExit ? "" : " (end-of-replay close)"}`,
    );
  }
}

async function loadAll(): Promise<Map<string, { candles: Candle[]; source: string }>> {
  const out = new Map<string, { candles: Candle[]; source: string }>();
  for (const sym of SYMBOLS) {
    const got = await getCandles(sym, TIMEFRAME, BARS);
    console.log(`Data: ${sym} ${got.candles.length} x ${TIMEFRAME} bars from ${got.source}`);
    out.set(sym, got);
  }
  return out;
}

export async function replayRaw(): Promise<void> {
  ensureFiles();
  console.log(`\n=== replay:raw — honest baseline, memory IGNORED ===`);
  console.log(`Strategy: long-only 9/21 SMA crossover, $${NOTIONAL_USD} paper stake, ` +
    `${FEE_PCT}%/side fees, signal on close -> fill at next bar open\n`);
  const data = await loadAll();

  const allTrades: Trade[] = [];
  for (const sym of SYMBOLS) {
    const { trades } = runBot(sym, data.get(sym)!.candles);
    console.log(`\n${sym}: ${trades.length} completed trades`);
    printTrades(trades);
    allTrades.push(...trades);
  }

  const s = summarize(allTrades);
  console.log(`\nRAW BASELINE: ${s.n} trades | ${s.wins} wins / ${s.losses} losses | net ${money(s.pnl)} after fees`);

  const { appended, duplicates } = appendLedgerRows(tradeRows(allTrades, "raw"));
  console.log(`Ledger: ${appended} rows appended to data/ledger.csv` +
    (duplicates ? ` (${duplicates} already recorded — not duplicated)` : ""));

  // Learn ONLY from real signal-exited losses. End-of-replay marks are not
  // the setup's verdict, and zero losses means zero lessons — no seeding.
  const realLosses = allTrades.filter((t) => t.outcome === "loss" && t.signalExit);
  let lessons = 0;
  for (const t of realLosses) {
    const text =
      `On ${t.entryTime} a ${describeSetup(t.setupKey)} entered at $${t.entryPrice} ` +
      `and exited at $${t.exitPrice} for ${money(t.pnl)} after ${FEE_PCT}%/side fees. ` +
      `Real loss — skip this setup on this symbol until memory is reset.`;
    if (addLesson(t.symbol, t.setupKey, text)) lessons++;
  }
  if (realLosses.length === 0) {
    console.log("Learnings: no losing crossover setups in this window — nothing written (no seeding).");
  } else {
    console.log(`Learnings: ${lessons} new lesson(s) written to data/learnings.md ` +
      `from ${realLosses.length} real loss(es)` +
      (lessons < realLosses.length ? " (rest already covered)" : ""));
  }
}

export async function replayMemory(): Promise<void> {
  ensureFiles();
  console.log(`\n=== replay:memory — same strategy, memory CONSULTED before every entry ===\n`);
  const ledger = readLedger();
  const executed = ledger.filter((r) => (r.outcome === "win" || r.outcome === "loss"));
  if (executed.length === 0) {
    console.log("Memory is empty: data/ledger.csv has no real trade outcomes yet.");
    console.log("Run `npm run replay:raw` first to build honest history — nothing to learn from until then.");
    return;
  }

  const filter = new AdaptiveFilter(ledger, readWarnings());
  if (!filter.hasAnyHistory()) {
    console.log(`Ledger holds ${executed.length / 2} trades but none lost — no real warning exists, so`);
    console.log("the memory path has nothing to skip. HOLD: keep paper testing to gather more history.");
  }

  const data = await loadAll();
  const memTrades: Trade[] = [];
  const memSkips: Skip[] = [];
  const rawTrades: Trade[] = [];
  for (const sym of SYMBOLS) {
    const candles = data.get(sym)!.candles;
    const mem = runBot(sym, candles, filter);
    const raw = runBot(sym, candles); // in-memory baseline on the SAME bars, never written
    memTrades.push(...mem.trades);
    memSkips.push(...mem.skips);
    rawTrades.push(...raw.trades);

    console.log(`\n${sym}: ${mem.trades.length} trades, ${mem.skips.length} skipped by memory`);
    printTrades(mem.trades);
    for (const sk of mem.skips) {
      console.log(`  SKIP ${sk.symbol} ${sk.time} @ $${sk.price} | ${sk.setupKey}`);
      console.log(`       reason: ${sk.reason}`);
    }
  }

  const { appended } = appendLedgerRows([...tradeRows(memTrades, "memory"), ...skipRows(memSkips)]);

  const rawS = summarize(rawTrades);
  const memS = summarize(memTrades);
  console.log(`\n--- raw vs memory on the same candles ---`);
  console.log(`raw    : ${rawS.n} trades | ${rawS.wins}W/${rawS.losses}L | net ${money(rawS.pnl)}`);
  console.log(`memory : ${memS.n} trades | ${memS.wins}W/${memS.losses}L | net ${money(memS.pnl)} | ${memSkips.length} skipped`);
  console.log(`edge   : memory ${money(memS.pnl - rawS.pnl)} vs raw ` +
    `(${memSkips.length} skip(s) based on real prior losses only)`);
  console.log(`Ledger: ${appended} rows appended (memory trades + SKIP rows)`);
  if (memSkips.length === 0 && filter.hasAnyHistory()) {
    console.log("No setup in this window matched a known bad trade — memory made no change. Keep paper testing.");
  }
}

// The memory-enabled path's gate. Before any entry it asks, from the two
// memory files: (1) has this symbol lost on this same setup before (real
// ledger loss)? (2) does learnings.md carry a warning for this setup on
// this symbol? Any real match -> SKIP. With no real prior loss it stays
// silent — it never blocks on invented history.
import type { LedgerRow, Warning } from "./memory.ts";

export interface Verdict {
  skip: boolean;
  reason: string;
}

export class AdaptiveFilter {
  private lossIndex = new Map<string, { count: number; totalPnl: number }>();
  private warnIndex = new Map<string, Warning>();

  constructor(ledger: LedgerRow[], warnings: Warning[]) {
    // A trade's setup lives in the reason of its BUY row ("entry <setupKey>");
    // its realized result is stamped on both rows of the round trip.
    for (const row of ledger) {
      if (row.action !== "BUY" || row.outcome !== "loss") continue;
      const setupKey = row.reason.replace(/^entry /, "").trim();
      if (!setupKey) continue;
      const key = `${row.symbol}|${setupKey}`;
      const cur = this.lossIndex.get(key) ?? { count: 0, totalPnl: 0 };
      cur.count += 1;
      this.lossIndex.set(key, cur);
    }
    // Fold realized loss dollars in from the SELL rows (they carry pnl).
    for (const row of ledger) {
      if (row.action !== "SELL" || row.outcome !== "loss") continue;
      const setupKey = (row.reason.match(/setup=(\S+)/) ?? [])[1];
      if (!setupKey) continue;
      const key = `${row.symbol}|${setupKey}`;
      const cur = this.lossIndex.get(key);
      if (cur) cur.totalPnl += Number(row.pnl) || 0;
    }
    for (const w of warnings) {
      this.warnIndex.set(`${w.symbol}|${w.setupKey}`, w);
    }
  }

  hasAnyHistory(): boolean {
    return this.lossIndex.size > 0 || this.warnIndex.size > 0;
  }

  shouldSkip(symbol: string, setupKey: string): Verdict {
    const key = `${symbol}|${setupKey}`;
    const losses = this.lossIndex.get(key);
    const warning = this.warnIndex.get(key);
    if (losses && warning) {
      return {
        skip: true,
        reason:
          `known bad trade: ${losses.count} real prior loss(es) on ${symbol} ` +
          `setup ${setupKey} (total $${losses.totalPnl.toFixed(2)}) and learnings.md warns about it`,
      };
    }
    if (losses) {
      return {
        skip: true,
        reason:
          `prior real loss on ${symbol} setup ${setupKey}: ` +
          `${losses.count} loss(es) totaling $${losses.totalPnl.toFixed(2)} in ledger.csv`,
      };
    }
    if (warning) {
      return { skip: true, reason: `learnings.md warns: ${warning.text}` };
    }
    return { skip: false, reason: "" };
  }
}

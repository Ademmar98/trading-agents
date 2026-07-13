// The two-file memory: data/ledger.csv (every action with its real outcome)
// and data/learnings.md (plain-English lessons distilled from real losses).
// Nothing here seeds fake history — both files start empty and only fill
// from actual replay outcomes.
import * as fs from "node:fs";
import * as path from "node:path";

const ROOT = path.resolve(import.meta.dirname, "..");
const DATA_DIR = path.join(ROOT, "data");
export const LEDGER_PATH = path.join(DATA_DIR, "ledger.csv");
export const LEARNINGS_PATH = path.join(DATA_DIR, "learnings.md");

export const LEDGER_HEADER = "timestamp,symbol,action,price,quantity,reason,mode,outcome,pnl";

const LEARNINGS_TEMPLATE = `# Learnings

Plain-English lessons the bot has learned from REAL replay outcomes.
Nothing is seeded: if there are no lessons below, the bot has not yet
observed a losing setup — run \`npm run replay:raw\` to build real history.

## Lessons
`;

export interface LedgerRow {
  timestamp: string;
  symbol: string;
  action: string; // BUY | SELL | SKIP
  price: string;
  quantity: string;
  reason: string;
  mode: string; // raw | memory
  outcome: string; // win | loss | skipped
  pnl: string;
}

export interface Warning {
  symbol: string;
  setupKey: string;
  text: string;
}

export function ensureFiles(): void {
  fs.mkdirSync(DATA_DIR, { recursive: true });
  if (!fs.existsSync(LEDGER_PATH)) fs.writeFileSync(LEDGER_PATH, LEDGER_HEADER + "\n");
  if (!fs.existsSync(LEARNINGS_PATH)) fs.writeFileSync(LEARNINGS_PATH, LEARNINGS_TEMPLATE);
}

export function resetMemory(): void {
  fs.mkdirSync(DATA_DIR, { recursive: true });
  fs.writeFileSync(LEDGER_PATH, LEDGER_HEADER + "\n");
  fs.writeFileSync(LEARNINGS_PATH, LEARNINGS_TEMPLATE);
}

export function readLedger(): LedgerRow[] {
  if (!fs.existsSync(LEDGER_PATH)) return [];
  const lines = fs.readFileSync(LEDGER_PATH, "utf8").split(/\r?\n/).filter((l) => l.trim());
  const cols = LEDGER_HEADER.split(",");
  return lines
    .filter((l) => l !== LEDGER_HEADER)
    .map((line) => {
      const parts = line.split(",");
      const row = {} as Record<string, string>;
      cols.forEach((c, i) => (row[c] = (parts[i] ?? "").trim()));
      return row as unknown as LedgerRow;
    });
}

// Reason fields must stay comma-free so the CSV parses by simple split.
function csvSafe(s: string): string {
  return s.replace(/,/g, ";");
}

export function appendLedgerRows(rows: LedgerRow[]): { appended: number; duplicates: number } {
  ensureFiles();
  const existing = new Set(
    readLedger().map((r) => `${r.timestamp}|${r.symbol}|${r.action}|${r.mode}`),
  );
  let appended = 0;
  let duplicates = 0;
  const out: string[] = [];
  for (const r of rows) {
    const key = `${r.timestamp}|${r.symbol}|${r.action}|${r.mode}`;
    if (existing.has(key)) {
      duplicates++;
      continue;
    }
    existing.add(key);
    out.push(
      [r.timestamp, r.symbol, r.action, r.price, r.quantity, csvSafe(r.reason), r.mode, r.outcome, r.pnl].join(","),
    );
    appended++;
  }
  if (out.length) fs.appendFileSync(LEDGER_PATH, out.join("\n") + "\n");
  return { appended, duplicates };
}

const WARNING_RE = /^- \*\*AVOID\*\* `(.+?)` on (\S+): (.*)$/;

export function readWarnings(): Warning[] {
  if (!fs.existsSync(LEARNINGS_PATH)) return [];
  const warnings: Warning[] = [];
  for (const line of fs.readFileSync(LEARNINGS_PATH, "utf8").split(/\r?\n/)) {
    const m = line.match(WARNING_RE);
    if (m) warnings.push({ setupKey: m[1], symbol: m[2], text: m[3] });
  }
  return warnings;
}

export function addLesson(symbol: string, setupKey: string, text: string): boolean {
  ensureFiles();
  const already = readWarnings().some((w) => w.symbol === symbol && w.setupKey === setupKey);
  if (already) return false; // one lesson per symbol+setup; no duplicates
  fs.appendFileSync(LEARNINGS_PATH, `- **AVOID** \`${setupKey}\` on ${symbol}: ${text}\n`);
  return true;
}

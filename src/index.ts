// CLI entry for the standalone TypeScript paper bot.
//   npm run replay:raw     — baseline replay, memory ignored
//   npm run replay:memory  — memory-gated replay + comparison vs raw
//   npm run memory:reset   — wipe ledger.csv and learnings.md back to empty
import { replayRaw, replayMemory } from "./replay.ts";
import { resetMemory, LEDGER_PATH, LEARNINGS_PATH } from "./memory.ts";

const cmd = process.argv[2];

switch (cmd) {
  case "replay:raw":
    await replayRaw();
    break;
  case "replay:memory":
    await replayMemory();
    break;
  case "memory:reset":
    resetMemory();
    console.log(`Memory reset: ${LEDGER_PATH} (header only) and ${LEARNINGS_PATH} (no lessons).`);
    break;
  default:
    console.log("Usage: node src/index.ts <replay:raw | replay:memory | memory:reset>");
    process.exit(1);
}

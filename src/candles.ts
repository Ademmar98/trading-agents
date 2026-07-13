// Real market data only. Candles come from the Crypto.com Exchange public
// API and are cached to data/candles/ with their source URL and fetch time.
// If neither the network nor a cache can supply real candles, we abort —
// this module never fabricates a bar.
import * as fs from "node:fs";
import * as path from "node:path";

export interface Candle {
  t: number; // bar open time, ms epoch
  o: number;
  h: number;
  l: number;
  c: number;
  v: number;
}

const ROOT = path.resolve(import.meta.dirname, "..");
const CACHE_DIR = path.join(ROOT, "data", "candles");
const API = "https://api.crypto.com/exchange/v1/public/get-candlestick";
// Reuse a recent cache so replay:raw and replay:memory compare the SAME bars.
const CACHE_FRESH_MS = 60 * 60 * 1000;

interface CacheFile {
  source: string;
  fetchedAt: string;
  symbol: string;
  timeframe: string;
  candles: Candle[];
}

async function fetchFromApi(symbol: string, timeframe: string, count: number): Promise<Candle[]> {
  const url = `${API}?instrument_name=${symbol}&timeframe=${timeframe}&count=${count}`;
  const res = await fetch(url, { signal: AbortSignal.timeout(20000) });
  if (!res.ok) throw new Error(`HTTP ${res.status} from ${url}`);
  const body = await res.json();
  const rows = body?.result?.data;
  if (!Array.isArray(rows) || rows.length === 0) {
    throw new Error(`Empty candle response for ${symbol}: ${JSON.stringify(body).slice(0, 200)}`);
  }
  const candles: Candle[] = rows.map((r: Record<string, string | number>) => ({
    t: Number(r.t),
    o: Number(r.o),
    h: Number(r.h),
    l: Number(r.l),
    c: Number(r.c),
    v: Number(r.v),
  }));
  candles.sort((a, b) => a.t - b.t);
  const cache: CacheFile = {
    source: url,
    fetchedAt: new Date().toISOString(),
    symbol,
    timeframe,
    candles,
  };
  fs.mkdirSync(CACHE_DIR, { recursive: true });
  fs.writeFileSync(cachePath(symbol, timeframe), JSON.stringify(cache, null, 1));
  return candles;
}

function cachePath(symbol: string, timeframe: string): string {
  return path.join(CACHE_DIR, `${symbol}_${timeframe}.json`);
}

function readCache(symbol: string, timeframe: string): CacheFile | null {
  const p = cachePath(symbol, timeframe);
  if (!fs.existsSync(p)) return null;
  try {
    return JSON.parse(fs.readFileSync(p, "utf8")) as CacheFile;
  } catch {
    return null;
  }
}

export async function getCandles(
  symbol: string,
  timeframe = "M15",
  count = 300,
): Promise<{ candles: Candle[]; source: string }> {
  const cached = readCache(symbol, timeframe);
  if (cached && Date.now() - Date.parse(cached.fetchedAt) < CACHE_FRESH_MS) {
    return { candles: cached.candles, source: `cache (fetched ${cached.fetchedAt})` };
  }
  try {
    const candles = await fetchFromApi(symbol, timeframe, count);
    return { candles, source: "live Crypto.com API" };
  } catch (err) {
    if (cached) {
      return {
        candles: cached.candles,
        source: `STALE cache from ${cached.fetchedAt} (network failed: ${(err as Error).message})`,
      };
    }
    throw new Error(
      `No real candles available for ${symbol} (network failed, no cache) — refusing to invent data. ${(err as Error).message}`,
    );
  }
}

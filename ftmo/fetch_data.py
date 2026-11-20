# -*- coding: utf-8 -*-
"""Fetch OHLCV history for the FTMO crypto CFD universe from Bybit spot.

FTMO crypto CFDs (spot-based) track the underlying spot price. We pull real
spot history for each base asset so strategy development reflects tradable
prices. Symbols follow FTMO's ticker -> Bybit spot pair mapping.

Output: ftmo/data/{SYMBOL}.csv with columns time(UTC epoch s), open, high, low,
close, volume. 1h bars.
"""
import csv
import os
import sys
import time
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import requests

BASE = "https://api.bybit.com"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
os.makedirs(OUT, exist_ok=True)

# FTMO crypto CFDs as of Jul 2025 revamp -> Bybit spot symbols (USDT pairs)
FMSO_UNIVERSE = {
    "BTCUSD": "BTCUSDT", "ETHUSD": "ETHUSDT", "SOLUSD": "SOLUSDT",
    "XRPUSD": "XRPUSDT", "DOGEUSD": "DOGEUSDT", "ADAUSD": "ADAUSDT",
    "DOTUSD": "DOTUSDT", "DASHUSD": "DASHUSDT", "LTCUSD": "LTCUSDT",
    "XMRUSD": "XMRUSDT", "NEOUSD": "NEOUSDT", "BCHUSD": "BCHUSDT",
    "BNBUSD": "BNBUSDT", "SANUSD": "SANDUSDT", "GRTUSD": "GRTUSDT",
    "IMXUSD": "IMXUSDT", "MANUSD": "MANAUSDT", "LNKUSD": "LINKUSDT",
    "MKRUSD": "MKRUSDT", "NERUSD": "NEARUSDT", "VECUSD": "VETUSDT",
    "ALGUSD": "ALGOUSDT", "XLMUSD": "XLMUSDT", "ETCUSD": "ETCUSDT",
    "UNIUSD": "UNIUSDT", "ICPUSD": "ICPUSDT", "FETUSD": "FETUSDT",
    "AAVUSD": "AAVEUSDT", "XTZUSD": "XTZUSDT", "BARUSD": "HBARUSDT",
    "GALUSD": "GALAUSDT", "AVAUSD": "AVAXUSDT",
}


def fetch_kline(symbol: str, interval: str, start: int, end: int, limit: int = 1000) -> list:
    """Fetch one page of klines (newest-first). interval='60' = 1h."""
    params = {
        "category": "spot", "symbol": symbol, "interval": interval,
        "start": start, "end": end, "limit": limit,
    }
    for attempt in range(5):
        try:
            r = requests.get(BASE + "/v5/market/kline", params=params, timeout=20)
            d = r.json()
            if d.get("retCode") == 0:
                return d["result"]["list"]
            time.sleep(1 + attempt)
        except Exception as e:
            time.sleep(1 + attempt)
    return []


def full_history(symbol: str, interval: str, start: int) -> list:
    """Paged fetch stepping `end` backward; start pinned to epoch (any old ts)."""
    rows = []
    end = int(time.time() * 1000)
    anchor = 100_000_000_000  # way before any crypto listing on Bybit spot
    pages = 0
    while True:
        page = fetch_kline(symbol, interval, anchor, end)
        if not page:
            break
        rows.extend(page)
        pages += 1
        oldest = int(page[-1][0])  # newest-first list, so [-1] is the oldest page bar
        if len(page) < 1000 or oldest <= start:
            break
        end = oldest - 1
        if pages % 25 == 0:
            print(f"  {symbol}: {pages} pages, oldest={oldest}", flush=True)
    # de-dup by startTime, sort ascending
    seen = {}
    for r in rows:
        if int(r[0]) >= start:
            seen[int(r[0])] = r
    return [seen[k] for k in sorted(seen)]


def main():
    # Start 1 Jan 2023 (3+ years is enough for swing-strategy evidence)
    start_ms = int(time.mktime(time.strptime("2023-01-01", "%Y-%m-%d"))) * 1000
    ok = []
    missing = []
    for ftmo_sym, bybit_sym in FMSO_UNIVERSE.items():
        path = os.path.join(OUT, f"{ftmo_sym}.csv")
        if os.path.exists(path):
            print(f"{ftmo_sym}: cached ({os.path.getsize(path):,} bytes)", flush=True)
            ok.append(ftmo_sym)
            continue
        rows = full_history(bybit_sym, "60", start_ms)
        if not rows:
            missing.append(ftmo_sym)
            print(f"{ftmo_sym}: NO DATA", flush=True)
            continue
        path = os.path.join(OUT, f"{ftmo_sym}.csv")
        with open(path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["time", "open", "high", "low", "close", "volume"])
            for r in rows:
                w.writerow([
                    int(r[0]) // 1000,
                    r[1], r[2], r[3], r[4], r[5],
                ])
        first = rows[0][0]
        last = rows[-1][0]
        n = len(rows)
        print(f"{ftmo_sym}: {n} bars  {time.strftime('%Y-%m-%d', time.gmtime(int(first)//1000))} -> {time.strftime('%Y-%m-%d', time.gmtime(int(last)//1000))}", flush=True)
        ok.append(ftmo_sym)
        time.sleep(0.15)

    print(f"\nOK {len(ok)} | missing {missing}")


if __name__ == "__main__":
    main()
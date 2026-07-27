#!/usr/bin/env python3
"""
STUDY 18 — PUMP CATALYST CENSUS (1 year, whole Binance spot USDT market).

QUESTION (user's, verbatim intent)
----------------------------------
Find every pair that printed a GREEN candle of >10% on the 15m / 30m / 1h / 4h
timeframes over the last year, work out WHY the price rose, and report the
groups where >= 5 pairs moved for the SAME reason.

WHY THIS IS NOT ANOTHER DEAD STUDY
----------------------------------
Studies 1-17 all asked "does this price pattern predict the next bar" and all
answered no. This asks a different question: it does not predict anything, it
CENSUSES what actually moved and attributes cause. The output is a map of
catalyst types, not a signal. Whether any of it is tradeable is a separate
question this file deliberately does not answer (see the report).

METHOD
------
1. Universe: every TRADING spot *USDT pair on Binance (~458), minus stables and
   leveraged tokens. NOTE THE SURVIVORSHIP HOLE: coins delisted during the year
   are absent, and delisting is correlated with having pumped and died. Counts
   here are a floor, not a total.
2. Data: 15m klines for the full year, one paginated fetch per symbol
   (data-api.binance.vision - api.binance.com is geo-blocked from this host).
   Reindexed onto a complete 15m grid; intervals Binance omits (no trades) are
   filled as flat zero-volume bars o=h=l=c=prev_close, which is what actually
   happened. 30m/1h/4h are resampled from that grid, so every higher-TF bar is
   built from a complete set of 15m bars and no gap can fake a candle.
3. Event: a single candle with (close - open) / open >= 0.10. Body, not wick -
   the user asked for a green candle that closed >10% up.
4. Attribution, in the order the evidence supports:
   a. NEW LISTING   - event inside the symbol's first 24h of history.
   b. MARKET-WIDE   - BTC moved >= +1.5% over the same window.
   c. CO-MOVEMENT   - >= 5 distinct symbols pumped in the same time bucket
                      without BTC moving = a shared narrative/sector catalyst.
   d. IDIOSYNCRATIC - everything left: single-pair news, unlock, hack, listing
                      elsewhere, whale. Named by hand from the top-N list.
   (a)-(c) are derived from the data. Naming the actual headline behind each
   cluster is done afterwards from the dated cluster list.

OUTPUT
------
  pump_catalyst_events.json    every event, with its attribution fields
  pump_catalyst_clusters.json  time buckets holding >= 5 distinct symbols
Raw klines cache lives in the scratchpad, not the repo (~400MB).
"""
import gzip
import json
import os
import pickle
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
except Exception:
    pass

HOST = "https://data-api.binance.vision"
HERE = Path(__file__).parent
CACHE = Path(
    os.environ.get(
        "PUMP_CACHE",
        r"C:\Users\DELL\AppData\Local\Temp\claude\C--Users-DELL-OneDrive-1m"
        r"\461d6d4d-db6f-4366-adba-df2562ad4a11\scratchpad\pump_cache",
    )
)
CACHE.mkdir(parents=True, exist_ok=True)

END = datetime(2026, 7, 28, tzinfo=timezone.utc)
START = END - timedelta(days=365)
THRESHOLD = 0.10
TFS = {"15m": "15min", "30m": "30min", "1h": "1h", "4h": "4h"}
NEW_LISTING_BARS = 96          # 24h of 15m bars
BTC_MARKET_MOVE = 0.015        # BTC return over the same window that counts as market-wide
CLUSTER_MIN_PAIRS = 5

STABLES = {"USDC", "USDT", "BUSD", "TUSD", "FDUSD", "DAI", "USDP", "EUR", "EURI",
           "AEUR", "XUSD", "USD1", "PYUSD", "RLUSD", "USDS", "BFUSD"}
LEV_SUFFIX = ("UP", "DOWN", "BULL", "BEAR")

SESSION = requests.Session()
SESSION.headers["User-Agent"] = "pump-catalyst-study/1"


# ----------------------------------------------------------------- fetch ----
def universe():
    ei = SESSION.get(f"{HOST}/api/v3/exchangeInfo", timeout=60).json()
    out = []
    for s in ei["symbols"]:
        if s["status"] != "TRADING" or s["quoteAsset"] != "USDT":
            continue
        if not s.get("isSpotTradingAllowed"):
            continue
        base = s["baseAsset"]
        if base in STABLES or base.endswith(LEV_SUFFIX):
            continue
        out.append(s["symbol"])
    return sorted(out)


def _klines(symbol, start_ms, end_ms):
    """Paginated 15m klines. Returns list of raw rows."""
    rows, cursor = [], start_ms
    while cursor < end_ms:
        for attempt in range(5):
            try:
                r = SESSION.get(
                    f"{HOST}/api/v3/klines",
                    params={"symbol": symbol, "interval": "15m",
                            "startTime": cursor, "endTime": end_ms, "limit": 1000},
                    timeout=30,
                )
                if r.status_code == 429 or r.status_code >= 500:
                    time.sleep(2 * (attempt + 1))
                    continue
                r.raise_for_status()
                batch = r.json()
                break
            except Exception:
                if attempt == 4:
                    raise
                time.sleep(1.5 * (attempt + 1))
        else:
            break
        if not batch:
            break
        rows.extend(batch)
        nxt = batch[-1][0] + 15 * 60 * 1000
        if nxt <= cursor:
            break
        cursor = nxt
        if len(batch) < 1000:
            break
    return rows


def load(symbol):
    """15m OHLCV on a complete grid, cached. None if the symbol has no history."""
    path = CACHE / f"{symbol}.pkl.gz"
    if path.exists():
        try:
            with gzip.open(path, "rb") as f:
                return pickle.load(f)
        except Exception:
            path.unlink(missing_ok=True)

    raw = _klines(symbol, int(START.timestamp() * 1000), int(END.timestamp() * 1000))
    if len(raw) < 96:
        return None

    df = pd.DataFrame(raw, columns=[
        "ts", "open", "high", "low", "close", "volume", "close_time",
        "quote_volume", "trades", "tbb", "tbq", "ignore"])
    df["ts"] = pd.to_datetime(df["ts"].astype("int64"), unit="ms", utc=True)
    df = df.set_index("ts").sort_index()
    df = df[~df.index.duplicated(keep="first")]
    df = df[["open", "high", "low", "close", "volume", "quote_volume", "trades"]].astype(float)

    # Complete 15m grid. Binance omits intervals with zero trades on illiquid
    # pairs; those are flat bars, not missing data. Filling them keeps every
    # resampled 30m/1h/4h bar built from a full set of 15m bars.
    grid = pd.date_range(df.index[0], df.index[-1], freq="15min", tz="UTC")
    df = df.reindex(grid)
    prev_close = df["close"].ffill().shift(1)
    flat = df["open"].isna()
    for col in ("open", "high", "low", "close"):
        df.loc[flat, col] = prev_close[flat]
    df[["volume", "quote_volume", "trades"]] = df[["volume", "quote_volume", "trades"]].fillna(0.0)
    df = df.dropna(subset=["close"])
    df.attrs["filled_bars"] = int(flat.sum())

    with gzip.open(path, "wb", compresslevel=4) as f:
        pickle.dump(df.astype(np.float64), f)
    return df


# ------------------------------------------------------------- detection ----
def resample(df15, rule):
    if rule == "15min":
        return df15
    o = df15.resample(rule, label="left", closed="left").agg({
        "open": "first", "high": "max", "low": "min", "close": "last",
        "volume": "sum", "quote_volume": "sum", "trades": "sum"})
    return o.dropna(subset=["open", "close"])


def detect(symbol, df15):
    """Every >=10% green body across the four timeframes."""
    events = []
    listing_cutoff = df15.index[0] + timedelta(minutes=15 * NEW_LISTING_BARS)
    history_start = df15.index[0]
    for tf, rule in TFS.items():
        d = resample(df15, rule)
        pct = (d["close"] - d["open"]) / d["open"]
        hit = d[pct >= THRESHOLD]
        if hit.empty:
            continue
        hp = pct[pct >= THRESHOLD]
        for ts, row in hit.iterrows():
            events.append({
                "symbol": symbol,
                "base": symbol[:-4],
                "tf": tf,
                "ts": ts.isoformat(),
                "date": ts.strftime("%Y-%m-%d"),
                "open": float(row["open"]),
                "close": float(row["close"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "pct": round(float(hp.loc[ts]) * 100, 3),
                "wick_pct": round(float((row["high"] - row["open"]) / row["open"]) * 100, 3),
                "quote_volume": float(row["quote_volume"]),
                "trades": int(row["trades"]),
                "new_listing": bool(ts < listing_cutoff),
                "history_start": history_start.isoformat(),
            })
    return events


# ------------------------------------------------------------------ main ----
def main():
    syms = universe()
    print(f"universe: {len(syms)} spot USDT pairs")
    print(f"window:   {START:%Y-%m-%d} -> {END:%Y-%m-%d} (365d)")

    print("\nfetching BTC reference...")
    btc = load("BTCUSDT")
    if btc is None:
        sys.exit("BTCUSDT fetch failed")
    btc_tf = {tf: resample(btc, rule) for tf, rule in TFS.items()}
    btc_ret = {tf: ((d["close"] - d["open"]) / d["open"]) for tf, d in btc_tf.items()}
    print(f"  BTC bars: {len(btc)} 15m, {btc.index[0]:%Y-%m-%d} -> {btc.index[-1]:%Y-%m-%d}")

    events, meta, failed = [], {}, []
    done = 0
    t0 = time.time()

    def work(sym):
        df = load(sym)
        if df is None or len(df) < 96:
            return sym, None, None
        return sym, detect(sym, df), {
            "bars": len(df),
            "start": df.index[0].isoformat(),
            "end": df.index[-1].isoformat(),
            "filled_bars": int(df.attrs.get("filled_bars", 0)),
            "days": round(len(df) * 15 / 1440, 1),
        }

    with ThreadPoolExecutor(max_workers=12) as ex:
        futs = {ex.submit(work, s): s for s in syms}
        for fut in as_completed(futs):
            sym = futs[fut]
            try:
                sym, evs, m = fut.result()
                if evs is None:
                    failed.append(sym)
                else:
                    events.extend(evs)
                    meta[sym] = m
            except Exception as e:
                failed.append(f"{sym}:{type(e).__name__}")
            done += 1
            if done % 25 == 0:
                el = time.time() - t0
                print(f"  [{done}/{len(syms)}] {len(events)} events  "
                      f"{el:.0f}s  eta {el / done * (len(syms) - done):.0f}s")

    print(f"\n{len(events)} raw events across {len(meta)} symbols "
          f"({len(failed)} failed/short)")

    # --- attribution -------------------------------------------------------
    for e in events:
        ts = pd.Timestamp(e["ts"])
        r = btc_ret[e["tf"]]
        e["btc_pct"] = round(float(r.loc[ts]) * 100, 3) if ts in r.index else None
        e["btc_up"] = bool(e["btc_pct"] is not None and e["btc_pct"] >= BTC_MARKET_MOVE * 100)

    # --- clustering: same TF, same bar timestamp, >= 5 distinct symbols -----
    clusters = []
    ev_df = pd.DataFrame(events)
    if not ev_df.empty:
        for (tf, ts), g in ev_df.groupby(["tf", "ts"]):
            syms_hit = sorted(g["base"].unique())
            if len(syms_hit) < CLUSTER_MIN_PAIRS:
                continue
            clusters.append({
                "tf": tf,
                "ts": ts,
                "date": g["date"].iloc[0],
                "n_pairs": len(syms_hit),
                "pairs": syms_hit,
                "btc_pct": float(g["btc_pct"].iloc[0]) if g["btc_pct"].iloc[0] is not None else None,
                "median_pct": round(float(g["pct"].median()), 2),
                "max_pct": round(float(g["pct"].max()), 2),
                "total_quote_volume": float(g["quote_volume"].sum()),
                "n_new_listings": int(g["new_listing"].sum()),
            })
        clusters.sort(key=lambda c: (-c["n_pairs"], c["ts"]))

        # daily clusters too - a narrative rotation does not fire on one bar
        day_clusters = []
        for (tf, day), g in ev_df.groupby(["tf", "date"]):
            syms_hit = sorted(g["base"].unique())
            if len(syms_hit) < CLUSTER_MIN_PAIRS:
                continue
            day_clusters.append({
                "tf": tf, "date": day, "n_pairs": len(syms_hit), "pairs": syms_hit,
                "median_pct": round(float(g["pct"].median()), 2),
                "max_pct": round(float(g["pct"].max()), 2),
                "n_new_listings": int(g["new_listing"].sum()),
                "btc_day_pct": None,
            })
        day_clusters.sort(key=lambda c: (-c["n_pairs"], c["date"]))
    else:
        day_clusters = []

    # BTC daily return for the day clusters
    btc_day = btc.resample("1D").agg({"open": "first", "close": "last"})
    btc_day_ret = ((btc_day["close"] - btc_day["open"]) / btc_day["open"] * 100).round(2)
    for c in day_clusters:
        k = pd.Timestamp(c["date"], tz="UTC")
        c["btc_day_pct"] = float(btc_day_ret.loc[k]) if k in btc_day_ret.index else None

    out = {
        "meta": {
            "generated": datetime.now(timezone.utc).isoformat(),
            "window_start": START.isoformat(),
            "window_end": END.isoformat(),
            "days": 365,
            "threshold_pct": THRESHOLD * 100,
            "definition": "single candle (close-open)/open >= 10%",
            "timeframes": list(TFS),
            "universe_requested": len(syms),
            "symbols_with_data": len(meta),
            "failed_or_short": failed,
            "survivorship_note": "delisted coins absent; counts are a floor",
            "source": HOST,
        },
        "symbol_meta": meta,
        "events": events,
    }
    (HERE / "pump_catalyst_events.json").write_text(json.dumps(out), encoding="utf-8")
    (HERE / "pump_catalyst_clusters.json").write_text(json.dumps({
        "bar_clusters": clusters, "day_clusters": day_clusters,
    }), encoding="utf-8")

    # --- console summary ---------------------------------------------------
    print("\n=== EVENTS BY TIMEFRAME ===")
    for tf in TFS:
        sub = [e for e in events if e["tf"] == tf]
        pairs = len({e["base"] for e in sub})
        print(f"  {tf:>4}: {len(sub):>6} events  {pairs:>4} distinct pairs")

    print("\n=== ATTRIBUTION (all TFs) ===")
    n = len(events) or 1
    listing = sum(e["new_listing"] for e in events)
    btcup = sum(e["btc_up"] and not e["new_listing"] for e in events)
    print(f"  new listing (<24h old): {listing:>6} ({listing/n*100:.1f}%)")
    print(f"  BTC +1.5%+ same bar:    {btcup:>6} ({btcup/n*100:.1f}%)")
    print(f"  neither:                {n-listing-btcup:>6} ({(n-listing-btcup)/n*100:.1f}%)")

    print(f"\n=== BAR CLUSTERS (>= {CLUSTER_MIN_PAIRS} pairs, same bar) === {len(clusters)}")
    for c in clusters[:20]:
        b = f"BTC {c['btc_pct']:+.2f}%" if c["btc_pct"] is not None else "BTC n/a"
        print(f"  {c['ts'][:16]} {c['tf']:>3} {c['n_pairs']:>3} pairs  "
              f"med {c['median_pct']:>5.1f}%  {b}  new={c['n_new_listings']}")
        print(f"      {', '.join(c['pairs'][:18])}")

    print(f"\n=== DAY CLUSTERS (>= {CLUSTER_MIN_PAIRS} pairs, same UTC day) === {len(day_clusters)}")
    for c in day_clusters[:25]:
        b = f"BTC {c['btc_day_pct']:+.2f}%" if c["btc_day_pct"] is not None else "n/a"
        print(f"  {c['date']} {c['tf']:>3} {c['n_pairs']:>3} pairs  "
              f"med {c['median_pct']:>5.1f}%  {b}  new={c['n_new_listings']}")

    top = sorted(events, key=lambda e: -e["pct"])[:25]
    print("\n=== 25 BIGGEST SINGLE CANDLES ===")
    for e in top:
        tag = "LISTING" if e["new_listing"] else ("BTC" if e["btc_up"] else "solo")
        print(f"  {e['ts'][:16]} {e['tf']:>3} {e['base']:>10} {e['pct']:>7.1f}%  "
              f"${e['quote_volume']/1e6:>7.2f}M  {tag}")

    print(f"\nwrote pump_catalyst_events.json ({len(events)} events)")
    print(f"wrote pump_catalyst_clusters.json")


if __name__ == "__main__":
    main()

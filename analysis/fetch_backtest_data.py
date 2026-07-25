#!/usr/bin/env python3
"""
Binance Historical Data Fetcher for Multi-Alpha Backtesting.
Fetches 6 months of 4h klines + funding rates + aggregated trades.
"""
import time
import json
import requests
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime, timezone, timedelta

SPOT_BASE = "https://api.binance.com"
FUTURES_BASE = "https://fapi.binance.com"
DATA_DIR = Path(__file__).parent / "backtest_data"
DATA_DIR.mkdir(exist_ok=True)

# Top 10 high-volume halal spot assets for basket
BASKET_SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
    "ADAUSDT", "DOGEUSDT", "DOTUSDT", "AVAXUSDT", "LINKUSDT",
]

# Additional symbols for absorption engine scan
ABSORPTION_SYMBOLS = [
    "SUIUSDT", "NEARUSDT", "AAVEUSDT", "UNIUSDT", "INJUSDT",
    "RENDERUSDT", "FETUSDT", "SEIUSDT", "TIAUSDT", "ONDOUSDT",
]


def fetch_klines(symbol, interval="4h", limit=1000, base_url=SPOT_BASE):
    """Fetch klines from Binance REST API."""
    url = f"{base_url}/api/v3/klines"
    r = requests.get(url, params={
        "symbol": symbol, "interval": interval, "limit": limit
    }, timeout=30)
    r.raise_for_status()
    data = r.json()
    df = pd.DataFrame(data, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trades", "taker_buy_base",
        "taker_buy_quote", "ignore"
    ])
    for col in ["open", "high", "low", "close", "volume", "quote_volume"]:
        df[col] = df[col].astype(float)
    df["timestamp"] = pd.to_datetime(df["open_time"], unit="ms")
    return df[["timestamp", "open", "high", "low", "close", "volume", "quote_volume"]]


def fetch_funding_rates(symbol, start_ms, end_ms, limit=1000):
    """Fetch historical funding rates from Binance Futures."""
    url = f"{FUTURES_BASE}/fapi/v1/fundingRate"
    all_data = []
    current_start = start_ms

    while current_start < end_ms:
        r = requests.get(url, params={
            "symbol": symbol, "startTime": current_start,
            "endTime": end_ms, "limit": limit
        }, timeout=30)
        r.raise_for_status()
        data = r.json()
        if not data:
            break
        all_data.extend(data)
        current_start = data[-1]["fundingTime"] + 1
        time.sleep(0.2)  # Rate limit

    if not all_data:
        return pd.DataFrame()

    df = pd.DataFrame(all_data)
    df["fundingRate"] = df["fundingRate"].astype(float)
    df["timestamp"] = pd.to_datetime(df["fundingTime"], unit="ms")
    df["symbol"] = symbol
    return df[["timestamp", "symbol", "fundingRate", "fundingTime"]]


def fetch_perp_klines(symbol, interval="4h", limit=1000):
    """Fetch perpetual futures klines."""
    url = f"{FUTURES_BASE}/fapi/v1/klines"
    r = requests.get(url, params={
        "symbol": symbol, "interval": interval, "limit": limit
    }, timeout=30)
    r.raise_for_status()
    data = r.json()
    df = pd.DataFrame(data, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trades", "taker_buy_base",
        "taker_buy_quote", "ignore"
    ])
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)
    df["timestamp"] = pd.to_datetime(df["open_time"], unit="ms")
    return df[["timestamp", "close", "volume"]].rename(
        columns={"close": "perp_close", "volume": "perp_volume"}
    )


def fetch_agg_trades(symbol, start_ms, end_ms, limit=1000):
    """Fetch aggregated trades for CVD calculation."""
    url = f"{SPOT_BASE}/api/v3/aggTrades"
    all_trades = []
    current_start = start_ms

    while current_start < end_ms:
        r = requests.get(url, params={
            "symbol": symbol, "startTime": current_start,
            "endTime": end_ms, "limit": limit
        }, timeout=30)
        r.raise_for_status()
        data = r.json()
        if not data:
            break
        all_trades.extend(data)
        current_start = data[-1]["T"] + 1
        time.sleep(0.1)

        if len(all_trades) > 50000:
            break

    if not all_trades:
        return pd.DataFrame()

    df = pd.DataFrame(all_trades)
    df["qty"] = df["q"].astype(float)
    df["price"] = df["p"].astype(float)
    df["is_buyer_maker"] = df["m"]
    df["timestamp"] = pd.to_datetime(df["T"], unit="ms")
    return df[["timestamp", "price", "qty", "is_buyer_maker"]]


def fetch_24h_tickers():
    """Fetch current 24h volume for symbol selection."""
    r = requests.get(f"{SPOT_BASE}/api/v3/ticker/24hr", timeout=30)
    r.raise_for_status()
    return r.json()


def fetch_order_book_snapshot(symbol, limit=20):
    """Fetch current order book (depth 20)."""
    r = requests.get(f"{SPOT_BASE}/api/v3/depth", params={
        "symbol": symbol, "limit": limit
    }, timeout=15)
    r.raise_for_status()
    data = r.json()
    bids = [(float(p), float(q)) for p, q in data["bids"]]
    asks = [(float(p), float(q)) for p, q in data["asks"]]
    return {"bids": bids, "asks": asks, "spread_pct": (asks[0][0] - bids[0][0]) / bids[0][0] * 100}


def fetch_6month_klines(symbol, interval="4h"):
    """Fetch 6 months of 4h klines (~1095 bars)."""
    all_bars = []
    now_ms = int(time.time() * 1000)
    six_months_ms = 180 * 24 * 3600 * 1000
    start_ms = now_ms - six_months_ms

    current = start_ms
    while current < now_ms:
        try:
            r = requests.get(f"{SPOT_BASE}/api/v3/klines", params={
                "symbol": symbol, "interval": interval,
                "startTime": current, "limit": 1000
            }, timeout=30)
            r.raise_for_status()
            data = r.json()
            if not data:
                break
            all_bars.extend(data)
            current = data[-1][0] + 1
            time.sleep(0.2)
        except Exception as e:
            print(f"  Retry for {symbol}: {e}")
            time.sleep(2)
            continue

    if not all_bars:
        return pd.DataFrame()

    df = pd.DataFrame(all_bars, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trades", "taker_buy_base",
        "taker_buy_quote", "ignore"
    ])
    for col in ["open", "high", "low", "close", "volume", "quote_volume", "taker_buy_base"]:
        df[col] = df[col].astype(float)
    df["timestamp"] = pd.to_datetime(df["open_time"], unit="ms")
    return df[["timestamp", "open", "high", "low", "close", "volume", "quote_volume", "taker_buy_base"]]


def fetch_6month_perp_klines(symbol, interval="4h"):
    """Fetch 6 months of perpetual 4h klines."""
    all_bars = []
    now_ms = int(time.time() * 1000)
    six_months_ms = 180 * 24 * 3600 * 1000
    start_ms = now_ms - six_months_ms

    current = start_ms
    while current < now_ms:
        try:
            r = requests.get(f"{FUTURES_BASE}/fapi/v1/klines", params={
                "symbol": symbol, "interval": interval,
                "startTime": current, "limit": 1000
            }, timeout=30)
            r.raise_for_status()
            data = r.json()
            if not data:
                break
            all_bars.extend(data)
            current = data[-1][0] + 1
            time.sleep(0.2)
        except Exception as e:
            print(f"  Retry perp {symbol}: {e}")
            time.sleep(2)
            continue

    if not all_bars:
        return pd.DataFrame()

    df = pd.DataFrame(all_bars, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trades", "taker_buy_base",
        "taker_buy_quote", "ignore"
    ])
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)
    df["timestamp"] = pd.to_datetime(df["open_time"], unit="ms")
    return df[["timestamp", "open", "high", "low", "close", "volume"]]


def fetch_6month_funding(symbol):
    """Fetch 6 months of funding rates."""
    now_ms = int(time.time() * 1000)
    six_months_ms = 180 * 24 * 3600 * 1000
    return fetch_funding_rates(symbol, now_ms - six_months_ms, now_ms)


def run_full_fetch():
    """Fetch all data needed for both backtests."""
    print("=" * 60)
    print("FETCHING 6-MONTH HISTORICAL DATA")
    print("=" * 60)

    all_symbols = list(set(BASKET_SYMBOLS + ABSORPTION_SYMBOLS))

    for sym in all_symbols:
        print(f"\n--- {sym} ---")

        # 1. Spot klines
        cache_file = DATA_DIR / f"{sym}_spot_4h.parquet"
        if not cache_file.exists():
            print(f"  Fetching spot 4h klines...")
            df = fetch_6month_klines(sym, "4h")
            if not df.empty:
                df.to_parquet(cache_file)
                print(f"  Saved {len(df)} spot bars")
            else:
                print(f"  WARNING: No spot data")
        else:
            print(f"  Spot klines cached ({len(pd.read_parquet(cache_file))} bars)")

        # 2. Perp klines
        perp_cache = DATA_DIR / f"{sym}_perp_4h.parquet"
        if not perp_cache.exists():
            print(f"  Fetching perp 4h klines...")
            df = fetch_6month_perp_klines(sym, "4h")
            if not df.empty:
                df.to_parquet(perp_cache)
                print(f"  Saved {len(df)} perp bars")
            else:
                print(f"  No perp data (expected for some)")
        else:
            print(f"  Perp klines cached")

        # 3. Funding rates
        fund_cache = DATA_DIR / f"{sym}_funding.parquet"
        if not fund_cache.exists():
            print(f"  Fetching funding rates...")
            df = fetch_6month_funding(sym)
            if not df.empty:
                df.to_parquet(fund_cache)
                print(f"  Saved {len(df)} funding records")
            else:
                print(f"  No funding data")
        else:
            print(f"  Funding cached")

        time.sleep(0.5)  # Global rate limit

    print("\n" + "=" * 60)
    print("DATA FETCH COMPLETE")
    print("=" * 60)

    # Summary
    files = list(DATA_DIR.glob("*.parquet"))
    total_size = sum(f.stat().st_size for f in files) / 1024 / 1024
    print(f"Total files: {len(files)}")
    print(f"Total size: {total_size:.1f} MB")
    print(f"Location: {DATA_DIR}")


if __name__ == "__main__":
    run_full_fetch()

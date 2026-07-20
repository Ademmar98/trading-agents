"""Fetch 2022→2026 1H crypto bars from Alpaca (the firm's own data feed)."""
from datetime import datetime, timezone

import pandas as pd
from alpaca.data.historical import CryptoHistoricalDataClient
from alpaca.data.requests import CryptoBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

OUT = str(__import__("pathlib").Path(__file__).parent / "data")

client = CryptoHistoricalDataClient()
for sym in ["BTC/USD", "ETH/USD", "SOL/USD"]:
    req = CryptoBarsRequest(
        symbol_or_symbols=sym,
        timeframe=TimeFrame(1, TimeFrameUnit.Hour),
        start=datetime(2022, 1, 1, tzinfo=timezone.utc),
        end=datetime(2026, 7, 19, tzinfo=timezone.utc),
    )
    df = client.get_crypto_bars(req).df.reset_index()
    df = df[["timestamp", "open", "high", "low", "close", "volume"]]
    df = df.rename(columns={"timestamp": "ts"}).drop_duplicates("ts").sort_values("ts")
    name = sym.replace("/", "")
    df.to_parquet(f"{OUT}/{name}_1h.parquet")
    print(name, len(df), df["ts"].iloc[0], "->", df["ts"].iloc[-1])

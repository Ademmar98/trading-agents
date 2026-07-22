"""
Real free data adapters — data.binance.vision (public static dumps)
===================================================================
The binance.vision CDN serves historical market data as ZIP/CSV and is
US-accessible (unlike the geo-blocked api.binance.com trading endpoint). This
wires 3 of the strategy's 4 inputs on REAL data:

  * Price 1H OHLCV ......... spot monthly klines
  * Aggressor delta ........ from the kline `taker_buy_base` column:
                             delta = taker_buy_base - (volume - taker_buy_base)
                                   = 2*taker_buy_base - volume
  * Perp funding rate ...... futures/um monthly fundingRate

The 4th input — exchange netflow / reserve balance — is a PAID metric
(CryptoQuant/Glassnode). Supply it via onchain_csv; without it, real_bundle()
falls back to a clearly-labelled synthetic on-chain placeholder so the
price/funding/delta pipeline runs on real data (results still not a valid edge
test until real on-chain is connected).

No API key, no auth, no `ccxt` dependency — just urllib + pandas.
"""
import urllib.request, io, zipfile, csv
import numpy as np
import pandas as pd

BASE = "https://data.binance.vision/data"


def _months(start: str, end: str):
    """Inclusive 'YYYY-MM' month range."""
    return [str(p) for p in pd.period_range(pd.Period(start, "M"), pd.Period(end, "M"), freq="M")]


def _fetch_zip_csv(url: str):
    try:
        b = urllib.request.urlopen(url, timeout=60).read()
    except Exception:
        return None                       # month missing (e.g. before listing) -> skip
    z = zipfile.ZipFile(io.BytesIO(b))
    return list(csv.reader(io.TextIOWrapper(z.open(z.namelist()[0]))))


def load_klines_1h(symbol: str = "BTCUSDT", start: str = "2023-01", end: str = "2024-12"):
    """Return (price_df[open,high,low,close,volume], aggressor_delta_series)."""
    rows = []
    for m in _months(start, end):
        data = _fetch_zip_csv(f"{BASE}/spot/monthly/klines/{symbol}/1h/{symbol}-1h-{m}.zip")
        if not data:
            continue
        for r in data:
            try:
                ot = int(r[0])            # open_time ms (header rows fail here -> skipped)
            except (ValueError, IndexError):
                continue
            # cols: 0 open_time,1 o,2 h,3 l,4 c,5 vol,...,9 taker_buy_base
            rows.append((ot, float(r[1]), float(r[2]), float(r[3]), float(r[4]),
                         float(r[5]), float(r[9])))
    if not rows:
        raise RuntimeError(f"no kline data fetched for {symbol} {start}..{end}")
    df = pd.DataFrame(rows, columns=["t", "open", "high", "low", "close", "volume", "taker_buy"])
    df["t"] = pd.to_datetime(df["t"], unit="ms", utc=True)
    df = df.set_index("t").sort_index()
    df = df[~df.index.duplicated(keep="first")]
    delta = (2 * df["taker_buy"] - df["volume"]).rename("delta")   # aggressor buy - sell
    return df[["open", "high", "low", "close", "volume"]], delta


def load_funding(symbol: str = "BTCUSDT", start: str = "2023-01", end: str = "2024-12") -> pd.Series:
    """Perp funding rate series (published ~every 8h). Sentiment gate only — never traded."""
    rows = []
    for m in _months(start, end):
        data = _fetch_zip_csv(f"{BASE}/futures/um/monthly/fundingRate/{symbol}/{symbol}-fundingRate-{m}.zip")
        if not data:
            continue
        for r in data:
            try:
                ct = int(r[0])            # calc_time ms
            except (ValueError, IndexError):
                continue
            rows.append((ct, float(r[2])))                        # last_funding_rate
    if not rows:
        raise RuntimeError(f"no funding data fetched for {symbol} {start}..{end}")
    s = pd.Series([v for _, v in rows], index=[t for t, _ in rows])
    s.index = pd.to_datetime(s.index, unit="ms", utc=True)
    return s.sort_index()


def real_bundle(symbol: str = "BTC/USDT", start: str = "2023-01", end: str = "2024-12",
                onchain_csv: str = None) -> dict:
    """Assemble a strategy bundle with REAL price/funding/aggressor-delta.
    on-chain: real if onchain_csv given, else a labelled synthetic placeholder."""
    sym = symbol.replace("/", "")
    price, delta = load_klines_1h(sym, start, end)
    funding = load_funding(sym, start, end)
    if onchain_csv:
        from onchain_regime_strategy import load_onchain_csv
        onchain = load_onchain_csv(onchain_csv)
        synthetic = False
    else:
        # On-chain NOT connected -> placeholder. netflow/reserve GATES ARE NOT REAL.
        t = np.arange(len(price))
        onchain = pd.DataFrame({"netflow": np.sin(t * 0.05) * 500,
                                "reserve": 2_000_000 + np.cumsum(np.sin(t * 0.02)) * 100},
                               index=price.index)
        synthetic = True
    return {"price": price, "funding": funding, "delta": delta, "onchain": onchain,
            "_synthetic": synthetic}


if __name__ == "__main__":
    # quick real-data smoke test (one recent year)
    price, delta = load_klines_1h("BTCUSDT", "2024-01", "2024-03")
    fund = load_funding("BTCUSDT", "2024-01", "2024-03")
    print(f"price bars: {len(price)}  range {price.index[0]} .. {price.index[-1]}")
    print(f"aggressor delta: mean={delta.mean():.1f}  (sign split: "
          f"{(delta>0).mean()*100:.0f}% buy-dominant bars)")
    print(f"funding points: {len(fund)}  mean={fund.mean()*100:.4f}%/8h  "
          f"max={fund.max()*100:.4f}%  min={fund.min()*100:.4f}%")

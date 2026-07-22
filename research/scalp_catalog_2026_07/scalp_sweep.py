"""Expanded halal-spot scalping backtest — real Alpaca data, runs ON THE VPS.

User brief: top-liquid spot pairs, 4y, 1m/3m/5m/15m, FIXED $1000/trade,
0.1% fee/leg + 0.05% slip/side (=0.30% round-trip), every valid signal.
Long-only spot (halal: no shorts/leverage/funding). Canonical FIXED params
(no per-pair optimization) => honest full-sample realized performance, not
curve-fit. Reports per-strategy AND per-pair metrics incl. max consec losses.

Data reality (Alpaca US crypto): ~24 USD majors with usable history, not 50.
15m/5m on the full universe; 1m/3m on a 6-major subset (data volume).
"""
import json, time, os, urllib.parse, urllib.request
import numpy as np
import pandas as pd

END = "2026-07-20T00:00:00Z"
START_FULL = "2022-01-01T00:00:00Z"
START_1M = "2024-01-01T00:00:00Z"   # 1m subset window (data volume)

# Halal-eligible USD majors available on Alpaca with usable history.
# 'defi' flag = lending/AMM token (halal-caution: riba-linked) — reported separately.
UNIVERSE = [
    ("BTC/USD", 0), ("ETH/USD", 0), ("SOL/USD", 0), ("DOGE/USD", 0), ("AVAX/USD", 0),
    ("LINK/USD", 0), ("LTC/USD", 0), ("BCH/USD", 0), ("UNI/USD", 1), ("MATIC/USD", 0),
    ("NEAR/USD", 0), ("TRX/USD", 0), ("ALGO/USD", 0), ("GRT/USD", 0), ("BAT/USD", 0),
    ("XRP/USD", 0), ("DOT/USD", 0), ("XTZ/USD", 0), ("SHIB/USD", 0), ("CRV/USD", 1),
    ("AAVE/USD", 1), ("MKR/USD", 1), ("YFI/USD", 1), ("SUSHI/USD", 1),
]
SUBSET_1M = ["BTC/USD", "ETH/USD", "SOL/USD", "DOGE/USD", "AVAX/USD", "LINK/USD"]

FEE = 0.001      # 0.1% per leg
SLIP = 0.0005    # 0.05% per side
RT_COST = 2 * (FEE + SLIP)   # 0.30% round trip
USD_PER_TRADE = 1000.0
OUT = "/tmp/scalp_expanded"
BARS_PER_YEAR = {"1Min": 525600, "3Min": 175200, "5Min": 105120, "15Min": 35040}


def fetch(symbol, tf, start):
    native = "1Min" if tf == "3Min" else tf
    rows, token = [], None
    base = ("https://data.alpaca.markets/v1beta3/crypto/us/bars"
            f"?symbols={urllib.parse.quote(symbol, safe='')}"
            f"&timeframe={native}&start={start}&end={END}&limit=10000")
    pages = 0
    while True:
        url = base + (f"&page_token={token}" if token else "")
        for a in range(5):
            try:
                d = json.loads(urllib.request.urlopen(url, timeout=40).read()); break
            except Exception:
                time.sleep(2 * (a + 1))
        else:
            return None
        for b in d.get("bars", {}).get(symbol, []):
            rows.append((b["t"], b["o"], b["h"], b["l"], b["c"], b["v"]))
        token = d.get("next_page_token"); pages += 1
        if not token:
            break
    if len(rows) < 500:
        return None
    df = pd.DataFrame(rows, columns=["t", "o", "h", "l", "c", "v"])
    if tf == "3Min":  # resample 1Min -> 3Min
        df["t"] = pd.to_datetime(df["t"])
        df = df.set_index("t").resample("3min").agg(
            {"o": "first", "h": "max", "l": "min", "c": "last", "v": "sum"}).dropna().reset_index()
    a = df[["o", "h", "l", "c", "v"]].to_numpy(float)
    return {"o": a[:, 0], "h": a[:, 1], "l": a[:, 2], "c": a[:, 3], "v": a[:, 4]}


# ── indicators ──
def ema(x, n): return pd.Series(x).ewm(span=n, adjust=False).mean().values
def sma(x, n): return pd.Series(x).rolling(n).mean().values
def std(x, n): return pd.Series(x).rolling(n).std().values
def rsi(x, n=14):
    d = np.diff(x, prepend=x[0]); up = np.clip(d, 0, None); dn = np.clip(-d, 0, None)
    ru = pd.Series(up).ewm(alpha=1/n, adjust=False).mean().values
    rd = pd.Series(dn).ewm(alpha=1/n, adjust=False).mean().values
    return 100 - 100/(1 + ru/np.where(rd == 0, 1e-9, rd))
def atr(h, l, c, n=14):
    pc = np.roll(c, 1); pc[0] = c[0]
    tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
    return pd.Series(tr).ewm(alpha=1/n, adjust=False).mean().values
def roll_max(x, n): return pd.Series(x).rolling(n).max().values
def roll_min(x, n): return pd.Series(x).rolling(n).min().values
def macd_hist(x, f=12, s=26, g=9):
    m = ema(x, f) - ema(x, s); return m - ema(m, g)


def _stateful(enter, exit_):
    """Build a 0/1 long/flat position array from boolean enter/exit conditions."""
    n = len(enter); s = np.zeros(n); st = 0
    for i in range(n):
        if st == 0 and enter[i]:
            st = 1
        elif st == 1 and exit_[i]:
            st = 0
        s[i] = st
    return s


# ── strategy pool (long/flat) — canonical fixed params, all 4 categories ──
def signal(name, D):
    c, h, l, v = D["c"], D["h"], D["l"], D["v"]
    if name == "EMA_cross":                      # trend
        return (ema(c, 9) > ema(c, 21)).astype(float)
    if name == "MACD_mom":                       # trend/momentum
        return (macd_hist(c) > 0).astype(float)
    if name == "RSI_surge":                      # momentum surge
        r = rsi(c, 14); e50 = ema(c, 50)
        return _stateful((r > 55) & (c > e50), r < 45)
    if name == "RSI_meanrev":                    # mean reversion
        r = rsi(c, 14)
        return _stateful(r < 30, r > 50)
    if name == "Boll_meanrev":                   # mean reversion
        m = sma(c, 20); sd = std(c, 20); lo = m - 2*sd
        return _stateful(c < lo, c > m)
    if name == "Boll_squeeze_break":             # volatility/breakout
        m = sma(c, 20); sd = std(c, 20); up = m + 2*sd
        bw = (4*sd) / np.where(m == 0, 1e-9, m)
        squeeze = bw < (sma(bw, 50) * 0.8)
        prev_sq = np.roll(squeeze, 1)
        return _stateful(prev_sq & (c > up), c < m)
    if name == "Donchian_break":                 # volatility/breakout
        hh = roll_max(np.roll(h, 1), 20); ll = roll_min(np.roll(l, 1), 10)
        return _stateful(c > hh, c < ll)
    if name == "VWAP_dev":                        # mean-rev / order-flow proxy
        tp = (h + l + c) / 3; win = 96
        num = pd.Series(tp * v).rolling(win).sum().values
        den = pd.Series(v).rolling(win).sum().values
        vwap = num / np.where(den == 0, 1e-9, den)
        return _stateful(c < vwap * 0.997, c > vwap)
    if name == "Vol_burst":                       # order-flow proxy (burst)
        a = atr(h, l, c, 14); am = sma(a, 50)
        return ((a > am * 1.5) & (c > np.roll(c, 1))).astype(float)
    raise ValueError(name)

STRATS = ["EMA_cross", "MACD_mom", "RSI_surge", "RSI_meanrev", "Boll_meanrev",
          "Boll_squeeze_break", "Donchian_break", "VWAP_dev", "Vol_burst"]
STRAT_CAT = {"EMA_cross": "Trend/Momentum", "MACD_mom": "Trend/Momentum",
             "RSI_surge": "Trend/Momentum", "RSI_meanrev": "Mean Reversion",
             "Boll_meanrev": "Mean Reversion", "Boll_squeeze_break": "Volatility/Breakout",
             "Donchian_break": "Volatility/Breakout", "VWAP_dev": "Mean Rev / Order-flow proxy",
             "Vol_burst": "Order-flow proxy"}


def backtest_trades(D, pos):
    """Extract per-trade $ P&L (fixed $1000/trade) and bar-return series."""
    c = D["c"]
    ret = np.zeros(len(c)); ret[1:] = c[1:] / c[:-1] - 1
    p = np.zeros(len(c)); p[1:] = pos[:-1]           # act next bar
    flips = np.abs(np.diff(p, prepend=0)) > 0
    bar = p * ret - flips * (RT_COST / 2)            # bar-level (cost split per flip)
    trades_usd = []
    i = 0
    while i < len(p):
        if p[i] == 1:
            j = i
            while j < len(p) and p[j] == 1:
                j += 1
            entry = c[i-1] if i > 0 else c[i]
            exit_ = c[j-1]
            tr_ret = (exit_/entry - 1) - RT_COST
            trades_usd.append(USD_PER_TRADE * tr_ret)
            i = j
        else:
            i += 1
    return np.array(bar), np.array(trades_usd)


def max_consec_losses(trades):
    m = c = 0
    for t in trades:
        if t < 0:
            c += 1; m = max(m, c)
        else:
            c = 0
    return m


def pair_metrics(bar, trades, ppy):
    if len(trades) == 0:
        return None
    wins = trades[trades > 0]; losses = trades[trades < 0]
    total = float(trades.sum())
    # equity curve of a constant-$1000-at-risk sleeve
    eq = USD_PER_TRADE + np.cumsum(trades)
    peak = np.maximum.accumulate(eq)
    dd = (peak - eq)
    maxdd_usd = float(dd.max()) if len(dd) else 0.0
    maxdd_pct = float((dd / np.where(peak <= 0, np.nan, peak)).max() * 100) if len(dd) else 0.0
    pf = float(wins.sum() / abs(losses.sum())) if len(losses) and losses.sum() != 0 else (99.0 if len(wins) else 0.0)
    b = bar[np.isfinite(bar)]
    sharpe = float(b.mean()/b.std()*np.sqrt(ppy)) if len(b) > 30 and b.std() > 0 else 0.0
    downside = b[b < 0]
    sortino = float(b.mean()/downside.std()*np.sqrt(ppy)) if len(downside) > 5 and downside.std() > 0 else 0.0
    return {
        "trades": int(len(trades)),
        "win_rate": round(len(wins)/len(trades)*100, 1),
        "total_pnl_usd": round(total, 2),
        "total_pnl_pct": round(total/USD_PER_TRADE*100, 1),  # on a recycled $1000 sleeve
        "avg_pnl_usd": round(total/len(trades), 3),
        "maxdd_usd": round(maxdd_usd, 2),
        "maxdd_pct": round(maxdd_pct, 1),
        "pf": round(min(pf, 99), 2),
        "sharpe": round(sharpe, 3),
        "sortino": round(sortino, 3),
        "max_consec_losses": max_consec_losses(trades),
    }


def agg_metrics(all_trades, sharpes, sortinos):
    """Aggregate a strategy across pairs: pool trades for PF/WR/$; mean per-pair Sharpe."""
    t = np.concatenate(all_trades) if all_trades else np.array([])
    if len(t) == 0:
        return None
    wins = t[t > 0]; losses = t[t < 0]
    eq = USD_PER_TRADE + np.cumsum(t); peak = np.maximum.accumulate(eq); dd = peak - eq
    pf = float(wins.sum()/abs(losses.sum())) if len(losses) and losses.sum() != 0 else (99.0 if len(wins) else 0.0)
    return {
        "trades": int(len(t)),
        "win_rate": round(len(wins)/len(t)*100, 1),
        "total_pnl_usd": round(float(t.sum()), 2),
        "avg_pnl_usd": round(float(t.sum())/len(t), 3),
        "maxdd_usd": round(float(dd.max()), 2),
        "pf": round(min(pf, 99), 2),
        "mean_sharpe": round(float(np.mean(sharpes)), 3) if sharpes else 0.0,
        "mean_sortino": round(float(np.mean(sortinos)), 3) if sortinos else 0.0,
        "max_consec_losses": max_consec_losses(t),
        "pairs_tested": len(all_trades),
    }


def run_tf(tf, pairs, start):
    ppy = BARS_PER_YEAR[tf]
    by_pair = {s: {} for s in STRATS}
    data = {}
    for sym, _flag in pairs:
        D = fetch(sym, tf, start)
        if D is not None:
            data[sym] = D
        print(f"  [{tf}] fetched {sym}: {len(D['c']) if D else 0} bars", flush=True)
    for name in STRATS:
        for sym, D in data.items():
            pos = signal(name, D)
            bar, trades = backtest_trades(D, pos)
            m = pair_metrics(bar, trades, ppy)
            if m:
                by_pair[name][sym.replace('/USD', '')] = m
    # aggregate per strategy
    by_strat = {}
    for name in STRATS:
        tr_list, shs, sos = [], [], []
        for sym, D in data.items():
            pos = signal(name, D)
            bar, trades = backtest_trades(D, pos)
            if len(trades):
                tr_list.append(trades)
                pm = pair_metrics(bar, trades, ppy)
                if pm:
                    shs.append(pm["sharpe"]); sos.append(pm["sortino"])
        by_strat[name] = agg_metrics(tr_list, shs, sos)
    return {"pairs": [s for s, _ in pairs if s in data], "by_strategy": by_strat, "by_pair": by_pair}


def main():
    os.makedirs(OUT, exist_ok=True)
    results = {"config": {"fee_per_leg": FEE, "slip_per_side": SLIP, "rt_cost": RT_COST,
                          "usd_per_trade": USD_PER_TRADE, "universe": [s for s, _ in UNIVERSE],
                          "defi_flagged": [s for s, f in UNIVERSE if f],
                          "end": END}, "timeframes": {}}
    # 15m and 5m on full universe; 3m and 1m on subset
    plan = [("15Min", UNIVERSE, START_FULL), ("5Min", UNIVERSE, START_FULL),
            ("3Min", [(s, 0) for s in SUBSET_1M], START_1M),
            ("1Min", [(s, 0) for s in SUBSET_1M], START_1M)]
    for tf, pairs, start in plan:
        print(f"=== {tf} ({len(pairs)} pairs, start {start}) ===", flush=True)
        results["timeframes"][tf] = run_tf(tf, pairs, start)
        json.dump(results, open(f"{OUT}/RESULTS.json", "w"), indent=1, default=str)
        print(f"=== {tf} DONE ===", flush=True)
    results["strat_cat"] = STRAT_CAT
    json.dump(results, open(f"{OUT}/RESULTS.json", "w"), indent=1, default=str)
    print("ALL DONE", flush=True)


if __name__ == "__main__":
    main()

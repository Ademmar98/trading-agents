"""
Halal-Spot On-Chain / Regime-Gated Swing Strategy — backtest framework
======================================================================
Principal-quant reference implementation for the strategy specified 2026-07.

WHY THIS EXISTS
---------------
Nine prior studies on this firm found NO edge in *price-only* signals (scalp,
ICT, HTF swing) after honest friction. The one untested lever is **non-price
information**. This framework is built around that lever: on-chain exchange
netflow, perpetual funding (as a *sentiment* gate only — never traded), and
aggressor volume delta, combined with a Donchian breakout, on 1H spot bars.

DESIGN PRINCIPLES
-----------------
1. Long-only spot (Shariah): a position is either LONG spot or 100% cash. No
   shorting, leverage, or perp execution. Funding/OBI are *indicators* only.
2. Strict anti-lookahead: every signal input is `.shift(1)` before it can drive
   a decision, so the order placed FOR bar t uses only data through t-1. The 4H
   Donchian is mapped to 1H using the last *closed* 4H bar (the exact lookahead
   bug that faked a 3.5-profit-factor result in research/swing_htf_2026_07 — do
   not reintroduce it).
3. Maker-only (POST_ONLY) fills modelled honestly: a resting buy limit fills
   only if price trades down to it — breakouts that run away are MISSED, which
   is the real cost of maker execution on momentum entries.
4. Pluggable data: swap the synthetic loaders for real feeds (see DATA SOURCES).

DATA SOURCES (what's free vs paid) — READ BEFORE TRUSTING ANY NUMBER
-------------------------------------------------------------------
- Price 1H OHLCV .......... FREE: Binance public dumps (data.binance.vision),
  Bybit/OKX/Coinbase/Kraken REST, or `ccxt`.
- Perp funding rate ....... FREE: Binance/Bybit `fundingRate` history (ccxt).
- Aggressor volume delta .. FREE-ish: Binance aggTrades dumps -> per-hour
  sum(qty where buyerIsMaker=False)  minus  sum(qty where buyerIsMaker=True).
- Exchange netflow / reserve balance .. **PAID**: CryptoQuant or Glassnode.
  There is no reliable free exchange-reserve feed. Supply it via the CSV adapter
  (`load_onchain_csv`) with columns: [timestamp, netflow, reserve].
  ==> Until a REAL on-chain feed is connected, results are MEANINGLESS. The
      synthetic generator below exists ONLY to prove the plumbing runs.

Deps: pandas, numpy (matplotlib optional, for the equity-curve PNG).
"""
from __future__ import annotations
import warnings
from dataclasses import dataclass, field
from typing import Optional
import numpy as np
import pandas as pd


# ----------------------------------------------------------------------------
# 1. CONFIG — every number from the spec lives here, nowhere else.
# ----------------------------------------------------------------------------
@dataclass
class Config:
    symbol: str = "BTC/USDT"
    # --- friction (maker-only): 0.07%/side fee + 0.05%/side slip = 0.24% RT ---
    maker_fee_side: float = 0.0007
    slippage_side: float = 0.0005
    # --- regime cash-gate thresholds ---
    funding_gate_8h: float = 0.0004          # funding > 0.04%/8h -> force cash
    reserve_growth_z: float = 2.0            # reserve growth > +2 sigma -> force cash
    reserve_z_window: int = 168              # 7 days of 1H bars for reserve z-score
    # --- entry ---
    netflow_window_h: int = 24               # netflow summed over trailing 24h (<0 = accumulation)
    donchian_period: int = 20                # 20-period 4H Donchian (resistance breakout)
    # --- exits ---
    atr_period: int = 14                     # ATR on 1H
    atr_stop_mult: float = 2.5               # dynamic stop = entry - 2.5*ATR
    tp1_pct: float = 0.06                    # partial +6%
    tp2_pct: float = 0.12                    # partial +12%
    tp1_fraction: float = 0.5                # sell half at TP1, remainder runs to TP2/stop/time
    time_stop_h: int = 96                    # 4-day stagnation exit
    # --- sizing ---
    position_pct: float = 0.20               # deploy 20% of equity per trade (never 100%)
    start_equity: float = 10_000.0

    @property
    def side_cost(self) -> float:
        return self.maker_fee_side + self.slippage_side   # 0.12%/side

    @property
    def round_trip(self) -> float:
        return 2 * self.side_cost                          # 0.24%


# ----------------------------------------------------------------------------
# 2. DATA ADAPTERS — return tidy hourly frames; real loaders documented above.
# ----------------------------------------------------------------------------
def load_onchain_csv(path: str) -> pd.DataFrame:
    """Real on-chain adapter. CSV columns: timestamp(UTC), netflow, reserve.
    netflow>0 = coins moving TO exchanges (bearish); reserve = exchange balance."""
    df = pd.read_csv(path, parse_dates=["timestamp"]).set_index("timestamp").sort_index()
    return df[["netflow", "reserve"]].astype(float)


def synthetic_bundle(n_hours: int = 24 * 365 * 3, seed_offset: int = 0) -> dict:
    """SYNTHETIC data so the framework runs end-to-end. NOT REAL — the on-chain
    series in particular is noise. Replace with real feeds before believing output.
    Deterministic (no RNG seeding needed for cron-safety): built from index math."""
    idx = pd.date_range("2022-01-01", periods=n_hours, freq="h", tz="UTC")
    t = np.arange(n_hours)
    # price: gentle drift + cycles + deterministic 'noise' from trig hashing (no RNG)
    noise = (np.sin(t * 0.7 + seed_offset) + np.sin(t * 0.13) + np.sin(t * 2.3)) * 0.004
    ret = 0.00002 + 0.02 * np.sin(t / 1500.0) * 0.0 + noise
    close = 40000 * np.cumprod(1 + ret)
    high = close * (1 + np.abs(np.sin(t * 1.1)) * 0.004)
    low = close * (1 - np.abs(np.sin(t * 0.9)) * 0.004)
    openp = np.concatenate([[close[0]], close[:-1]])
    vol = 100 + 50 * np.abs(np.sin(t * 0.05))
    price = pd.DataFrame({"open": openp, "high": high, "low": low, "close": close, "volume": vol}, index=idx)
    # funding every 8h, ffilled to 1h
    fund_idx = pd.date_range(idx[0], idx[-1], freq="8h")
    funding = pd.Series(0.0001 * np.sin(np.arange(len(fund_idx)) * 0.2), index=fund_idx).reindex(idx).ffill()
    # aggressor delta (buy vol - sell vol) per hour
    delta = pd.Series(np.sin(t * 0.3) * 20, index=idx)
    # on-chain (SYNTHETIC — noise): netflow and reserve
    netflow = pd.Series(np.sin(t * 0.05) * 500, index=idx)
    reserve = pd.Series(2_000_000 + np.cumsum(np.sin(t * 0.02)) * 100, index=idx)
    onchain = pd.DataFrame({"netflow": netflow, "reserve": reserve})
    return {"price": price, "funding": funding, "delta": delta, "onchain": onchain, "_synthetic": True}


# ----------------------------------------------------------------------------
# 3. INDICATORS — pure functions; NO shifting here (shifting happens in signals).
# ----------------------------------------------------------------------------
def atr(h, l, c, n):
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def donchian_4h_on_1h(price: pd.DataFrame, period: int) -> pd.Series:
    """20-period 4H Donchian upper band, mapped onto the 1H index using ONLY the
    last CLOSED 4H bar (anti-lookahead). Uses np.roll(1) so the current forming
    4H bar never contributes."""
    h4 = price["high"].resample("4h").max()
    # prior-N-bar high, excluding the current 4H bar (shift(1)) -> resistance to break
    dch4 = h4.shift(1).rolling(period).max()
    # reindex to 1H and forward-fill, but only with values whose 4H bar has CLOSED:
    # a 4H bar timestamped T covers [T, T+4h); it is knowable at 1H times >= T+4h.
    dch4_closed = dch4.copy()
    dch4_closed.index = dch4_closed.index + pd.Timedelta("4h")   # publish at close time
    return dch4_closed.reindex(price.index, method="ffill")


# ----------------------------------------------------------------------------
# 4. SIGNALS — assemble regime gate + entry trigger. ALL inputs shifted by 1.
# ----------------------------------------------------------------------------
def build_signals(bundle: dict, cfg: Config) -> pd.DataFrame:
    price = bundle["price"]
    idx = price.index
    funding = bundle["funding"].reindex(idx).ffill()
    delta = bundle["delta"].reindex(idx).fillna(0.0)
    onc = bundle["onchain"].reindex(idx).ffill()

    df = pd.DataFrame(index=idx)
    df["close"] = price["close"]; df["high"] = price["high"]; df["low"] = price["low"]
    df["atr"] = atr(price["high"], price["low"], price["close"], cfg.atr_period)
    df["dch_upper"] = donchian_4h_on_1h(price, cfg.donchian_period)

    # raw indicator series (unshifted)
    netflow_24 = onc["netflow"].rolling(cfg.netflow_window_h).sum()
    res = onc["reserve"]
    res_growth = res.diff()
    res_z = (res_growth - res_growth.rolling(cfg.reserve_z_window).mean()) \
        / res_growth.rolling(cfg.reserve_z_window).std(ddof=0)
    breakout = price["close"] > df["dch_upper"]     # close above prior 4H resistance

    # ---- STRICT ANTI-LOOKAHEAD: shift EVERY signal input by 1 bar ----
    S = lambda s: s.shift(1)
    f_ = S(funding); nf_ = S(netflow_24); rz_ = S(res_z); d_ = S(delta); bo_ = S(breakout)
    df["dch_upper_prev"] = S(df["dch_upper"])
    df["atr_prev"] = S(df["atr"])
    df["ref_prev"] = S(df["close"])          # prior close = POST_ONLY maker limit reference (anti-lookahead)

    # REGIME CASH-GATE: no trading if over-leveraged long OR whale-deposit regime
    df["regime_cash"] = (f_ > cfg.funding_gate_8h) | (rz_ > cfg.reserve_growth_z)

    # LONG ENTRY: all must be TRUE (and regime not in cash)
    df["entry"] = (
        (~df["regime_cash"].fillna(True))       # regime compliant
        & (nf_ < 0)                             # 24h net accumulation off exchanges
        & (f_ <= 0)                             # funding neutral/negative (shorts pay longs)
        & (bo_.fillna(False))                   # Donchian breakout (confirmed on prior bar)
        & (d_ > 0)                              # positive aggressor delta
    ).fillna(False)
    return df


# ----------------------------------------------------------------------------
# 5. BACKTEST — event-driven, maker(POST_ONLY) fills, partial TPs, ATR/time stop.
# ----------------------------------------------------------------------------
def backtest(df: pd.DataFrame, cfg: Config) -> dict:
    equity = cfg.start_equity
    eq_curve = []                     # (ts, equity) marked each bar
    trades = []
    pos = None                        # dict when in a position
    idx = df.index

    for i in range(len(df)):
        ts = idx[i]; row = df.iloc[i]
        px_close = row["close"]; px_high = row["high"]; px_low = row["low"]

        # ---- manage an open position ----
        if pos is not None:
            exit_px = None; reason = None
            # 1) stop loss (ATR, fixed at entry) — check low first (conservative)
            if px_low <= pos["stop"]:
                exit_px = pos["stop"]; reason = "SL"
            # 2) partial TP1 (once)
            elif not pos["tp1_done"] and px_high >= pos["tp1"]:
                qty1 = pos["qty"] * cfg.tp1_fraction
                proceeds = qty1 * pos["tp1"] * (1 - cfg.side_cost)
                pos["realized"] += proceeds - qty1 * pos["entry"]   # pnl vs cost basis
                equity += proceeds - qty1 * pos["entry"]
                pos["qty"] -= qty1; pos["tp1_done"] = True
            # 3) TP2 (full remainder)
            if pos is not None and pos["tp1_done"] and px_high >= pos["tp2"]:
                exit_px = pos["tp2"]; reason = "TP2"
            # 4) time stop
            if exit_px is None and (ts - pos["entry_ts"]) >= pd.Timedelta(hours=cfg.time_stop_h):
                exit_px = px_close; reason = "TIME"

            if exit_px is not None:
                proceeds = pos["qty"] * exit_px * (1 - cfg.side_cost)
                pnl = pos["realized"] + (proceeds - pos["qty"] * pos["entry"])
                equity += proceeds - pos["qty"] * pos["entry"]
                trades.append({
                    "entry_ts": pos["entry_ts"], "exit_ts": ts, "entry": pos["entry_fill"],
                    "exit": exit_px, "reason": reason, "pnl": pnl,
                    "ret_pct": pnl / pos["notional0"] * 100,
                    "hold_h": (ts - pos["entry_ts"]) / pd.Timedelta(hours=1),
                })
                pos = None

        # ---- consider a new entry (flat only; long-only spot) ----
        if pos is None and bool(row["entry"]):
            # maker POST_ONLY: rest a buy limit at the prior close. It fills when a
            # seller trades down to it (normal intrabar oscillation); a gap-up bar
            # whose low never reaches the limit is a genuine MISS (maker runaway cost).
            limit = row["ref_prev"]
            if np.isfinite(limit) and px_low <= limit and np.isfinite(row["atr_prev"]):
                fill = limit * (1 + cfg.slippage_side)          # maker fill + slippage
                notional = equity * cfg.position_pct
                qty = notional / fill
                cost = qty * fill * (1 + cfg.maker_fee_side)    # pay entry fee
                equity -= cost - qty * fill                      # deduct entry fee from cash
                pos = {
                    "entry_ts": ts, "entry": fill, "entry_fill": fill, "qty": qty,
                    "notional0": notional, "stop": fill - cfg.atr_stop_mult * row["atr_prev"],
                    "tp1": fill * (1 + cfg.tp1_pct), "tp2": fill * (1 + cfg.tp2_pct),
                    "tp1_done": False, "realized": 0.0,
                }
        eq_curve.append((ts, equity + (pos["qty"] * px_close - pos["qty"] * pos["entry"] if pos else 0)))

    eq = pd.Series(dict(eq_curve)); eq.index = pd.DatetimeIndex(eq.index)
    return {"equity": eq, "trades": pd.DataFrame(trades)}


# ----------------------------------------------------------------------------
# 6. METRICS — PF, MDD + duration, Sharpe/Sortino (0% rf), WR, durations, vs hold.
# ----------------------------------------------------------------------------
def metrics(res: dict, price: pd.DataFrame, cfg: Config) -> dict:
    eq = res["equity"]; tr = res["trades"]
    rets = eq.pct_change().dropna()
    ann = np.sqrt(24 * 365)                      # hourly -> annualised
    sharpe = rets.mean() / rets.std() * ann if rets.std() > 0 else 0.0
    downside = rets[rets < 0]
    sortino = rets.mean() / downside.std() * ann if len(downside) > 1 and downside.std() > 0 else 0.0
    peak = eq.cummax(); dd = (eq - peak) / peak
    mdd = dd.min() * 100
    # max drawdown DURATION (hours underwater)
    underwater = eq < peak
    mdd_dur = 0; cur = 0
    for u in underwater:
        cur = cur + 1 if u else 0; mdd_dur = max(mdd_dur, cur)
    if len(tr):
        wins = tr[tr.pnl > 0].pnl; losses = tr[tr.pnl <= 0].pnl
        pf = wins.sum() / abs(losses.sum()) if len(losses) and losses.sum() != 0 else float("inf")
        wr = len(wins) / len(tr) * 100; avg_dur = tr.hold_h.mean()
    else:
        pf = 0.0; wr = 0.0; avg_dur = 0.0
    net_pct = (eq.iloc[-1] / cfg.start_equity - 1) * 100
    hold_pct = (price["close"].iloc[-1] / price["close"].iloc[0] - 1) * 100
    return {
        "net_pnl_pct": round(net_pct, 2), "buy_hold_pct": round(hold_pct, 2),
        "profit_factor": round(pf, 3) if np.isfinite(pf) else 99.0,
        "max_drawdown_pct": round(mdd, 2), "max_dd_duration_h": int(mdd_dur),
        "sharpe": round(sharpe, 3), "sortino": round(sortino, 3),
        "total_trades": int(len(tr)), "win_rate_pct": round(wr, 1),
        "avg_trade_duration_h": round(avg_dur, 1),
    }


def plot_equity(res: dict, price: pd.DataFrame, cfg: Config, path: str = "equity_curve.png"):
    try:
        import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
    except Exception:
        print("[plot skipped — matplotlib unavailable]"); return
    eq = res["equity"]
    hold = cfg.start_equity * price["close"] / price["close"].iloc[0]
    fig, ax = plt.subplots(figsize=(11, 5))
    ax.plot(eq.index, eq.values, label="Strategy (net of 0.24% RT)", lw=1.3)
    ax.plot(hold.index, hold.reindex(eq.index).values, label="Buy & Hold", lw=1.0, alpha=0.7)
    ax.set_title(f"{cfg.symbol} — On-Chain Regime Strategy vs Buy&Hold"); ax.legend(); ax.grid(alpha=0.3)
    fig.tight_layout(); fig.savefig(path, dpi=110); print(f"[equity curve -> {path}]")


# ----------------------------------------------------------------------------
# 7. MAIN — wire the pipeline. Swap `synthetic_bundle()` for real loaders.
# ----------------------------------------------------------------------------
def run(bundle: dict, cfg: Optional[Config] = None) -> dict:
    cfg = cfg or Config()
    if bundle.get("_synthetic"):
        warnings.warn("SYNTHETIC DATA — on-chain series is noise. Results are for "
                      "PLUMBING VALIDATION ONLY, not a real edge estimate.", stacklevel=2)
    df = build_signals(bundle, cfg)
    res = backtest(df, cfg)
    m = metrics(res, bundle["price"], cfg)
    return {"metrics": m, "result": res, "signals": df}


if __name__ == "__main__":
    cfg = Config(symbol="BTC/USDT")
    bundle = synthetic_bundle()          # <-- REPLACE with real price/funding/delta/onchain
    out = run(bundle, cfg)
    print("\n=== METRICS (SYNTHETIC — not a real edge) ===")
    for k, v in out["metrics"].items():
        print(f"  {k:24s}: {v}")
    plot_equity(out["result"], bundle["price"], cfg)
    # sanity: how often was the cash-gate active vs entries fired
    s = out["signals"]
    print(f"\n  regime_cash active: {s['regime_cash'].mean()*100:.1f}% of bars | "
          f"entry signals: {int(s['entry'].sum())} | trades taken: {out['metrics']['total_trades']}")

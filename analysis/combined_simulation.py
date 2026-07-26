#!/usr/bin/env python3
"""
Combined Paper-Trading Simulation v3
======================================
Runs all 3 modules simultaneously against historical data.
Fixes: Module 1 funding threshold, position sizing, Module 3 thresholds.
"""

import pandas as pd
import numpy as np
from dataclasses import dataclass
from typing import List, Dict
import json
import os
import sys

sys.stdout.reconfigure(encoding='utf-8')


@dataclass
class Position:
    symbol: str
    module: str
    entry_price: float
    size_usd: float
    entry_time: pd.Timestamp
    stop_loss: float
    take_profit: float
    status: str = "open"
    exit_price: float = 0
    exit_time = None
    pnl: float = 0
    pnl_pct: float = 0
    hold_bars: int = 0


class CombinedSimulator:
    def __init__(self, initial_capital=10_000, sl_atr=None, tp_atr=None,
                 fee_rate=0.0, quiet=False):
        # sl_atr/tp_atr override the per-module ATR multipliers when set (used
        # by the SL/TP grid search). None keeps the original per-module values.
        self.sl_atr = sl_atr
        self.tp_atr = tp_atr
        self.fee_rate = fee_rate  # round-trip, charged half on entry/half on exit
        self.quiet = quiet
        self.fees_paid = 0.0

        self.initial_capital = initial_capital
        self.cash = initial_capital
        self.positions: List[Position] = []
        self.equity_curve = []
        self.timestamps = []
        self.peak_equity = initial_capital
        self.trade_log = []
        self.cb_log = []
        self.total_trades = 0
        self.wins = 0
        self.losses = 0

        # Risk
        self.monthly_max_dd = 0.15
        self.max_exposure = 0.80
        self.max_positions = 8
        self.per_module_max = 3
        self.kelly_fraction = 1.0  # Full Kelly (was 0.25)

        # Weights (optimized: M2+M3 with full Kelly)
        self.m1_weight = 0.00
        self.m2_weight = 0.50
        self.m3_weight = 0.50

        # Per-module position size as % of equity (fractional Kelly applied)
        self.m1_position_pct = 0.00  # DISABLED
        self.m2_position_pct = 0.35  # 35% of equity per M2 trade
        self.m3_position_pct = 0.30  # 30% of equity per M3 trade

        self.current_month = None

    def load_data(self):
        base = "analysis/backtest_data"
        symbols = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "XRPUSDT", "ADAUSDT",
                   "AVAXUSDT", "LINKUSDT", "SUIUSDT", "NEARUSDT", "AAVEUSDT",
                   "INJUSDT", "FETUSDT"]
        data = {}
        for sym in symbols:
            spot_path = f"{base}/{sym}_spot_4h.parquet"
            perp_path = f"{base}/{sym}_perp_4h.parquet"
            fund_path = f"{base}/{sym}_funding.parquet"
            if os.path.exists(spot_path):
                data[sym] = {
                    "spot": pd.read_parquet(spot_path),
                    "perp": pd.read_parquet(perp_path) if os.path.exists(perp_path) else None,
                    "funding": pd.read_parquet(fund_path) if os.path.exists(fund_path) else None,
                }
        return data

    def prepare(self, sym_data):
        spot = sym_data["spot"].copy()
        perp = sym_data["perp"]
        fund = sym_data["funding"]

        if "timestamp" in spot.columns:
            spot["timestamp"] = pd.to_datetime(spot["timestamp"])
            spot = spot.set_index("timestamp").sort_index()
        df = spot[["open", "high", "low", "close", "volume"]].copy()

        # Indicators
        df["sma50"] = df["close"].rolling(50).mean()
        df["sma20"] = df["close"].rolling(20).mean()
        df["sma10"] = df["close"].rolling(10).mean()
        df["sma200"] = df["close"].rolling(200).mean()
        df["std20"] = df["close"].rolling(20).std()
        df["atr"] = (df["high"] - df["low"]).rolling(14).mean()
        df["vol_sma"] = df["volume"].rolling(20).mean()
        df["rel_vol"] = df["volume"] / df["vol_sma"]
        df["roll_max_31"] = df["close"].rolling(31, min_periods=1).max()
        df["price_pct"] = df["close"].pct_change()
        df["price_pct_6h"] = df["close"].pct_change(6)
        df["price_pct_24h"] = df["close"].pct_change(6)  # ~24h at 4h bars

        # Bollinger Band width
        df["bb_upper"] = df["sma20"] + 2 * df["std20"]
        df["bb_lower"] = df["sma20"] - 2 * df["std20"]
        df["bb_pct"] = (df["close"] - df["bb_lower"]) / (df["bb_upper"] - df["bb_lower"]).clip(lower=0.01)

        # RSI (14-period)
        delta = df["close"].diff()
        gain = delta.where(delta > 0, 0).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / loss.clip(lower=0.0001)
        df["rsi"] = 100 - (100 / (1 + rs))

        # CVD
        df["tick_dir"] = np.where(df["close"] > df["close"].shift(1), 1,
                         np.where(df["close"] < df["close"].shift(1), -1, 0))
        df["cvd"] = (df["tick_dir"] * df["volume"]).cumsum()
        df["cvd_slope"] = df["cvd"].diff(6)
        df["cvd_ma20"] = df["cvd"].rolling(20).mean()
        df["cvd_dev"] = (df["cvd"] - df["cvd_ma20"]) / df["cvd"].rolling(20).std().clip(lower=1)

        # Perp data
        if perp is not None and len(perp) > 0:
            p = perp.copy()
            if "timestamp" in p.columns:
                p["timestamp"] = pd.to_datetime(p["timestamp"])
                p = p.set_index("timestamp").sort_index()
            if "close" in p.columns:
                df["perp_close"] = p["close"]
                df["spread"] = (df["close"] - df["perp_close"]) / df["close"] * 100
                if "volume" in p.columns:
                    df["perp_vol"] = p["volume"]
                    df["perp_vol_sma"] = df["perp_vol"].rolling(20).mean()
                    df["oi_ratio"] = df["perp_vol"] / df["perp_vol_sma"].clip(lower=1)

        # Funding - forward fill to align with 4h bars
        if fund is not None and len(fund) > 0:
            f = fund.copy()
            if "timestamp" in f.columns:
                f["timestamp"] = pd.to_datetime(f["timestamp"])
                f = f.set_index("timestamp").sort_index()
            if "fundingRate" in f.columns:
                # Forward-fill funding rate to every 4h bar
                df["funding"] = f["fundingRate"].reindex(df.index, method="ffill")
                # Compute z-score on the raw funding series
                fr = df["funding"].dropna()
                if len(fr) > 20:
                    roll_mean = df["funding"].rolling(20, min_periods=10).mean()
                    roll_std = df["funding"].rolling(20, min_periods=10).std().clip(lower=0.00001)
                    df["fund_zscore"] = (df["funding"] - roll_mean) / roll_std
                else:
                    df["fund_zscore"] = np.nan

        return df.dropna(subset=["sma50"])

    def _equity(self):
        pos_val = 0
        for p in self.positions:
            if p.status == "open":
                pos_val += p.size_usd
        return self.cash + pos_val

    def _total_position_value(self):
        return sum(p.size_usd for p in self.positions if p.status == "open")

    def check_circuit_breakers(self):
        eq = self._equity()
        if eq <= 0:
            return True
        dd = (self.peak_equity - eq) / self.peak_equity if self.peak_equity > 0 else 0
        if dd > self.monthly_max_dd:
            return True
        if self._total_position_value() > eq * self.max_exposure:
            return True
        open_count = len([p for p in self.positions if p.status == "open"])
        if open_count >= self.max_positions:
            return True
        return False

    def _sl_tp(self, entry, atr, sl_default, tp_default):
        """Resolve SL/TP prices, honouring grid-search overrides when set."""
        sl_m = self.sl_atr if self.sl_atr is not None else sl_default
        tp_m = self.tp_atr if self.tp_atr is not None else tp_default
        return entry - sl_m * atr, entry + tp_m * atr

    def _open(self, module, symbol, entry, sl, tp, ts):
        open_count = len([p for p in self.positions if p.status == "open"])
        if open_count >= self.max_positions:
            return

        mod_count = len([p for p in self.positions if p.status == "open" and p.module == module])
        if mod_count >= self.per_module_max:
            return

        existing = [p for p in self.positions if p.status == "open" and p.symbol == symbol]
        if existing:
            return

        # Position sizing
        eq = self._equity()
        if module == "module1":
            size = eq * self.m1_position_pct
        elif module == "module2":
            size = eq * self.m2_position_pct
        elif module == "module3":
            size = eq * self.m3_position_pct
        else:
            return

        # Apply Kelly fraction
        size *= self.kelly_fraction

        if size <= 0 or size > self.cash * 0.95:
            return

        pos = Position(
            symbol=symbol, module=module, entry_price=entry,
            size_usd=size, entry_time=ts, stop_loss=sl, take_profit=tp,
        )
        self.positions.append(pos)
        # Half the round-trip cost on entry.
        entry_fee = size * self.fee_rate / 2
        self.fees_paid += entry_fee
        self.cash -= size + entry_fee

    def _close(self, pos, exit_price, ts, reason):
        pos.status = "closed"
        pos.exit_price = exit_price
        pos.exit_time = ts

        pct = (exit_price - pos.entry_price) / pos.entry_price
        gross = pos.size_usd * pct
        exit_fee = (pos.size_usd + gross) * self.fee_rate / 2
        self.fees_paid += exit_fee

        pos.pnl = gross - exit_fee - (pos.size_usd * self.fee_rate / 2)
        pos.pnl_pct = pos.pnl / pos.size_usd * 100

        self.cash += pos.size_usd + gross - exit_fee
        self.total_trades += 1
        if pos.pnl > 0:
            self.wins += 1
        else:
            self.losses += 1

        self.trade_log.append({
            "time": str(ts), "module": pos.module, "symbol": pos.symbol,
            "entry": pos.entry_price, "exit": exit_price,
            "pnl": round(pos.pnl, 2), "pnl_pct": round(pos.pnl_pct, 2),
            "reason": reason, "hold_bars": pos.hold_bars,
        })

    def run(self, prepared=None, times=None):
        """
        prepared: pre-built {symbol: DataFrame} to reuse across grid-search runs.
        times:    explicit bar timestamps to iterate (an IS or OOS slice).
        """
        p = print if not self.quiet else (lambda *a, **k: None)

        p("=" * 70)
        p("COMBINED PAPER-TRADING SIMULATION v3")
        p("=" * 70)

        if prepared is None:
            data = self.load_data()
            p(f"Loaded {len(data)} symbols")

            prepared = {}
            for sym, d in data.items():
                try:
                    prepared[sym] = self.prepare(d)
                except Exception as e:
                    p(f"  Skip {sym}: {e}")

        p(f"Prepared {len(prepared)} symbols")

        # Check M1 signal availability
        m1_signals = 0
        for sym, df in prepared.items():
            if "fund_zscore" in df.columns:
                zs = df["fund_zscore"].dropna()
                below2 = (zs < -2.0).sum()
                below1 = (zs < -1.0).sum()
                m1_signals += below2
                if below2 > 0:
                    p(f"  {sym}: {below2} bars with zscore < -2.0")
        p(f"Total M1 signal opportunities (zscore < -2.0): {m1_signals}")

        # Check M3 signal availability
        m3_signals = 0
        for sym, df in prepared.items():
            if "cvd_dev" in df.columns and "rel_vol" in df.columns:
                cvd_ok = (df["cvd_dev"] < -0.5).sum()
                rv_ok = (df["rel_vol"] > 1.3).sum()
                dip_ok = (df["price_pct_6h"] < -0.01).sum()
                combined = ((df["cvd_dev"] < -0.5) & (df["rel_vol"] > 1.3) & (df["price_pct_6h"] < -0.01)).sum()
                m3_signals += combined
        p(f"Total M3 signal opportunities: {m3_signals}")

        all_times = sorted(set().union(*[set(d.index) for d in prepared.values()]))
        if times is not None:
            wanted = set(times)
            all_times = [t for t in all_times if t in wanted]
        p(f"Time range: {all_times[0]} to {all_times[-1]}")
        p(f"Total bars: {len(all_times)}")
        p(f"\nConfig: M1=DISABLED, M2=60%, M3=40%, Kelly={self.kelly_fraction}x")
        p(f"Position sizing: M2={self.m2_position_pct*100}%, M3={self.m3_position_pct*100}% of equity")
        p(f"Stops: M2=2.0x ATR, M3=2.5x ATR")
        p(f"Time exits: DISABLED")
        p(f"M2 trend filter: BTC > 200-SMA only")
        p()

        for ts in all_times:
            month_key = (ts.year, ts.month)
            if month_key != self.current_month:
                self.current_month = month_key

            eq = self._equity()
            if eq > self.peak_equity:
                self.peak_equity = eq

            self.equity_curve.append(eq)
            self.timestamps.append(ts)

            halted = self.check_circuit_breakers()

            # Manage existing positions
            for pos in self.positions:
                if pos.status != "open":
                    continue
                pos.hold_bars += 1

                sym_df = prepared.get(pos.symbol)
                if sym_df is None or ts not in sym_df.index:
                    continue

                bar = sym_df.loc[ts]
                high = bar["high"]
                low = bar["low"]
                close = bar["close"]
                atr_val = bar.get("atr", np.nan)

                if low <= pos.stop_loss:
                    self._close(pos, pos.stop_loss, ts, "STOP_LOSS")
                    continue

                if high >= pos.take_profit:
                    self._close(pos, pos.take_profit, ts, "TAKE_PROFIT")
                    continue

                # Trailing stop: if 2x ATR in profit, trail at 1.5x ATR below
                if not np.isnan(atr_val) and atr_val > 0:
                    if close > pos.entry_price + 2 * atr_val:
                        new_sl = close - 1.5 * atr_val
                        if new_sl > pos.stop_loss:
                            pos.stop_loss = new_sl

            if halted:
                continue

            # Generate signals
            for sym, df in prepared.items():
                if ts not in df.index:
                    continue
                bar = df.loc[ts]
                idx = df.index.get_loc(ts)
                if idx < 50:
                    continue

                atr_val = bar.get("atr", np.nan)
                if np.isnan(atr_val) or atr_val <= 0:
                    atr_val = bar["close"] * 0.02

                # ======== MODULE 1: Z-Score Funding Squeeze ========
                # Conditions: funding_zscore < -2.0, price above SMA50, OI expansion
                # Also uses a relaxed threshold if zscore < -1.5 with high OI
                fs = bar.get("fund_zscore", np.nan)
                above_sma = bar["close"] > bar.get("sma50", 0)
                oi_ratio = bar.get("oi_ratio", 0)
                oi_exp = oi_ratio > 1.3 if not np.isnan(oi_ratio) else False

                zscore_threshold = -2.0
                # Relaxed: zscore < -1.0 if OI expanding strongly
                if not np.isnan(fs) and oi_ratio > 1.5:
                    zscore_threshold = -1.0

                if (not np.isnan(fs) and fs < zscore_threshold and above_sma and oi_exp):
                    entry = bar["close"]
                    sl, tp = self._sl_tp(entry, atr_val, 2.0, 3.0)
                    self._open("module1", sym, entry, sl, tp, ts)

                # ======== MODULE 3: Microstructure Absorption ========
                cvd_dev = bar.get("cvd_dev", np.nan)
                rv = bar.get("rel_vol", np.nan)
                ppct6 = bar.get("price_pct_6h", np.nan)
                spread = bar.get("spread", np.nan)

                # Config: Standard (relaxed thresholds for more signals)
                # Buy when CVD divergence (smart money accumulating), volume spike, price dipped
                if (not np.isnan(cvd_dev) and not np.isnan(rv) and not np.isnan(ppct6)):
                    # Standard config
                    if (cvd_dev < -0.3 and rv > 1.2 and ppct6 < -0.005):
                        entry = bar["close"]
                        sl, tp = self._sl_tp(entry, atr_val, 1.5, 3.0)
                        self._open("module3", sym, entry, sl, tp, ts)

                # ======== MODULE 2: Basket Rebalancing (Trend-Filtered) ========
                # Only rebalance when BTC > 200-SMA (uptrend filter)
                btc_df = prepared.get("BTCUSDT")
                if btc_df is not None and ts in btc_df.index:
                    btc_bar = btc_df.loc[ts]
                    btc_above_200sma = btc_bar["close"] > btc_bar.get("sma200", 0)

                    if btc_above_200sma and not np.isnan(btc_bar.get("sma200", np.nan)):
                        # Inverse-volatility weight for this symbol
                        # Buy if price dropped >5% below recent high (mean reversion)
                        # Precomputed in prepare(); identical to
                        # close.iloc[max(0, idx-30):idx+1].max() but hoisted out
                        # of the per-bar loop (it dominated grid-search runtime).
                        recent_high = bar["roll_max_31"]
                        drop_pct = (bar["close"] - recent_high) / recent_high

                        # Only buy if dropped > 5% (threshold rebalancing)
                        if drop_pct < -0.05:
                            entry = bar["close"]
                            sl, tp = self._sl_tp(entry, atr_val, 1.5, 2.5)
                            self._open("module2", sym, entry, sl, tp, ts)

        # Force-close anything still open at the last available price. Without
        # this, _equity() carries open positions at their ENTRY cost, so their
        # unrealised PnL is silently excluded from the final return -- and a
        # grid search would reward configs that simply leave losers open.
        if all_times:
            last_ts = all_times[-1]
            for pos in self.positions:
                if pos.status != "open":
                    continue
                sym_df = prepared.get(pos.symbol)
                if sym_df is None or len(sym_df) == 0:
                    continue
                upto = sym_df.loc[:last_ts]
                if len(upto) == 0:
                    continue
                self._close(pos, upto["close"].iloc[-1], last_ts, "END_OF_DATA")
            self.equity_curve.append(self._equity())
            self.timestamps.append(last_ts)

        # ======== RESULTS ========
        final_eq = self.equity_curve[-1] if self.equity_curve else self.initial_capital

        p()
        p("=" * 70)
        p("RESULTS")
        p("=" * 70)

        total_ret = (final_eq - self.initial_capital) / self.initial_capital * 100

        monthly_eq = {}
        for i, ts in enumerate(self.timestamps):
            key = ts.strftime("%Y-%m")
            monthly_eq[key] = self.equity_curve[i]

        monthly_rets = []
        prev = self.initial_capital
        for key, eq in sorted(monthly_eq.items()):
            ret = (eq - prev) / prev * 100
            monthly_rets.append((key, ret, eq))
            prev = eq

        avg_monthly = np.mean([r[1] for r in monthly_rets]) if monthly_rets else 0
        max_dd = max((self.peak_equity - e) / self.peak_equity for e in self.equity_curve) * 100

        p(f"\n  Final Equity:     ${final_eq:,.2f}")
        p(f"  Total Return:     {total_ret:+.2f}%")
        p(f"  Avg Monthly:      {avg_monthly:+.2f}%")
        p(f"  Max Drawdown:     {max_dd:.1f}%")

        p(f"\n  Monthly Returns:")
        for key, ret, eq in monthly_rets:
            p(f"    {key}: {ret:+.2f}%  (${eq:,.2f})")

        wr = self.wins / max(self.total_trades, 1) * 100
        pf = 0.0
        p(f"\n  Trades:           {self.total_trades}")
        p(f"  Win Rate:         {wr:.1f}%")

        if self.total_trades > 0:
            wins_pnl = [t["pnl"] for t in self.trade_log if t["pnl"] > 0]
            loss_pnl = [abs(t["pnl"]) for t in self.trade_log if t["pnl"] < 0]
            avg_win = np.mean(wins_pnl) if wins_pnl else 0
            avg_loss = np.mean(loss_pnl) if loss_pnl else 0
            gross_win = sum(wins_pnl)
            gross_loss = sum(loss_pnl)
            pf = gross_win / gross_loss if gross_loss > 0 else 999

            p(f"  Avg Win:          ${avg_win:,.2f}")
            p(f"  Avg Loss:         ${avg_loss:,.2f}")
            p(f"  Profit Factor:    {pf:.2f}")
            p(f"  Expectancy:       ${(gross_win - gross_loss) / self.total_trades:+,.2f} per trade")

        p(f"\n  By Module:")
        for mod in ["module1", "module2", "module3"]:
            trades = [t for t in self.trade_log if t["module"] == mod]
            if trades:
                mw = len([t for t in trades if t["pnl"] > 0])
                mp = sum(t["pnl"] for t in trades)
                mwr = mw / len(trades) * 100
                p(f"    {mod.upper()}: {len(trades)} trades, {mwr:.0f}% WR, ${mp:+.2f} PnL")
            else:
                label = "DISABLED" if mod == "module2" else "NO SIGNALS"
                p(f"    {mod.upper()}: {label}")

        if self.trade_log:
            p(f"\n  Top 5 Trades:")
            st = sorted(self.trade_log, key=lambda t: t["pnl"], reverse=True)
            for t in st[:5]:
                p(f"    {t['symbol']:12s} ({t['module']:8s}): {t['pnl_pct']:+6.2f}%  ${t['pnl']:+8.2f}  [{t['reason']}]")

            p(f"\n  Bottom 5 Trades:")
            for t in st[-5:]:
                p(f"    {t['symbol']:12s} ({t['module']:8s}): {t['pnl_pct']:+6.2f}%  ${t['pnl']:+8.2f}  [{t['reason']}]")

        # Exit reason breakdown
        p(f"\n  Exit Reasons:")
        reasons = {}
        for t in self.trade_log:
            r = t["reason"]
            if r not in reasons:
                reasons[r] = {"count": 0, "total_pnl": 0}
            reasons[r]["count"] += 1
            reasons[r]["total_pnl"] += t["pnl"]
        for r, v in sorted(reasons.items(), key=lambda x: x[1]["count"], reverse=True):
            p(f"    {r:15s}: {v['count']:3d} trades, ${v['total_pnl']:+8.2f}")

        # Save
        results = {
            "initial_capital": self.initial_capital,
            "final_equity": final_eq,
            "total_return_pct": round(total_ret, 2),
            "avg_monthly_return_pct": round(avg_monthly, 2),
            "max_drawdown_pct": round(max_dd, 1),
            "total_trades": self.total_trades,
            "wins": self.wins,
            "losses": self.losses,
            "win_rate": round(wr, 1),
            "monthly_returns": [{"month": m, "return_pct": round(r, 2), "equity": round(e, 2)} for m, r, e in monthly_rets],
            "trade_log": self.trade_log,
            "exit_reasons": reasons,
            "profit_factor": round(pf, 3) if self.total_trades > 0 else 0.0,
            "net_return": round(total_ret, 3),
            "fees_paid": round(self.fees_paid, 2),
        }

        if not self.quiet:
            with open("analysis/combined_simulation_results.json", "w") as f:
                json.dump(results, f, indent=2, default=str)
            p(f"\n  Saved: analysis/combined_simulation_results.json")

        return results


if __name__ == "__main__":
    sim = CombinedSimulator(initial_capital=10_000, fee_rate=0.001, sl_atr=1.5, tp_atr=3.0)
    sim.run()

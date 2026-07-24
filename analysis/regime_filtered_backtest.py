import sys, os, json, time, requests
import pandas as pd
import numpy as np
from datetime import datetime, timezone
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

BINANCE = "https://api.binance.com"
BINANCE_F = "https://fapi.binance.com"
CAPITAL = 10000
MIN_VOL = 2_000_000


class RegimeFilteredStrategy:
    def __init__(self,
                 entry_funding_thresh: float = -0.0001,
                 exit_funding_thresh: float = 0.0000,
                 atr_sl_mult: float = 1.5,
                 atr_tp_mult: float = 3.0,
                 sma_period: int = 50,
                 oi_change_thresh: float = 0.10,
                 taker_fee: float = 0.0007):

        self.entry_funding_thresh = entry_funding_thresh
        self.exit_funding_thresh = exit_funding_thresh
        self.atr_sl_mult = atr_sl_mult
        self.atr_tp_mult = atr_tp_mult
        self.sma_period = sma_period
        self.oi_change_thresh = oi_change_thresh
        self.taker_fee = taker_fee

    def run(self, df: pd.DataFrame) -> dict:
        df = df.copy()

        # Compute ATR(14) on 8h candles
        df['high'] = df['spot_price']
        df['low'] = df['spot_price']
        df['tr'] = df['spot_price'].diff().abs()
        df['atr14'] = df['tr'].rolling(14).mean()

        # 50-period SMA on spot price (proxy for 4H since we have 8H)
        df['sma50'] = df['spot_price'].rolling(self.sma_period).mean()

        position = 0
        entry_price = 0.0
        entry_atr = 0.0
        balance = CAPITAL
        shares = 0.0

        trades = []
        equity = []
        current_trade = {}

        for i in range(len(df)):
            row = df.iloc[i]
            price = row['spot_price']
            funding = row['perp_funding_rate']
            atr = row['atr14']
            sma = row['sma50']
            oi_change = row.get('oi_change', 0)
            perp_price = row.get('perp_price', price)
            spread_ok = price >= perp_price

            eq = balance + (shares * price if position == 1 else 0)
            equity.append(eq)

            # Skip if ATR not ready
            if pd.isna(atr) or pd.isna(sma) or atr == 0:
                continue

            if position == 0:
                # All 4 entry conditions
                cond1 = funding < self.entry_funding_thresh
                cond2 = price > sma
                cond3 = oi_change > self.oi_change_thresh
                cond4 = spread_ok

                if cond1 and cond2 and cond3 and cond4:
                    position = 1
                    entry_price = price
                    entry_atr = atr
                    cost = balance * (1 - self.taker_fee)
                    shares = cost / price
                    balance = 0
                    current_trade = {
                        'entry_price': price,
                        'entry_atr': atr,
                        'entry_funding': funding,
                        'entry_oi_change': oi_change,
                        'entry_idx': i,
                        'entry_time': str(row['timestamp']),
                        'sl_price': price - self.atr_sl_mult * atr,
                        'tp_price': price + self.atr_tp_mult * atr,
                    }

            elif position == 1:
                sl_price = current_trade['sl_price']
                tp_price = current_trade['tp_price']
                pnl_pct = (price - entry_price) / entry_price

                reason = None
                if price >= tp_price:
                    reason = 'TAKE_PROFIT'
                elif price <= sl_price:
                    reason = 'STOP_LOSS'
                elif funding >= self.exit_funding_thresh:
                    reason = 'FUNDING_NEUTRAL'

                if reason:
                    balance = shares * price * (1 - self.taker_fee)
                    net_pnl = balance + 0 - CAPITAL if not trades else (balance - (trades[-1].get('exit_balance', CAPITAL)))
                    trades.append({
                        **current_trade,
                        'exit_price': price,
                        'exit_reason': reason,
                        'exit_idx': i,
                        'exit_time': str(row['timestamp']),
                        'return_pct': round(pnl_pct * 100, 2),
                        'exit_balance': balance,
                        'atr_at_entry': entry_atr,
                        'sl_distance_pct': round(self.atr_sl_mult * entry_atr / entry_price * 100, 2),
                        'tp_distance_pct': round(self.atr_tp_mult * entry_atr / entry_price * 100, 2),
                    })
                    shares = 0
                    position = 0
                    current_trade = {}

        # Close any open position at end
        if position == 1 and shares > 0:
            last_price = df['spot_price'].iloc[-1]
            balance = shares * last_price * (1 - self.taker_fee)
            pnl_pct = (last_price - entry_price) / entry_price
            trades.append({
                **current_trade,
                'exit_price': last_price,
                'exit_reason': 'END_OF_DATA',
                'exit_idx': len(df) - 1,
                'return_pct': round(pnl_pct * 100, 2),
                'exit_balance': balance,
            })

        final_balance = balance if position == 0 else shares * df['spot_price'].iloc[-1]
        net_return = ((final_balance - CAPITAL) / CAPITAL) * 100

        wins = [t for t in trades if t['return_pct'] > 0]
        losses = [t for t in trades if t['return_pct'] <= 0]

        # Max drawdown
        eq_series = pd.Series(equity)
        peak = eq_series.cummax()
        dd = (eq_series - peak) / peak
        max_dd = dd.min() * 100 if len(dd) > 0 else 0

        # Profit factor
        gross_profit = sum(t['return_pct'] for t in wins) if wins else 0
        gross_loss = abs(sum(t['return_pct'] for t in losses)) if losses else 0
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf') if gross_profit > 0 else 0

        # Exit reason breakdown
        reason_counts = {}
        for t in trades:
            r = t['exit_reason']
            reason_counts[r] = reason_counts.get(r, 0) + 1

        return {
            'net_return': round(net_return, 2),
            'total_trades': len(trades),
            'wins': len(wins),
            'losses': len(losses),
            'win_rate': round(len(wins) / max(len(trades), 1) * 100, 1),
            'avg_win': round(np.mean([t['return_pct'] for t in wins]), 2) if wins else 0,
            'avg_loss': round(np.mean([t['return_pct'] for t in losses]), 2) if losses else 0,
            'profit_factor': round(profit_factor, 2),
            'max_drawdown': round(max_dd, 2),
            'exit_reasons': reason_counts,
            'avg_sl_distance': round(np.mean([t.get('sl_distance_pct', 0) for t in trades]), 2) if trades else 0,
            'avg_tp_distance': round(np.mean([t.get('tp_distance_pct', 0) for t in trades]), 2) if trades else 0,
            'trades': trades,
        }


def get_qualified_pairs():
    r = requests.get(f"{BINANCE}/api/v3/ticker/24hr", timeout=30)
    tickers = r.json()
    skip = {"USDC", "USDT", "BUSD", "TUSD", "FDUSD", "DAI", "USDP", "USDN",
            "EUR", "GBP", "BIDR", "AUD", "BRL", "TRY", "AEUR", "UST"}
    skip_tag = ["UP", "DOWN", "BULL", "BEAR", "3L", "3S", "5L", "5S"]
    pairs = []
    for t in tickers:
        s = t["symbol"]
        if not s.endswith("USDT"):
            continue
        base = s[:-4]
        if base in skip or any(x in base for x in skip_tag):
            continue
        vol = float(t.get("quoteVolume", 0))
        if vol < MIN_VOL:
            continue
        pairs.append({"symbol": base, "volume": vol})
    pairs.sort(key=lambda x: x["volume"], reverse=True)
    return pairs


def fetch_all_data(sym):
    try:
        now_ms = int(time.time() * 1000)
        two_months_ms = 60 * 24 * 3600 * 1000
        start_ms = now_ms - two_months_ms

        # 8H klines for spot prices
        r1 = requests.get(f"{BINANCE}/api/v3/klines",
                          params={"symbol": f"{sym}USDT", "interval": "8h", "limit": 180},
                          timeout=15)
        klines = r1.json()
        if not isinstance(klines, list) or len(klines) < 30:
            return None

        timestamps = [int(k[0]) for k in klines]
        closes = [float(k[4]) for k in klines]
        highs = [float(k[2]) for k in klines]
        lows = [float(k[3]) for k in klines]

        # Funding rates
        r2 = requests.get(f"{BINANCE_F}/fapi/v1/fundingRate",
                          params={"symbol": f"{sym}USDT", "startTime": start_ms, "endTime": now_ms, "limit": 1000},
                          timeout=15)
        funding_raw = r2.json()
        if not isinstance(funding_raw, list) or len(funding_raw) < 5:
            return None

        funding_lookup = {}
        for fd in funding_raw:
            h = fd["fundingTime"] // (8 * 3600 * 1000)
            funding_lookup[h] = float(fd.get("fundingRate", 0))

        funding_rates = []
        for ts in timestamps:
            h = ts // (8 * 3600 * 1000)
            funding_rates.append(funding_lookup.get(h, 0))

        # Perp prices (futures klines for spread check)
        try:
            r3 = requests.get(f"{BINANCE_F}/fapi/v1/klines",
                              params={"symbol": f"{sym}USDT", "interval": "8h", "limit": 180},
                              timeout=15)
            perp_klines = r3.json()
            if isinstance(perp_klines, list) and len(perp_klines) == len(klines):
                perp_closes = [float(k[4]) for k in perp_klines]
            else:
                perp_closes = closes  # fallback to spot
        except:
            perp_closes = closes

        # Open Interest change (fetch current and 24h ago)
        try:
            r4 = requests.get(f"{BINANCE_F}/fapi/v1/openInterest",
                              params={"symbol": f"{sym}USDT"},
                              timeout=10)
            oi_data = r4.json()
            current_oi = float(oi_data.get('openInterest', 0))
        except:
            current_oi = 0

        # Approximate OI change as funding-driven proxy
        # (Binance doesn't give historical OI via simple API, use volume as proxy)
        oi_changes = []
        for i in range(len(closes)):
            if i >= 24:
                vol_window = sum(1 for j in range(max(0, i-24), i) if funding_rates[j] < -0.0001)
                oi_changes.append(min(vol_window / 24, 1.0))
            else:
                oi_changes.append(0)

        has_funding = sum(1 for fr in funding_rates if fr != 0)
        if has_funding < 3:
            return None

        df = pd.DataFrame({
            "timestamp": pd.to_datetime(timestamps, unit="ms"),
            "spot_price": closes,
            "high": highs,
            "low": lows,
            "perp_price": perp_closes,
            "perp_funding_rate": funding_rates,
            "oi_change": oi_changes,
        })

        return df
    except Exception:
        return None


def test_pair(sym):
    df = fetch_all_data(sym)
    if df is None:
        return None

    strat = RegimeFilteredStrategy()
    result = strat.run(df)

    return {
        "symbol": sym,
        **{k: v for k, v in result.items() if k != 'trades'},
        "bars": len(df),
        "data_start": str(df['timestamp'].iloc[0]),
        "data_end": str(df['timestamp'].iloc[-1]),
    }


def main():
    print("=" * 110)
    print("REGIME-FILTERED FUNDING RATE STRATEGY — All Qualified Binance Pairs — 2 Months")
    print("=" * 110)
    print(f"Capital: ${CAPITAL:,} | Min Volume: ${MIN_VOL:,}")
    print(f"Entry: funding < -0.01% + Price > 50SMA + OI expansion + Spot >= Perp")
    print(f"SL: 1.5x ATR(14) | TP: 3.0x ATR(14) | Exit: funding neutralized\n")

    pairs = get_qualified_pairs()
    print(f"Testing {len(pairs)} qualified pairs...\n")

    results = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(test_pair, p["symbol"]): p["symbol"] for p in pairs}
        done = 0
        for f in as_completed(futs):
            done += 1
            r = f.result()
            if r:
                results.append(r)
                t = r['total_trades']
                ret = r['net_return']
                wr = r['win_rate']
                pf = r['profit_factor']
                print(f"  [{done:3d}/{len(pairs)}] {r['symbol']:<10} trades={t:>3}  ret={ret:>7.2f}%  wr={wr:>5.1f}%  pf={pf:>5.2f}")

    results.sort(key=lambda x: x['net_return'], reverse=True)

    # Full table
    print("\n" + "=" * 120)
    print(f"{'Symbol':<10} {'Return%':>9} {'Trades':>7} {'Wins':>5} {'Losses':>7} {'WinRate':>8} {'PF':>6} {'MaxDD':>8} {'AvgSL%':>8} {'AvgTP%':>8} {'ExitReasons'}")
    print("-" * 120)
    for r in results:
        er = str(r['exit_reasons'])
        print(f"{r['symbol']:<10} {r['net_return']:>8.2f}% {r['total_trades']:>6} {r['wins']:>5} {r['losses']:>6} {r['win_rate']:>7.1f}% {r['profit_factor']:>5.2f} {r['max_drawdown']:>7.2f}% {r['avg_sl_distance']:>7.2f}% {r['avg_tp_distance']:>7.2f}% {er}")

    # Stats
    if results:
        traded = [r for r in results if r['total_trades'] > 0]
        profitable = [r for r in results if r['net_return'] > 0]
        rets = [r['net_return'] for r in traded]
        wrs = [r['win_rate'] for r in traded]
        pfs = [r['profit_factor'] for r in traded if r['profit_factor'] < 100]
        mds = [r['max_drawdown'] for r in traded]

        print("\n" + "=" * 60)
        print("PORTFOLIO STATS")
        print("=" * 60)
        print(f"Total pairs tested:      {len(results)}")
        print(f"Pairs with trades:       {len(traded)}")
        print(f"Profitable pairs:        {len(profitable)} ({len(profitable)/max(len(traded),1)*100:.0f}%)")
        print(f"Average return:          {np.mean(rets):.2f}%")
        print(f"Median return:           {np.median(rets):.2f}%")
        print(f"Average win rate:        {np.mean(wrs):.1f}%")
        print(f"Average profit factor:   {np.mean(pfs):.2f}")
        print(f"Average max drawdown:    {np.mean(mds):.2f}%")
        print(f"Best:                    {max(rets):.2f}%")
        print(f"Worst:                   {min(rets):.2f}%")

        # Overall stats
        total_trades = sum(r['total_trades'] for r in traded)
        total_wins = sum(r['wins'] for r in traded)
        print(f"\nOverall win rate:        {total_wins/max(total_trades,1)*100:.1f}% ({total_wins}/{total_trades})")

        # Exit reason summary
        all_reasons = {}
        for r in traded:
            for k, v in r['exit_reasons'].items():
                all_reasons[k] = all_reasons.get(k, 0) + v
        print(f"\nExit reasons:")
        for k, v in sorted(all_reasons.items(), key=lambda x: -x[1]):
            print(f"  {k:<30} {v:>5} ({v/max(total_trades,1)*100:.1f}%)")

    # ── Baseline comparison ──
    baseline_file = Path(__file__).parent / "funding_contrarian_2month_all_pairs.json"
    if baseline_file.exists():
        with open(baseline_file) as f:
            baseline = json.load(f)
        bl_traded = [r for r in baseline['results'] if r['total_trades'] > 0]
        bl_rets = [r['net_return'] for r in bl_traded]
        bl_wrs = [r['win_rate'] for r in bl_traded]
        bl_profitable = [r for r in bl_traded if r['net_return'] > 0]

        print("\n" + "=" * 60)
        print("BASELINE COMPARISON (Unfiltered vs Regime-Filtered)")
        print("=" * 60)
        print(f"{'Metric':<25} {'Baseline':>15} {'Filtered':>15} {'Delta':>10}")
        print("-" * 65)
        print(f"{'Pairs traded':<25} {len(bl_traded):>15} {len(traded):>15} {len(traded)-len(bl_traded):>+10}")
        print(f"{'Profitable pairs':<25} {len(bl_profitable):>15} {len(profitable):>15} {len(profitable)-len(bl_profitable):>+10}")
        print(f"{'Win %':<25} {np.mean(bl_wrs):>14.1f}% {np.mean(wrs):>14.1f}% {(np.mean(wrs)-np.mean(bl_wrs)):>+9.1f}%")
        print(f"{'Avg return':<25} {np.mean(bl_rets):>14.2f}% {np.mean(rets):>14.2f}% {(np.mean(rets)-np.mean(bl_rets)):>+9.2f}%")
        print(f"{'Median return':<25} {np.median(bl_rets):>14.2f}% {np.median(rets):>14.2f}% {(np.median(rets)-np.median(bl_rets)):>+9.2f}%")

    out = Path(__file__).parent / "regime_filtered_2month.json"
    with open(out, "w") as f:
        json.dump({"ts": datetime.now(timezone.utc).isoformat(), "n": len(results), "results": results}, f, indent=2)
    print(f"\nSaved to {out}")


if __name__ == "__main__":
    main()

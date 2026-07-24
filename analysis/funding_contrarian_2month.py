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


class FundingContrarianStrategy:
    def __init__(self,
                 entry_funding_thresh: float = -0.0001,
                 exit_funding_thresh: float = 0.001,
                 take_profit_pct: float = 0.10,
                 stop_loss_pct: float = 0.05,
                 maker_fee: float = 0.0004,
                 taker_fee: float = 0.0007):

        self.entry_funding_thresh = entry_funding_thresh
        self.exit_funding_thresh = exit_funding_thresh
        self.tp_pct = take_profit_pct
        self.sl_pct = stop_loss_pct
        self.maker_fee = maker_fee
        self.taker_fee = taker_fee

    def generate_signals_and_backtest(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        position = 0
        entry_price = 0.0
        balance = CAPITAL
        shares = 0.0

        signals = []
        exit_reasons = []
        balances = []

        for i in range(len(df)):
            current_price = df['spot_price'].iloc[i]
            current_funding = df['perp_funding_rate'].iloc[i]

            signal = 0
            reason = "HOLD"

            if position == 0:
                if current_funding < self.entry_funding_thresh:
                    position = 1
                    entry_price = current_price
                    cost = balance * (1 - self.taker_fee)
                    shares = cost / current_price
                    balance = 0
                    signal = 1
                    reason = "ENTRY_FUNDING_NEGATIVE"

            elif position == 1:
                price_change = (current_price - entry_price) / entry_price

                if price_change >= self.tp_pct:
                    balance = shares * current_price * (1 - self.taker_fee)
                    shares = 0
                    position = 0
                    signal = -1
                    reason = "EXIT_TAKE_PROFIT"
                elif price_change <= -self.sl_pct:
                    balance = shares * current_price * (1 - self.taker_fee)
                    shares = 0
                    position = 0
                    signal = -1
                    reason = "EXIT_STOP_LOSS"
                elif current_funding > self.exit_funding_thresh:
                    balance = shares * current_price * (1 - self.taker_fee)
                    shares = 0
                    position = 0
                    signal = -1
                    reason = "EXIT_FUNDING_POSITIVE"

            signals.append(signal)
            exit_reasons.append(reason)
            if position == 1:
                balances.append(shares * current_price)
            else:
                balances.append(balance)

        df['signal'] = signals
        df['exit_reason'] = exit_reasons
        df['balance'] = balances
        return df


def get_all_pairs():
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
        if vol < 50000:
            continue
        pairs.append({"symbol": base, "volume": vol})
    pairs.sort(key=lambda x: x["volume"], reverse=True)
    return pairs


def fetch_data(sym):
    try:
        now_ms = int(time.time() * 1000)
        two_months_ms = 60 * 24 * 3600 * 1000
        start_ms = now_ms - two_months_ms

        r1 = requests.get(f"{BINANCE}/api/v3/klines",
                          params={"symbol": f"{sym}USDT", "interval": "8h", "limit": 180},
                          timeout=15)
        klines = r1.json()
        if not isinstance(klines, list) or len(klines) < 10:
            return None

        timestamps = [int(k[0]) for k in klines]
        closes = [float(k[4]) for k in klines]

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

        df = pd.DataFrame({
            "timestamp": pd.to_datetime(timestamps, unit="ms"),
            "spot_price": closes,
            "perp_funding_rate": funding_rates,
        })

        has_funding = sum(1 for fr in funding_rates if fr != 0)
        if has_funding < 5:
            return None

        return df
    except Exception:
        return None


def run_backtest(sym):
    df = fetch_data(sym)
    if df is None:
        return None

    bot = FundingContrarianStrategy()
    result_df = bot.generate_signals_and_backtest(df)

    entries = result_df[result_df['signal'] == 1]
    exits = result_df[result_df['signal'] == -1]
    final_balance = result_df['balance'].iloc[-1]
    net_return = ((final_balance - CAPITAL) / CAPITAL) * 100

    wins = 0
    losses = 0
    wins_sum = 0
    losses_sum = 0
    entry_prices = []
    exit_prices = []
    exit_reasons = []

    in_trade = False
    entry_p = 0

    for i in range(len(result_df)):
        row = result_df.iloc[i]
        if row['signal'] == 1:
            in_trade = True
            entry_p = row['spot_price']
        elif row['signal'] == -1 and in_trade:
            in_trade = False
            exit_p = row['spot_price']
            pnl = (exit_p - entry_p) / entry_p
            if pnl > 0:
                wins += 1
                wins_sum += pnl
            else:
                losses += 1
                losses_sum += pnl
            exit_reasons.append(row['exit_reason'])

    total_trades = wins + losses
    win_rate = (wins / total_trades * 100) if total_trades > 0 else 0
    avg_win = (wins_sum / wins * 100) if wins > 0 else 0
    avg_loss = (losses_sum / losses * 100) if losses > 0 else 0
    reason_counts = {}
    for r in exit_reasons:
        reason_counts[r] = reason_counts.get(r, 0) + 1

    return {
        "symbol": sym,
        "net_return": round(net_return, 2),
        "total_trades": total_trades,
        "wins": wins,
        "losses": losses,
        "win_rate": round(win_rate, 1),
        "avg_win_pct": round(avg_win, 2),
        "avg_loss_pct": round(avg_loss, 2),
        "exit_reasons": reason_counts,
        "bars": len(result_df),
        "data_start": str(df['timestamp'].iloc[0]),
        "data_end": str(df['timestamp'].iloc[-1]),
    }


def main():
    print("=" * 100)
    print("FUNDING CONTRARIAN BACKTEST — All Binance Pairs — 2 Months")
    print("=" * 100)
    print(f"Capital: ${CAPITAL:,} | TP: +10% | SL: -5% | Fees: 0.07% taker")
    print(f"Entry: funding < -0.01% | Exit: funding > +0.10%\n")

    pairs = get_all_pairs()
    print(f"Testing {len(pairs)} pairs...\n")

    results = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(run_backtest, p["symbol"]): p["symbol"] for p in pairs}
        done = 0
        for f in as_completed(futs):
            done += 1
            r = f.result()
            if r:
                results.append(r)
                t = r['total_trades']
                ret = r['net_return']
                print(f"  [{done:3d}/{len(pairs)}] {r['symbol']:<10} trades={t:>3}  return={ret:>7.2f}%  winrate={r['win_rate']:>5.1f}%")
            else:
                if done % 50 == 0:
                    print(f"  [{done:3d}/{len(pairs)}] (skipped/no data)")

    results.sort(key=lambda x: x['net_return'], reverse=True)

    # Full table
    print("\n" + "=" * 110)
    print(f"{'Symbol':<10} {'Return%':>9} {'Trades':>7} {'Wins':>5} {'Losses':>7} {'WinRate':>8} {'AvgWin':>8} {'AvgLoss':>8} {'ExitReasons'}")
    print("-" * 110)
    for r in results:
        er = str(r['exit_reasons'])
        print(f"{r['symbol']:<10} {r['net_return']:>8.2f}% {r['total_trades']:>6} {r['wins']:>5} {r['losses']:>6} {r['win_rate']:>7.1f}% {r['avg_win_pct']:>7.2f}% {r['avg_loss_pct']:>7.2f}% {er}")

    # Stats
    if results:
        traded = [r for r in results if r['total_trades'] > 0]
        profitable = [r for r in results if r['net_return'] > 0]
        rets = [r['net_return'] for r in traded]
        print("\n" + "=" * 60)
        print("PORTFOLIO STATS")
        print("=" * 60)
        print(f"Total pairs tested:      {len(results)}")
        print(f"Pairs with trades:       {len(traded)}")
        print(f"Profitable pairs:        {len(profitable)} ({len(profitable)/max(len(traded),1)*100:.0f}%)")
        print(f"Average return:          {np.mean(rets):.2f}%")
        print(f"Median return:           {np.median(rets):.2f}%")
        print(f"Best:                    {max(rets):.2f}%")
        print(f"Worst:                   {min(rets):.2f}%")
        total_trades = sum(r['total_trades'] for r in traded)
        total_wins = sum(r['wins'] for r in traded)
        print(f"Overall win rate:        {total_wins/max(total_trades,1)*100:.1f}% ({total_wins}/{total_trades})")

        # Exit reason summary
        all_reasons = {}
        for r in traded:
            for k, v in r['exit_reasons'].items():
                all_reasons[k] = all_reasons.get(k, 0) + v
        print(f"\nExit reasons:")
        for k, v in sorted(all_reasons.items(), key=lambda x: -x[1]):
            print(f"  {k:<30} {v:>5} ({v/max(total_trades,1)*100:.1f}%)")

    out = Path(__file__).parent / "funding_contrarian_2month_all_pairs.json"
    with open(out, "w") as f:
        json.dump({"ts": datetime.now(timezone.utc).isoformat(), "n": len(results), "results": results}, f, indent=2)
    print(f"\nSaved to {out}")


if __name__ == "__main__":
    main()

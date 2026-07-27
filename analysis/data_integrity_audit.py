#!/usr/bin/env python3
"""
Study #16 — Data integrity audit of the backtests.

Answers one question: did the studies, and Deep Hunt v4 in particular, run on
data that is what the code assumes it is?

Checks, in order:
  A. Raw feed      — bar counts vs requested, 5000-cap truncation, gaps,
                     duplicate timestamps, OHLC violations, forming last bar.
  B. Alignment     — backtest_propr() indexes EVERY symbol with the SAME iloc
                     index taken from one reference symbol. If two symbols'
                     indexes differ by even one bar, prices from different
                     hours get mixed. Measured directly, per walk-forward window.
  C. Reproduction  — re-run the frozen v4 config on the same 6 windows and
                     compare against the stored deep_hunt_v4_walkforward.json.
  D. Caches        — parquet datasets used by the fib / basket studies.
  E. Equity guard  — deep_hunt_backtest_v4.py used to add the entry cost of open
                     positions ON TOP of their mark-to-market, double-counting
                     them and inventing a ~23pp drawdown. Fixed 2026-07-27; this
                     section now FAILS if the double-count comes back, and keeps
                     measuring what it cost by patching the old line back in.

Read-only. Touches the network (Hyperliquid) and the local parquet caches.
"""
import json
import sys
import time
import types
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HL = "https://api.hyperliquid.xyz/info"
MAJORS = ["BTC", "ETH", "SOL", "DOGE", "XRP", "AVAX",
          "LINK", "SUI", "NEAR", "AAVE", "INJ", "FET"]
HISTORY_DAYS = 215          # what deep_hunt_v4_walkforward.py asks for
WINDOW_H = 720
N_WINDOWS = 6
WF_JSON = HERE / "deep_hunt_v4_walkforward.json"
OUT = HERE / "data_integrity_audit_results.json"

findings = []       # (severity, area, message)


def flag(sev, area, msg):
    findings.append((sev, area, msg))
    print(f"    [{sev}] {msg}")


def fetch(symbol, days=HISTORY_DAYS, interval="1h"):
    now = int(datetime.now(timezone.utc).timestamp() * 1000)
    start = now - days * 24 * 3600 * 1000
    r = requests.post(HL, json={"type": "candleSnapshot", "req": {
        "coin": symbol, "interval": interval,
        "startTime": start, "endTime": now}}, timeout=30)
    r.raise_for_status()
    d = r.json()
    if not d:
        return pd.DataFrame()
    df = pd.DataFrame([{
        "open": float(c["o"]), "high": float(c["h"]), "low": float(c["l"]),
        "close": float(c["c"]), "volume": float(c["v"]),
        "timestamp": pd.to_datetime(int(c["t"]), unit="ms", utc=True)} for c in d])
    return df.set_index("timestamp").sort_index()


def bar_report(sym, df, step=pd.Timedelta("1h")):
    """Structural facts about one symbol's candles."""
    idx = df.index
    dupes = int(idx.duplicated().sum())
    deltas = idx.to_series().diff().dropna()
    gaps = deltas[deltas != step]
    missing = int(sum((g / step) - 1 for g in gaps)) if len(gaps) else 0
    bad_ohlc = int((
        (df["high"] < df["low"]) |
        (df["close"] > df["high"]) | (df["close"] < df["low"]) |
        (df["open"] > df["high"]) | (df["open"] < df["low"])
    ).sum())
    nulls = int(df.isna().sum().sum())
    nonpos = int((df[["open", "high", "low", "close"]] <= 0).sum().sum())
    return {
        "symbol": sym, "bars": len(df),
        "first": str(idx.min()), "last": str(idx.max()),
        "span_days": round((idx.max() - idx.min()).total_seconds() / 86400, 2),
        "dupe_timestamps": dupes, "gap_events": len(gaps),
        "missing_bars": missing, "bad_ohlc": bad_ohlc,
        "nulls": nulls, "nonpositive_prices": nonpos,
        "zero_volume_bars": int((df["volume"] == 0).sum()),
    }


# ─────────────────────────────  A. raw feed  ─────────────────────────────
def section_a():
    print("\n" + "=" * 96)
    print("A. RAW FEED — what Hyperliquid actually returns for the request the studies make")
    print("=" * 96)
    requested = HISTORY_DAYS * 24
    print(f"  requested: {HISTORY_DAYS}d of 1h = {requested} bars per symbol\n")
    data, reports = {}, []
    for i, s in enumerate(MAJORS, 1):
        try:
            df = fetch(s)
        except Exception as e:
            flag("FAIL", "feed", f"{s}: fetch error {e}")
            continue
        data[s] = df
        rep = bar_report(s, df)
        reports.append(rep)
        print(f"  [{i:2d}/{len(MAJORS)}] {s:6s} {rep['bars']:5d} bars  "
              f"{rep['first'][:16]} -> {rep['last'][:16]}  "
              f"gaps={rep['gap_events']:3d} missing={rep['missing_bars']:4d} "
              f"dupes={rep['dupe_timestamps']} badOHLC={rep['bad_ohlc']} "
              f"nulls={rep['nulls']}")
        time.sleep(0.25)

    if not data:
        flag("FAIL", "feed", "no data fetched at all — audit cannot continue")
        return data, reports

    counts = {r["bars"] for r in reports}
    if len(counts) > 1:
        flag("WARN", "feed",
             f"symbols do not agree on bar count: {sorted(counts)} "
             f"— iloc-aligned code will mix timestamps (see section B)")
    cap = max(r["bars"] for r in reports)
    if cap >= 5000 and requested > 5000:
        flag("WARN", "feed",
             f"response capped at {cap} bars but {requested} were requested — "
             f"the studies silently got {round(cap/24)}d, not {HISTORY_DAYS}d")
    for r in reports:
        if r["missing_bars"]:
            flag("WARN", "feed",
                 f"{r['symbol']}: {r['missing_bars']} missing 1h bars across "
                 f"{r['gap_events']} gaps — rolling windows span more wall-clock than assumed")
        if r["dupe_timestamps"]:
            flag("FAIL", "feed", f"{r['symbol']}: {r['dupe_timestamps']} duplicate timestamps")
        if r["bad_ohlc"] or r["nulls"] or r["nonpositive_prices"]:
            flag("FAIL", "feed",
                 f"{r['symbol']}: {r['bad_ohlc']} OHLC violations, {r['nulls']} nulls, "
                 f"{r['nonpositive_prices']} non-positive prices")

    # forming bar: the studies do NOT drop the last, still-open candle
    now = datetime.now(timezone.utc)
    last = max(pd.Timestamp(r["last"]) for r in reports)
    age_min = (now - last.to_pydatetime()).total_seconds() / 60
    if age_min < 60:
        flag("WARN", "feed",
             f"last candle opened {age_min:.0f} min ago — it is still forming. "
             f"deep_hunt_*.py keep it; dip_runner.py drops it. Backtest and live "
             f"therefore evaluate different final bars.")
    return data, reports


# ────────────────────────  B. cross-symbol alignment  ────────────────────
def windows_from(hist, anchor=None):
    end = anchor if anchor is not None else min(df.index.max() for df in hist.values())
    out = []
    for w in range(N_WINDOWS):
        w_end = end - pd.Timedelta(hours=WINDOW_H * w)
        w_start = w_end - pd.Timedelta(hours=WINDOW_H)
        wd = {s: df.loc[(df.index > w_start) & (df.index <= w_end)]
              for s, df in hist.items()}
        wd = {s: d for s, d in wd.items() if len(d) >= 220}
        out.append((w, w_start, w_end, wd))
    return out


def alignment_report(wd):
    """Exactly the assumption backtest_propr() makes: processed[sym].iloc[i]
    is the same hour for every sym, where i indexes the FIRST symbol."""
    ref = list(wd.keys())[0]
    ridx = wd[ref].index
    worst, offenders = 0, []
    for s, d in wd.items():
        n = min(len(d), len(ridx))
        mism = int((d.index[:n] != ridx[:n]).sum())
        if mism:
            offenders.append((s, mism, len(d), len(ridx)))
            worst = max(worst, mism)
    return ref, len(ridx), worst, offenders


def section_b(hist):
    print("\n" + "=" * 96)
    print("B. ALIGNMENT — backtest_propr() addresses every symbol with one shared iloc index")
    print("=" * 96)
    print(f"  reference symbol is list(processed.keys())[0]; a mismatch means a price\n"
          f"  from the wrong hour was used for entries, exits and equity.\n")
    rows = []
    for w, ws, we, wd in windows_from(hist):
        if len(wd) < 4:
            print(f"  window {w} ({ws.date()} -> {we.date()}): only {len(wd)} symbols, skipped")
            continue
        ref, reflen, worst, off = alignment_report(wd)
        lens = sorted({len(d) for d in wd.values()})
        print(f"  window {w} ({ws.date()} -> {we.date()}) symbols={len(wd):2d} "
              f"ref={ref} ref_bars={reflen} bar_counts={lens} misaligned_symbols={len(off)}")
        if off:
            for s, m, ls, lr in off[:5]:
                flag("FAIL", "alignment",
                     f"window {w}: {s} index differs from ref {ref} at {m} of "
                     f"{min(ls, lr)} shared positions")
        if len(lens) > 1:
            flag("WARN", "alignment",
                 f"window {w}: symbols have {len(lens)} different bar counts "
                 f"{lens} — the short ones get iloc[-1] stale prices past their end")
        rows.append({"window": w, "start": str(ws.date()), "end": str(we.date()),
                     "symbols": len(wd), "ref": ref, "ref_bars": reflen,
                     "bar_counts": lens, "misaligned": [o[0] for o in off]})
    return rows


# ────────────────────────  C. reproduce walk-forward  ────────────────────
def section_c(hist):
    print("\n" + "=" * 96)
    print("C. REPRODUCTION — re-run the frozen config on the same windows")
    print("=" * 96)
    if not WF_JSON.exists():
        flag("WARN", "repro", "deep_hunt_v4_walkforward.json missing — nothing to compare")
        return []
    stored = json.loads(WF_JSON.read_text(encoding="utf-8"))
    best = stored["config"]
    try:
        from analysis.deep_hunt_v4_propr import backtest_propr, SYMBOLS as PROPR_SYMBOLS
    except Exception as e:
        flag("FAIL", "repro", f"cannot import backtest_propr: {e}")
        return []

    if set(PROPR_SYMBOLS) != set(MAJORS):
        flag("FAIL", "repro",
             f"deep_hunt_v4_propr.SYMBOLS is now {len(PROPR_SYMBOLS)} coins, but the stored "
             f"walk-forward ran on {stored['windows'][0]['symbols']}. The file on disk is NOT "
             f"the file that produced the result — re-running it does not reproduce the study.")

    # anchor to the stored window 0 end so the periods line up with the study
    anchor = pd.Timestamp(stored["windows"][0]["end"], tz="UTC") + pd.Timedelta(hours=23)
    by_window = {w["window"]: w for w in stored["windows"]}
    rows = []
    print(f"  config: {best['name']}   universe forced to the 12 majors   "
          f"anchor={anchor.date()}\n")
    print(f"  {'win':>3} {'period':<24} {'trades new/old':>15} {'return new/old':>18} "
          f"{'maxDD% new/old':>18}")
    print("  " + "-" * 82)
    for w, ws, we, wd in windows_from(hist, anchor=anchor):
        if len(wd) < 4:
            continue
        r = backtest_propr(wd, best)
        old = by_window.get(w, {}).get("strat") or {}
        if not r:
            print(f"  {w:>3} {str(ws.date())+' -> '+str(we.date()):<24} "
                  f"{'no trades':>15}")
            continue
        print(f"  {w:>3} {str(ws.date())+' -> '+str(we.date()):<24} "
              f"{r['trades']:>7}/{old.get('trades','?'):<7} "
              f"{r['return_pct']:>+8.2f}%/{old.get('return_pct','?'):<8} "
              f"{r['max_dd_pct']:>8.2f}%/{old.get('max_dd_pct','?'):<8}")
        rows.append({"window": w, "start": str(ws.date()), "end": str(we.date()),
                     "new": {k: r[k] for k in ("trades", "return_pct", "win_rate",
                                               "max_dd_pct", "challenge_failed")},
                     "old": old})
        if old and old.get("trades") != r["trades"]:
            flag("WARN", "repro",
                 f"window {w}: {r['trades']} trades now vs {old['trades']} in the stored "
                 f"study — same config, same period, different result")
    if rows:
        rets_new = [x["new"]["return_pct"] for x in rows]
        rets_old = [x["old"].get("return_pct") for x in rows if x["old"]]
        print(f"\n  mean return  new {np.mean(rets_new):+.2f}%   "
              f"old {np.mean([r for r in rets_old if r is not None]):+.2f}%")
        fails = sum(1 for x in rows if x["new"]["challenge_failed"])
        print(f"  challenge failures new {fails}/{len(rows)}")
    return rows


# ────────────────────────────  D. parquet caches  ────────────────────────
def section_d():
    print("\n" + "=" * 96)
    print("D. CACHED DATASETS — the parquet the fib / basket / absorption studies read")
    print("=" * 96)
    steps = {"1m": pd.Timedelta("1min"), "1h": pd.Timedelta("1h"),
             "4h": pd.Timedelta("4h"), "funding": None}
    rows = []
    for d in ("hl_1h_cache", "fib_1m_data", "backtest_data"):
        dirp = HERE / d
        files = sorted(dirp.glob("*.parquet")) if dirp.exists() else []
        if not files:
            print(f"\n  {d}/ — absent or empty")
            continue
        sample = files[:400]
        print(f"\n  {d}/ — {len(files)} files (checking {len(sample)})")
        spans, bad, gappy, dup, short = [], [], [], [], []
        for f in sample:
            try:
                df = pd.read_parquet(f)
            except Exception as e:
                bad.append((f.name, f"unreadable: {e}"))
                continue
            if not isinstance(df.index, pd.DatetimeIndex):
                for c in ("ts", "timestamp", "time", "date", "open_time"):
                    if c in df.columns:
                        df = df.set_index(pd.to_datetime(df[c], utc=True))
                        break
            if not isinstance(df.index, pd.DatetimeIndex) or df.empty:
                bad.append((f.name, "no datetime index"))
                continue
            df = df.sort_index()
            kind = f.stem.split("_")[-1]
            step = steps.get(kind)
            spans.append((f.name, len(df), df.index.min(), df.index.max()))
            if df.index.duplicated().any():
                dup.append((f.name, int(df.index.duplicated().sum())))
            cols = {c.lower() for c in df.columns}
            if {"open", "high", "low", "close"} <= cols:
                v = int(((df["high"] < df["low"]) | (df["close"] > df["high"]) |
                         (df["close"] < df["low"])).sum())
                if v:
                    bad.append((f.name, f"{v} OHLC violations"))
            if df.isna().any().any():
                bad.append((f.name, f"{int(df.isna().sum().sum())} nulls"))
            if step is not None and len(df) > 2:
                dl = df.index.to_series().diff().dropna()
                miss = int(sum((g / step) - 1 for g in dl[dl != step]))
                if miss > 0:
                    gappy.append((f.name, miss, len(df)))
            if len(df) < 250:
                short.append((f.name, len(df)))

        if spans:
            starts = {s[2].date() for s in spans}
            ends = {s[3].date() for s in spans}
            lens = [s[1] for s in spans]
            print(f"    rows: min {min(lens)} max {max(lens)}   "
                  f"starts {min(starts)}..{max(starts)}   ends {min(ends)}..{max(ends)}")
            if len(ends) > 1 and (max(ends) - min(ends)).days > 2:
                flag("WARN", "cache",
                     f"{d}: files end on different dates ({min(ends)}..{max(ends)}) — "
                     f"a cross-sectional study over these compares unequal periods")
            stale = (datetime.now(timezone.utc).date() - max(ends)).days
            if stale > 3:
                flag("WARN", "cache", f"{d}: newest data is {stale} days old")
        for n, why in bad[:8]:
            flag("FAIL", "cache", f"{d}/{n}: {why}")
        for n, m, tot in sorted(gappy, key=lambda x: -x[1])[:8]:
            flag("WARN", "cache", f"{d}/{n}: {m} missing bars of {tot}")
        if len(gappy) > 8:
            flag("WARN", "cache", f"{d}: {len(gappy)} of {len(sample)} files have gaps "
                                  f"(showing 8)")
        for n, c in dup[:5]:
            flag("FAIL", "cache", f"{d}/{n}: {c} duplicate timestamps")
        if short:
            flag("WARN", "cache",
                 f"{d}: {len(short)} files under 250 rows — below the 220-bar warmup "
                 f"the backtests require (e.g. {short[0][0]} has {short[0][1]})")
        rows.append({"dir": d, "files": len(files), "checked": len(sample),
                     "unreadable_or_invalid": len(bad), "gappy": len(gappy),
                     "duplicated": len(dup), "too_short": len(short)})
    return rows


# ────────────────  E. the v4 equity-curve regression guard  ──────────────
CORRECT_LINE = "equity_curve.append(capital + mtm)"
OLD_BUGGY_LINE = """equity_curve.append(capital + mtm + sum(
            p["entry_price"] * p["qty"] for p in positions if p["symbol"] == symbol))"""


def load_patched(path, old, new):
    src = path.read_text(encoding="utf-8").replace(old, new)
    if src == path.read_text(encoding="utf-8"):
        return None
    mod = types.ModuleType("patched_v4")
    mod.__file__ = str(path)
    exec(compile(src, str(path), "exec"), mod.__dict__)
    return mod


def section_e(hist):
    print("\n" + "=" * 96)
    print("E. EQUITY CURVE — regression guard on the max-drawdown that scores the v4 sweep")
    print("=" * 96)
    v4 = HERE / "deep_hunt_backtest_v4.py"
    src = v4.read_text(encoding="utf-8")
    if OLD_BUGGY_LINE in src:
        flag("FAIL", "equity",
             "the entry-cost double-count is BACK in deep_hunt_backtest_v4.py — "
             "max_dd and every score derived from it are invalid again")
    if CORRECT_LINE not in src:
        flag("FAIL", "equity",
             "deep_hunt_backtest_v4.py no longer computes equity = capital + mtm; "
             "re-derive the equity curve before trusting any max_dd it reports")
        return []
    print("  shipped (fixed):  equity = capital + mark_to_market")
    print("  old (removed):    equity = capital + mark_to_market + entry_cost")
    print("  capital already has the entry cost deducted, so the old line counted it twice.\n")

    import importlib
    shipped = importlib.import_module("analysis.deep_hunt_backtest_v4")
    buggy = load_patched(v4, CORRECT_LINE, OLD_BUGGY_LINE)
    if buggy is None:
        flag("WARN", "equity", "could not rebuild the old formula — magnitude not measured")
        return []

    cfgs = [c for c in shipped.CONFIGS if c["name"] in
            ("v3_best", "trail_1pct_act", "agg_trail_tp3", "combo_full")]
    rows = []
    print(f"  {'config':<18} {'symbol':>6} {'trades':>7} {'maxDD% now':>12} "
          f"{'maxDD% old':>12} {'old overstated by':>19}")
    print("  " + "-" * 80)
    for cfg in cfgs:
        for sym in ("BTC", "ETH", "SOL", "DOGE"):
            df = hist.get(sym)
            if df is None or df.empty:
                continue
            df30 = df.iloc[-720:]
            a = shipped.backtest_v4(sym, df30, cfg)
            b = buggy.backtest_v4(sym, df30, cfg)
            if not a or not b:
                continue
            err = b["max_dd_pct"] - a["max_dd_pct"]
            print(f"  {cfg['name']:<18} {sym:>6} {a['trades']:>7} "
                  f"{a['max_dd_pct']:>11.2f}% {b['max_dd_pct']:>11.2f}% "
                  f"{err:>+18.2f}pp")
            rows.append({"config": cfg["name"], "symbol": sym,
                         "max_dd_pct": a["max_dd_pct"],
                         "old_max_dd_pct": b["max_dd_pct"],
                         "old_overstated_pp": round(err, 2)})
            if a["total_pnl"] != b["total_pnl"]:
                flag("FAIL", "equity",
                     f"{cfg['name']}/{sym}: the equity formula changed realised PnL "
                     f"({a['total_pnl']} vs {b['total_pnl']}) — it must only affect "
                     f"the equity curve")
    if rows:
        worst = max(rows, key=lambda r: r["old_overstated_pp"])
        print(f"\n  fix confirmed live: max_dd now {min(r['max_dd_pct'] for r in rows):.2f}"
              f"-{max(r['max_dd_pct'] for r in rows):.2f}%, the old line reported up to "
              f"{worst['old_max_dd_pct']:.2f}% "
              f"(+{worst['old_overstated_pp']:.2f}pp of pure artifact)")
    return rows


# ──────────────  F. declared period vs the data actually used  ──────────
def section_f():
    """A results file that says days=90 while its own bar counts say 9 is not a
    90-day study. Checks the claim against the evidence in the same file."""
    print("\n" + "=" * 96)
    print("F. DECLARED PERIOD vs DATA ACTUALLY USED")
    print("=" * 96)
    rows = []
    for f in sorted(set(HERE.glob("*_results.json"))):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(d, dict) or "days" not in d:
            continue
        claimed = d["days"]
        bars, per = [], d.get("pairs") or d.get("symbols") or {}
        it = per.values() if isinstance(per, dict) else per
        for v in it:
            if isinstance(v, dict) and isinstance(v.get("bars"), int):
                bars.append(v["bars"])
        if not bars:
            continue
        # bar interval inferred from the widest entry's start/end
        actual = sorted(b / 1440 for b in bars)          # 1m data
        med = actual[len(actual) // 2]
        print(f"  {f.name:38s} claims {claimed:>4} days | actual per-symbol "
              f"min {min(actual):.1f}d median {med:.1f}d max {max(actual):.1f}d "
              f"({len(bars)} symbols)")
        rows.append({"file": f.name, "claimed_days": claimed,
                     "actual_days_min": round(min(actual), 2),
                     "actual_days_median": round(med, 2),
                     "actual_days_max": round(max(actual), 2),
                     "symbols": len(bars)})
        if med < claimed * 0.5:
            flag("FAIL", "coverage",
                 f"{f.name}: labelled a {claimed}-day study, but the median symbol holds "
                 f"{med:.1f} days ({sum(1 for a in actual if a >= claimed*0.9)}/{len(bars)} "
                 f"symbols reach the claimed period). The conclusion drawn from it "
                 f"describes {med:.0f} days of one regime, not {claimed}.")
    if not rows:
        print("  no results file declares a period to check")
    return rows


def main():
    print("=" * 96)
    print("STUDY #16 — DATA INTEGRITY AUDIT OF THE BACKTESTS")
    print(f"run {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}")
    print("=" * 96)

    hist, feed = section_a()
    align = section_b(hist) if hist else []
    repro = section_c(hist) if hist else []
    caches = section_d()
    equity = section_e(hist) if hist else []
    coverage = section_f()

    print("\n" + "=" * 96)
    print("VERDICT")
    print("=" * 96)
    fails = [f for f in findings if f[0] == "FAIL"]
    warns = [f for f in findings if f[0] == "WARN"]
    for sev, area, msg in fails + warns:
        print(f"  [{sev}] {area}: {msg}")
    if not findings:
        print("  clean — no data problems found")
    print(f"\n  {len(fails)} FAIL, {len(warns)} WARN")

    OUT.write_text(json.dumps({
        "run_utc": datetime.now(timezone.utc).isoformat(),
        "feed": feed, "alignment": align, "reproduction": repro,
        "caches": caches, "equity_curve": equity, "coverage": coverage,
        "findings": [{"severity": s, "area": a, "message": m} for s, a, m in findings],
    }, indent=2, default=str), encoding="utf-8")
    print(f"\n  saved -> {OUT}")


if __name__ == "__main__":
    main()

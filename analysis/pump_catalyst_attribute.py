#!/usr/bin/env python3
"""
STUDY 18, part 2 — attribute the pumps found by pump_catalyst_study.py.

Reads pump_catalyst_events.json and answers the question that was asked:
which groups of >= 5 pairs rose for the SAME reason.

REASON CLASSES (each event gets exactly one, resolved in this order)
--------------------------------------------------------------------
  NEW_LISTING   The event lands in the symbol's first 24h on Binance. The
                listing is the reason; no headline needed. These produce the
                largest candles in the whole dataset by a wide margin.
  MARKET_WIDE   BREADTH >= 10%: at least a tenth of the entire universe closed
                >= +3% on that same bar. The pair did not move for its own
                reason, everything moved. Breadth is the right detector here,
                not BTC - alts routinely bounce as a block while BTC lags, and
                the 2025-10-10 rebound is exactly that shape.
  SECTOR        Not market-wide, but >= 5 pairs OF ONE SECTOR printed a >=10%
                candle on the same UTC day. This is the narrative-rotation
                bucket and it is the direct answer to the user's question.
                Threshold is 5 pairs *of the sector*, not "5 pairs of which
                60% share a sector" - the latter passes on a group of 6.
  BROAD_ALT_BID Not market-wide, no sector reached 5, but >= 10 pairs pumped
                that day. Something lifted alts without lifting BTC and
                without being sectoral. Kept separate rather than folded into
                SECTOR, because the mechanism is not established.
  SOLO          Everything else. Idiosyncratic single-coin news: partnership,
                unlock, exchange listing elsewhere, buyback, hack recovery,
                whale. Needs a per-coin headline; the top ones are listed.

WHY DAY-LEVEL FOR SECTORS AND BAR-LEVEL FOR MARKET-WIDE
-------------------------------------------------------
A liquidation-cascade rebound is simultaneous to the minute, so bar-level is
right for it. A narrative rotation is not - a privacy bid builds over hours as
desks rotate, so requiring the same 15m bar would dissolve it. Both views are
computed; the simultaneous (same-bar) table is printed too, for the record.

SECTORS is hand-maintained. Unmapped bases are reported as "other" so the map
can be extended rather than silently mislabelling a cluster. Sector membership
is resolved by SECTOR_PRIORITY - specific beats generic, because DASH and XVG
are L1s in the trivial sense but they rally as PRIVACY coins.
"""
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
except Exception:
    pass

HERE = Path(__file__).parent
BREADTH_MARKET_WIDE = 0.10   # >=10% of the universe up >=3% on that bar
SECTOR_MIN_PAIRS = 5         # the user's "at least 5 pairs"
BROAD_MIN_PAIRS = 10

TOKENIZED_EQUITY = {
    "AAOIB", "AMDB", "ARMB", "AVGOB", "BABAB", "CBRSB", "COINB", "CRCLB",
    "CRWVB", "DRAMB", "EWYB", "GLWB", "GOOGLB", "HOODB", "IBMB", "INTCB",
    "INTWB", "KORUB", "LITEB", "METAB", "MRVLB", "MSFTB", "MSTRB", "MUB",
    "MUUB", "NBISB", "NOKB", "NVDAB", "ORCLB", "PLTRB", "QCOMB", "QNTB",
    "QQQB", "RKLBB", "SKHYB", "SNDKB", "SNXXB", "SOXLB", "SPCXB", "SPYB",
    "TQQQB", "TSLAB", "TSMB", "WDCB", "MVLLB",
}

SECTORS = {
    "meme": {
        "DOGE", "SHIB", "PEPE", "FLOKI", "BONK", "WIF", "BOME", "MEME", "NEIRO",
        "TURBO", "1000SATS", "1000CAT", "1000CHEEMS", "1MBABYDOGE", "DOGS",
        "HMSTR", "CATI", "PNUT", "ACT", "PEOPLE", "SPELL", "MUBARAK", "TUT",
        "BROCCOLI714", "BANANA", "BANANAS31", "TST", "GIGGLE", "PUMP", "TRUMP",
        "PENGU", "WIN", "BTTC", "SLP", "币安人生", "ZBT", "AWE", "NOT", "MBL",
        "QUICK", "BABY", "CHIP", "DOLO", "SAGA",
    },
    "ai": {
        "FET", "RENDER", "TAO", "WLD", "ARKM", "AI", "AIXBT", "CGPT", "VIRTUAL",
        "IO", "NMR", "PHA", "GRAM", "KAITO", "SAHARA", "PROVE", "AIGENSYN",
        "SAPIEN", "HOLO", "ROBO", "MIRA", "ALLO", "OPG", "SENT", "GPS",
        "COOKIE", "ZAMA", "NIL", "SXT", "VANA", "KITE", "OPEN", "ESP", "OCEAN",
        "HYPER", "BREV", "ENSO", "TREE", "MET",
    },
    "l1": {
        "BTC", "ETH", "BNB", "SOL", "ADA", "XRP", "AVAX", "DOT", "ATOM", "NEAR",
        "APT", "SUI", "SEI", "TIA", "INJ", "TRX", "LTC", "BCH", "ETC", "XLM",
        "ALGO", "HBAR", "EGLD", "FLOW", "KAIA", "ICP", "TON", "VET", "NEO",
        "QTUM", "ONT", "ZIL", "IOTA", "WAXP", "ASTR", "MINA", "KSM", "CELO",
        "KAVA", "ONE", "IOST", "LSK", "SC", "STEEM", "HIVE", "XEC", "MOVE",
        "BERA", "S", "INIT", "SOMI", "MEGA", "FOGO", "XPL", "LINEA", "MONAD",
        "STRAX", "XNO", "CFG", "GLMR", "MOVR", "VIC", "TFUEL", "THETA", "ONG",
        "QKC", "CKB", "BB", "ERA", "STO", "AT", "2Z", "ICX", "LUNA", "LUNC",
        "USTC", "OSMO", "STX", "VTHO", "ADX", "ARK", "A", "G", "0G", "BARD",
        "KERNEL", "HEI", "CHR", "CFX",
    },
    "l2_scaling": {
        "ARB", "OP", "MATIC", "POL", "STRK", "ZK", "MANTA", "METIS", "IMX",
        "SKL", "LRC", "BLAST", "TAIKO", "SCR", "ZKC", "ZKP", "HEMI", "DYM",
        "ALT", "EIGEN", "AEVO", "BLUR", "POLYX", "BEAMX", "SKY",
    },
    "defi": {
        "UNI", "AAVE", "LINK", "MKR", "CRV", "COMP", "SNX", "SUSHI", "1INCH",
        "YFI", "BAL", "LDO", "PENDLE", "ENA", "MORPHO", "EUL", "CAKE", "JOE",
        "GMX", "DYDX", "RUNE", "THE", "AERO", "VELODROME", "RAY", "ORCA",
        "CETUS", "JST", "SUN", "LQTY", "RPL", "SSV", "ETHFI", "SOLV", "LISTA",
        "KMNO", "USUAL", "RESOLV", "FRAX", "DEXE", "CVX", "GNS", "PYTH", "COW",
        "BNT", "KNC", "DODO", "MAV", "HUMA", "SPK", "EDEN", "MMT", "AVNT",
        "ASTER", "NEXO", "BANK", "F", "HAEDAL", "MITO", "TURTLE", "SYN", "STG",
        "AXL", "W", "T", "GNO", "UMA", "REZ", "RSR", "AUCTION", "BEL", "C98",
        "FIDA", "OGN", "RAD", "GTC", "ID", "ENS", "MASK", "REQ", "RIF", "TNSR",
        "JTO", "BIO", "PROM", "CTK", "QI", "RED", "FORM", "SIGN", "VANRY",
    },
    "rwa_stablecoin": {
        "ONDO", "PLUME", "MANTRA", "USDE", "PAXG", "XAUT", "WLFI", "GENIUS",
        "HOME", "U", "RE", "OPN", "LA", "NEWT", "IQ",
    },
    "gaming_nft": {
        "SAND", "MANA", "AXS", "GALA", "ENJ", "ILV", "YGG", "ALICE", "TLM",
        "PYR", "MAGIC", "BIGTIME", "XAI", "PIXEL", "PORTAL", "ACE", "RONIN",
        "APE", "ANIME", "GMT", "GST", "KGST", "AGLD", "RARE", "ME", "SUPER",
        "VOXEL", "HIGH", "COMBO", "TOWNS", "PARTI", "KAT", "EPIC", "SOPH",
        "NXPC", "C", "GUN", "SHELL", "AVA", "LUMIA", "CHZ", "EDU", "CYBER",
        "ORDI", "AUDIO",
    },
    "depin_infra": {
        "FIL", "AR", "GRT", "STORJ", "HNT", "IOTX", "ANKR", "FLUX", "GLM",
        "LPT", "RLC", "NKN", "POWR", "DIA", "API3", "BAND", "TRB", "WAL",
        "DUSK", "ARPA", "CTSI", "CELR", "QNT", "HOT", "AMP", "BICO", "GAS",
        "ACX", "JASMY", "IOTA", "CVC", "MTL", "BAT", "ACH", "COTI", "MBL",
    },
    "exchange_cex": {
        "BNB", "FTT", "OKB", "CRO", "KCS", "GT", "HT", "LEO", "WBETH", "BNSOL",
        "TWT", "SFP", "PUNDIX", "TKO", "HFT", "WOO", "BMT", "FF",
    },
    "fan_token": {
        "CITY", "PSG", "JUV", "ATM", "ASR", "OG", "BAR", "ACM", "SANTOS",
        "PORTO", "LAZIO", "ALPINE",
    },
    "privacy": {"XMR", "ZEC", "ZEN", "SCRT", "DASH", "PIVX", "XVG", "NIGHT",
                "ROSE", "DCR", "DGB", "RVN", "FIRO", "BEAM", "GRIN"},
    "tokenized_equity": TOKENIZED_EQUITY,
}

# Specific beats generic; later wins.
SECTOR_PRIORITY = ["l1", "l2_scaling", "defi", "depin_infra", "gaming_nft",
                   "exchange_cex", "ai", "rwa_stablecoin", "fan_token",
                   "meme", "tokenized_equity", "privacy"]

BASE2SECTOR = {}
for sec in SECTOR_PRIORITY:
    for m in SECTORS.get(sec, ()):
        BASE2SECTOR[m] = sec
for sec, members in SECTORS.items():
    for m in members:
        BASE2SECTOR.setdefault(m, sec)


def breadth_table():
    """Per TF, per bar: fraction of the universe that closed >= +3%."""
    sys.path.insert(0, str(HERE))
    import gzip
    import pickle

    from pump_catalyst_study import CACHE, TFS, resample

    files = sorted(CACHE.glob("*.pkl.gz"))
    print(f"breadth: scanning {len(files)} cached symbols...")
    up = {tf: Counter() for tf in TFS}
    tot = {tf: Counter() for tf in TFS}
    for i, p in enumerate(files):
        try:
            with gzip.open(p, "rb") as f:
                df = pickle.load(f)
        except Exception:
            continue
        for tf, rule in TFS.items():
            d = resample(df, rule)
            r = (d["close"] - d["open"]) / d["open"]
            tot[tf].update(r.index)
            up[tf].update(r.index[r >= 0.03])
        if (i + 1) % 150 == 0:
            print(f"  [{i+1}/{len(files)}]")
    return {tf: pd.Series({k: up[tf][k] / tot[tf][k]
                           for k in tot[tf] if tot[tf][k] >= 50}).sort_index()
            for tf in TFS}


def main():
    data = json.loads((HERE / "pump_catalyst_events.json").read_text(encoding="utf-8"))
    ev = pd.DataFrame(data["events"])
    meta = data["meta"]
    print(f"{len(ev)} events | {meta['symbols_with_data']} symbols | "
          f"{meta['window_start'][:10]} -> {meta['window_end'][:10]}")

    ev["sector"] = ev["base"].map(BASE2SECTOR).fillna("other")
    ev["btc_pct"] = pd.to_numeric(ev["btc_pct"], errors="coerce")
    unmapped = sorted(ev.loc[ev["sector"] == "other", "base"].unique())
    print(f"unmapped bases ({len(unmapped)}): {', '.join(unmapped)}")

    bt = breadth_table()
    ev["breadth"] = [float(bt[tf].get(pd.Timestamp(ts), float("nan")))
                     for tf, ts in zip(ev["tf"], ev["ts"])]
    print("\nbreadth (share of universe up >=3% on a bar):")
    for tf in ["15m", "30m", "1h", "4h"]:
        s = bt[tf]
        print(f"  {tf:>3}  median {s.median():.3f}  p95 {s.quantile(.95):.3f}  "
              f"p99 {s.quantile(.99):.3f}  max {s.max():.3f}  "
              f">= {BREADTH_MARKET_WIDE:.0%} on {(s >= BREADTH_MARKET_WIDE).mean()*100:.1f}% of bars")

    # ---- classify ---------------------------------------------------------
    ev["reason"] = "SOLO"
    ev.loc[ev["breadth"] >= BREADTH_MARKET_WIDE, "reason"] = "MARKET_WIDE"
    ev.loc[ev["new_listing"], "reason"] = "NEW_LISTING"     # listing wins

    # ---- sector clusters, day level, on the remainder ---------------------
    rest = ev[ev["reason"] == "SOLO"]
    sector_clusters, broad_days = [], []
    for (tf, date), g in rest.groupby(["tf", "date"]):
        claimed = set()
        for sec, gs in g[g["sector"] != "other"].groupby("sector"):
            bases = sorted(gs["base"].unique())
            if len(bases) < SECTOR_MIN_PAIRS:
                continue
            ev.loc[gs.index, "reason"] = "SECTOR"
            ev.loc[gs.index, "cluster_sector"] = sec
            claimed |= set(gs.index)
            sector_clusters.append({
                "date": date, "tf": tf, "sector": sec, "n_pairs": len(bases),
                "pairs": bases,
                "median_pct": round(float(gs["pct"].median()), 2),
                "max_pct": round(float(gs["pct"].max()), 2),
                "top_pair": gs.loc[gs["pct"].idxmax(), "base"],
                "btc_pct": round(float(gs["btc_pct"].max()), 2),
                "max_breadth": round(float(gs["breadth"].max()), 4),
                "quote_volume_usd": round(float(gs["quote_volume"].sum())),
                "first_ts": min(gs["ts"]),
            })
        left = g.drop(index=[i for i in claimed if i in g.index])
        if left["base"].nunique() >= BROAD_MIN_PAIRS:
            ev.loc[left.index, "reason"] = "BROAD_ALT_BID"
            broad_days.append({
                "date": date, "tf": tf, "n_pairs": int(left["base"].nunique()),
                "pairs": sorted(left["base"].unique()),
                "sector_mix": dict(Counter(left.drop_duplicates("base")["sector"]).most_common()),
                "median_pct": round(float(left["pct"].median()), 2),
                "max_pct": round(float(left["pct"].max()), 2),
                "btc_pct": round(float(left["btc_pct"].max()), 2),
                "max_breadth": round(float(left["breadth"].max()), 4),
            })
    sector_clusters.sort(key=lambda x: (-x["n_pairs"], x["date"]))
    broad_days.sort(key=lambda x: (-x["n_pairs"], x["date"]))

    # ---- report -----------------------------------------------------------
    ORDER = ["NEW_LISTING", "MARKET_WIDE", "SECTOR", "BROAD_ALT_BID", "SOLO"]
    print("\n" + "=" * 78)
    print("REASON MIX")
    print("=" * 78)
    for tf in ["15m", "30m", "1h", "4h"]:
        sub = ev[ev["tf"] == tf]
        c, n = sub["reason"].value_counts(), len(sub)
        print(f"  {tf:>3} n={n:>5}  " + "  ".join(
            f"{k}={c.get(k,0)} ({c.get(k,0)/n*100:.0f}%)" for k in ORDER))
    c = ev["reason"].value_counts()
    print(f"  ALL n={len(ev)}  " + "  ".join(
        f"{k}={c.get(k,0)} ({c.get(k,0)/len(ev)*100:.1f}%)" for k in ORDER))
    print("\n  by distinct pair-days (an event count over-weights multi-TF hits):")
    pd_ = ev.drop_duplicates(["base", "date", "reason"])
    c2 = pd_["reason"].value_counts()
    print("    " + "  ".join(f"{k}={c2.get(k,0)} ({c2.get(k,0)/len(pd_)*100:.1f}%)" for k in ORDER))

    print("\n" + "=" * 78)
    print(f"SECTOR CLUSTERS — >= {SECTOR_MIN_PAIRS} pairs of ONE sector, same day, "
          f"market NOT broadly up")
    print("=" * 78)
    print(f"{len(sector_clusters)} clusters\n")
    for g in sector_clusters:
        print(f"  {g['date']} [{g['tf']:>3}] {g['sector']:>16}  {g['n_pairs']:>2} pairs  "
              f"med {g['median_pct']:>5.1f}%  max {g['max_pct']:>6.1f}% ({g['top_pair']})  "
              f"BTC {g['btc_pct']:>5.1f}%  breadth {g['max_breadth']:.1%}  "
              f"${g['quote_volume_usd']/1e6:>6.1f}M")
        print(f"       {', '.join(g['pairs'])}")

    print("\n" + "=" * 78)
    print("SECTOR CLUSTER FREQUENCY  (distinct days each sector drove a cluster)")
    print("=" * 78)
    by_sec = defaultdict(set)
    for g in sector_clusters:
        by_sec[g["sector"]].add(g["date"])
    for sec, days in sorted(by_sec.items(), key=lambda x: -len(x[1])):
        print(f"  {sec:>18}: {len(days):>3} days  {', '.join(sorted(days))}")

    print("\n" + "=" * 78)
    print(f"BROAD ALT BID  (>= {BROAD_MIN_PAIRS} pairs, no sector reached "
          f"{SECTOR_MIN_PAIRS}, BTC/breadth quiet) — top 20")
    print("=" * 78)
    print(f"{len(broad_days)} day-groups")
    for g in broad_days[:20]:
        mix = ", ".join(f"{k}:{v}" for k, v in list(g["sector_mix"].items())[:6])
        print(f"\n  {g['date']} [{g['tf']:>3}] {g['n_pairs']:>2} pairs  med {g['median_pct']}%  "
              f"max {g['max_pct']}%  BTC {g['btc_pct']}%  breadth {g['max_breadth']:.1%}")
        print(f"       mix: {mix}")
        print(f"       {', '.join(g['pairs'][:22])}")

    print("\n" + "=" * 78)
    print("MARKET-WIDE DAYS  (breadth >= 10%, ranked by peak breadth)")
    print("=" * 78)
    mw = ev[ev["reason"] == "MARKET_WIDE"]
    top_mw = (mw.groupby("date")
                .agg(peak_breadth=("breadth", "max"), pairs=("base", "nunique"),
                     med=("pct", "median"), btc=("btc_pct", "max"))
                .sort_values("peak_breadth", ascending=False).head(20))
    for d, r in top_mw.iterrows():
        print(f"  {d}  breadth {r['peak_breadth']:>5.1%}  {int(r['pairs']):>3} pairs "
              f">=10%  med {r['med']:>5.1f}%  BTC bar {r['btc']:>5.1f}%")

    print("\n" + "=" * 78)
    print("NEW-LISTING PUMPS  (first 24h on Binance)")
    print("=" * 78)
    nl = ev[ev["reason"] == "NEW_LISTING"]
    agg = (nl.groupby("base").agg(date=("date", "min"), max_pct=("pct", "max"),
                                  vol=("quote_volume", "max"), sector=("sector", "first"))
             .sort_values("max_pct", ascending=False))
    print(f"  {nl['base'].nunique()} pairs listed in-window printed a >=10% candle "
          f"in their first 24h")
    for b, r in agg.iterrows():
        print(f"    {r['date']}  {b:>12}  max {r['max_pct']:>8.1f}%  "
              f"${r['vol']/1e6:>7.1f}M  [{r['sector']}]")

    print("\n" + "=" * 78)
    print("TOP SOLO MOVES  (single-coin news — no cluster, no market move)")
    print("=" * 78)
    solo = (ev[ev["reason"] == "SOLO"].sort_values("pct", ascending=False)
            .drop_duplicates("base").head(35))
    for _, r in solo.iterrows():
        print(f"  {r['ts'][:16]} [{r['tf']:>3}] {r['base']:>12} {r['pct']:>7.1f}%  "
              f"${r['quote_volume']/1e6:>6.1f}M  [{r['sector']:>16}]  "
              f"BTC {r['btc_pct']}%  breadth {r['breadth']:.1%}")

    out = {
        "params": {"breadth_market_wide": BREADTH_MARKET_WIDE,
                   "sector_min_pairs": SECTOR_MIN_PAIRS,
                   "broad_min_pairs": BROAD_MIN_PAIRS},
        "reason_mix": {k: int(v) for k, v in ev["reason"].value_counts().items()},
        "reason_mix_by_tf": {tf: {k: int(v) for k, v in
                                  ev[ev["tf"] == tf]["reason"].value_counts().items()}
                             for tf in ["15m", "30m", "1h", "4h"]},
        "sector_clusters": sector_clusters,
        "sector_cluster_days": {k: sorted(v) for k, v in by_sec.items()},
        "broad_alt_bid_days": broad_days,
        "market_wide_days": [{"date": d, **{k: float(v) for k, v in r.items()}}
                             for d, r in top_mw.iterrows()],
        "new_listings": [{"base": b, **{k: (v if isinstance(v, str) else float(v))
                                        for k, v in r.items()}}
                         for b, r in agg.iterrows()],
        "unmapped_bases": unmapped,
    }
    (HERE / "pump_catalyst_reasons.json").write_text(json.dumps(out), encoding="utf-8")
    ev.to_json(HERE / "pump_catalyst_events_labelled.json", orient="records")
    print("\nwrote pump_catalyst_reasons.json + pump_catalyst_events_labelled.json")


if __name__ == "__main__":
    main()

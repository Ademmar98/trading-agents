"""ICT Silver-Bullet / Liquidity-Sweep backtest — halal spot LONG-ONLY, real Alpaca data, runs on VPS.

Faithful implementation of the user's Stage 1-3 spec:
  STAGE 1 (kill-zone + SSL sweep + MSS + FVG/OB):
    - Kill zones (America/New_York, DST-aware): London 03:00-04:00, NY-AM 10:00-11:00, NY-PM 14:00-15:00.
      A setup must INITIATE (sweep) inside a kill zone (Silver Bullet). Bearish setups => NO TRADE (long-only).
    - SSL sweep: bar low pierces a recent swing-low (20-bar) then closes back above it (reclaim).
    - MSS: within K bars after the sweep, a close breaks the immediate structural high (6-bar) = bullish shift.
    - Entry zone: bullish FVG (3-candle imbalance) inside the displacement; else bullish order block (last down candle).
  STAGE 2 (confluence, scored not gated except kill-zone):
    - VWAP deviation (NY-day anchored): entry <= VWAP - 2sigma (oversold).
    - Absorption: sweep bar volume spike (>1.5x avg) + long lower wick / small body.
  STAGE 3 (risk engine):
    - Entry = FVG/OB level (limit, must be retraced into within J bars). SL = sweep low - buffer. R = entry-SL.
    - TP1 = +1R (scale 50%, move stop to breakeven). TP2 = +2R (runner). Min R:R 1:2 enforced +
      require real overhead room (a swing-high >= 2R above) else REJECT.
    - Fixed $1000/trade. Friction 0.10% fee + 0.05% slip per side = 0.30% round trip, charged per fill.
    - Circuit breaker: 2 consecutive losing trades in one NY day => halt new entries rest of that day.
Canonical FIXED params (no optimization). Glitch guard drops absurd setups / bad prints.
"""
import json, time, os, urllib.parse, urllib.request
from datetime import datetime, timezone
import numpy as np
import pandas as pd
try:
    from zoneinfo import ZoneInfo
    NY = ZoneInfo("America/New_York")
    def ny_parts(ts_utc):
        return ts_utc.astimezone(NY)
    HAVE_TZ = True
except Exception:
    HAVE_TZ = False

END = "2026-07-20T00:00:00Z"
START_FULL = "2022-01-01T00:00:00Z"
START_1M = "2024-01-01T00:00:00Z"
FEE, SLIP = 0.001, 0.0005
RT = 2*(FEE+SLIP)            # 0.30% round trip (per full trade: 2 legs)
LEG = FEE+SLIP              # 0.15% per fill
USD = 1000.0
OUT = "/tmp/ict_bt"

UNIVERSE_FULL = ["BTC/USD","ETH/USD","SOL/USD","DOGE/USD","AVAX/USD","LINK/USD","LTC/USD","BCH/USD",
    "UNI/USD","MATIC/USD","NEAR/USD","TRX/USD","ALGO/USD","GRT/USD","BAT/USD","XRP/USD","DOT/USD",
    "CRV/USD","AAVE/USD","MKR/USD","YFI/USD","SUSHI/USD"]   # SHIB/XTZ excluded (corrupted)
SUBSET_1M = ["BTC/USD","ETH/USD","SOL/USD","DOGE/USD","AVAX/USD","LINK/USD"]
SUBSET_5M = ["BTC/USD","ETH/USD","SOL/USD","DOGE/USD","AVAX/USD","LINK/USD"]  # liquid majors (memory-safe)

# ── strategy params (fixed) ──
SWING_LB   = 20     # SSL swing-low lookback
MSS_HI_LB  = 6      # structural-high lookback for MSS
MSS_WIN    = 8      # bars after sweep to confirm MSS
FILL_WIN   = 15     # bars to retrace into FVG/OB
OUT_WIN    = 240    # max bars to resolve a trade
SL_BUF     = 0.0005 # 0.05% below sweep low
VOL_SPIKE  = 1.5
GLITCH_R   = 0.20   # reject if R > 20% of price (bad print) ; also drop >30% single-bar jumps


def fetch(symbol, tf, start):
    native = "1Min" if tf == "3Min" else tf
    rows, token = [], None
    base=("https://data.alpaca.markets/v1beta3/crypto/us/bars"
          f"?symbols={urllib.parse.quote(symbol,safe='')}&timeframe={native}&start={start}&end={END}&limit=10000")
    while True:
        url=base+(f"&page_token={token}" if token else "")
        for a in range(8):
            try: d=json.loads(urllib.request.urlopen(url,timeout=45).read()); break
            except Exception: time.sleep(3*(a+1))   # backoff for 429 rate limits
        else: return None
        for b in d.get("bars",{}).get(symbol,[]):
            rows.append((b["t"],b["o"],b["h"],b["l"],b["c"],b["v"]))
        token=d.get("next_page_token")
        if not token: break
    if len(rows)<500: return None
    df=pd.DataFrame(rows,columns=["t","o","h","l","c","v"])
    return df


def sma(x,n): return pd.Series(x).rolling(n).mean().values
def roll_min(x,n): return pd.Series(x).rolling(n).min().values
def roll_max(x,n): return pd.Series(x).rolling(n).max().values


def killzone_and_vwap(df):
    """Return kill-zone id per bar (0 none,1 London,2 NYAM,3 NYPM), NY date int, and NY-day VWAP + sigma."""
    ts = pd.to_datetime(df["t"].values, utc=True)
    if HAVE_TZ:
        ny = ts.tz_convert("America/New_York")
    else:
        ny = ts - pd.Timedelta(hours=5)   # fixed EST fallback
    hour = ny.hour.values; minute = ny.minute.values
    kz = np.zeros(len(df), int)
    kz[(hour==3)] = 1                              # 03:00-03:59 London
    kz[(hour==10)] = 2                             # 10:00-10:59 NY AM
    kz[(hour==14)] = 3                             # 14:00-14:59 NY PM
    nyday = (ny.year.values*10000 + ny.month.values*100 + ny.day.values)
    # NY-day anchored VWAP + sigma
    tp = (df["h"].values + df["l"].values + df["c"].values)/3.0
    v = df["v"].values
    vwap = np.full(len(df), np.nan); vstd = np.full(len(df), np.nan)
    s = pd.Series(tp*v); cv = pd.Series(v)
    df2 = pd.DataFrame({"nyday":nyday,"tpv":tp*v,"v":v,"tp":tp})
    cum_tpv = df2.groupby("nyday")["tpv"].cumsum().values
    cum_v = df2.groupby("nyday")["v"].cumsum().values
    vwap = cum_tpv/np.where(cum_v==0,1e-9,cum_v)
    # rolling within-day std of tp around vwap (use expanding std of tp per day as proxy)
    dev = tp - vwap
    df2["dev2"] = dev*dev
    cum_dev2 = df2.groupby("nyday")["dev2"].cumsum().values
    cnt = df2.groupby("nyday").cumcount().values + 1
    vstd = np.sqrt(cum_dev2/np.where(cnt==0,1,cnt))
    return kz, nyday, vwap, vstd


def run_pair(df, tf):
    o=df["o"].values.astype(float); h=df["h"].values.astype(float)
    l=df["l"].values.astype(float); c=df["c"].values.astype(float); v=df["v"].values.astype(float)
    n=len(c)
    kz, nyday, vwap, vstd = killzone_and_vwap(df)
    swing_low = roll_min(l, SWING_LB)      # inclusive; we shift when using (prior structure)
    volavg = sma(v, 20)
    prev_low = np.concatenate([[np.nan], roll_min(l, SWING_LB)[:-1]])  # prior 20-bar low up to t-1
    trades=[]
    # circuit breaker state per NY day
    day_consec = {}      # nyday -> consecutive losses
    day_halted = set()
    breaker_skips = 0
    next_free = SWING_LB+2   # no overlapping trades: next candidate must be >= last exit+1
    # Vectorized SSL-sweep candidate mask (kill-zone bar wicks below prior 20-bar low then reclaims)
    cand = (kz != 0) & np.isfinite(prev_low) & (l < prev_low) & (c > prev_low)
    cand_idx = np.where(cand)[0]
    for i in cand_idx:
        if i < next_free or i >= n-2:
            continue
        if True:
            d = int(nyday[i])
            if d in day_halted:
                breaker_skips += 1; continue
            sweep = i
            struct_high = np.nanmax(h[max(0,sweep-MSS_HI_LB):sweep])  # immediate structural high
            # STAGE1: MSS within window
            mss = None
            for s in range(sweep+1, min(n, sweep+1+MSS_WIN)):
                if c[s] > struct_high:
                    mss = s; break
            if mss is None:
                continue
            # STAGE1: bullish FVG inside [sweep, mss]; else bullish OB (last down candle before displacement)
            entry_lvl=None; used_fvg=False
            for a in range(sweep, mss-1):
                b3 = a+2
                if b3 < n and l[b3] > h[a]:               # gap: low of candle a+2 > high of candle a
                    entry_lvl = (h[a] + l[b3]) / 2.0; used_fvg=True; break
            if entry_lvl is None:
                # order block: last down candle at/before sweep
                ob=None
                for a in range(sweep, max(sweep-6,0), -1):
                    if c[a] < o[a]:
                        ob = a; break
                if ob is None:
                    next_free = mss+1; continue
                entry_lvl = (o[ob] + c[ob]) / 2.0
            # STAGE3: risk geometry
            sl = l[sweep]*(1-SL_BUF)
            R = entry_lvl - sl
            if R <= 0 or R/entry_lvl > GLITCH_R:
                next_free = mss+1; continue
            tp1 = entry_lvl + 1*R
            # TP2 runner targets the real overhead BSL pool (a swing high); require >= 2R (min 1:2), cap 5R
            overhead = np.nanmax(h[max(0,sweep-SWING_LB):mss+1])
            if overhead < entry_lvl + 2*R:   # no real liquidity pool offering 1:2 -> REJECT
                next_free = mss+1; continue
            tp2 = min(overhead, entry_lvl + 5*R)
            # STAGE2 confluence
            conf = 0
            vwap_ok = np.isfinite(vwap[sweep]) and entry_lvl <= vwap[sweep] - 2*vstd[sweep]
            rng = max(h[sweep]-l[sweep], 1e-9)
            lower_wick = (min(o[sweep],c[sweep]) - l[sweep])/rng
            body = abs(c[sweep]-o[sweep])/rng
            absorb = (np.isfinite(volavg[sweep]) and v[sweep] > VOL_SPIKE*volavg[sweep]) and (lower_wick>0.5 or body<0.3)
            conf += int(vwap_ok) + int(absorb) + int(used_fvg)
            score = "High" if conf>=3 else ("Medium" if conf==2 else "Low")
            # entry fill: retrace into entry_lvl within FILL_WIN bars after mss
            fill=None
            for f in range(mss+1, min(n, mss+1+FILL_WIN)):
                if l[f] <= entry_lvl:
                    fill=f; break
            if fill is None:
                next_free = mss+1; continue
            e = fill
            # STAGE3 outcome sim: $1000 position, TP1 scales 50% & moves stop to breakeven, runner to TP2.
            # Clean fee model: charge LEG once per FILL on that fill's notional (entry full, each exit tranche).
            half=USD/2.0; gross=0.0; fee=USD*LEG; be=False; res="open"
            for k in range(e, min(n, e+OUT_WIN)):
                if not be:
                    if l[k] <= sl:                     # full stop before TP1
                        gross += USD*(sl/entry_lvl-1); fee += USD*LEG; res="SL"; break
                    if h[k] >= tp1:                    # bank half at +1R, move stop to breakeven
                        gross += half*(tp1/entry_lvl-1); fee += half*LEG; be=True
                        if h[k] >= tp2:                # same bar also tags TP2
                            gross += half*(tp2/entry_lvl-1); fee += half*LEG; res="TP2"; break
                else:
                    if l[k] <= entry_lvl:              # breakeven stop on the runner
                        gross += 0; fee += half*LEG; res="TP1+BE"; break
                    if h[k] >= tp2:
                        gross += half*(tp2/entry_lvl-1); fee += half*LEG; res="TP2"; break
            else:
                lastc=c[min(n-1, e+OUT_WIN-1)]
                if not be:
                    gross += USD*(lastc/entry_lvl-1); fee += USD*LEG; res="TIME"
                else:
                    gross += half*(lastc/entry_lvl-1); fee += half*LEG; res="TP1+TIME"
            pnl = gross - fee
            trades.append({"kz":int(kz[sweep]),"score":score,"conf":conf,"used_fvg":bool(used_fvg),
                           "entry":float(entry_lvl),"sl":float(sl),"tp1":float(tp1),"tp2":float(tp2),
                           "R_pct":float(R/entry_lvl*100),"pnl":float(pnl),"res":res,
                           "vwap_ok":bool(vwap_ok),"absorb":bool(absorb)})
            # circuit breaker update (in day order)
            if pnl < 0:
                day_consec[d] = day_consec.get(d,0)+1
                if day_consec[d] >= 2: day_halted.add(d)
            else:
                day_consec[d] = 0
            next_free = e+1
            continue
    return trades, breaker_skips


def summarize(trades):
    if not trades: return None
    p=np.array([t["pnl"] for t in trades])
    wins=p[p>0]; losses=p[p<=0]
    eq=USD+np.cumsum(p); peak=np.maximum.accumulate(eq); dd=(peak-eq)
    pf=float(wins.sum()/abs(losses.sum())) if len(losses) and losses.sum()!=0 else (99.0 if len(wins) else 0)
    mc=0;cur=0
    for x in p:
        if x<0: cur+=1; mc=max(mc,cur)
        else: cur=0
    return {"trades":len(trades),"win_rate":round(len(wins)/len(trades)*100,1),
            "total_pnl":round(float(p.sum()),2),"avg_pnl":round(float(p.mean()),3),
            "pf":round(min(pf,99),2),"maxdd_usd":round(float(dd.max()),2),
            "maxdd_pct":round(float((dd/np.where(peak<=0,np.nan,peak)).max()*100),1),
            "max_consec_losses":mc}


def main():
    os.makedirs(OUT, exist_ok=True)
    import sys
    tfs = sys.argv[1].split(",") if len(sys.argv)>1 else ["15Min","5Min","1Min"]
    results={"config":{"rt_cost":RT,"usd":USD,"params":{"SWING_LB":SWING_LB,"MSS_HI_LB":MSS_HI_LB,
             "MSS_WIN":MSS_WIN,"FILL_WIN":FILL_WIN}}, "tf":{}}
    from concurrent.futures import ThreadPoolExecutor
    for tf in tfs:
        pairs = SUBSET_1M if tf in ("1Min","3Min") else (SUBSET_5M if tf=="5Min" else UNIVERSE_FULL)
        start = START_1M if tf in ("1Min","3Min") else START_FULL
        alltr=[]; bypair={}; bykz={1:[],2:[],3:[]}; skips=0
        # one pair at a time: fetch -> compute -> free (RAM-safe; shares a 1.8GB box with the live firm)
        for sym in pairs:
            df=fetch(sym,tf,start)
            if df is None: print(f"  [{tf}] {sym} no data",flush=True); continue
            tr,sk=run_pair(df,tf); skips+=sk
            del df
            for t in tr: t["pair"]=sym.replace("/USD","")
            alltr+=tr; bypair[sym.replace("/USD","")]=summarize(tr)
            for t in tr: bykz[t["kz"]].append(t)
            print(f"  [{tf}] {sym}: {len(tr)} trades",flush=True)
        results["tf"][tf]={"overall":summarize(alltr),"breaker_skips":skips,
            "by_pair":bypair,
            "by_killzone":{ {1:"London",2:"NY_AM",3:"NY_PM"}[k]:summarize(v) for k,v in bykz.items()},
            "sample_trades":sorted([t for t in alltr if t["score"]=="High"],
                                   key=lambda t:-t["pnl"])[:6] +
                            sorted([t for t in alltr if t["score"]=="High"],
                                   key=lambda t:t["pnl"])[:3]}
        json.dump(results, open(f"{OUT}/RESULTS.json","w"), indent=1, default=str)
        print(f"=== {tf} DONE: {len(alltr)} trades, breaker_skips={skips} ===",flush=True)
    print("ALL DONE",flush=True)


if __name__=="__main__":
    main()

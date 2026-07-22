"""Halal spot LONG-ONLY swing backtest — 1H/4H, real Alpaca data, runs on VPS.
Three strategies (A: MTF trend+VWAP/EMA pullback [1H exec, 4H filter];
B: 4H MSS + 1H FVG retest [1H exec]; C: 4H Donchian breakout + SuperTrend trail [4H exec]).
Fixed $1000/trade. Min initial R:R 2.5 (A,B). Friction run BOTH ways: maker 0.14% and taker 0.30% RT.
CRITICAL honesty: buy-and-hold benchmark per pair (long-only trend in a 2022-26 bull = mostly beta;
the real test is whether it BEATS holding). Canonical fixed params, no optimization.
"""
import json, time, os, urllib.parse, urllib.request
import numpy as np, pandas as pd

END="2026-07-20T00:00:00Z"; START="2022-01-01T00:00:00Z"
USD=1000.0
FRIC={"maker_0.14":0.0014, "taker_0.30":0.0030}   # round-trip
OUT="/tmp/swing_bt"
UNIVERSE=["BTC/USD","ETH/USD","SOL/USD","DOGE/USD","AVAX/USD","LINK/USD","LTC/USD","BCH/USD",
 "UNI/USD","MATIC/USD","NEAR/USD","TRX/USD","ALGO/USD","GRT/USD","BAT/USD","XRP/USD","DOT/USD",
 "CRV/USD","AAVE/USD","MKR/USD","YFI/USD","SUSHI/USD"]   # 22 clean (SHIB/XTZ excluded)
MIN_RR=2.5

def fetch_1h(sym):
    cache=f"/tmp/swing_cache/{sym.replace('/','_')}.pkl"
    if os.path.exists(cache):
        try: return pd.read_pickle(cache)
        except Exception: pass
    rows,token=[],None
    base=("https://data.alpaca.markets/v1beta3/crypto/us/bars"
          f"?symbols={urllib.parse.quote(sym,safe='')}&timeframe=1Hour&start={START}&end={END}&limit=10000")
    while True:
        url=base+(f"&page_token={token}" if token else "")
        for a in range(8):
            try: d=json.loads(urllib.request.urlopen(url,timeout=45).read()); break
            except Exception: time.sleep(3*(a+1))
        else: return None
        for b in d.get("bars",{}).get(sym,[]): rows.append((b["t"],b["o"],b["h"],b["l"],b["c"],b["v"]))
        token=d.get("next_page_token")
        if not token: break
    if len(rows)<500: return None
    df=pd.DataFrame(rows,columns=["t","o","h","l","c","v"]); df["t"]=pd.to_datetime(df["t"],utc=True)
    os.makedirs("/tmp/swing_cache",exist_ok=True)
    try: df.to_pickle(cache)
    except Exception: pass
    return df

def resample(df,rule):
    r=df.set_index("t").resample(rule).agg({"o":"first","h":"max","l":"min","c":"last","v":"sum"}).dropna().reset_index()
    return r

def ema(x,n): return pd.Series(x).ewm(span=n,adjust=False).mean().values
def sma(x,n): return pd.Series(x).rolling(n).mean().values
def rsi(x,n=14):
    d=np.diff(x,prepend=x[0]); up=np.clip(d,0,None); dn=np.clip(-d,0,None)
    ru=pd.Series(up).ewm(alpha=1/n,adjust=False).mean().values; rd=pd.Series(dn).ewm(alpha=1/n,adjust=False).mean().values
    return 100-100/(1+ru/np.where(rd==0,1e-9,rd))
def atr(h,l,c,n=14):
    pc=np.roll(c,1); pc[0]=c[0]; tr=np.maximum(h-l,np.maximum(np.abs(h-pc),np.abs(l-pc)))
    return pd.Series(tr).ewm(alpha=1/n,adjust=False).mean().values
def rmax(x,n): return pd.Series(x).rolling(n).max().values
def rmin(x,n): return pd.Series(x).rolling(n).min().values

def anchored_vwap_1h(df1):
    ts=df1["t"].dt; day=(ts.year*10000+ts.month*100+ts.day).values
    tp=(df1["h"].values+df1["l"].values+df1["c"].values)/3.0; v=df1["v"].values
    d=pd.DataFrame({"day":day,"tpv":tp*v,"v":v})
    return d.groupby("day")["tpv"].cumsum().values/np.where(d.groupby("day")["v"].cumsum().values==0,1e-9,d.groupby("day")["v"].cumsum().values)

def map_4h_to_1h(df1, series4, df4):
    """Forward-fill a 4H series onto the 1H index using ONLY the last FULLY-CLOSED 4H bar
    (no lookahead). Bar j spans [t4[j], t4[j+1]); it is knowable only at 1H times >= t4[j+1].
    So at t1[i] the forming bar is the largest j with t4[j] <= t1[i]; the last CLOSED bar is j-1."""
    t4=df4["t"].values; out=np.full(len(df1), np.nan)
    j=-1; t1=df1["t"].values
    for i in range(len(df1)):
        while j+1<len(t4) and t4[j+1]<=t1[i]: j+=1
        if j>=1: out[i]=series4[j-1]     # last CLOSED 4H bar (j is the forming/unclosed one)
    return out

def simulate(c,h,l, e_idx, entry, sl, tp1, tp2, rt, trail_atr=None):
    """Generic long outcome: TP1 scales 50% & SL->BE, runner to TP2. trail_atr (array) => trailing stop mode (C)."""
    half=USD/2.0; gross=0.0; fee=USD*(rt/2); be=False; res="open"; n=len(c); exit_i=e_idx
    s0=e_idx+1   # outcome starts the bar AFTER entry (no same-bar lookahead)
    if trail_atr is not None:
        # trailing: exit when close < trailing stop; stop = max so far of (running_high - k*ATR) ratchet via supertrend proxy
        stop=sl; peak=entry
        for k in range(s0, n):
            exit_i=k
            peak=max(peak,h[k]); stop=max(stop, peak-trail_atr[k])   # ratchet up
            if l[k]<=stop:
                gross+=USD*(stop/entry-1); fee+=USD*(rt/2); res="TRAIL"; break
        else:
            gross+=USD*(c[n-1]/entry-1); fee+=USD*(rt/2); res="END"
        return gross-fee, exit_i, res
    for k in range(s0, n):
        exit_i=k
        if not be:
            if l[k]<=sl: gross+=USD*(sl/entry-1); fee+=USD*(rt/2); res="SL"; break
            if h[k]>=tp1:
                gross+=half*(tp1/entry-1); fee+=half*(rt/2); be=True
                if h[k]>=tp2: gross+=half*(tp2/entry-1); fee+=half*(rt/2); res="TP2"; break
        else:
            if l[k]<=entry: gross+=0; fee+=half*(rt/2); res="TP1+BE"; break
            if h[k]>=tp2: gross+=half*(tp2/entry-1); fee+=half*(rt/2); res="TP2"; break
    else:
        lastc=c[n-1]
        if not be: gross+=USD*(lastc/entry-1); fee+=USD*(rt/2); res="END"
        else: gross+=half*(lastc/entry-1); fee+=half*(rt/2); res="TP1+END"
    return gross-fee, exit_i, res

def run_strategy(strat, df1, df4, rt, tf_hours_map):
    c1=df1["c"].values; h1=df1["h"].values; l1=df1["l"].values; v1=df1["v"].values
    c4=df4["c"].values; h4=df4["h"].values; l4=df4["l"].values; v4=df4["v"].values
    trades=[]
    if strat=="C":
        # 4H Donchian breakout + SuperTrend/ATR trail
        dch=rmax(np.roll(h4,1),20); vol_sma=sma(v4,20); a4=atr(h4,l4,c4,10)
        trail=3.0*a4  # SuperTrend-style ATR trail width
        i=25; n=len(c4)
        while i<n-1:
            if np.isfinite(dch[i]) and c4[i]>dch[i] and np.isfinite(vol_sma[i]) and v4[i]>1.5*vol_sma[i]:
                entry=c4[i]; sl=entry-trail[i]
                pnl,exit_i,res=simulate(c4,h4,l4,i,entry,sl,0,0,rt,trail_atr=trail)
                trades.append({"pnl":pnl,"ret":pnl/USD,"hold_h":(exit_i-i)*tf_hours_map["4H"],"res":res})
                i=exit_i+1; continue
            i+=1
        return trades
    # A & B run on 1H with 4H context
    ema50_4=ema(c4,50); ema200_4=ema(c4,200)
    bull4=(c4>ema50_4)&(c4>ema200_4)
    bull_on1=map_4h_to_1h(df1, bull4.astype(float), df4)
    sh4_on1=map_4h_to_1h(df1, rmax(h4,10), df4)     # prior 4H swing-high pool
    r1=rsi(c1,14); e20=ema(c1,20); vwap=anchored_vwap_1h(df1)
    priorhigh4=rmax(np.roll(h4,1),10)               # for MSS
    mss4=(c4>priorhigh4)
    mss_on1=map_4h_to_1h(df1, mss4.astype(float), df4)
    dispRlow4=rmin(l4,3)                              # displacement swing low proxy
    displow_on1=map_4h_to_1h(df1, dispRlow4, df4)
    n=len(c1); i=30
    while i<n-1:
        if strat=="A":
            if bull_on1[i]==1.0:
                touched=(l1[i]<=vwap[i]) or (l1[i]<=e20[i])
                reclaim=c1[i]>max(vwap[i],e20[i])
                if touched and reclaim and r1[i]>40:
                    entry=c1[i]; sl=float(np.min(l1[max(0,i-10):i+1]))*0.999   # recent 1H structural swing low
                    R=entry-sl
                    if R>0:
                        pool=sh4_on1[i]
                        if np.isfinite(pool) and pool>=entry+MIN_RR*R:
                            tp1=min(pool, entry*1.05);
                            if tp1<entry+MIN_RR*R: tp1=entry+MIN_RR*R
                            tp2=max(entry*1.08, pool)
                            pnl,exit_i,res=simulate(c1,h1,l1,i,entry,sl,tp1,tp2,rt)
                            trades.append({"pnl":pnl,"ret":pnl/USD,"hold_h":(exit_i-i)*tf_hours_map["1H"],"res":res})
                            i=exit_i+1; continue
        elif strat=="B":
            if mss_on1[i]==1.0:
                # find 1H bullish FVG in the recent displacement (last ~6 1H bars)
                entry=None
                for a in range(max(0,i-6),i-1):
                    if a+2<=i and l1[a+2]>h1[a]:
                        entry=(h1[a]+l1[a+2])/2.0; break
                if entry is not None and l1[i]<=entry:   # price retraced into FVG this bar
                    disp_low=displow_on1[i]
                    sl=(disp_low if np.isfinite(disp_low) else float(np.min(l1[max(0,i-6):i+1])))*0.999
                    R=entry-sl
                    if R>0:
                        pool=sh4_on1[i]
                        tp1=entry+2*R
                        tp2=pool if (np.isfinite(pool) and pool>=entry+MIN_RR*R) else entry+3*R
                        if tp2>=entry+MIN_RR*R:
                            pnl,exit_i,res=simulate(c1,h1,l1,i,entry,sl,tp1,tp2,rt)
                            trades.append({"pnl":pnl,"ret":pnl/USD,"hold_h":(exit_i-i)*tf_hours_map["1H"],"res":res})
                            i=exit_i+1; continue
        i+=1
    return trades

def summarize(trades):
    if not trades: return None
    p=np.array([t["pnl"] for t in trades]); wins=p[p>0]; losses=p[p<=0]
    eq=USD+np.cumsum(p); peak=np.maximum.accumulate(eq); dd=peak-eq
    pf=float(wins.sum()/abs(losses.sum())) if len(losses) and losses.sum()!=0 else (99.0 if len(wins) else 0)
    mc=0;cur=0
    for x in p:
        if x<0: cur+=1; mc=max(mc,cur)
        else: cur=0
    return {"trades":len(trades),"win_rate":round(len(wins)/len(trades)*100,1),
            "total_pnl":round(float(p.sum()),2),"avg_pnl":round(float(p.mean()),3),
            "avg_ret_pct":round(float(np.mean([t["ret"] for t in trades]))*100,3),
            "total_ret_pct":round(float(p.sum()/USD)*100,1),
            "avg_hold_h":round(float(np.mean([t["hold_h"] for t in trades])),1),
            "pf":round(min(pf,99),2),"maxdd_usd":round(float(dd.max()),2),"max_consec_losses":mc}

def main():
    os.makedirs(OUT,exist_ok=True)
    tf_hours={"1H":1,"4H":4}
    res={"config":{"usd":USD,"friction":FRIC,"min_rr":MIN_RR,"universe":UNIVERSE},
         "strategies":{}, "buy_hold":{}}
    strat_tf={"A":"1H","B":"1H","C":"4H"}
    data={}
    for sym in UNIVERSE:
        df1=fetch_1h(sym)
        if df1 is None: print(f"  {sym} no data",flush=True); continue
        df4=resample(df1,"4h")
        data[sym]=(df1,df4)
        # buy-hold benchmark
        hold=float(df1["c"].values[-1]/df1["c"].values[0]-1)
        res["buy_hold"][sym.replace("/USD","")]={"hold_ret_pct":round(hold*100,1),"hold_pnl_1000":round(hold*USD,2),
                                                 "bars_1h":len(df1)}
        print(f"  fetched {sym}: 1H={len(df1)} 4H={len(df4)} hold={hold*100:+.0f}%",flush=True)
    for strat in ["A","B","C"]:
        for fname,rt in FRIC.items():
            alltr=[]; bypair={}
            for sym,(df1,df4) in data.items():
                tr=run_strategy(strat,df1,df4,rt,tf_hours)
                for t in tr: t["pair"]=sym.replace("/USD","")
                alltr+=tr; bypair[sym.replace("/USD","")]=summarize(tr)
            res["strategies"][f"{strat}_{fname}"]={"tf":strat_tf[strat],"overall":summarize(alltr),"by_pair":bypair}
            o=res["strategies"][f"{strat}_{fname}"]["overall"]
            print(f"  [{strat} {fname}] "+(f"tr={o['trades']} WR={o['win_rate']}% net=${o['total_pnl']} avg%={o['avg_ret_pct']} holdH={o['avg_hold_h']} PF={o['pf']}" if o else "no trades"),flush=True)
        json.dump(res,open(f"{OUT}/RESULTS.json","w"),indent=1,default=str)
    json.dump(res,open(f"{OUT}/RESULTS.json","w"),indent=1,default=str)
    print("ALL DONE",flush=True)

if __name__=="__main__": main()

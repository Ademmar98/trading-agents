"""Honest meta-analysis of the firm's real live trades (VPS backups).
Segments logical trades by asset / session / regime / vol-state / exit-reason,
computes per-segment WR/avgR/E[X]/maxDD WITH sample sizes, then runs a strict
chronological IS/OOS test + a bootstrap null on the best in-sample segment to
show whether any 'edge' survives or is small-sample noise. Anti-lookahead: daily
regime/vol context uses only the last CLOSED daily bar before each entry."""
import sqlite3, glob, json, urllib.request, urllib.parse, time, bisect
import numpy as np, pandas as pd

# ---------- 1. load logical trades (group scaled exits by position, dedupe across backups) ----------
dbs = sorted(glob.glob("/root/firm/data_backups/*/data/trading.db")+glob.glob("/root/firm/data_backups/*/trading.db"))
logical=[]; seen=set()
for db in dbs:
    try:
        con=sqlite3.connect(db); con.row_factory=sqlite3.Row
        q="""SELECT t.symbol AS symbol, MIN(t.opened_at) AS opened_at, MAX(t.closed_at) AS closed_at,
             SUM(t.pnl) AS pnl, SUM(ABS(t.qty)) AS qty, MAX(p.entry_price) AS pe,
             MAX(p.stop_loss) AS sl, MAX(p.take_profit) AS tp,
             GROUP_CONCAT(LOWER(COALESCE(t.reason,''))) AS reasons, MAX(t.entry_price) AS ep
             FROM trades t LEFT JOIN positions p ON p.id=t.position_id
             GROUP BY COALESCE(t.position_id, t.id)"""
        for r in con.execute(q):
            d=dict(r); k=(d["symbol"], round(d["pnl"] or 0,2), d["closed_at"], round(d["qty"] or 0,4))
            if k in seen: continue
            seen.add(k); logical.append(d)
        con.close()
    except Exception: pass

def outcome(reasons):
    reasons=reasons or ""
    if "stop" in reasons: return "stop_loss"
    if "take" in reasons or "tp" in reasons or "partial" in reasons: return "take_profit"
    if "trail" in reasons: return "trailing"
    if "time" in reasons: return "time_stop"
    return "other"

trades=[]
for d in logical:
    e = d["pe"] or d["ep"]; sl=d["sl"]; qty=d["qty"]
    risk = abs(e-sl)*qty if (e and sl and qty and abs(e-sl)>0) else None
    R = (d["pnl"]/risk) if risk and risk>0 else None
    t = d["opened_at"] or d["closed_at"]
    hour = int(t[11:13]) if t and len(t)>=13 else None
    date = t[:10] if t else None
    sess = None if hour is None else ("Asia" if hour<7 else "London" if hour<13 else "US" if hour<21 else "Late")
    trades.append({"symbol":d["symbol"],"asset":d["symbol"].replace("/USD",""),"pnl":d["pnl"] or 0,
        "R":R,"date":date,"hour":hour,"session":sess,"outcome":outcome(d["reasons"]),
        "entry":e,"stop":sl,"dt":t})
trades=[t for t in trades if t["date"]]
trades.sort(key=lambda x:x["dt"] or "")
print(f"logical trades: {len(trades)}", flush=True)

# ---------- 2. daily price context per symbol (regime + vol), anti-lookahead ----------
def fetch_daily(sym):
    rows,token=[],None
    base=("https://data.alpaca.markets/v1beta3/crypto/us/bars"
          f"?symbols={urllib.parse.quote(sym,safe='')}&timeframe=1Day&start=2021-06-01T00:00:00Z&end=2026-07-20T00:00:00Z&limit=10000")
    while True:
        url=base+(f"&page_token={token}" if token else "")
        for a in range(5):
            try: d=json.loads(urllib.request.urlopen(url,timeout=30).read()); break
            except Exception: time.sleep(2*(a+1))
        else: return None
        for b in d.get("bars",{}).get(sym,[]): rows.append((b["t"][:10],b["o"],b["h"],b["l"],b["c"]))
        token=d.get("next_page_token")
        if not token: break
    if len(rows)<210: return None
    a=np.array([r[1:] for r in rows],float); dates=[r[0] for r in rows]
    return dates,a[:,0],a[:,1],a[:,2],a[:,3]

def context(sym):
    got=fetch_daily(sym)
    if not got: return None,None
    dates,o,h,l,c=got
    sma200=pd.Series(c).rolling(200).mean().values
    pc=np.roll(c,1);pc[0]=c[0]
    tr=np.maximum(h-l,np.maximum(np.abs(h-pc),np.abs(l-pc)))
    atr=pd.Series(tr).ewm(alpha=1/14,adjust=False).mean().values
    atr30=pd.Series(atr).rolling(30).mean().values
    up=h-np.roll(h,1); dn=np.roll(l,1)-l
    pDM=np.where((up>dn)&(up>0),up,0.0); mDM=np.where((dn>up)&(dn>0),dn,0.0)
    atrw=pd.Series(tr).ewm(alpha=1/14,adjust=False).mean().values
    pDI=100*pd.Series(pDM).ewm(alpha=1/14,adjust=False).mean().values/np.where(atrw==0,1e-9,atrw)
    mDI=100*pd.Series(mDM).ewm(alpha=1/14,adjust=False).mean().values/np.where(atrw==0,1e-9,atrw)
    dx=100*np.abs(pDI-mDI)/np.where((pDI+mDI)==0,1e-9,pDI+mDI)
    adx=pd.Series(dx).ewm(alpha=1/14,adjust=False).mean().values
    ctx={}
    for i in range(1,len(c)):
        j=i-1  # last closed bar
        regime="trending" if (np.isfinite(adx[j]) and adx[j]>25) else "ranging"
        trend="up" if (np.isfinite(sma200[j]) and c[j]>sma200[j]) else "down"
        vs=("high" if (np.isfinite(atr30[j]) and atr[j]>1.2*atr30[j]) else
            "low" if (np.isfinite(atr30[j]) and atr[j]<0.8*atr30[j]) else "normal")
        ctx[dates[i]]={"regime":regime,"trend":trend,"vol":vs}
    return ctx, sorted(ctx.keys())

CTX={}
for sym in sorted(set(t["symbol"] for t in trades)):
    ctx,keys=context(sym)
    if ctx: CTX[sym]=(ctx,keys)
    print(f"ctx {sym}: {'ok' if ctx else 'no-data'}", flush=True)

for t in trades:
    pair=CTX.get(t["symbol"])
    if not pair: t["regime"]=t["trend"]=t["vol"]="unknown"; continue
    ctx,keys=pair
    i=bisect.bisect_right(keys,t["date"])-1   # nearest date <= entry
    if i>=0: info=ctx[keys[i]]; t.update(regime=info["regime"],trend=info["trend"],vol=info["vol"])
    else: t["regime"]=t["trend"]=t["vol"]="unknown"

# ---------- 3. segment metrics ----------
def metrics(sub):
    n=len(sub)
    if n==0: return None
    pnls=[x["pnl"] for x in sub]; Rs=[x["R"] for x in sub if x["R"] is not None]
    wins=[p for p in pnls if p>0]
    o=sorted(sub,key=lambda x:x["dt"] or ""); eq=0;peak=0;dd=0
    for x in o: eq+=x["pnl"];peak=max(peak,eq);dd=min(dd,eq-peak)
    return {"n":n,"win_rate":round(len(wins)/n*100,1),
            "avg_R":round(float(np.mean(Rs)),3) if Rs else None,"n_R":len(Rs),
            "exp_usd":round(float(np.mean(pnls)),2),"sum_usd":round(float(sum(pnls)),2),
            "maxdd_usd":round(dd,2)}

def by(axis):
    g={}
    for t in trades: g.setdefault(t.get(axis,"?"),[]).append(t)
    return {k:metrics(v) for k,v in sorted(g.items(),key=lambda kv:-(metrics(kv[1])["exp_usd"]))}

result={"overall":metrics(trades),
        "by_asset":by("asset"),"by_session":by("session"),"by_regime":by("regime"),
        "by_trend":by("trend"),"by_vol":by("vol"),"by_outcome":by("outcome")}

# two-way: regime x session (small cells expected)
tw={}
for t in trades:
    key=f'{t.get("regime","?")}|{t.get("session","?")}'
    tw.setdefault(key,[]).append(t)
result["regime_x_session"]={k:metrics(v) for k,v in tw.items()}

# ---------- 4. IS/OOS honesty test on the best in-sample single-axis filter ----------
split=int(len(trades)*0.70)
IS=trades[:split]; OOS=trades[split:]
# candidate filters: every value of each axis with >=6 IS trades; pick best IS expectancy
axes=["asset","session","regime","trend","vol","outcome"]
cands=[]
for ax in axes:
    vals=set(t.get(ax) for t in IS)
    for v in vals:
        sub=[t for t in IS if t.get(ax)==v]
        if len(sub)>=6:
            m=metrics(sub); cands.append((ax,v,m["exp_usd"],m))
cands.sort(key=lambda x:-x[2])
best=cands[0] if cands else None
oos_test=None
if best:
    ax,v,_,mIS=best
    subOOS=[t for t in OOS if t.get(ax)==v]
    mOOS=metrics(subOOS) if subOOS else {"n":0}
    # bootstrap null: is IS expectancy of this segment beyond chance vs random same-size IS subsets?
    rng=np.random.default_rng(7)
    isp=[t["pnl"] for t in IS]; k=mIS["n"]; obs=mIS["exp_usd"]
    boot=[float(np.mean(rng.choice(isp,k,replace=False))) for _ in range(2000)]
    pval=float(np.mean([b>=obs for b in boot]))
    oos_test={"filter":f"{ax}={v}","IS":mIS,"OOS":mOOS,
              "bootstrap_pctile":round((1-pval)*100,1),"bootstrap_p":round(pval,3)}
result["is_oos"]={"n_IS":len(IS),"n_OOS":len(OOS),"top_candidates":[(a,vv,e) for a,vv,e,_ in cands[:6]],"best_test":oos_test}

json.dump(result, open("/tmp/meta_analysis.json","w"), indent=1, default=str)
print("DONE trades=%d syms_ctx=%d" % (len(trades),len(CTX)), flush=True)

import json, time, math, requests
from collections import defaultdict
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

API = "https://api.hyperliquid.xyz/info"
retry = Retry(total=5, backoff_factor=1.5, status_forcelist=[429,500,502,503,504], allowed_methods=["POST","GET"])
s = requests.Session()
s.mount("https://", HTTPAdapter(max_retries=retry, pool_connections=1, pool_maxsize=1))

def get_fills(addr, start_ms, end_ms=None):
    end_ms = end_ms or int(time.time()*1000)
    out=[]; cursor=None
    for _ in range(80):
        body={"type":"userFillsByTime","user":addr,"startTime":start_ms,"endTime":end_ms}
        if cursor: body["cursor"]=cursor
        r=s.post(API,json=body,timeout=30)
        if r.status_code!=200: break
        d=r.json()
        if isinstance(d,list): out.extend(d); break
        f=d.get("fills",[])
        if not f: break
        out.extend(f)
        cursor=d.get("cursor")
        if not cursor: break
        time.sleep(0.2)
    return out

A="0x183d0567c33e7591c22540e45d2f74730b42a0ca"
fills=get_fills(A, int(time.time()*1000)-180*86400*1000)
fills.sort(key=lambda f:int(f["time"]))
print(f"fills={len(fills)}")

# pair by coin using dir Open/Close
cur={}
trips=[]
for f in fills:
    coin=f["coin"]; d=f["dir"]; px=float(f["px"]); t=int(f["time"]); cp=float(f.get("closedPnl","0") or 0)
    if "Open" in d:
        cur[coin]=(px,t)
    elif "Close" in d:
        o=cur.get(coin)
        if o:
            trips.append({"coin":coin,"in":o[0],"tin":o[1],"out":px,"tout":t,"cp":cp})
            cur.pop(coin,None)
        else:
            trips.append({"coin":coin,"in":px,"tin":t,"out":px,"tout":t,"cp":cp})

print(f"paired trips={len(trips)}")
wins=[t for t in trips if t["cp"]>0]; losses=[t for t in trips if t["cp"]<=0]
print(f"wins={len(wins)} losses={len(losses)} wr={len(wins)/max(1,len(trips)):.2%}")
if trips:
    holds=sorted((t["tout"]-t["tin"])/3600 for t in trips)
    print(f"hold hours med={holds[len(holds)//2]:.1f} p25={holds[len(holds)//4]:.1f} p75={holds[3*len(holds)//4]:.1f} max={holds[-1]:.1f}")
    rets=[(t["out"]/t["in"]-1)*100 for t in trips if t["in"]>0]
    rets.sort()
    print(f"round-trip % med={rets[len(rets)//2]:+.2f} p25={rets[len(rets)//4]:+.2f} p75={rets[3*len(rets)//4]:+.2f}")

coinc=defaultdict(int); coincp=defaultdict(float); coinh=defaultdict(list)
for t in trips:
    coinc[t["coin"]]+=1; coincp[t["coin"]]+=t["cp"]
    if t["cp"]>0: coinh[t["coin"]].append((t["in"],t["out"], (t["tout"]-t["tin"])/3600, t["cp"]))
print("\ntrip counts:", dict(sorted(coinc.items(),key=lambda x:-x[1])[:12]))
print("pnl by coin:", {k:round(v) for k,v in sorted(coincp.items(),key=lambda x:-x[1])[:10]})

print("\nwinning round-trips (coin, entry, exit, hold_h, pnl):")
n=0
for c,arr in coinh.items():
    for r in sorted(arr,key=lambda x:-x[3])[:6]:
        print(f"  {n+1:>3}) {c:<10} in={r[0]:<12} out={r[1]:<12} hold={r[2]:>7.1f}h  pnl={r[3]:>10.1f}")
        n+=1
    if n>25: break

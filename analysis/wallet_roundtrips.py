import json, time, math, requests
from collections import defaultdict, deque
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

API = "https://api.hyperliquid.xyz/info"
retry = Retry(total=5, backoff_factor=1.5, status_forcelist=[429,500,502,503,504], allowed_methods=["POST","GET"])
s = requests.Session()
s.mount("https://", HTTPAdapter(max_retries=retry, pool_connections=1, pool_maxsize=1))

def get_fills(addr, start_ms, end_ms=None):
    end_ms = end_ms or int(time.time()*1000)
    out=[]; cursor=None
    for _ in range(60):
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

# Top directional long wallet
A = "0x8af700ba841f30e0a3fcb0ee4c4a9d223e1efa05"
now=int(time.time()*1000); day180=now-180*86400*1000
fills=get_fills(A,day180)
fills.sort(key=lambda f: int(f["time"]))
print(f"wallet {A}  fills in 180d: {len(fills)}")

# reconstruct round trips per coin using startPosition to detect direction
def reconstruct(fills):
    trips=[]
    cur={}  # coin -> dict(entry_price, t, side, size)
    for f in fills:
        coin=f["coin"]; t=int(f["time"]); px=float(f["px"]); sz=float(f["sz"])
        d=f["dir"]; cp=float(f.get("closedPnl","0") or 0)
        if "Open" in d:
            # new position: open a stack
            cur.setdefault(coin, {"open":0.0,"close":0.0})
            cur[coin]["open"] += cp if "Short" in d else 0
            cur[coin]["_last_open"] = (px, t, sz, d)
        elif "Close" in d:
            o = cur.get(coin)
            if o is not None and o.get("_last_open"):
                lpx, lt, _, od = o["_last_open"]
                trips.append({"coin":coin,"entry":lpx,"exit":px,
                              "t_in":lt,"t_out":t,"dir":d,"cp":cp})
                o["_last_open"] = None
            else:
                # unpaired close (window cut / partial) - record without entry
                trips.append({"coin":coin,"entry":px,"exit":px,
                              "t_in":t,"t_out":t,"dir":d,"cp":cp})
            # clean stale coin state occasionally
            if o is not None and not any(o.get(k) for k in ("open","close")):
                pass
    return trips

trips=reconstruct(fills)
print("reconstructed round trips:", len(trips))
wins=[t for t in trips if t["cp"]>0]; loss=[t for t in trips if t["cp"]<=0]
print(f"wins={len(wins)} losses={len(loss)} wr={len(wins)/max(1,len(trips)):.2%}")

# holding periods
holds=sorted((t["t_out"]-t["t_in"])/3600 for t in trips)
hold=holds[len(holds)//2] if holds else 0
print(f"median hold: {hold:.2f}h   min={holds[0]:.1f}h max={holds[-1]:.1f}h" if holds else "n/a")

# round-trip return (price move) and per-trade distribution
rets=[ (t["exit"]/t["entry"]-1)*100 for t in trips if t["entry"]>0]
if rets:
    rets.sort()
    print(f"median round-trip price move: {rets[len(rets)//2]:+.2f}%   "
          f"p25={rets[len(rets)//4]:+.2f}% p75={rets[3*len(rets)//4]:+.2f}%")

# which coins dominate
coinc=defaultdict(int); coincp=defaultdict(float)
for t in trips:
    coinc[t["coin"]]+=1; coincp[t["coin"]]+=t["cp"]
print("\ntop coins by trip count:",
      sorted(coinc.items(),key=lambda x:-x[1])[:12])
print("top coins by pnl:",
      sorted(coincp.items(),key=lambda x:-x[1])[:8])

# sample a few winning trip timestamps for context later
print("\nsample winning entries (coin, entry_px, t_in):")
for t in [x for x in trips if x["cp"]>0][:10]:
    print(f'  {t["coin"]:<8} entry={t["entry"]:<10} out={t["exit"]:<10} '
          f'hold={ (t["t_out"]-t["t_in"])/3600:>6.1f}h  cp={t["cp"]:>9.1f}')

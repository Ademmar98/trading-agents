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

def analyze(addr, label):
    fills=get_fills(addr, int(time.time()*1000)-180*86400*1000)
    fills.sort(key=lambda f:int(f["time"]))
    # dirs distribution + hold time: how many open->close within same second/bar
    opens=closes=0
    close_ms=[]
    open_same_price_close=0
    for f in fills:
        d=f["dir"]
        if "Open" in d: opens+=1
        elif "Close" in d:
            closes+=1; close_ms.append((int(f["time"]), float(f["px"])))
    # match last open to each close within tight window
    import bisect
    open_ts=[int(f["time"]) for f in fills if "Open" in f["dir"]]
    open_px=[float(f["px"]) for f in fills if "Open" in f["dir"]]
    n_sampled=0; holds=[]
    for ct,cpx in close_ms:
        # find most recent open before this close
        j=bisect.bisect_right(open_ts, ct)-1
        if j>=0:
            dt=(ct-open_ts[j])/1000.0
            holds.append(dt)
            n_sampled+=1
    holds.sort()
    med=holds[len(holds)//2] if holds else 0
    p25=holds[len(holds)//4] if holds else 0
    p75=holds[3*len(holds)//4] if holds else 0
    print(f"---- {label} {addr[:12]} --")
    print(f"  fills={len(fills)} opens={opens} closes={closes}")
    print(f"  open->close gap seconds: p25={p25:.2f} med={med:.2f} p75={p75:.2f}")
    tot=sum(j is not None for j in [1])  # noop
    # distinct coins
    coins=set(f["coin"] for f in fills)
    print(f"  distinct coins: {len(coins)}  sample: {sorted(coins)[:6]}")
    print()

for a,lbl in [("0x72655b3926db3afbe914a53b0604905af7ce11a5","100%WR #1 72655"),
              ("0x183d0567c33e7591c22540e45d2f74730b42a0ca","96.8%WR 183d0"),
              ("0x8af700ba841f30e0a3fcb0ee4c4a9d223e1efa05","75.8%WR 8af70")]:
    try:
        analyze(a,lbl)
    except Exception as e:
        print(f"{lbl}: error {e}")
    time.sleep(0.4)

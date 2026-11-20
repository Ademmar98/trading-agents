import json, time, requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

API = "https://api.hyperliquid.xyz/info"
retry = Retry(total=5, backoff_factor=1.5, status_forcelist=[429,500,502,503,504], allowed_methods=["POST","GET"])
s = requests.Session()
s.mount("https://", HTTPAdapter(max_retries=retry, pool_connections=1, pool_maxsize=1))

def get_fills(addr, start_ms, end_ms=None):
    end_ms = end_ms or int(time.time()*1000)
    out=[]; cursor=None
    for _ in range(40):
        body={"type":"userFillsByTime","user":addr,"startTime":start_ms,"endTime":end_ms}
        if cursor: body["cursor"]=cursor
        r=s.post(API,json=body,timeout=30)
        if r.status_code!=200: break
        d=r.json()
        if isinstance(d,list):
            out.extend(d); break
        f=d.get("fills",[])
        if not f: break
        out.extend(f)
        cursor=d.get("cursor")
        if not cursor: break
        time.sleep(0.2)
    return out

# strongest directional long wallets (not market-makers)
addrs = {
 "8af700":"0x8af700ba841f30e0a3fcb0ee4c4a9d223e1efa05",
 "183d0567":"0x183d0567c33e7591c22540e45d2f74730b42a0ca",
 "24eeb524":"0x24eeb5249ce20cfb667e26b463c0ef394588d65f",
}
now=int(time.time()*1000)
day180=now-180*86400*1000
for tag,a in addrs.items():
    f=get_fills(a,day180)
    print(f"\n########## {tag} {a}  n_fills={len(f)} ##########")
    if f:
        rec=f[0]
        print("FIELDS:", sorted(rec.keys()))
        print("sample:", json.dumps(rec, indent=1)[:1500])
    time.sleep(0.3)

"""Fetch daily bars for ~12 MORE liquid Alpaca majors (to thicken the
cross-section) ON the VPS, pull into daily_bars/. Keeps whatever has data."""
import re

pw = re.search(r"password='([^']+)'",
               open(r"C:\Users\DELL\OneDrive\1m\_unleash_vps.py").read()).group(1)

REMOTE = r'''
import csv, datetime, json, time, urllib.parse, urllib.request, os
SYMS = ["MKR/USD","GRT/USD","SUSHI/USD","YFI/USD","CRV/USD","BAT/USD",
        "MATIC/USD","ALGO/USD","ATOM/USD","FIL/USD","MANA/USD","SAND/USD",
        "SHIB/USD","NEAR/USD","APE/USD","AAVE/USD","XTZ/USD","DASH/USD"]
START="2021-01-01T00:00:00Z"; END="2026-07-19T00:00:00Z"
os.makedirs("/tmp/daily_bars", exist_ok=True)
def epoch(iso): return int(datetime.datetime.fromisoformat(iso.replace("Z","+00:00")).timestamp())
for sym in SYMS:
    rows, token = [], None
    base=("https://data.alpaca.markets/v1beta3/crypto/us/bars"
          f"?symbols={urllib.parse.quote(sym,safe='')}&timeframe=1Day&start={START}&end={END}&limit=10000")
    try:
        while True:
            url=base+(f"&page_token={token}" if token else "")
            for a in range(4):
                try: data=json.loads(urllib.request.urlopen(url,timeout=30).read()); break
                except Exception: time.sleep(2*(a+1))
            else: raise RuntimeError("net")
            for b in data.get("bars",{}).get(sym,[]):
                rows.append([epoch(b["t"]), b["o"], b["h"], b["l"], b["c"], b["v"]])
            token=data.get("next_page_token")
            if not token: break
            time.sleep(0.1)
    except Exception as e:
        print(sym, "SKIP", e); continue
    if len(rows) < 300:
        print(sym, "too short", len(rows)); continue
    name=sym.replace("/","")
    with open(f"/tmp/daily_bars/{name}.csv","w",newline="") as f:
        w=csv.writer(f); w.writerow(["ts","open","high","low","close","volume"]); w.writerows(rows)
    print(name, len(rows))
print("DONE")
'''

import paramiko, os
ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect("69.48.202.100", username="root", password=pw, timeout=20)
print("fetching more symbols on VPS...")
_, stdout, stderr = ssh.exec_command(f"python3 - << 'EOF'\n{REMOTE}\nEOF", timeout=600)
print(stdout.read().decode())
err = stderr.read().decode().strip()
if err:
    print("STDERR:", err[-400:])
out = r"C:\Users\DELL\AppData\Local\Temp\claude\C--Users-DELL-OneDrive-1m--claude-worktrees-festive-pare-bbce48\ca03efac-2fb5-4b2b-4a2d-3c6c93082860\scratchpad\quant\daily_bars"
sftp = ssh.open_sftp()
for name in sftp.listdir("/tmp/daily_bars"):
    sftp.get(f"/tmp/daily_bars/{name}", os.path.join(out, name))
sftp.close()
ssh.close()
print("pulled all; local universe now:")
import glob
print(sorted(os.path.basename(f)[:-4] for f in glob.glob(os.path.join(out, "*.csv"))))

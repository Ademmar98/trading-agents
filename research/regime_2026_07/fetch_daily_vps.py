"""Fetch daily bars for a broad major set via Alpaca (keyless) ON the VPS,
write CSVs to /tmp, pull them back. Stdlib only."""
import re

pw = re.search(r"password='([^']+)'",
               open(r"C:\Users\DELL\OneDrive\1m\_unleash_vps.py").read()).group(1)

REMOTE = r'''
import csv, datetime, json, time, urllib.parse, urllib.request, os
SYMS = ["BTC/USD","ETH/USD","SOL/USD","LINK/USD","DOGE/USD","AVAX/USD",
        "LTC/USD","BCH/USD","UNI/USD","AAVE/USD","XRP/USD","DOT/USD"]
START="2021-01-01T00:00:00Z"; END="2026-07-19T00:00:00Z"
os.makedirs("/tmp/daily_bars", exist_ok=True)
def epoch(iso): return int(datetime.datetime.fromisoformat(iso.replace("Z","+00:00")).timestamp())
for sym in SYMS:
    rows, token = [], None
    base=("https://data.alpaca.markets/v1beta3/crypto/us/bars"
          f"?symbols={urllib.parse.quote(sym,safe='')}&timeframe=1Day&start={START}&end={END}&limit=10000")
    while True:
        url=base+(f"&page_token={token}" if token else "")
        for a in range(4):
            try: data=json.loads(urllib.request.urlopen(url,timeout=30).read()); break
            except Exception: time.sleep(2*(a+1))
        else: raise SystemExit(f"fail {sym}")
        for b in data.get("bars",{}).get(sym,[]):
            rows.append([epoch(b["t"]), b["o"], b["h"], b["l"], b["c"], b["v"]])
        token=data.get("next_page_token")
        if not token: break
        time.sleep(0.1)
    name=sym.replace("/","")
    with open(f"/tmp/daily_bars/{name}.csv","w",newline="") as f:
        w=csv.writer(f); w.writerow(["ts","open","high","low","close","volume"]); w.writerows(rows)
    print(name, len(rows))
print("FETCH DONE")
'''

import paramiko
ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect("69.48.202.100", username="root", password=pw, timeout=20)
print("connected; fetching daily bars on VPS...")
_, stdout, stderr = ssh.exec_command(f"python3 - << 'EOF'\n{REMOTE}\nEOF", timeout=600)
print(stdout.read().decode())
err = stderr.read().decode().strip()
if err:
    print("STDERR:", err[-500:])

import os
out = r"C:\Users\DELL\AppData\Local\Temp\claude\C--Users-DELL-OneDrive-1m--claude-worktrees-festive-pare-bbce48\ca03efac-2fb5-4b2b-4a2d-3c6c93082860\scratchpad\quant\daily_bars"
os.makedirs(out, exist_ok=True)
sftp = ssh.open_sftp()
for name in sftp.listdir("/tmp/daily_bars"):
    sftp.get(f"/tmp/daily_bars/{name}", os.path.join(out, name))
    print("pulled", name)
sftp.close()
ssh.close()

"""Verify the unleashed cycle on the VPS."""
import paramiko

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('69.48.202.100', username='root', password='CyVMhAYs8W3or', timeout=15)

def run(cmd, t=60):
    _, o, e = ssh.exec_command(cmd, timeout=t)
    return (o.read().decode(errors='replace') + e.read().decode(errors='replace')).strip()

print('=== JOURNAL ===')
print(run('tail -40 /root/firm/data/logs/journal.jsonl | grep -iE "regime_agent|Regime scan|Compliance:|Execution plan|Trade OPENED|opened|debate|Sizing:" | tail -18'))
print('\n=== SUMMARY ===')
print(run('curl -s localhost/api/summary'))
print('\n=== POSITIONS (count + first 12) ===')
print(run("curl -s localhost/api/positions | python3 -c \"import json,sys; p=json.load(sys.stdin); print(len(p)); [print(r['symbol'], round(r['quantity'],6), r['entry_price']) for r in p[:12]]\""))
ssh.close()
